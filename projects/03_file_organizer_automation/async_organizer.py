"""Asyncio engine for the file organizer (Day 11).

The organizer's real cost is the filesystem call — ``mkdir`` plus ``move`` per
file — not the Python around it. Those calls release the GIL while they wait on
the kernel, so running them on an ``asyncio`` event loop gives the same overlap
a thread pool does, with a hard, explicit ceiling on how many can be in flight
at once.

This module provides:

* :class:`AsyncFileOrganizer` — the ``asyncio`` implementation, using a worker
  pool of ``concurrency`` tasks pulling from a queue so the number of in-flight
  moves is bounded by construction rather than by hope.
* :func:`run_coroutine_sync` — a bridge that lets the synchronous
  :class:`organizer.FileOrganizer` keep its blocking API while delegating to
  this engine.
* :class:`_DestinationReserver` — collision resolution that stays correct when
  two workers target the same category folder at the same time.

Typical use::

    import asyncio
    from async_organizer import AsyncFileOrganizer

    report = asyncio.run(AsyncFileOrganizer(concurrency=8).organize("~/Downloads"))
    print(report.moved, "files moved in", f"{report.duration_ms:.0f} ms")

The synchronous API in :mod:`organizer` is unchanged: ``FileOrganizer.organize``
now runs this engine internally and returns the identical
:class:`~organizer.OrganizeReport`.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, TypeVar, Union

from edge_cases import iter_files
from errors import ValidationError
from organizer import (
    CATEGORY_MAP,
    MoveRecord,
    OrganizeReport,
    category_for,
    find_duplicates,
)
from structured_logging import structured_extra

__all__ = [
    "DEFAULT_CONCURRENCY",
    "MAX_CONCURRENCY",
    "AsyncFileOrganizer",
    "run_coroutine_sync",
]

logger = logging.getLogger(__name__)

#: Default number of moves allowed in flight at once. Filesystem work is
#: latency-bound, so a modest fan-out is enough to saturate it without
#: thrashing the disk.
DEFAULT_CONCURRENCY: int = 8

#: Upper bound accepted for ``concurrency``. Beyond this the disk queue, not
#: the event loop, becomes the bottleneck and per-file latency degrades.
MAX_CONCURRENCY: int = 64

T = TypeVar("T")

#: A callable that moves ``source`` to ``destination``. Injected by tests so a
#: fake mover can simulate latency without touching the filesystem.
Mover = Callable[[Path, Path], Any]


def _default_mover(source: Path, destination: Path) -> None:
    """Move ``source`` to ``destination`` with :func:`shutil.move`.

    Args:
        source: File to move.
        destination: Target path; must not already exist.
    """
    shutil.move(str(source), str(destination))


def run_coroutine_sync(factory: Callable[[], Awaitable[T]]) -> T:
    """Run an awaitable to completion from synchronous code.

    ``asyncio.run`` refuses to start when the calling thread already has a
    running event loop — which happens inside an async test, a notebook, or an
    application that embeds the organizer in its own loop. In that case the
    coroutine is driven on a private loop in a short-lived worker thread, so the
    synchronous API works from either context.

    Args:
        factory: A zero-argument callable returning the awaitable to run. A
            factory (rather than a coroutine object) is required so the
            coroutine is created on the thread that will await it.

    Returns:
        Whatever the coroutine returns.

    Raises:
        BaseException: Anything the coroutine raises is re-raised unchanged, so
            callers see the same exceptions as the thread-pool version.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()


class _DestinationReserver:
    """Hand out collision-free destination paths to concurrent workers.

    :func:`organizer.unique_destination` is correct for a single thread but racy
    once two workers classify into the same category folder: both can see the
    same free name before either has moved its file. The reserver serialises the
    "look, then claim" step behind an :class:`asyncio.Lock` and remembers the
    names it has already handed out, so a claimed-but-not-yet-created path is
    never issued twice.

    Args:
        max_attempts: How many ``" (n)"`` suffixes to try before giving up.
    """

    def __init__(self, max_attempts: int = 1000) -> None:
        self._lock = asyncio.Lock()
        self._taken: Set[str] = set()
        self._max_attempts = max_attempts

    async def reserve(self, source: Union[str, Path], dest_dir: Union[str, Path]) -> Path:
        """Claim a free destination path for ``source`` inside ``dest_dir``.

        Args:
            source: The file being moved; its name is preserved.
            dest_dir: Target directory (need not exist yet).

        Returns:
            A path that neither exists on disk nor has been handed to another
            worker.

        Raises:
            FileExistsError: If no free name is found within ``max_attempts``.
        """
        source_path = Path(source)
        target_dir = Path(dest_dir)
        async with self._lock:
            candidate = target_dir / source_path.name
            if self._is_free(candidate):
                return self._claim(candidate)

            stem, suffix = source_path.stem, source_path.suffix
            for counter in range(1, self._max_attempts + 1):
                candidate = target_dir / f"{stem} ({counter}){suffix}"
                if self._is_free(candidate):
                    return self._claim(candidate)
        raise FileExistsError(
            f"could not find a free name for {source_path.name!r} in {target_dir} "
            f"after {self._max_attempts} attempts"
        )

    def _is_free(self, candidate: Path) -> bool:
        """Return whether ``candidate`` is unclaimed and absent from disk."""
        return str(candidate) not in self._taken and not candidate.exists()

    def _claim(self, candidate: Path) -> Path:
        """Record ``candidate`` as taken and return it."""
        self._taken.add(str(candidate))
        return candidate


class AsyncFileOrganizer:
    """Organise a directory into category subfolders on an asyncio event loop.

    Args:
        mapping: Extension → category mapping. Defaults to
            :data:`organizer.CATEGORY_MAP`.
        concurrency: Maximum number of files being moved at once. Must be
            between 1 and :data:`MAX_CONCURRENCY`.
        mover: Optional replacement for the move call, used by tests to observe
            scheduling without real I/O.

    Attributes:
        peak_in_flight: The largest number of moves that were in flight
            simultaneously during the last run — the observable proof that the
            bound held.
    """

    def __init__(
        self,
        mapping: Optional[Dict[str, str]] = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        *,
        mover: Optional[Mover] = None,
    ) -> None:
        if not isinstance(concurrency, int) or isinstance(concurrency, bool):
            raise ValidationError(
                f"concurrency must be an integer, got {type(concurrency).__name__}.",
                hint="pass a whole number such as 8",
                context={"field": "concurrency", "value": str(concurrency)},
            )
        if concurrency < 1 or concurrency > MAX_CONCURRENCY:
            raise ValidationError(
                f"concurrency must be between 1 and {MAX_CONCURRENCY}, got {concurrency}.",
                hint=f"choose a value in the range 1–{MAX_CONCURRENCY}",
                context={"field": "concurrency", "value": concurrency},
            )
        self.mapping: Dict[str, str] = dict(mapping) if mapping else dict(CATEGORY_MAP)
        self.concurrency = concurrency
        self._mover: Mover = mover if mover is not None else _default_mover
        self.peak_in_flight: int = 0
        self._in_flight: int = 0

    # -- single file ----------------------------------------------------
    async def _process(
        self,
        source: Path,
        root: Path,
        dry_run: bool,
        reserver: _DestinationReserver,
    ) -> MoveRecord:
        """Classify one file and, unless this is a dry run, move it.

        The blocking parts — ``mkdir`` and the move itself — are handed to the
        default executor so the event loop keeps scheduling other files while
        the kernel works.

        Args:
            source: File to process.
            root: Directory being organised.
            dry_run: When ``True``, only plan the move.
            reserver: Shared destination allocator for this run.

        Returns:
            A :class:`~organizer.MoveRecord` describing the outcome. Filesystem
            failures are captured in the record rather than raised, so one bad
            file cannot abort the run.
        """
        category = category_for(source, self.mapping)
        dest_dir = root / category
        started = time.perf_counter()
        self._in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        try:
            destination = await reserver.reserve(source, dest_dir)
            if dry_run:
                return MoveRecord(
                    str(source),
                    str(destination),
                    category,
                    "planned",
                    "dry run — no change made",
                )
            await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(self._mover, source, destination)
            return MoveRecord(str(source), str(destination), category, "moved")
        except PermissionError as exc:
            return MoveRecord(str(source), "", category, "error", f"permission denied: {exc}")
        except FileExistsError as exc:
            return MoveRecord(str(source), "", category, "error", f"exists: {exc}")
        except OSError as exc:
            return MoveRecord(str(source), "", category, "error", f"os error: {exc}")
        finally:
            self._in_flight -= 1
            logger.debug(
                "file processed",
                extra=structured_extra(
                    source=str(source),
                    category=category,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
                ),
            )

    # -- whole directory ------------------------------------------------
    async def organize(
        self,
        directory: Union[str, Path],
        dry_run: bool = False,
        detect_duplicates: bool = False,
    ) -> OrganizeReport:
        """Organise every file directly inside ``directory``.

        Files are pulled from a queue by ``concurrency`` worker tasks, so no
        more than that many moves are ever in flight. Records are returned in
        the deterministic order :func:`edge_cases.iter_files` produced, not in
        completion order, so two runs over the same directory report identically.

        Args:
            directory: Directory to organise.
            dry_run: When ``True``, only report what *would* happen.
            detect_duplicates: When ``True``, compute SHA-256 hashes on the
                executor and record groups of identical files.

        Returns:
            An :class:`~organizer.OrganizeReport`, with ``duration_ms`` filled
            in for the run.

        Raises:
            NotADirectoryError: If ``directory`` is not a directory.
            PermissionError: If ``directory`` cannot be listed.
        """
        root = Path(directory).expanduser()
        files = iter_files(root)

        report = OrganizeReport(directory=str(root), dry_run=dry_run, engine="asyncio")
        started = time.perf_counter()
        self.peak_in_flight = 0

        if detect_duplicates:
            groups = await asyncio.to_thread(find_duplicates, root)
            report.duplicates = {
                digest: [str(p) for p in paths] for digest, paths in groups.items()
            }

        if not files:
            report.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return report

        reserver = _DestinationReserver()
        queue: "asyncio.Queue[Path]" = asyncio.Queue()
        for source in files:
            queue.put_nowait(source)
        records: Dict[str, MoveRecord] = {}

        async def worker() -> None:
            """Drain the queue until it is empty."""
            while True:
                try:
                    source = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    record = await self._process(source, root, dry_run, reserver)
                except Exception as exc:  # defensive: never lose a file silently
                    record = MoveRecord(
                        str(source),
                        "",
                        category_for(source, self.mapping),
                        "error",
                        f"unexpected: {exc}",
                    )
                finally:
                    queue.task_done()
                records[str(source)] = record

        workers: List["asyncio.Task[None]"] = [
            asyncio.create_task(worker(), name=f"organizer-worker-{index}")
            for index in range(min(self.concurrency, len(files)))
        ]
        await asyncio.gather(*workers)

        report.records = [records[str(source)] for source in files if str(source) in records]
        report.duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        logger.debug(
            "async organize complete",
            extra=structured_extra(
                directory=str(root),
                concurrency=self.concurrency,
                peak_in_flight=self.peak_in_flight,
                duration_ms=report.duration_ms,
            ),
        )
        return report

    # -- blocking wrapper ------------------------------------------------
    def organize_sync(
        self,
        directory: Union[str, Path],
        dry_run: bool = False,
        detect_duplicates: bool = False,
    ) -> OrganizeReport:
        """Blocking wrapper around :meth:`organize`.

        Args:
            directory: Directory to organise.
            dry_run: When ``True``, only report what *would* happen.
            detect_duplicates: When ``True``, also record duplicate groups.

        Returns:
            An :class:`~organizer.OrganizeReport`.
        """
        return run_coroutine_sync(
            lambda: self.organize(
                directory,
                dry_run=dry_run,
                detect_duplicates=detect_duplicates,
            )
        )
