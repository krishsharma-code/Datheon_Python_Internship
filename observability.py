"""Turn the tools' JSON logs into the Week 2 observability report (Day 14).

Days 8–13 each added a piece of the picture: one logging setup, JSON records
with run IDs and durations, atomic writes, an async organizer, a profiler and a
dataset cache. Day 14 is the consolidation — a *single* answer to two questions
a reviewer will actually ask:

* **Performance:** what did the last two weeks buy, and where is the remaining
  time? (the numbers live in ``docs/performance_profiling.md`` and
  ``docs/caching.md``; this module makes the cache half reproducible on demand)
* **Observability:** what can an operator learn about a run from the logs alone,
  without re-running it?

The second question is what this module computes. :mod:`structured_logging`
already writes one JSON object per record with a ``run_id``, a ``project``, a
``duration_ms`` and a ``run_outcome``; :func:`build_report` reads a directory of
those files and reduces them to the numbers an operator wants:

* how many runs happened, and how they ended (``success`` / ``error`` /
  ``interrupted`` / ``incomplete``);
* how long they took — p50, p95 and the worst case, not just the mean, because
  one 30-second outlier is what a user remembers;
* which error codes appeared and how often, so a pager can point at a taxonomy
  entry instead of a stack trace;
* what the dataset caches did while the process ran (hits, misses, hit rate).

Everything is standard library and read-only: the module never writes, rotates
or deletes a log, and a malformed line is *counted and reported*, never fatal —
a log file is exactly the kind of artefact that gets a half-written line from a
killed process.

Typical use::

    python observability.py ~/.datheon/logs
    python observability.py --log-dir /var/log/datheon --json

and from Python::

    from observability import build_report

    report = build_report(["~/.datheon/logs"])
    print(report.totals()["p95_ms"])
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from cli_contract import ExitCode, build_parser, run_cli
from errors import AccessDeniedError, MissingFileError, ValidationError
from logging_config import default_log_dir
from structured_logging import OUTCOMES

__all__ = [
    "DEFAULT_TOP",
    "LOG_GLOB",
    "MalformedLine",
    "ObservabilityReport",
    "ProjectSummary",
    "RunSummary",
    "build_report",
    "cache_snapshot",
    "discover_log_files",
    "duration_stats",
    "percentile",
    "read_json_records",
    "summarize_runs",
]

#: Glob used to find log files, including the rotated ``name.log.1`` siblings.
LOG_GLOB: str = "*.log*"

#: How many projects the text report lists unless asked for more.
DEFAULT_TOP: int = 10

#: The outcome a run carries while it is still open, before it finishes.
RUNNING: str = "running"

#: Longest snippet of a malformed line the report quotes back.
PREVIEW_CHARS: int = 80


@dataclass(frozen=True)
class MalformedLine:
    """One log line that could not be read as a JSON record.

    A log file is written by a process that may be killed mid-write, so a
    truncated final line is normal rather than exceptional. Counting these
    keeps the report honest: a run count is only trustworthy if the caller can
    see how many lines were dropped to produce it.

    Attributes:
        path: The file the line came from.
        line_number: 1-based line number inside that file.
        reason: Why the line was rejected, e.g. ``"invalid JSON: ..."``.
        preview: The first :data:`PREVIEW_CHARS` characters of the line.
    """

    path: Path
    line_number: int
    reason: str
    preview: str

    def to_dict(self) -> Dict[str, Any]:
        """Return the rejection as a JSON-serialisable dictionary."""
        return {
            "path": str(self.path),
            "line": self.line_number,
            "reason": self.reason,
            "preview": self.preview,
        }

    def describe(self) -> str:
        """Return a one-line human summary of the rejected line."""
        return f"{self.path.name}:{self.line_number}  {self.reason}  {self.preview}"


@dataclass(frozen=True)
class RunSummary:
    """The reduced form of one run: its outcome, its cost and its error.

    Attributes:
        run_id: The correlation ID shared by every record of the run.
        project: The program that produced the run, or ``""`` if unknown.
        outcome: One of :data:`structured_logging.OUTCOMES`, or ``"incomplete"``
            when the run opened but never reported a terminal state.
        duration_ms: Wall-clock cost of the run in milliseconds — the finish
            record's ``duration_ms``, or the largest offset seen otherwise.
        records: How many log records belonged to this run.
        error_code: Taxonomy code (e.g. ``"DT-1001"``) when the run failed.
        error_type: Exception class name when the run failed.
    """

    run_id: str
    project: str
    outcome: str
    duration_ms: float
    records: int
    error_code: Optional[str] = None
    error_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return the summary as a JSON-serialisable dictionary."""
        return {
            "run_id": self.run_id,
            "project": self.project,
            "outcome": self.outcome,
            "duration_ms": round(self.duration_ms, 3),
            "records": self.records,
            "error_code": self.error_code,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class ProjectSummary:
    """One program's runs, outcomes, durations and error codes.

    Attributes:
        project: The program name, e.g. ``"organizer"``.
        runs: How many runs the project contributed.
        outcomes: Count per outcome name, always carrying every key in
            :data:`structured_logging.OUTCOMES` so a table column never moves.
        durations_ms: One duration per run, in milliseconds.
        error_codes: Count per taxonomy code across the project's failures.
    """

    project: str
    runs: int
    outcomes: Dict[str, int]
    durations_ms: Tuple[float, ...]
    error_codes: Dict[str, int]

    @property
    def failures(self) -> int:
        """Runs that did not end in ``success``."""
        return sum(self.outcomes.get(outcome, 0) for outcome in OUTCOMES if outcome != "success")

    @property
    def success_rate(self) -> float:
        """Fraction of the project's runs that succeeded, in ``[0.0, 1.0]``."""
        return (self.outcomes.get("success", 0) / self.runs) if self.runs else 0.0

    def duration_stats(self) -> Dict[str, Any]:
        """Return count, p50, p95, worst and mean for this project's runs."""
        return duration_stats(self.durations_ms)

    def to_dict(self) -> Dict[str, Any]:
        """Return the summary as a JSON-serialisable dictionary."""
        payload: Dict[str, Any] = {
            "project": self.project,
            "runs": self.runs,
            "outcomes": dict(self.outcomes),
            "failures": self.failures,
            "success_rate": round(self.success_rate, 4),
            "error_codes": dict(self.error_codes),
        }
        payload.update(self.duration_stats())
        return payload


@dataclass(frozen=True)
class ObservabilityReport:
    """Everything one pass over a set of JSON log files can say.

    Attributes:
        files: The log files that were read, in discovery order.
        records: How many JSON records those files yielded.
        runs: One :class:`RunSummary` per distinct ``run_id``.
        malformed: Lines that could not be read, with the reason.
        unattributed: Records carrying no ``run_id``, so no run owns them.
        caches: The live cache counters, keyed by cache name.
    """

    files: Tuple[Path, ...] = ()
    records: int = 0
    runs: Tuple[RunSummary, ...] = ()
    malformed: Tuple[MalformedLine, ...] = ()
    unattributed: int = 0
    caches: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def projects(self) -> List[ProjectSummary]:
        """Group the runs by project, busiest first.

        Returns:
            One :class:`ProjectSummary` per distinct project, sorted by run
            count descending and then by name, so the table order is stable.
        """
        buckets: Dict[str, List[RunSummary]] = {}
        for run in self.runs:
            buckets.setdefault(run.project or "unknown", []).append(run)

        summaries: List[ProjectSummary] = []
        for project, runs in buckets.items():
            outcomes = {outcome: 0 for outcome in OUTCOMES}
            codes: Dict[str, int] = {}
            for run in runs:
                outcomes[run.outcome] = outcomes.get(run.outcome, 0) + 1
                if run.error_code:
                    codes[run.error_code] = codes.get(run.error_code, 0) + 1
            summaries.append(
                ProjectSummary(
                    project=project,
                    runs=len(runs),
                    outcomes=outcomes,
                    durations_ms=tuple(run.duration_ms for run in runs),
                    error_codes=codes,
                )
            )
        return sorted(summaries, key=lambda summary: (-summary.runs, summary.project))

    def outcome_totals(self) -> Dict[str, int]:
        """Count every run by outcome, with a zero for outcomes never seen."""
        totals = {outcome: 0 for outcome in OUTCOMES}
        for run in self.runs:
            totals[run.outcome] = totals.get(run.outcome, 0) + 1
        return totals

    def error_codes(self) -> Dict[str, int]:
        """Count taxonomy codes across all runs, most frequent first."""
        codes: Dict[str, int] = {}
        for run in self.runs:
            if run.error_code:
                codes[run.error_code] = codes.get(run.error_code, 0) + 1
        return dict(sorted(codes.items(), key=lambda item: (-item[1], item[0])))

    def totals(self) -> Dict[str, Any]:
        """Return the whole-report figures as a flat dictionary."""
        outcomes = self.outcome_totals()
        runs = len(self.runs)
        payload: Dict[str, Any] = {
            "files": len(self.files),
            "records": self.records,
            "runs": runs,
            "unattributed_records": self.unattributed,
            "malformed_lines": len(self.malformed),
            "outcomes": outcomes,
            "success_rate": round((outcomes.get("success", 0) / runs) if runs else 0.0, 4),
            "error_codes": self.error_codes(),
        }
        payload.update(duration_stats([run.duration_ms for run in self.runs]))
        return payload

    def to_dict(self, top: int = DEFAULT_TOP) -> Dict[str, Any]:
        """Return the report as a JSON-serialisable dictionary.

        Args:
            top: How many projects to include, busiest first.

        Returns:
            A dictionary with the totals, the per-project summaries, the
            malformed lines and the cache counters.

        Raises:
            ValidationError: If ``top`` is not positive.
        """
        selected = _select(self.projects(), top)
        return {
            "totals": self.totals(),
            "projects": [summary.to_dict() for summary in selected],
            "malformed": [line.to_dict() for line in self.malformed],
            "caches": {name: dict(stats) for name, stats in self.caches.items()},
        }

    def render(self, top: int = DEFAULT_TOP) -> str:
        """Render the report as a fixed-width text block.

        Args:
            top: How many projects to list, busiest first.

        Returns:
            A multi-line string: a headline, the project table, the outcome and
            error-code totals, the cache counters and any malformed lines.

        Raises:
            ValidationError: If ``top`` is not positive.
        """
        projects = _select(self.projects(), top)
        totals = self.totals()
        lines = [
            f"observability report — {totals['files']} file(s), "
            f"{totals['records']} record(s), {totals['runs']} run(s)",
            "",
        ]

        header = (
            f"{'project':<14} {'runs':>5} {'ok':>5} {'err':>5} {'intr':>5} {'inc':>5} "
            f"{'p50_ms':>9} {'p95_ms':>9} {'max_ms':>9}"
        )
        lines.extend([header, "-" * len(header)])
        for summary in projects:
            stats = summary.duration_stats()
            lines.append(
                f"{summary.project:<14} {summary.runs:>5} "
                f"{summary.outcomes.get('success', 0):>5} "
                f"{summary.outcomes.get('error', 0):>5} "
                f"{summary.outcomes.get('interrupted', 0):>5} "
                f"{summary.outcomes.get('incomplete', 0):>5} "
                f"{stats['p50_ms']:>9.2f} {stats['p95_ms']:>9.2f} {stats['max_ms']:>9.2f}"
            )
        if not projects:
            lines.append("(no runs recorded)")

        outcomes = totals["outcomes"]
        lines.extend(
            [
                "",
                "outcomes: "
                + "  ".join(f"{name}={count}" for name, count in outcomes.items())
                + f"  (success rate {totals['success_rate']:.1%})",
                f"records without a run_id: {totals['unattributed_records']}",
            ]
        )

        codes = totals["error_codes"]
        lines.append("")
        lines.append("error codes")
        if codes:
            width = max(len(code) for code in codes)
            lines.extend(f"  {code:<{width}}  {count:>4}" for code, count in codes.items())
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("cache stats")
        if self.caches:
            lines.extend(_render_caches(self.caches))
        else:
            lines.append("  (no live caches)")

        lines.append("")
        lines.append(f"malformed lines: {len(self.malformed)}")
        lines.extend(f"  {line.describe()}" for line in self.malformed)
        return "\n".join(lines)


def _render_caches(caches: Mapping[str, Mapping[str, Any]]) -> List[str]:
    """Render the live cache counters as aligned rows.

    Args:
        caches: Cache counters keyed by cache name, as produced by
            :func:`cache_snapshot`.

    Returns:
        One header line plus one line per cache.
    """
    header = (
        f"  {'cache':<20} {'requests':>9} {'hits':>7} {'misses':>7} {'hit_rate':>9} {'size':>6}"
    )
    rows = [header, "  " + "-" * (len(header) - 2)]
    for name in sorted(caches):
        stats = caches[name]
        hit_rate = stats.get("hit_rate", 0.0)
        rows.append(
            f"  {name:<20} {_as_int(stats.get('requests')):>9} "
            f"{_as_int(stats.get('hits')):>7} {_as_int(stats.get('misses')):>7} "
            f"{float(hit_rate):>9.1%} {_as_int(stats.get('size')):>6}"
        )
    return rows


def _select(summaries: Sequence[ProjectSummary], top: int) -> List[ProjectSummary]:
    """Return the first ``top`` project summaries.

    Args:
        summaries: The summaries to slice, already in display order.
        top: How many to keep; must be positive.

    Returns:
        At most ``top`` summaries.

    Raises:
        ValidationError: If ``top`` is not positive.
    """
    if top < 1:
        raise ValidationError(
            f"--top must be at least 1, got {top}.",
            hint="ask for at least one project, e.g. --top 5",
            context={"field": "top", "value": top},
        )
    return list(summaries[:top])


def _as_int(value: Any) -> int:
    """Coerce a counter to ``int``, treating anything unusable as zero."""
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _text(value: Any) -> str:
    """Return ``value`` stripped when it is a string, else ``""``."""
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any, default: float) -> float:
    """Return ``value`` as a float when it is a real number, else ``default``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def percentile(values: Sequence[float], quantile: float) -> float:
    """Return the nearest-rank percentile of ``values``.

    Nearest-rank is used rather than interpolation because a log-derived
    duration is a *measured* value: reporting a p95 that no run actually took
    would be inventing a number. The result is always one of the samples.

    Args:
        values: The samples; must not be empty.
        quantile: The quantile to read, in ``(0, 1]`` — ``0.5`` for the
            median, ``0.95`` for a p95.

    Returns:
        The sample at the requested rank.

    Raises:
        ValidationError: If ``values`` is empty or ``quantile`` is out of range.
    """
    if not values:
        raise ValidationError(
            "percentile() needs at least one sample.",
            hint="guard with `if values:` before asking for a percentile",
            context={"field": "values"},
        )
    if not 0.0 < quantile <= 1.0:
        raise ValidationError(
            f"quantile must be in (0, 1], got {quantile}.",
            hint="pass 0.5 for the median or 0.95 for a p95",
            context={"field": "quantile", "value": quantile},
        )
    ordered = sorted(float(value) for value in values)
    index = math.ceil(quantile * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def duration_stats(values: Sequence[float]) -> Dict[str, Any]:
    """Summarise a set of durations.

    Args:
        values: Durations in milliseconds; may be empty.

    Returns:
        A dictionary with ``count``, ``p50_ms``, ``p95_ms``, ``max_ms`` and
        ``mean_ms``. An empty input reports zeros rather than raising, so a
        report can always be printed.
    """
    if not values:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}
    samples = [float(value) for value in values]
    return {
        "count": len(samples),
        "p50_ms": round(percentile(samples, 0.50), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "max_ms": round(max(samples), 3),
        "mean_ms": round(sum(samples) / len(samples), 3),
    }


def read_json_records(path: Path) -> Tuple[List[Dict[str, Any]], List[MalformedLine]]:
    """Read one log file, tolerating the lines that are not JSON.

    Blank lines are skipped silently (they carry nothing). A line that is not
    JSON, or is JSON but not an object, is recorded in the returned malformed
    list and the read continues — one bad line must not hide the rest of the
    file.

    Args:
        path: The log file to read.

    Returns:
        A ``(records, malformed)`` pair.

    Raises:
        AccessDeniedError: If the file cannot be read.
    """
    records: List[Dict[str, Any]] = []
    malformed: List[MalformedLine] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    malformed.append(
                        MalformedLine(
                            path=path,
                            line_number=number,
                            reason=f"invalid JSON: {exc.msg}",
                            preview=text[:PREVIEW_CHARS],
                        )
                    )
                    continue
                if not isinstance(payload, dict):
                    malformed.append(
                        MalformedLine(
                            path=path,
                            line_number=number,
                            reason=f"not a JSON object ({type(payload).__name__})",
                            preview=text[:PREVIEW_CHARS],
                        )
                    )
                    continue
                records.append(payload)
    except OSError as exc:
        raise AccessDeniedError(
            f"cannot read log file {path}: {exc.strerror or exc}.",
            hint="check the path and its permissions",
            context={"path": str(path)},
        ) from exc
    return records, malformed


def discover_log_files(paths: Iterable[Union[str, Path]]) -> List[Path]:
    """Expand paths into the log files to read, without duplicates.

    A directory contributes every ``*.log*`` file it holds — the rotated
    ``name.log.1`` siblings included, because a rotation may have moved the run
    the caller is looking for. A file path is taken as-is.

    Args:
        paths: Log files or directories, in the order they should be read.

    Returns:
        The discovered files, deduplicated by resolved path, discovery order
        preserved.

    Raises:
        MissingFileError: If a path does not exist, or if nothing at all was
            found — an empty report would otherwise look like a quiet day.
    """
    requested = [Path(raw).expanduser() for raw in paths]
    discovered: List[Path] = []
    seen: Set[Path] = set()
    for path in requested:
        if path.is_dir():
            candidates = sorted(entry for entry in path.glob(LOG_GLOB) if entry.is_file())
        elif path.is_file():
            candidates = [path]
        else:
            raise MissingFileError(
                f"log path not found: {path}.",
                hint="pass a log file, or the directory that holds the *.log files",
                context={"path": str(path)},
            )
        for candidate in candidates:
            key = candidate.resolve()
            if key in seen:
                continue
            seen.add(key)
            discovered.append(candidate)

    if not discovered:
        raise MissingFileError(
            "no log files found.",
            hint="run a tool with --log-level INFO --json-logs to produce one",
            context={"paths": [str(path) for path in requested]},
        )
    return discovered


def summarize_runs(records: Sequence[Mapping[str, Any]]) -> List[RunSummary]:
    """Group records by ``run_id`` and reduce each group to a run summary.

    Records without a ``run_id`` belong to no run and are skipped here; the
    report counts them separately as unattributed.

    Args:
        records: Parsed JSON log records.

    Returns:
        One :class:`RunSummary` per distinct run ID, in first-seen order.
    """
    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for record in records:
        run_id = _text(record.get("run_id"))
        if not run_id:
            continue
        grouped.setdefault(run_id, []).append(record)
    return [_summarize_run(run_id, group) for run_id, group in grouped.items()]


def _summarize_run(run_id: str, group: Sequence[Mapping[str, Any]]) -> RunSummary:
    """Reduce one run's records to a :class:`RunSummary`.

    Args:
        run_id: The run's correlation ID.
        group: Every record carrying that ID, in file order.

    Returns:
        The summary; a run with no terminal record reports ``incomplete`` and
        the largest offset it reached.
    """
    project = ""
    outcome = ""
    terminal_duration: Optional[float] = None
    peak_duration = 0.0
    error_code: Optional[str] = None
    error_type: Optional[str] = None

    for record in group:
        project = project or _text(record.get("project"))
        candidate = _text(record.get("run_outcome"))
        offset = _number(record.get("duration_ms"), 0.0)
        if candidate in OUTCOMES:
            outcome = candidate
            terminal_duration = offset
            fields = record.get("fields")
            error = fields.get("error") if isinstance(fields, Mapping) else None
            if isinstance(error, Mapping):
                error_code = _text(error.get("code")) or error_code
                error_type = _text(error.get("type")) or error_type
        elif candidate == RUNNING:
            peak_duration = max(peak_duration, offset)
        else:
            peak_duration = max(peak_duration, offset)

    duration_ms = terminal_duration if terminal_duration is not None else peak_duration
    return RunSummary(
        run_id=run_id,
        project=project,
        outcome=outcome or "incomplete",
        duration_ms=duration_ms,
        records=len(group),
        error_code=error_code,
        error_type=error_type,
    )


def cache_snapshot() -> Dict[str, Dict[str, Any]]:
    """Return the live dataset-cache counters.

    The import is local so that reading a log directory never has to load the
    cache machinery (and so a report can be produced with an empty registry).

    Returns:
        Counters keyed by cache name, as :func:`caching.cache_stats` returns
        them; an empty dictionary when no cache has been used yet.
    """
    import caching

    return caching.cache_stats()


def build_report(
    paths: Iterable[Union[str, Path]],
    *,
    caches: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> ObservabilityReport:
    """Read the given logs and build the full observability report.

    Args:
        paths: Log files or directories to read.
        caches: Cache counters to embed; defaults to a live
            :func:`cache_snapshot`. Pass ``{}`` to omit them.

    Returns:
        The assembled :class:`ObservabilityReport`.

    Raises:
        MissingFileError: If a path does not exist or no log file was found.
        AccessDeniedError: If a log file cannot be read.
    """
    files = discover_log_files(paths)
    records: List[Dict[str, Any]] = []
    malformed: List[MalformedLine] = []
    for path in files:
        file_records, file_malformed = read_json_records(path)
        records.extend(file_records)
        malformed.extend(file_malformed)

    runs = summarize_runs(records)
    attributed = sum(run.records for run in runs)
    snapshot = dict(caches) if caches is not None else cache_snapshot()
    return ObservabilityReport(
        files=tuple(files),
        records=len(records),
        runs=tuple(runs),
        malformed=tuple(malformed),
        unattributed=len(records) - attributed,
        caches={name: dict(stats) for name, stats in snapshot.items()},
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the observability CLI.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        :data:`cli_contract.ExitCode.OK` on success. Domain errors propagate to
        :func:`cli_contract.run_cli`, which maps them onto exit code ``1``.
    """
    parser = build_parser(
        "observability",
        "Summarise Datheon JSON logs into a performance and run report.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Log files or directories of *.log files (default: the tool log directory).",
    )
    parser.add_argument(
        "--log-dir",
        help="Directory of *.log files; overrides the default log directory.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help="How many projects to list.",
    )
    parser.add_argument(
        "--no-caches",
        action="store_true",
        help="Skip the live dataset-cache counters.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a table.",
    )
    args = parser.parse_args(argv)

    if args.log_dir and args.paths:
        raise ValidationError(
            "pass either positional paths or --log-dir, not both.",
            hint="drop --log-dir when you name the log files directly",
            context={"field": "log_dir", "value": args.log_dir},
        )

    targets: List[Union[str, Path]] = list(args.paths)
    if args.log_dir:
        targets = [args.log_dir]
    elif not targets:
        targets = [default_log_dir()]

    report = build_report(targets, caches={} if args.no_caches else None)
    if args.json:
        print(json.dumps(report.to_dict(args.top), indent=2))
    else:
        print(report.render(args.top))
    return int(ExitCode.OK)


if __name__ == "__main__":  # pragma: no cover - exercised via run_cli in tests
    sys.exit(run_cli(lambda: main()))
