"""Coverage floor gate: fail the build when line coverage drops below 90% (Day 20).

Day 19 proved the suite has teeth by mutating the source. Day 20 answers the
cheaper, blunter question that has to be asked on *every* commit: is the code
still being executed at all? ``pytest-cov`` will happily print a number and
exit ``0`` while that number slides from 94% to 61%, because a coverage report
is a *measurement*, not a *verdict*. This module turns it into a verdict.

Three things make the gate trustworthy rather than decorative:

* **The floor lives in one place.** :data:`DEFAULT_MINIMUM` is 90.0 and
  ``[tool.coverage.report] fail_under`` in ``pyproject.toml`` carries the same
  number, so a bare ``pytest --cov`` and this gate cannot disagree. CI passes
  ``--minimum 90`` explicitly as well; the tests assert all three agree.
* **The scope is the shipped code.** ``[tool.coverage.run]`` in
  ``pyproject.toml`` measures the five projects and the shared core modules,
  and omits ``tests/``, ``tools/`` and ``conftest.py``. Measuring the test
  files would flatter the number — every line of a test that runs is a covered
  line — and the gate would then be watching the wrong thing.
* **A drop is reproducible.** ``--report-only --from-json`` re-reads a saved
  report, so the verdict on a past run can be recomputed offline, and the
  pytest suite can test the gate itself without spawning a second pytest.

Run it directly::

    python tools/coverage_gate.py                      # run pytest, then gate at 90%
    python tools/coverage_gate.py --minimum 92         # stricter floor
    python tools/coverage_gate.py --report-only        # gate the existing coverage.json
    python tools/coverage_gate.py --from-json cov.json # gate a saved report
    python tools/coverage_gate.py --json               # machine-readable verdict
    python tools/coverage_gate.py --per-file-minimum 80  # no module below 80%

Exit codes: ``0`` the floor is met, ``1`` the floor is breached, ``2`` the gate
could not reach a verdict (pytest failed, or no report was produced). A gate
that cannot measure must never report success.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The coverage floor, in percent. Mirrors ``[tool.coverage.report] fail_under``.
DEFAULT_MINIMUM: float = 90.0

#: Where ``pytest-cov`` writes the machine-readable report by default.
DEFAULT_JSON_REPORT: Path = ROOT / "coverage.json"

#: Arguments handed to pytest when the gate runs the suite itself. ``--cov``
#: with no value picks up ``source`` from ``[tool.coverage.run]``.
#:
#: ``--cov-fail-under=0`` deliberately switches off ``pytest-cov``'s own floor,
#: which ``[tool.coverage.report] fail_under`` would otherwise apply. Two
#: enforcers with one number is one too many: pytest-cov would exit ``1`` on a
#: breach, and a non-zero pytest exit means "no verdict" to this gate, so the
#: real finding — *which* floor was breached, by how much — would be lost
#: behind a generic failure. The bare ``pytest --cov`` run keeps the configured
#: floor; the gate owns the verdict when it is the one running the suite.
DEFAULT_PYTEST_ARGS: Tuple[str, ...] = (
    "-q",
    "--cov",
    "--cov-report=term-missing",
    "--cov-fail-under=0",
)

#: Exit code used when the gate cannot reach a verdict at all.
EXIT_UNMEASURABLE: int = 2


@dataclass(frozen=True)
class FileCoverage:
    """Coverage for one measured file.

    Attributes:
        path: Path relative to the repository root, POSIX-style.
        statements: Number of executable statements (plus branches when branch
            coverage is on).
        missing: Number of those that no test executed.
        percent: Covered share, ``0.0``–``100.0``.
    """

    path: str
    statements: int
    missing: int
    percent: float

    @property
    def covered(self) -> int:
        """Return the number of executed statements."""
        return self.statements - self.missing


@dataclass(frozen=True)
class CoverageSummary:
    """The totals of a coverage report, plus its per-file rows.

    Attributes:
        statements: Total executable statements measured.
        missing: Total statements no test executed.
        percent: Overall covered share, ``0.0``–``100.0``.
        files: Per-file rows, worst-covered first.
    """

    statements: int
    missing: int
    percent: float
    files: Tuple[FileCoverage, ...] = ()

    @property
    def covered(self) -> int:
        """Return the number of executed statements across every file."""
        return self.statements - self.missing


@dataclass(frozen=True)
class GateFailure:
    """One reason the gate refused to pass.

    Attributes:
        kind: ``"total"``, ``"file"`` or ``"unmeasurable"``.
        path: The offending file for ``"file"`` failures, else ``None``.
        actual: The measured percentage.
        minimum: The floor it had to clear.
        message: A one-line explanation suitable for a build log.
    """

    kind: str
    path: Optional[str]
    actual: float
    minimum: float
    message: str


@dataclass(frozen=True)
class GateVerdict:
    """The gate's decision.

    Attributes:
        passed: Whether the summary cleared every configured floor.
        failures: Every breach, in reporting order.
    """

    passed: bool
    failures: Tuple[GateFailure, ...] = ()


def percent(covered: int, total: int) -> float:
    """Return ``covered / total`` as a percentage.

    Args:
        covered: Lines (or statements) that were executed.
        total: Lines (or statements) that could be executed.

    Returns:
        The percentage, or ``100.0`` when ``total`` is zero — a file with
        nothing to execute cannot be under-covered, and dividing by zero here
        would turn an empty module into a build failure.
    """
    if total <= 0:
        return 100.0
    return 100.0 * covered / total


def _number(mapping: Mapping[str, Any], key: str) -> float:
    """Read a numeric field from a coverage JSON mapping.

    Args:
        mapping: The mapping to read from.
        key: The field name.

    Returns:
        The value as a float.

    Raises:
        ValueError: If the field is missing or is not a number.
    """
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"coverage report is missing a numeric {key!r} field")
    return float(value)


def parse_coverage_json(payload: Mapping[str, Any]) -> CoverageSummary:
    """Parse the JSON emitted by ``coverage json`` / ``--cov-report=json``.

    Args:
        payload: The decoded report, as written by ``pytest-cov``.

    Returns:
        The totals and per-file rows, sorted worst-covered first.

    Raises:
        ValueError: If the payload has no ``totals`` mapping or the totals
            lack the fields the gate needs.
    """
    totals = payload.get("totals")
    if not isinstance(totals, Mapping):
        raise ValueError("coverage report has no 'totals' mapping")

    statements = int(_number(totals, "num_statements"))
    missing = int(_number(totals, "missing_lines"))
    overall = _number(totals, "percent_covered")

    files: List[FileCoverage] = []
    raw_files = payload.get("files")
    if isinstance(raw_files, Mapping):
        for path, entry in raw_files.items():
            if not isinstance(entry, Mapping):
                continue
            summary = entry.get("summary")
            if not isinstance(summary, Mapping):
                continue
            file_statements = int(_number(summary, "num_statements"))
            file_missing = int(_number(summary, "missing_lines"))
            files.append(
                FileCoverage(
                    path=str(path).replace("\\", "/"),
                    statements=file_statements,
                    missing=file_missing,
                    percent=_number(summary, "percent_covered"),
                )
            )

    files.sort(key=lambda row: (row.percent, row.path))
    return CoverageSummary(
        statements=statements, missing=missing, percent=overall, files=tuple(files)
    )


def load_coverage_json(path: Path) -> CoverageSummary:
    """Read and parse a coverage JSON report from disk.

    Args:
        path: The report to read.

    Returns:
        The parsed summary.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the file is not valid JSON or not a coverage report.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} does not contain a coverage report")
    return parse_coverage_json(payload)


def evaluate(
    summary: CoverageSummary,
    minimum: float = DEFAULT_MINIMUM,
    per_file_minimum: Optional[float] = None,
) -> GateVerdict:
    """Decide whether a summary clears the floors.

    Args:
        summary: The measured coverage.
        minimum: The overall floor, in percent.
        per_file_minimum: Optional floor applied to every file individually.
            Files with nothing to execute are exempt.

    Returns:
        The verdict, with one failure per breach. The overall breach is
        reported first because it is the one the build log should shout about.
    """
    failures: List[GateFailure] = []

    if summary.percent < minimum:
        failures.append(
            GateFailure(
                kind="total",
                path=None,
                actual=summary.percent,
                minimum=minimum,
                message=(
                    f"total coverage {summary.percent:.2f}% is below the "
                    f"{minimum:g}% floor ({summary.missing} of "
                    f"{summary.statements} statements unexecuted)"
                ),
            )
        )

    if per_file_minimum is not None:
        for row in summary.files:
            if row.statements <= 0 or row.percent >= per_file_minimum:
                continue
            failures.append(
                GateFailure(
                    kind="file",
                    path=row.path,
                    actual=row.percent,
                    minimum=per_file_minimum,
                    message=(
                        f"{row.path} is at {row.percent:.2f}%, below the "
                        f"{per_file_minimum:g}% per-file floor"
                    ),
                )
            )

    return GateVerdict(passed=not failures, failures=tuple(failures))


def render_summary(
    summary: CoverageSummary,
    verdict: GateVerdict,
    minimum: float = DEFAULT_MINIMUM,
    show_missing: bool = True,
    worst: int = 10,
) -> str:
    """Render a human-readable gate report.

    Args:
        summary: The measured coverage.
        verdict: The gate's decision.
        minimum: The floor the verdict was computed against.
        show_missing: Whether to list the worst-covered files.
        worst: How many of the worst files to list.

    Returns:
        The report, ending in a newline.
    """
    lines = [
        "Coverage gate",
        f"  measured : {summary.percent:.2f}% "
        f"({summary.covered}/{summary.statements} statements)",
        f"  floor    : {minimum:g}%",
    ]

    if show_missing and summary.files:
        laggards = [row for row in summary.files if row.percent < minimum][:worst]
        if laggards:
            lines.append("")
            lines.append(f"  {'file':<58}{'stmts':>7}{'miss':>6}{'cover':>8}")
            for row in laggards:
                lines.append(
                    f"  {row.path:<58}{row.statements:>7}{row.missing:>6}" f"{row.percent:>7.1f}%"
                )

    lines.append("")
    if verdict.passed:
        lines.append(f"PASS — coverage is at or above the {minimum:g}% floor.")
    else:
        lines.append("FAIL — coverage floor breached:")
        for failure in verdict.failures:
            lines.append(f"  - {failure.message}")

    return "\n".join(lines) + "\n"


def summary_to_dict(
    summary: CoverageSummary,
    verdict: GateVerdict,
    minimum: float = DEFAULT_MINIMUM,
) -> Dict[str, Any]:
    """Convert a summary and verdict into a JSON-serialisable mapping.

    Args:
        summary: The measured coverage.
        verdict: The gate's decision.
        minimum: The floor the verdict was computed against.

    Returns:
        A mapping with the totals, the floor, the verdict and the per-file rows.
    """
    return {
        "statements": summary.statements,
        "covered": summary.covered,
        "missing": summary.missing,
        "percent": round(summary.percent, 2),
        "minimum": minimum,
        "passed": verdict.passed,
        "failures": [
            {
                "kind": failure.kind,
                "path": failure.path,
                "actual": round(failure.actual, 2),
                "minimum": failure.minimum,
                "message": failure.message,
            }
            for failure in verdict.failures
        ],
        "files": [
            {
                "path": row.path,
                "statements": row.statements,
                "missing": row.missing,
                "percent": round(row.percent, 2),
            }
            for row in summary.files
        ],
    }


def build_pytest_command(
    json_path: Path = DEFAULT_JSON_REPORT,
    extra_args: Sequence[str] = (),
) -> List[str]:
    """Build the pytest command line the gate runs.

    Args:
        json_path: Where ``pytest-cov`` should write the JSON report.
        extra_args: Additional arguments appended after the defaults.

    Returns:
        The command as an argument list, ready for :func:`subprocess.run`.
    """
    command = [sys.executable, "-m", "pytest"]
    command.extend(DEFAULT_PYTEST_ARGS)
    command.append(f"--cov-report=json:{json_path}")
    command.extend(extra_args)
    return command


def run_pytest(
    json_path: Path = DEFAULT_JSON_REPORT,
    extra_args: Sequence[str] = (),
) -> int:
    """Run the test suite under coverage in the repository root.

    Args:
        json_path: Where ``pytest-cov`` should write the JSON report.
        extra_args: Additional arguments appended after the defaults.

    Returns:
        The exit code of the pytest process.
    """
    command = build_pytest_command(json_path, extra_args)
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the coverage gate.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="coverage_gate.py",
        description="Fail when line coverage drops below the configured floor.",
    )
    parser.add_argument(
        "--minimum",
        type=float,
        default=DEFAULT_MINIMUM,
        metavar="PERCENT",
        help=f"overall coverage floor (default: {DEFAULT_MINIMUM:g})",
    )
    parser.add_argument(
        "--per-file-minimum",
        type=float,
        default=None,
        metavar="PERCENT",
        help="also require every measured file to reach this percentage",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="gate the existing report instead of running pytest",
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"report to gate (default: {DEFAULT_JSON_REPORT.name})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the verdict as JSON instead of a table",
    )
    parser.add_argument(
        "--no-missing",
        action="store_true",
        help="do not list the worst-covered files in the text report",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument for pytest when the gate runs the suite (repeatable)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the gate and return the process exit code.

    Args:
        argv: Command-line arguments, without the program name. Defaults to
            :data:`sys.argv` without the program name.

    Returns:
        ``0`` when the floor is met, ``1`` when it is breached, and ``2`` when
        no verdict could be reached — a failed pytest run, a missing report, or
        a report the parser cannot read.
    """
    args = build_parser().parse_args(None if argv is None else list(argv))
    report_path = Path(args.from_json) if args.from_json else DEFAULT_JSON_REPORT

    if not args.report_only:
        exit_code = run_pytest(report_path, args.pytest_arg)
        if exit_code != 0:
            print(
                f"pytest exited with {exit_code}; the coverage gate cannot pass "
                "on a failing suite",
                file=sys.stderr,
            )
            return EXIT_UNMEASURABLE

    try:
        summary = load_coverage_json(report_path)
    except (OSError, ValueError) as exc:
        print(f"cannot read a coverage report from {report_path}: {exc}", file=sys.stderr)
        print("run without --report-only to generate one", file=sys.stderr)
        return EXIT_UNMEASURABLE

    verdict = evaluate(summary, args.minimum, args.per_file_minimum)

    if args.json:
        print(json.dumps(summary_to_dict(summary, verdict, args.minimum), indent=2))
    else:
        print(
            render_summary(
                summary,
                verdict,
                minimum=args.minimum,
                show_missing=not args.no_missing,
            ),
            end="",
        )

    if verdict.passed:
        return 0

    for failure in verdict.failures:
        print(failure.message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
