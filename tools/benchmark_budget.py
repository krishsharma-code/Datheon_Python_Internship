"""Performance regression gate: turn a benchmark run into a pass/fail verdict.

Day 12 asked *where does the time go* and answered it with a profile. That
answer is a photograph, not a contract: a later change that doubles the cost of
parsing the S&P 500 constituents CSV produces a slower number in a report
nobody re-reads, and no failure anywhere. A benchmark without a comparison is a
table, not a gate.

This module is the comparison. It reads the JSON report
``pytest-benchmark`` writes, compares each benchmark's median against a
committed ceiling, and exits with a verdict:

* **machine variance is normalised, not ignored.** Absolute microseconds mean
  different things on a laptop, a shared runner and a container, so the budget
  is stored next to a *calibration* benchmark — a pure-arithmetic loop with no
  I/O whose cost tracks the machine. The ratio
  ``measured_calibration / budgeted_calibration`` scales every ceiling, so a
  runner that is twice as slow gets twice the budget instead of a false
  regression. The factor is clamped to :data:`FACTOR_MIN`–:data:`FACTOR_MAX`,
  and a clamped run says so, because a machine outside that band is a machine
  whose numbers cannot be trusted to judge anything;
* **the budget must cover the harness.** A benchmark that has no ceiling is a
  benchmark nothing protects, so an *unbudgeted* result is a finding, exactly
  as a page missing from the docs nav is a finding for ``tools/docs_site.py``.
  The reverse — a ceiling for a benchmark that no longer exists — is a finding
  too: a stale budget is a gate that quietly measures less each month.

Run it directly::

    python tools/benchmark_budget.py --list
    python tools/benchmark_budget.py --results benchmark-results.json
    python tools/benchmark_budget.py --results benchmark-results.json --json
    python tools/benchmark_budget.py --results benchmark-results.json --update

Exit codes: ``0`` every benchmark within budget, ``1`` at least one finding
(a regression, an unbudgeted benchmark, a missing measurement), ``2`` the gate
could not reach a verdict (no results file, an unreadable budget, no
calibration sample). A gate that cannot measure must never report success.

The module is development tooling only: nothing under ``projects/`` imports it,
and the five tools remain standard-library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "CALIBRATION_NAME",
    "DEFAULT_HEADROOM",
    "DEFAULT_TOLERANCE",
    "Budget",
    "BudgetEntry",
    "BudgetError",
    "Sample",
    "Verdict",
    "build_budget",
    "canonical_name",
    "compare",
    "load_budget",
    "load_results",
    "machine_factor",
    "main",
    "render_table",
]

#: Exit code for a run that is within budget.
EXIT_OK = 0

#: Exit code for a run with at least one finding.
EXIT_REGRESSION = 1

#: Exit code for a run the gate could not grade.
EXIT_UNMEASURABLE = 2

#: Layout version of ``benchmarks/budget.json``; bump it when the shape changes.
BUDGET_SCHEMA = 1

#: Extra slack applied on top of the recorded ceiling, as a fraction. Zero by
#: default: the headroom is already inside each ceiling, and a second, invisible
#: allowance on top of it is how a gate stops meaning anything.
DEFAULT_TOLERANCE = 0.0

#: How much slower than the recorded median a benchmark may become before it
#: counts as a regression. Doubling is the smallest change worth a comment, and
#: it sits far outside the run-to-run noise of a quiet machine.
DEFAULT_HEADROOM = 2.0

#: The benchmark whose cost normalises every other one. It is pure arithmetic:
#: no file, no clock, no allocation pattern a code change is likely to touch.
CALIBRATION_NAME = "bench_calibration"

#: Bounds on the normalising factor. Outside this band the machine is not
#: comparable to the one the budget was recorded on, and clamping keeps a
#: pathological runner from either failing everything or excusing everything.
FACTOR_MIN = 0.25
FACTOR_MAX = 4.0

#: pytest collects a function as a test by this prefix; the budget names the
#: benchmark without it, so the key reads the same in both files.
TEST_PREFIX = "test_"

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The committed budget.
DEFAULT_BUDGET: Path = ROOT / "benchmarks" / "budget.json"

#: Where ``pytest benchmarks/ --benchmark-json=...`` writes its report.
DEFAULT_RESULTS: Path = ROOT / "benchmark-results.json"


class BudgetError(ValueError):
    """Raised when a results file or a budget cannot be read as one.

    The message names the file and what was wrong with it; the hint says what to
    do next. Both end up in the CLI output, so they are written for a person.
    """


@dataclass(frozen=True)
class Sample:
    """One benchmark's measurement from a single run.

    Attributes:
        name: The benchmark's canonical name, without pytest's ``test_`` prefix.
        median_us: Median duration of one iteration, in microseconds.
        rounds: How many timing rounds pytest-benchmark ran.
        iterations: How many calls were timed per round.
    """

    name: str
    median_us: float
    rounds: int
    iterations: int


@dataclass(frozen=True)
class BudgetEntry:
    """The ceiling one benchmark is held to.

    Attributes:
        median_us: The median recorded when the budget was cut, in microseconds.
        max_median_us: The slowest median still accepted, in microseconds, before
            machine scaling and tolerance. Always at least ``median_us``.
    """

    median_us: float
    max_median_us: float


@dataclass(frozen=True)
class Budget:
    """A parsed ``benchmarks/budget.json``.

    Attributes:
        schema: Layout version of the document.
        calibration_us: The calibration median the ceilings were recorded against.
        benchmarks: Ceilings keyed by canonical benchmark name.
        tolerance: Extra slack as a fraction, applied on top of each ceiling.
        headroom: Multiplier ``--update`` writes between median and ceiling.
        recorded: ISO date the budget was cut.
        python: Interpreter the budget was recorded on, for the reader's benefit.
    """

    schema: int
    calibration_us: float
    benchmarks: Mapping[str, BudgetEntry]
    tolerance: float
    headroom: float
    recorded: str
    python: str

    def ceiling_us(self, name: str, factor: float) -> float:
        """Return the ceiling for one benchmark, scaled to this machine.

        Args:
            name: Canonical benchmark name.
            factor: Machine factor from :func:`machine_factor`.

        Returns:
            The accepted maximum median, in microseconds.

        Raises:
            KeyError: If ``name`` is not budgeted.
        """
        entry = self.benchmarks[name]
        return entry.max_median_us * factor * (1.0 + self.tolerance)


@dataclass(frozen=True)
class Verdict:
    """The gate's opinion about one benchmark.

    Attributes:
        name: Canonical benchmark name.
        status: ``pass``, ``regression``, ``unbudgeted`` or ``missing``.
        measured_us: What this run measured, or ``None`` when it did not run.
        ceiling_us: The scaled ceiling it was judged against, or ``None``.
        ratio: ``measured / ceiling``, or ``None`` when one side is absent.
        detail: One sentence a reader can act on.
    """

    name: str
    status: str
    measured_us: Optional[float]
    ceiling_us: Optional[float]
    ratio: Optional[float]
    detail: str

    @property
    def ok(self) -> bool:
        """Whether this verdict is a pass."""
        return self.status == "pass"

    def as_dict(self) -> Dict[str, Any]:
        """Return the verdict as a JSON-serialisable mapping."""
        return {
            "name": self.name,
            "status": self.status,
            "measured_us": None if self.measured_us is None else round(self.measured_us, 3),
            "ceiling_us": None if self.ceiling_us is None else round(self.ceiling_us, 3),
            "ratio": None if self.ratio is None else round(self.ratio, 3),
            "detail": self.detail,
        }


def canonical_name(raw: object) -> str:
    """Normalise a benchmark name so both files agree on it.

    Args:
        raw: The name pytest-benchmark reported, e.g. ``test_bench_word_list``.

    Returns:
        The name without pytest's ``test_`` prefix, e.g. ``bench_word_list``.

    Raises:
        BudgetError: If ``raw`` is not a non-empty string.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise BudgetError("a benchmark name must be a non-empty string")
    name = raw.strip()
    if name.startswith(TEST_PREFIX) and len(name) > len(TEST_PREFIX):
        return name[len(TEST_PREFIX) :]
    return name


def _read_json(path: Path, what: str) -> Any:
    """Read and parse one JSON document.

    Args:
        path: File to read.
        what: Human-readable description used in error messages.

    Returns:
        The parsed document.

    Raises:
        BudgetError: If the file is missing, unreadable or not JSON.
    """
    if not path.is_file():
        raise BudgetError(f"{what} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BudgetError(f"cannot read {what} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BudgetError(f"{what} {path} is not valid JSON: {exc}") from exc


def _positive(value: object, field: str, where: str) -> float:
    """Validate a positive number.

    Args:
        value: Candidate value.
        field: Field name used in the message.
        where: Document the value came from.

    Returns:
        The value as a float.

    Raises:
        BudgetError: If the value is missing, not a number, or not positive.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BudgetError(f"{where}: {field} must be a number, got {value!r}")
    if value <= 0:
        raise BudgetError(f"{where}: {field} must be positive, got {value!r}")
    return float(value)


def load_results(path: Optional[Path] = None) -> Dict[str, Sample]:
    """Parse a ``pytest-benchmark`` JSON report into samples.

    Args:
        path: Report to read; defaults to :data:`DEFAULT_RESULTS`.

    Returns:
        Samples keyed by canonical benchmark name.

    Raises:
        BudgetError: If the file is missing or unreadable, is not a benchmark
            report, contains no benchmarks, or repeats a name — a duplicated
            name would silently shadow one measurement with another.
    """
    target = Path(path) if path is not None else DEFAULT_RESULTS
    document = _read_json(target, "benchmark report")
    if not isinstance(document, dict):
        raise BudgetError(f"benchmark report {target} is not a JSON object")
    raw_benchmarks = document.get("benchmarks")
    if not isinstance(raw_benchmarks, list):
        raise BudgetError(
            f"benchmark report {target} has no 'benchmarks' list "
            "(run pytest with --benchmark-json=<file>)"
        )
    if not raw_benchmarks:
        raise BudgetError(f"benchmark report {target} contains no benchmarks")

    samples: Dict[str, Sample] = {}
    for index, item in enumerate(raw_benchmarks):
        where = f"{target} benchmark #{index + 1}"
        if not isinstance(item, dict):
            raise BudgetError(f"{where} is not an object")
        name = canonical_name(item.get("name"))
        stats = item.get("stats")
        if not isinstance(stats, dict):
            raise BudgetError(f"{where} ({name}) has no 'stats' object")
        median_seconds = _positive(stats.get("median"), "stats.median", where)
        if name in samples:
            raise BudgetError(f"benchmark report {target} lists {name} twice")
        samples[name] = Sample(
            name=name,
            median_us=median_seconds * 1_000_000.0,
            rounds=int(stats.get("rounds") or 0),
            iterations=int(stats.get("iterations") or 0),
        )
    return samples


def load_budget(path: Optional[Path] = None) -> Budget:
    """Parse the committed budget.

    Args:
        path: Budget to read; defaults to :data:`DEFAULT_BUDGET`.

    Returns:
        The parsed :class:`Budget`.

    Raises:
        BudgetError: If the document is missing, unreadable, of the wrong
            schema, or carries an entry that cannot be a ceiling.
    """
    target = Path(path) if path is not None else DEFAULT_BUDGET
    document = _read_json(target, "budget")
    if not isinstance(document, dict):
        raise BudgetError(f"budget {target} is not a JSON object")

    schema = document.get("schema")
    if schema != BUDGET_SCHEMA:
        raise BudgetError(
            f"budget {target} declares schema {schema!r}, this gate reads {BUDGET_SCHEMA}"
        )

    calibration_us = _positive(document.get("calibration_us"), "calibration_us", str(target))

    tolerance = document.get("tolerance", DEFAULT_TOLERANCE)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0:
        raise BudgetError(f"budget {target}: tolerance must be a number >= 0, got {tolerance!r}")

    headroom = document.get("headroom", DEFAULT_HEADROOM)
    if isinstance(headroom, bool) or not isinstance(headroom, (int, float)) or headroom < 1:
        raise BudgetError(f"budget {target}: headroom must be a number >= 1, got {headroom!r}")

    raw_entries = document.get("benchmarks")
    if not isinstance(raw_entries, dict) or not raw_entries:
        raise BudgetError(f"budget {target} has no 'benchmarks' object")

    entries: Dict[str, BudgetEntry] = {}
    for raw_name, raw_entry in raw_entries.items():
        where = f"budget {target}: benchmark {raw_name!r}"
        name = canonical_name(raw_name)
        if not isinstance(raw_entry, dict):
            raise BudgetError(f"{where} is not an object")
        median_us = _positive(raw_entry.get("median_us"), "median_us", where)
        max_median_us = _positive(raw_entry.get("max_median_us"), "max_median_us", where)
        if max_median_us < median_us:
            raise BudgetError(
                f"{where}: max_median_us ({max_median_us}) is below median_us ({median_us}); "
                "a ceiling under the recorded median fails on the run that records it"
            )
        entries[name] = BudgetEntry(median_us=median_us, max_median_us=max_median_us)

    return Budget(
        schema=BUDGET_SCHEMA,
        calibration_us=calibration_us,
        benchmarks=entries,
        tolerance=float(tolerance),
        headroom=float(headroom),
        recorded=str(document.get("recorded", "")),
        python=str(document.get("python", "")),
    )


def machine_factor(samples: Mapping[str, Sample], budget: Budget) -> Tuple[float, List[str]]:
    """Compute how much slower this machine is than the one the budget came from.

    Args:
        samples: Measurements from the current run.
        budget: The budget being applied.

    Returns:
        A ``(factor, notes)`` pair. ``factor`` multiplies every ceiling; ``notes``
        is empty unless the raw factor fell outside
        :data:`FACTOR_MIN`–:data:`FACTOR_MAX` and was clamped.

    Raises:
        BudgetError: If the run carries no calibration sample, in which case
            nothing can be normalised and every comparison would be a guess.
    """
    calibration = samples.get(CALIBRATION_NAME)
    if calibration is None:
        raise BudgetError(
            f"the run has no {CALIBRATION_NAME} sample, so machine variance cannot be "
            "normalised; the benchmark suite must always include it"
        )
    raw = calibration.median_us / budget.calibration_us
    clamped = min(max(raw, FACTOR_MIN), FACTOR_MAX)
    notes: List[str] = []
    if clamped != raw:
        notes.append(
            f"machine factor {raw:.2f} clamped to {clamped:.2f}: this run is "
            f"{'faster' if raw < clamped else 'slower'} than the budget's machine by more "
            "than the gate accepts, so the verdict is approximate"
        )
    return clamped, notes


def compare(
    budget: Budget,
    samples: Mapping[str, Sample],
    *,
    factor: Optional[float] = None,
    tolerance: Optional[float] = None,
) -> List[Verdict]:
    """Judge every benchmark in the budget, and every sample against it.

    Args:
        budget: The ceilings to apply.
        samples: Measurements from the current run.
        factor: Machine factor; computed with :func:`machine_factor` when omitted.
        tolerance: Overrides the budget's tolerance when given.

    Returns:
        One :class:`Verdict` per budget entry, plus one ``unbudgeted`` verdict
        per measured benchmark the budget does not name, sorted by name.

    Raises:
        BudgetError: If ``factor`` is omitted and cannot be computed.
    """
    if factor is None:
        factor, _ = machine_factor(samples, budget)
    if tolerance is None:
        tolerance = budget.tolerance
    if tolerance < 0:
        raise BudgetError(f"tolerance must be >= 0, got {tolerance!r}")

    verdicts: List[Verdict] = []
    for name in sorted(budget.benchmarks):
        entry = budget.benchmarks[name]
        ceiling = entry.max_median_us * factor * (1.0 + tolerance)
        sample = samples.get(name)
        if sample is None:
            verdicts.append(
                Verdict(
                    name=name,
                    status="missing",
                    measured_us=None,
                    ceiling_us=ceiling,
                    ratio=None,
                    detail=(
                        f"budgeted at {entry.median_us:.1f}us but not measured in this run: "
                        "either the benchmark was removed or the run was filtered"
                    ),
                )
            )
            continue
        ratio = sample.median_us / ceiling if ceiling else float("inf")
        if sample.median_us <= ceiling:
            verdicts.append(
                Verdict(
                    name=name,
                    status="pass",
                    measured_us=sample.median_us,
                    ceiling_us=ceiling,
                    ratio=ratio,
                    detail=(
                        f"{sample.median_us:.1f}us against a {ceiling:.1f}us ceiling "
                        f"({ratio:.2f}x)"
                    ),
                )
            )
        else:
            verdicts.append(
                Verdict(
                    name=name,
                    status="regression",
                    measured_us=sample.median_us,
                    ceiling_us=ceiling,
                    ratio=ratio,
                    detail=(
                        f"{sample.median_us:.1f}us against a {ceiling:.1f}us ceiling "
                        f"({ratio:.2f}x over): {ratio:.1f}x the accepted budget"
                    ),
                )
            )

    for name in sorted(set(samples) - set(budget.benchmarks) - {CALIBRATION_NAME}):
        verdicts.append(
            Verdict(
                name=name,
                status="unbudgeted",
                measured_us=samples[name].median_us,
                ceiling_us=None,
                ratio=None,
                detail=(
                    f"measured at {samples[name].median_us:.1f}us but has no ceiling; "
                    "record one with --update so a future regression can fail the build"
                ),
            )
        )
    return verdicts


def build_budget(
    samples: Mapping[str, Sample],
    *,
    headroom: float = DEFAULT_HEADROOM,
    tolerance: float = DEFAULT_TOLERANCE,
    recorded: Optional[str] = None,
    python: str = "",
) -> Dict[str, Any]:
    """Build a budget document from a measured run.

    Args:
        samples: Measurements from a run on the reference machine.
        headroom: Multiplier between a recorded median and its ceiling.
        tolerance: Extra slack stored alongside the ceilings.
        recorded: ISO date to stamp; defaults to today.
        python: Interpreter the budget was recorded on.

    Returns:
        The budget document, ready to be written as JSON.

    Raises:
        BudgetError: If the run carries no calibration sample, no benchmark at
            all, or a headroom below 1.
    """
    if headroom < 1:
        raise BudgetError(f"headroom must be >= 1, got {headroom!r}")
    calibration = samples.get(CALIBRATION_NAME)
    if calibration is None:
        raise BudgetError(
            f"cannot record a budget without a {CALIBRATION_NAME} sample: nothing would "
            "normalise it to another machine"
        )
    measured = {name: sample for name, sample in samples.items() if name != CALIBRATION_NAME}
    if not measured:
        raise BudgetError("cannot record a budget from a run with no benchmarks in it")

    return {
        "schema": BUDGET_SCHEMA,
        "recorded": recorded if recorded is not None else date.today().isoformat(),
        "python": python,
        "headroom": headroom,
        "tolerance": tolerance,
        "calibration_us": round(calibration.median_us, 3),
        "benchmarks": {
            name: {
                "median_us": round(sample.median_us, 3),
                "max_median_us": round(sample.median_us * headroom, 3),
            }
            for name, sample in sorted(measured.items())
        },
    }


def write_budget(document: Mapping[str, Any], path: Optional[Path] = None) -> Path:
    """Write a budget document, creating its directory.

    Args:
        document: The document from :func:`build_budget`.
        path: Destination; defaults to :data:`DEFAULT_BUDGET`.

    Returns:
        The path written.
    """
    target = Path(path) if path is not None else DEFAULT_BUDGET
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def render_table(budget: Budget, verdicts: Sequence[Verdict], factor: float) -> str:
    """Render the verdicts as an aligned table.

    Args:
        budget: The budget the verdicts were produced against.
        verdicts: Verdicts from :func:`compare`.
        factor: The machine factor that was applied.

    Returns:
        The table, one benchmark per line, ending with a blank line.
    """
    lines = [
        f"benchmark budget {budget.recorded or 'unrecorded'} · "
        f"machine factor {factor:.2f}x · tolerance {budget.tolerance:.0%}",
        "",
        f"{'benchmark':<34} {'measured':>11} {'ceiling':>11} {'ratio':>7}  verdict",
        f"{'-' * 34} {'-' * 11} {'-' * 11} {'-' * 7}  {'-' * 10}",
    ]
    for verdict in verdicts:
        measured = "-" if verdict.measured_us is None else f"{verdict.measured_us:.1f}us"
        ceiling = "-" if verdict.ceiling_us is None else f"{verdict.ceiling_us:.1f}us"
        ratio = "-" if verdict.ratio is None else f"{verdict.ratio:.2f}x"
        lines.append(
            f"{verdict.name:<34} {measured:>11} {ceiling:>11} {ratio:>7}  {verdict.status}"
        )
    findings = [verdict for verdict in verdicts if not verdict.ok]
    lines.append("")
    if findings:
        lines.append(f"{len(findings)} finding(s):")
        for verdict in findings:
            lines.append(f"  - {verdict.name}: {verdict.detail}")
    else:
        lines.append(f"all {len(verdicts)} benchmarks within budget")
    return "\n".join(lines)


def _payload(
    budget: Budget, verdicts: Sequence[Verdict], factor: float, notes: Sequence[str]
) -> Dict[str, Any]:
    """Build the machine-readable verdict document.

    Args:
        budget: The budget the verdicts were produced against.
        verdicts: Verdicts from :func:`compare`.
        factor: The machine factor that was applied.
        notes: Warnings raised while computing the factor.

    Returns:
        The document printed by ``--json``.
    """
    return {
        "ok": all(verdict.ok for verdict in verdicts),
        "recorded": budget.recorded,
        "machine_factor": round(factor, 3),
        "tolerance": budget.tolerance,
        "notes": list(notes),
        "verdicts": [verdict.as_dict() for verdict in verdicts],
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        The parser for this tool.
    """
    parser = argparse.ArgumentParser(
        prog="benchmark_budget",
        description="Compare a pytest-benchmark run against the committed budget.",
    )
    parser.add_argument(
        "--results",
        default=str(DEFAULT_RESULTS),
        help="pytest-benchmark JSON report (default: benchmark-results.json)",
    )
    parser.add_argument(
        "--budget",
        default=str(DEFAULT_BUDGET),
        help="budget file to apply or write (default: benchmarks/budget.json)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="extra slack as a fraction, overriding the budget's own value",
    )
    parser.add_argument(
        "--headroom",
        type=float,
        default=DEFAULT_HEADROOM,
        help=f"median-to-ceiling multiplier used by --update (default: {DEFAULT_HEADROOM})",
    )
    parser.add_argument(
        "--recorded",
        default=None,
        help="ISO date to stamp on a budget written by --update (default: today)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the budget from this run instead of comparing against it",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the committed budget and exit without reading a run",
    )
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    parser.add_argument("--version", action="version", version="benchmark-budget 1.0")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the gate.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        :data:`EXIT_OK`, :data:`EXIT_REGRESSION` or :data:`EXIT_UNMEASURABLE`.
    """
    args = build_parser().parse_args(argv)
    budget_path = Path(args.budget)
    results_path = Path(args.results)

    try:
        if args.list:
            budget = load_budget(budget_path)
            print(
                f"{budget.recorded or 'unrecorded'} · calibration {budget.calibration_us:.1f}us "
                f"· headroom {budget.headroom:.1f}x · tolerance {budget.tolerance:.0%}"
            )
            for name, entry in sorted(budget.benchmarks.items()):
                print(f"  {name:<34} {entry.median_us:>10.1f}us -> {entry.max_median_us:>10.1f}us")
            return EXIT_OK

        samples = load_results(results_path)

        if args.update:
            document = build_budget(
                samples,
                headroom=args.headroom,
                tolerance=DEFAULT_TOLERANCE if args.tolerance is None else args.tolerance,
                recorded=args.recorded,
                python=sys.version.split()[0],
            )
            written = write_budget(document, budget_path)
            print(f"recorded {len(document['benchmarks'])} benchmarks in {written}")
            return EXIT_OK

        budget = load_budget(budget_path)
        factor, notes = machine_factor(samples, budget)
        verdicts = compare(budget, samples, factor=factor, tolerance=args.tolerance)
    except BudgetError as exc:
        print(f"benchmark gate could not reach a verdict: {exc}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    if args.json:
        print(json.dumps(_payload(budget, verdicts, factor, notes), indent=2))
    else:
        for note in notes:
            print(f"note: {note}", file=sys.stderr)
        print(render_table(budget, verdicts, factor))

    return EXIT_OK if all(verdict.ok for verdict in verdicts) else EXIT_REGRESSION


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
