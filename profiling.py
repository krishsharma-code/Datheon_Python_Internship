"""Deterministic micro-profiling harness for the Datheon tools (Day 12).

Day 12 asks a blunt question: *where does the time actually go?* Answering it
needs two things a stopwatch alone cannot give — a per-function breakdown of a
hot path, and a repeatable timing so a before/after comparison means something.

This module provides both, using only the standard library:

* :func:`profile_callable` runs a callable under :mod:`cProfile` and returns a
  :class:`ProfileResult` whose entries are pre-converted to milliseconds, so no
  caller has to remember that :mod:`pstats` counts in seconds;
* :func:`time_callable` runs a callable repeatedly and returns a
  :class:`TimingResult` carrying the best, median, mean and spread of the
  samples, because the *fastest* run is the one least polluted by scheduler
  noise;
* :func:`compare_timings` turns two :class:`TimingResult` objects into the
  speedup and percentage change quoted in ``docs/performance_profiling.md``.

It also ships a small CLI that builds a throwaway fixture, profiles one of the
five real hot paths, and prints either a table or ``--json``::

    python profiling.py --target organizer --files 400
    python profiling.py --target duplicates --json

Nothing here is imported by the five tools at run time — the harness exists for
developers and CI, and its own cost never lands on a user's path.
"""

from __future__ import annotations

import cProfile
import io
import json
import pstats
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from cli_contract import build_parser, run_cli
from errors import ValidationError

__all__ = [
    "DEFAULT_REPEATS",
    "DEFAULT_TOP",
    "FunctionStat",
    "ProfileResult",
    "TimingResult",
    "compare_timings",
    "profile_callable",
    "time_callable",
]

#: Number of timed repetitions used by :func:`time_callable` by default.
DEFAULT_REPEATS: int = 5

#: How many hot functions a report shows unless asked for more.
DEFAULT_TOP: int = 10

#: The statistic a profile is ranked by. ``tottime`` (time in the function
#: itself) is the default: it points at the code worth changing, whereas
#: ``cumtime`` mostly re-ranks the callers that contain it.
SORT_KEYS: Tuple[str, ...] = ("tottime", "cumtime", "calls")


@dataclass(frozen=True)
class FunctionStat:
    """One function's share of a profiled run.

    Attributes:
        function: ``module:lineno(function)`` as reported by :mod:`pstats`.
        primitive_calls: Calls that were not recursive re-entries.
        total_calls: All calls, recursive ones included.
        tottime_ms: Time spent in the function's own bytecode, in milliseconds.
        cumtime_ms: Time spent in the function and everything it called, in
            milliseconds.
    """

    function: str
    primitive_calls: int
    total_calls: int
    tottime_ms: float
    cumtime_ms: float

    def to_dict(self) -> Dict[str, Any]:
        """Return the statistic as a JSON-serialisable dictionary."""
        return {
            "function": self.function,
            "primitive_calls": self.primitive_calls,
            "total_calls": self.total_calls,
            "tottime_ms": self.tottime_ms,
            "cumtime_ms": self.cumtime_ms,
        }


@dataclass
class ProfileResult:
    """The outcome of profiling one callable under :mod:`cProfile`.

    Attributes:
        label: Human name of the profiled call, used in tables.
        iterations: How many times the callable was invoked inside the profile.
        wall_ms: Wall-clock time for all iterations, in milliseconds.
        stats: Per-function statistics, ranked by the requested sort key.
        sort_by: The statistic ``stats`` was ranked by.
    """

    label: str
    iterations: int = 1
    wall_ms: float = 0.0
    stats: List[FunctionStat] = field(default_factory=list)
    sort_by: str = "tottime"

    def top(self, count: int = DEFAULT_TOP) -> List[FunctionStat]:
        """Return the ``count`` hottest functions.

        Args:
            count: How many entries to return; must be positive.

        Returns:
            The first ``count`` entries of :attr:`stats`.

        Raises:
            ValidationError: If ``count`` is not positive.
        """
        if count < 1:
            raise ValidationError(
                f"count must be at least 1, got {count}.",
                hint="ask for at least one function",
                context={"field": "count", "value": count},
            )
        return self.stats[:count]

    def to_dict(self, count: Optional[int] = None) -> Dict[str, Any]:
        """Return the profile as a JSON-serialisable dictionary.

        Args:
            count: Optional limit on how many statistics to include; ``None``
                includes every function that was profiled.

        Returns:
            A dictionary with the run metadata and the ranked statistics.
        """
        selected = self.stats if count is None else self.top(count)
        return {
            "label": self.label,
            "iterations": self.iterations,
            "wall_ms": self.wall_ms,
            "sort_by": self.sort_by,
            "stats": [stat.to_dict() for stat in selected],
        }

    def render(self, count: int = DEFAULT_TOP) -> str:
        """Render the hottest functions as a fixed-width table.

        Args:
            count: How many rows to show.

        Returns:
            A multi-line string with a header line and one row per function.
        """
        header = f"{'tottime':>10}  {'cumtime':>10}  {'calls':>8}  function"
        rows = [header, "-" * len(header)]
        for stat in self.top(count):
            rows.append(
                f"{stat.tottime_ms:>9.3f}ms  {stat.cumtime_ms:>9.3f}ms  "
                f"{stat.total_calls:>8}  {stat.function}"
            )
        summary = (
            f"{self.label}: {self.iterations} iteration(s) in {self.wall_ms:.1f} ms "
            f"(ranked by {self.sort_by})"
        )
        return "\n".join([summary, ""] + rows)


@dataclass(frozen=True)
class TimingResult:
    """Repeated wall-clock timings for one callable.

    Attributes:
        label: Human name of the timed call.
        number: Invocations per sample.
        samples_ms: Every sample, in milliseconds, in the order taken.
        best_ms: Fastest sample — the estimate least affected by noise.
        median_ms: Median sample.
        mean_ms: Arithmetic mean of the samples.
        stdev_ms: Sample standard deviation; zero for a single sample.
    """

    label: str
    number: int
    samples_ms: Tuple[float, ...]
    best_ms: float
    median_ms: float
    mean_ms: float
    stdev_ms: float

    def to_dict(self) -> Dict[str, Any]:
        """Return the timings as a JSON-serialisable dictionary."""
        return {
            "label": self.label,
            "number": self.number,
            "samples_ms": list(self.samples_ms),
            "best_ms": self.best_ms,
            "median_ms": self.median_ms,
            "mean_ms": self.mean_ms,
            "stdev_ms": self.stdev_ms,
        }

    def render(self) -> str:
        """Render a one-line summary of the timings."""
        return (
            f"{self.label}: best {self.best_ms:.3f} ms, median {self.median_ms:.3f} ms, "
            f"mean {self.mean_ms:.3f} ms ± {self.stdev_ms:.3f} ms "
            f"over {len(self.samples_ms)} sample(s) of {self.number} call(s)"
        )


def profile_callable(
    func: Callable[..., Any],
    *args: Any,
    label: Optional[str] = None,
    iterations: int = 1,
    top: Optional[int] = None,
    sort_by: str = "tottime",
    **kwargs: Any,
) -> ProfileResult:
    """Profile ``func(*args, **kwargs)`` under :mod:`cProfile`.

    The profiler is started and stopped inside this call, so the report covers
    exactly the invocations requested and not the harness around them. The
    callable's return value is discarded; use :func:`time_callable` when the
    result matters.

    Args:
        func: The callable to profile.
        *args: Positional arguments passed to ``func`` on every iteration.
        label: Name shown in the report; defaults to the callable's qualified
            name.
        iterations: How many times to invoke the callable inside the profile.
        top: Unused by the report itself; accepted so callers can pass the same
            ``top`` value to :meth:`ProfileResult.render`.
        sort_by: Ranking key, one of :data:`SORT_KEYS`.
        **kwargs: Keyword arguments passed to ``func`` on every iteration.

    Returns:
        A :class:`ProfileResult` with per-function statistics in milliseconds.

    Raises:
        ValidationError: If ``iterations`` is not positive or ``sort_by`` is not
            a supported ranking key.
    """
    if iterations < 1:
        raise ValidationError(
            f"iterations must be at least 1, got {iterations}.",
            hint="profile at least one iteration",
            context={"field": "iterations", "value": iterations},
        )
    if sort_by not in SORT_KEYS:
        raise ValidationError(
            f"sort_by must be one of {', '.join(SORT_KEYS)}, got {sort_by!r}.",
            hint="rank by tottime, cumtime or calls",
            context={"field": "sort_by", "value": sort_by},
        )

    name = label or getattr(func, "__qualname__", repr(func))
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    try:
        for _ in range(iterations):
            func(*args, **kwargs)
    finally:
        profiler.disable()
        wall_ms = (time.perf_counter() - started) * 1000.0

    result = ProfileResult(label=name, iterations=iterations, wall_ms=wall_ms, sort_by=sort_by)
    result.stats = _collect_stats(profiler, sort_by=sort_by, top=top)
    return result


def _collect_stats(
    profiler: cProfile.Profile,
    *,
    sort_by: str = "tottime",
    top: Optional[int] = None,
) -> List[FunctionStat]:
    """Convert a finished profiler's raw counters into ranked statistics.

    Args:
        profiler: A profiler that has already been disabled.
        sort_by: Ranking key, one of :data:`SORT_KEYS`.
        top: Optional cap on the number of entries returned.

    Returns:
        Statistics in milliseconds, hottest first.
    """
    # pstats needs somewhere to write its own diagnostics; a throwaway buffer
    # keeps them off the caller's stdout.
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    entries: List[FunctionStat] = []
    for key, (primitive_calls, total_calls, tottime, cumtime, _callers) in stats.stats.items():
        filename, lineno, function = key
        entries.append(
            FunctionStat(
                function=f"{Path(filename).name}:{lineno}({function})",
                primitive_calls=int(primitive_calls),
                total_calls=int(total_calls),
                tottime_ms=round(tottime * 1000.0, 3),
                cumtime_ms=round(cumtime * 1000.0, 3),
            )
        )

    keys = {
        "tottime": lambda stat: (-stat.tottime_ms, stat.function),
        "cumtime": lambda stat: (-stat.cumtime_ms, stat.function),
        "calls": lambda stat: (-stat.total_calls, stat.function),
    }
    entries.sort(key=keys[sort_by])
    return entries if top is None else entries[:top]


def time_callable(
    func: Callable[..., Any],
    *args: Any,
    label: Optional[str] = None,
    repeat: int = DEFAULT_REPEATS,
    number: int = 1,
    warmup: int = 1,
    **kwargs: Any,
) -> TimingResult:
    """Time ``func(*args, **kwargs)`` repeatedly and summarise the samples.

    ``warmup`` untimed invocations run first so that one-off costs — importing a
    lazily loaded module, populating a cache, the first ``stat`` of a fresh
    directory — do not land in the first sample. The reported ``best_ms`` is the
    fastest sample: on a shared machine the slowest samples are the ones that
    lost the CPU, so the minimum is the most stable estimator.

    Args:
        func: The callable to time.
        *args: Positional arguments passed to ``func`` on every call.
        label: Name shown in the report; defaults to the callable's qualified
            name.
        repeat: Number of timed samples.
        number: Invocations per sample; the per-sample time covers all of them.
        warmup: Untimed invocations to run before measuring.
        **kwargs: Keyword arguments passed to ``func`` on every call.

    Returns:
        A :class:`TimingResult` describing the samples.

    Raises:
        ValidationError: If ``repeat`` or ``number`` is not positive, or
            ``warmup`` is negative.
    """
    if repeat < 1:
        raise ValidationError(
            f"repeat must be at least 1, got {repeat}.",
            hint="take at least one timed sample",
            context={"field": "repeat", "value": repeat},
        )
    if number < 1:
        raise ValidationError(
            f"number must be at least 1, got {number}.",
            hint="run the callable at least once per sample",
            context={"field": "number", "value": number},
        )
    if warmup < 0:
        raise ValidationError(
            f"warmup must not be negative, got {warmup}.",
            hint="use 0 for no warm-up, or a positive count",
            context={"field": "warmup", "value": warmup},
        )

    for _ in range(warmup):
        func(*args, **kwargs)

    samples: List[float] = []
    for _ in range(repeat):
        started = time.perf_counter()
        for _ in range(number):
            func(*args, **kwargs)
        samples.append((time.perf_counter() - started) * 1000.0)

    return TimingResult(
        label=label or getattr(func, "__qualname__", repr(func)),
        number=number,
        samples_ms=tuple(round(sample, 4) for sample in samples),
        best_ms=round(min(samples), 4),
        median_ms=round(statistics.median(samples), 4),
        mean_ms=round(statistics.fmean(samples), 4),
        stdev_ms=round(statistics.stdev(samples), 4) if len(samples) > 1 else 0.0,
    )


def compare_timings(before: TimingResult, after: TimingResult) -> Dict[str, Any]:
    """Compare two timing results and quantify the change.

    Both results are compared on their best sample, and the speedup is expressed
    as ``before / after``: a value above ``1.0`` means the change made the call
    faster, ``1.0`` means no change, and below ``1.0`` means it regressed.

    Args:
        before: Timing of the original implementation.
        after: Timing of the changed implementation.

    Returns:
        A dictionary with ``label``, ``before_ms``, ``after_ms``, ``speedup``,
        ``change_pct`` (negative when faster) and ``verdict`` (``"faster"``,
        ``"slower"`` or ``"unchanged"``).

    Raises:
        ValidationError: If either best sample is not a positive number.
    """
    if before.best_ms <= 0 or after.best_ms <= 0:
        raise ValidationError(
            f"cannot compare non-positive timings ({before.best_ms} ms, {after.best_ms} ms).",
            hint="time each side at least once before comparing",
            context={"field": "best_ms"},
        )
    speedup = before.best_ms / after.best_ms
    change_pct = (after.best_ms - before.best_ms) / before.best_ms * 100.0
    if round(speedup, 2) == 1.00:
        verdict = "unchanged"
    else:
        verdict = "faster" if speedup > 1.0 else "slower"
    return {
        "label": before.label,
        "before_ms": before.best_ms,
        "after_ms": after.best_ms,
        "speedup": round(speedup, 3),
        "change_pct": round(change_pct, 2),
        "verdict": verdict,
    }


# -- fixture builders for the CLI --------------------------------------


def _build_file_fixture(root: Path, count: int, *, duplicates: int = 0) -> List[Path]:
    """Create ``count`` small files spread across extensions under ``root``.

    Args:
        root: Directory to populate; created if missing.
        count: Number of files to create.
        duplicates: How many extra files to write with content copied from an
            earlier file, so duplicate detection has something to find.

    Returns:
        The paths that were created, in creation order.

    Raises:
        ValidationError: If ``count`` is not positive.
    """
    if count < 1:
        raise ValidationError(
            f"count must be at least 1, got {count}.",
            hint="create at least one fixture file",
            context={"field": "count", "value": count},
        )
    suffixes = (".txt", ".csv", ".png", ".mp3", ".zip", ".py", ".log", ".unknown")
    root.mkdir(parents=True, exist_ok=True)
    created: List[Path] = []
    for index in range(count):
        path = root / f"file_{index:05d}{suffixes[index % len(suffixes)]}"
        # Each payload gets a distinct length, so the duplicate detector's
        # size pre-filter behaves as it would on a real directory: only the
        # copies written below share a size.
        path.write_bytes(f"payload {index:06d}\n".encode("utf-8") + b"." * index)
        created.append(path)
    for extra in range(duplicates):
        source = created[extra % len(created)]
        copy = root / f"copy_{extra:05d}{source.suffix}"
        copy.write_bytes(source.read_bytes())
        created.append(copy)
    return created


def _build_price_fixture(path: Path, rows: int = 500) -> Path:
    """Write a symbol/price CSV with ``rows`` data rows.

    Args:
        path: Destination CSV path.
        rows: Number of data rows to write.

    Returns:
        The path that was written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["Symbol,Price"]
    for index in range(rows):
        lines.append(f"SYM{index:04d},{100 + index * 0.5:.2f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run_target(target: str, workspace: Path, files: int, iterations: int) -> ProfileResult:
    """Profile one named hot path inside ``workspace``.

    Args:
        target: One of ``organizer``, ``duplicates``, ``wordlist``,
            ``portfolio`` or ``chatbot``.
        workspace: Scratch directory the fixtures are built in.
        files: Size of the synthetic directory fixture.
        iterations: How many times to invoke the hot path.

    Returns:
        The profile of that hot path.

    Raises:
        ValidationError: If ``target`` is not a known hot path.
    """
    if target not in TARGETS:
        raise ValidationError(
            f"unknown profiling target {target!r}.",
            hint="choose organizer, duplicates, wordlist, portfolio or chatbot",
            context={"field": "target", "value": target},
        )
    _ensure_import_path(target)

    if target == "organizer":
        from organizer import FileOrganizer

        root = workspace / "organizer"
        _build_file_fixture(root, files)
        organizer = FileOrganizer()
        return profile_callable(
            organizer.organize,
            root,
            dry_run=True,
            label=f"FileOrganizer.organize(dry_run, {files} files)",
            iterations=iterations,
        )

    if target == "duplicates":
        from organizer import find_duplicates

        root = workspace / "duplicates"
        _build_file_fixture(root, files, duplicates=max(1, files // 10))
        return profile_callable(
            find_duplicates,
            root,
            label=f"find_duplicates({files} files)",
            iterations=iterations,
        )

    if target == "wordlist":
        from words import load_words_from_file

        source = (
            Path(__file__).resolve().parent / "projects" / "01_hangman_game" / "data" / "words.txt"
        )
        return profile_callable(
            load_words_from_file,
            source,
            label="load_words_from_file(words.txt)",
            iterations=iterations,
        )

    if target == "portfolio":
        from portfolio import load_prices_from_csv

        csv_path = _build_price_fixture(workspace / "prices.csv", rows=files)
        return profile_callable(
            load_prices_from_csv,
            csv_path,
            label=f"load_prices_from_csv({files} rows)",
            iterations=iterations,
        )

    if target == "chatbot":
        from chatbot import IntentEngine

        intents_path = (
            Path(__file__).resolve().parent / "projects" / "04_terminal_assistant" / "intents.json"
        )
        bot = IntentEngine.from_json(intents_path)
        prompts = [
            "what is the weather like today",
            "set a reminder for tomorrow",
            "tell me a joke",
            "how do I install python",
            "thanks, that helps",
        ] * max(1, files // 100 or 1)

        def _drive() -> None:
            for prompt in prompts:
                bot.respond(prompt)

        return profile_callable(
            _drive,
            label=f"IntentEngine.respond x{len(prompts)}",
            iterations=iterations,
        )

    # Unreachable: the guard above rejects every other name. Kept so the return
    # type is total for a type checker.
    raise ValidationError(  # pragma: no cover - defensive
        f"unknown profiling target {target!r}.",
        hint="choose organizer, duplicates, wordlist, portfolio or chatbot",
        context={"field": "target", "value": target},
    )


#: The hot paths the CLI can profile.
TARGETS: Tuple[str, ...] = ("organizer", "duplicates", "wordlist", "portfolio", "chatbot")

#: Project folder for each hot path. The project directories carry numeric
#: prefixes (``01_hangman_game``) and so cannot be imported as packages; the
#: CLI therefore adds them to ``sys.path`` the same way ``conftest.py`` does for
#: the test suite.
PROJECT_DIRS: Dict[str, str] = {
    "organizer": "03_file_organizer_automation",
    "duplicates": "03_file_organizer_automation",
    "wordlist": "01_hangman_game",
    "portfolio": "02_portfolio_tracker",
    "chatbot": "04_terminal_assistant",
}


def _ensure_import_path(target: str) -> None:
    """Make ``target``'s project modules importable by their plain names.

    Args:
        target: A name from :data:`TARGETS`.
    """
    project = Path(__file__).resolve().parent / "projects" / PROJECT_DIRS[target]
    entry = str(project)
    if entry not in sys.path:
        sys.path.insert(0, entry)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the profiling CLI.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        :data:`cli_contract.ExitCode.OK` on success, ``2`` for a usage error.
    """
    parser = build_parser(
        "profiling",
        "Profile one of the Datheon hot paths with cProfile.",
    )
    parser.add_argument(
        "target",
        choices=TARGETS,
        help="Hot path to profile.",
    )
    parser.add_argument(
        "--files",
        type=int,
        default=400,
        help="Fixture size (files for the organizer, rows for the CSV loaders).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="How many times to invoke the hot path inside the profile.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help="How many functions to show.",
    )
    parser.add_argument(
        "--sort-by",
        choices=SORT_KEYS,
        default="tottime",
        help="Statistic used to rank the functions.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a table.",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="datheon-profile-") as workspace:
        result = _run_target(
            args.target,
            Path(workspace),
            files=args.files,
            iterations=args.iterations,
        )

    if args.json:
        payload = result.to_dict(args.top)
        payload["sort_by"] = args.sort_by
        print(json.dumps(payload, indent=2))
    else:
        print(result.render(args.top))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via run_cli in tests
    sys.exit(run_cli(lambda: main()))
