"""Automated file organizer and cleanup utility.

The module scans a directory, classifies each file by extension, and moves it
into a category subfolder. Moves run concurrently on the ``asyncio`` engine in
:mod:`async_organizer` with a bounded number of files in flight, collisions are
resolved by renaming, duplicates are detected by SHA-256 content hash, and every
action is recorded so it can be serialised to a JSON activity log.

:class:`FileOrganizer` remains the blocking, thread-agnostic API: it drives the
asyncio engine internally, so existing callers are unaffected.

Typical use::

    from organizer import FileOrganizer, write_json_log

    report = FileOrganizer().organize("~/Downloads", dry_run=True)
    print(report.moved, "files would move")
    write_json_log(report, "organizer.log")
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

from edge_cases import iter_files
from errors import ValidationError
from persistence import atomic_write_json, read_json_document, stamp_schema

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from async_organizer import AsyncFileOrganizer

#: Default extension → category mapping.
CATEGORY_MAP: Dict[str, str] = {
    # Documents
    ".pdf": "Documents",
    ".doc": "Documents",
    ".docx": "Documents",
    ".txt": "Documents",
    ".md": "Documents",
    ".rtf": "Documents",
    ".odt": "Documents",
    ".xls": "Documents",
    ".xlsx": "Documents",
    ".csv": "Documents",
    ".ppt": "Documents",
    ".pptx": "Documents",
    # Images
    ".jpg": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".gif": "Images",
    ".bmp": "Images",
    ".svg": "Images",
    ".webp": "Images",
    ".tiff": "Images",
    # Videos
    ".mp4": "Videos",
    ".mkv": "Videos",
    ".mov": "Videos",
    ".avi": "Videos",
    ".webm": "Videos",
    # Audio
    ".mp3": "Audio",
    ".wav": "Audio",
    ".flac": "Audio",
    ".aac": "Audio",
    ".ogg": "Audio",
    # Archives
    ".zip": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".bz2": "Archives",
    # Code
    ".py": "Code",
    ".js": "Code",
    ".ts": "Code",
    ".java": "Code",
    ".c": "Code",
    ".cpp": "Code",
    ".go": "Code",
    ".rs": "Code",
    ".sh": "Code",
    ".html": "Code",
    ".css": "Code",
    ".json": "Code",
}

#: Fallback category for unrecognised extensions.
DEFAULT_CATEGORY: str = "Other"

#: All category folder names, including the fallback.
CATEGORIES: List[str] = sorted(set(CATEGORY_MAP.values()) | {DEFAULT_CATEGORY})


def _suffix_of(path: Union[str, Path]) -> str:
    """Return the lower-cased final extension of ``path``.

    Identical to ``Path(path).suffix.lower()`` — including the awkward cases a
    hand-rolled split gets wrong (``"trailing."`` has *no* suffix, ``".bashrc"``
    is all stem, ``"..x"`` has the suffix ``".x"``) — but without building a
    :class:`~pathlib.Path`. :func:`category_for` runs once per file inside the
    organizer's hot loop, where the :mod:`pathlib` parsing overhead dominated the
    Day 12 profile: roughly 10 µs per call, of which the extension lookup itself
    is a few percent.

    Args:
        path: The file (or filename) to inspect.

    Returns:
        The extension including its leading dot, lower-cased, or ``""`` when the
        final path component has none.
    """
    name = os.path.basename(os.fspath(path))
    head, dot, tail = name.rpartition(".")
    if not dot or not head or not tail:
        return ""
    return (dot + tail).lower()


def category_for(path: Union[str, Path], mapping: Optional[Dict[str, str]] = None) -> str:
    """Return the category folder a file belongs to.

    Args:
        path: The file (or filename) to classify.
        mapping: Optional custom extension → category mapping. Defaults to
            :data:`CATEGORY_MAP`.

    Returns:
        The category name, or :data:`DEFAULT_CATEGORY` when the extension is
        unknown.
    """
    table = mapping if mapping is not None else CATEGORY_MAP
    return table.get(_suffix_of(path), DEFAULT_CATEGORY)


def _sha256_digest(path: Union[str, Path], chunk_size: int = 65536) -> str:
    """Hash a file's contents without checking that it exists first.

    The unchecked core of :func:`sha256_of_file`. :func:`find_duplicates` has
    already stat-ed and listed every candidate, so the extra ``is_file()`` probe
    would cost one ``stat`` per file for an answer it already has.

    Args:
        path: Path to an existing file.
        chunk_size: Read buffer size in bytes.

    Returns:
        The lowercase hexadecimal digest.

    Raises:
        OSError: If the file cannot be opened or read.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_of_file(path: Union[str, Path], chunk_size: int = 65536) -> str:
    """Compute the SHA-256 digest of a file's contents.

    Args:
        path: Path to the file.
        chunk_size: Read buffer size in bytes.

    Returns:
        The lowercase hexadecimal digest.

    Raises:
        FileNotFoundError: If ``path`` is not an existing file.
        OSError: If the file cannot be read.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Not a file: {file_path}")
    return _sha256_digest(file_path, chunk_size)


def find_duplicates(directory: Union[str, Path]) -> Dict[str, List[Path]]:
    """Group files in ``directory`` by content hash.

    Hashing reads every byte, so it is preceded by a size check: two files can
    only be identical if they are the same length, which means only files whose
    size occurs more than once need to be read at all. On a directory with no
    duplicates that turns a full read of every file into one ``stat`` each — the
    Day 12 profile measured a 4× drop on a 600-file fixture, with identical
    output.

    Args:
        directory: Directory to scan (non-recursive).

    Returns:
        A mapping of SHA-256 digest → list of paths sharing that content, for
        every hash with **two or more** files. An empty dict means no
        duplicates.

    Raises:
        NotADirectoryError: If ``directory`` is not a directory.
        PermissionError: If ``directory`` cannot be listed.
    """
    root = Path(directory).expanduser()
    by_size: Dict[int, List[Path]] = {}
    for entry in iter_files(root):
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        by_size.setdefault(size, []).append(entry)

    by_hash: Dict[str, List[Path]] = {}
    for candidates in by_size.values():
        if len(candidates) < 2:
            continue
        for entry in candidates:
            try:
                digest = _sha256_digest(entry)
            except OSError:
                continue
            by_hash.setdefault(digest, []).append(entry)
    return {digest: paths for digest, paths in by_hash.items() if len(paths) > 1}


def unique_destination(
    source: Union[str, Path],
    dest_dir: Union[str, Path],
    *,
    max_attempts: int = 1000,
) -> Path:
    """Return a collision-free destination path inside ``dest_dir``.

    If ``dest_dir/<name>`` already exists, ``" (1)"``, ``" (2)"`` … is appended
    to the stem until a free name is found. The search is bounded, so a
    pathological directory cannot trap the tool in an infinite loop.

    Args:
        source: The file being moved (its name is preserved).
        dest_dir: Target directory (need not exist yet).
        max_attempts: Maximum number of ``" (n)"`` suffixes to try before
            giving up.

    Returns:
        A :class:`~pathlib.Path` that does not yet exist.

    Raises:
        FileExistsError: If no free name is found within ``max_attempts``.
    """
    source_path = Path(source)
    target_dir = Path(dest_dir)
    candidate = target_dir / source_path.name
    if not candidate.exists():
        return candidate

    stem, suffix = source_path.stem, source_path.suffix
    counter = 1
    while counter <= max_attempts:
        candidate = target_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
    raise FileExistsError(
        f"could not find a free name for {source_path.name!r} in {target_dir} "
        f"after {max_attempts} attempts"
    )


@dataclass
class MoveRecord:
    """The outcome of processing a single file.

    Attributes:
        source: Original path.
        destination: Destination path (empty when only planned or errored).
        category: Category folder the file was assigned to.
        status: One of ``"moved"``, ``"planned"``, ``"skipped"``, ``"error"``.
        detail: Optional human-readable explanation.
    """

    source: str
    destination: str
    category: str
    status: str
    detail: str = ""


@dataclass
class OrganizeReport:
    """Aggregated result of an :meth:`FileOrganizer.organize` run.

    Attributes:
        directory: The directory that was organised.
        dry_run: Whether this was a preview run.
        records: Per-file outcomes.
        duplicates: Content-hash → list of duplicate paths (if detected).
        duration_ms: Wall-clock time the run took, in milliseconds. Zero for a
            report assembled by hand.
        engine: Which execution engine produced the report; ``"asyncio"`` for
            every run since Day 11.
    """

    directory: str
    dry_run: bool = False
    records: List[MoveRecord] = field(default_factory=list)
    duplicates: Dict[str, List[str]] = field(default_factory=dict)
    duration_ms: float = 0.0
    engine: str = "asyncio"

    @property
    def moved(self) -> int:
        """Number of files actually moved."""
        return sum(1 for r in self.records if r.status == "moved")

    @property
    def planned(self) -> int:
        """Number of files that would move in a dry run."""
        return sum(1 for r in self.records if r.status == "planned")

    @property
    def skipped(self) -> int:
        """Number of files left in place."""
        return sum(1 for r in self.records if r.status == "skipped")

    @property
    def errors(self) -> List[MoveRecord]:
        """Records that ended in an error."""
        return [r for r in self.records if r.status == "error"]

    @property
    def total_processed(self) -> int:
        """Total number of files considered."""
        return len(self.records)

    def to_dict(self) -> Dict[str, object]:
        """Return the report as a JSON-serialisable dictionary."""
        return {
            "directory": self.directory,
            "dry_run": self.dry_run,
            "summary": {
                "total_processed": self.total_processed,
                "moved": self.moved,
                "planned": self.planned,
                "skipped": self.skipped,
                "errors": len(self.errors),
                "duration_ms": self.duration_ms,
                "engine": self.engine,
            },
            "duplicates": self.duplicates,
            "records": [asdict(r) for r in self.records],
        }


class FileOrganizer:
    """Organises a directory into category subfolders.

    The blocking facade over :class:`async_organizer.AsyncFileOrganizer`. Moves
    are executed on an asyncio event loop with at most ``max_workers`` files in
    flight; the call itself still blocks and returns a report, so callers need
    no knowledge of asyncio.

    Args:
        mapping: Extension → category mapping. Defaults to :data:`CATEGORY_MAP`.
        max_workers: Maximum number of moves in flight at once. Kept under its
            original name for backwards compatibility; it is now the
            concurrency bound of the asyncio engine rather than a thread-pool
            size.
        mover: Optional replacement for the move call. Tests inject a fake to
            observe scheduling without real I/O.
    """

    def __init__(
        self,
        mapping: Optional[Dict[str, str]] = None,
        max_workers: int = 4,
        *,
        mover: Optional[Callable[[Path, Path], Any]] = None,
    ) -> None:
        if max_workers < 1:
            raise ValidationError(
                f"max_workers must be at least 1, got {max_workers}.",
                hint="use a pool size of 1 or more",
                context={"field": "max_workers", "value": max_workers},
            )
        self.mapping: Dict[str, str] = dict(mapping) if mapping else dict(CATEGORY_MAP)
        self.max_workers = max_workers
        self.mover = mover

    def _target_dir(self, root: Path, category: str) -> Path:
        """Return the category subfolder for ``category`` under ``root``."""
        return root / category

    def engine(self, concurrency: Optional[int] = None) -> "AsyncFileOrganizer":
        """Build the asyncio engine this facade delegates to.

        Args:
            concurrency: Explicit concurrency bound; defaults to
                :attr:`max_workers`.

        Returns:
            A configured :class:`async_organizer.AsyncFileOrganizer`.
        """
        # Imported here rather than at module scope: async_organizer imports the
        # report types from this module, so a top-level import would be circular.
        from async_organizer import AsyncFileOrganizer

        return AsyncFileOrganizer(
            mapping=self.mapping,
            concurrency=concurrency if concurrency is not None else self.max_workers,
            mover=self.mover,
        )

    def organize(
        self,
        directory: Union[str, Path],
        dry_run: bool = False,
        detect_duplicates: bool = False,
    ) -> OrganizeReport:
        """Organise every file directly inside ``directory``.

        Category subfolders are never themselves moved. Moves are dispatched to
        the asyncio engine with a bounded number in flight, and the results are
        collected in deterministic source order.

        Args:
            directory: Directory to organise.
            dry_run: When ``True``, only report what *would* happen.
            detect_duplicates: When ``True``, compute SHA-256 hashes and record
                groups of identical files.

        Returns:
            An :class:`OrganizeReport`.

        Raises:
            NotADirectoryError: If ``directory`` is not a directory.
            PermissionError: If ``directory`` cannot be listed.
        """
        return self.engine().organize_sync(
            directory,
            dry_run=dry_run,
            detect_duplicates=detect_duplicates,
        )


def write_json_log(report: OrganizeReport, path: Union[str, Path]) -> Path:
    """Write an organisation report to a JSON activity log.

    The log is published atomically (temporary file + :func:`os.replace`), so an
    interrupted run leaves the previous log intact rather than a truncated one,
    and it carries the persistence schema version so :func:`read_json_log` can
    reject a log written by an incompatible tool.

    Args:
        report: The report to serialise.
        path: Destination ``.json`` path. Parent directories are created.

    Returns:
        The resolved destination :class:`~pathlib.Path`.

    Raises:
        PersistenceError: If the log cannot be written; the previous log is
            left unchanged.
    """
    out_path = Path(path).expanduser()
    atomic_write_json(out_path, stamp_schema(report.to_dict()), indent=2)
    return out_path


def read_json_log(path: Union[str, Path]) -> Dict[str, Any]:
    """Read a JSON activity log written by :func:`write_json_log`.

    Args:
        path: The log to read.

    Returns:
        The decoded log, including its ``schema_version`` and the ``summary``
        and ``records`` sections.

    Raises:
        MissingFileError: If ``path`` is not an existing file.
        PersistenceError: If the log is not valid JSON, or lacks the expected
            sections.
        SchemaVersionError: If the log's schema version is missing or
            unsupported.
    """
    return read_json_document(
        Path(path).expanduser(),
        field="activity log",
        require_keys=("summary", "records"),
    )
