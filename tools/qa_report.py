"""Week 3 QA report: one command, one verdict, across every quality layer (Day 21).

Week 3 was six days of *separate* gates. Day 15 counted assertions, Day 16
audited test isolation, Day 17 added property tests, Day 18 fuzzed the CLIs,
Day 19 mutated the source, and Day 20 put a floor under coverage. Each of them
already exits non-zero on a breach — but they exit *independently*, which means
the honest answer to "is this repository healthy?" is still spread across six
commands, six exit codes and a reader who remembers to run all of them.

This module is the join. It collects one measurement per layer, compares each
against the floor that layer was given in the plan, and prints a single table
whose last line is a verdict:

========================  ===========================  =========  =========
Layer                     Metric                       Floor      Direction
========================  ===========================  =========  =========
``static``                flake8 + black violations    0          at most
``assertions``            assertion statements         200        at least
``isolation``             real-I/O violations          0          at most
``properties``            Hypothesis failures          0          at most
``fuzz``                  crashing cases               0          at most
``mutation``              mutation score               95.0%      at least
``coverage``              line coverage                90.0%      at least
========================  ===========================  =========  =========

Every floor is imported from the module that owns it rather than retyped, so
the report cannot drift away from the gate it reports on:
:data:`assertion_census.ASSERTION_BUDGET`, :data:`mutation.DEFAULT_MINIMUM_SCORE`
and :data:`coverage_gate.DEFAULT_MINIMUM`.

Two design rules carried over from the Day 20 gate:

* **A layer that cannot be measured is not a pass.** A missing ``flake8``, an
  absent mutmut cache, or a coverage report that will not parse leaves that
  layer ``unmeasured`` and the report exits ``2``. Silence is never reported as
  health; ``--allow-unmeasured`` is the explicit opt-out for a partial run.
* **The verdict is separable from the collection.** :func:`build_report` is a
  pure function from an evidence mapping to a report, so the suite can test
  every boundary — exactly at the floor, one unit under it, a missing layer —
  without running a six-minute campaign.

Run it directly::

    python tools/qa_report.py                     # collect everything, then report
    python tools/qa_report.py --skip-slow         # reuse coverage.json / mutants cache
    python tools/qa_report.py --json              # machine-readable report
    python tools/qa_report.py --write-json qa.json    # save the evidence
    python tools/qa_report.py --from-json qa.json     # re-judge a past run offline

Exit codes: ``0`` every layer measured and passing, ``1`` a measured layer
breached its floor, ``2`` the report could not be completed — a layer was
unmeasurable, or the evidence file could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:  # pragma: no cover - the tools package is importable
    sys.path.insert(0, str(ROOT))

from tools import assertion_census, coverage_gate, io_audit, mutation  # noqa: E402

#: The layers, in reporting order: cheap and static first, expensive last.
LAYER_ORDER: Tuple[str, ...] = (
    "static",
    "assertions",
    "isolation",
    "properties",
    "fuzz",
    "mutation",
    "coverage",
)

#: Floors, each owned by the module that enforces it.
ASSERTION_FLOOR: float = float(assertion_census.ASSERTION_BUDGET)
MUTATION_FLOOR: float = float(mutation.DEFAULT_MINIMUM_SCORE)
COVERAGE_FLOOR: float = float(coverage_gate.DEFAULT_MINIMUM)

#: A layer measured in violations or crashes is allowed none of them.
ZERO_TOLERANCE: float = 0.0

#: Comparator names, so a report can say *which way* a floor points.
AT_LEAST = "at_least"
AT_MOST = "at_most"

#: Symbol printed in the table for each comparator.
_COMPARATOR_SYMBOL: Dict[str, str] = {AT_LEAST: ">=", AT_MOST: "<="}

#: Campaign size used for the fuzz layer when the report runs it itself. CI
#: already runs a deeper 400-case corpus; this is a smoke layer, not a re-run.
DEFAULT_FUZZ_CASES: int = 3
DEFAULT_FUZZ_SEED: int = 18

#: Exit code used when the report cannot reach a verdict.
EXIT_UNMEASURABLE: int = 2

#: Where ``pytest-cov`` writes its machine-readable report.
DEFAULT_COVERAGE_JSON: Path = ROOT / "coverage.json"

#: Where mutmut writes its cache.
DEFAULT_MUTANTS_DIR: Path = mutation.DEFAULT_MUTANTS_DIR

#: ``"31 passed, 2 skipped in 1.20s"`` — the counts pytest prints on its last line.
_PYTEST_COUNT_RE = re.compile(r"(\d+) passed")


@dataclass(frozen=True)
class GateResult:
    """One layer's measurement and the verdict on it.

    Attributes:
        layer: Layer name, one of :data:`LAYER_ORDER`.
        metric: What was measured, in words, e.g. ``"violations"``.
        measured: The measured value, or ``None`` when the layer could not be
            measured at all.
        floor: The threshold the measurement is compared against.
        comparator: :data:`AT_LEAST` when the measurement must reach the floor,
            :data:`AT_MOST` when it must stay under it.
        unit: Suffix used when printing the number, e.g. ``"%"``.
        detail: One line of human context — counts, the failing command, or the
            reason a layer was unmeasurable.
    """

    layer: str
    metric: str
    measured: Optional[float]
    floor: float
    comparator: str
    unit: str
    detail: str

    @property
    def passed(self) -> Optional[bool]:
        """Return whether the layer cleared its floor, or ``None`` if unmeasured."""
        if self.measured is None:
            return None
        if self.comparator == AT_LEAST:
            return self.measured >= self.floor
        return self.measured <= self.floor

    @property
    def verdict(self) -> str:
        """Return ``"pass"``, ``"fail"`` or ``"unmeasured"``."""
        state = self.passed
        if state is None:
            return "unmeasured"
        return "pass" if state else "fail"


@dataclass(frozen=True)
class QualityReport:
    """The whole dashboard: one :class:`GateResult` per layer.

    Attributes:
        gates: The layer results, in :data:`LAYER_ORDER`.
        generated: ISO date the evidence was collected, for the header.
    """

    gates: Tuple[GateResult, ...]
    generated: str = ""

    @property
    def failing(self) -> Tuple[GateResult, ...]:
        """Return the layers that breached their floor."""
        return tuple(gate for gate in self.gates if gate.verdict == "fail")

    @property
    def unmeasured(self) -> Tuple[GateResult, ...]:
        """Return the layers that could not be measured."""
        return tuple(gate for gate in self.gates if gate.verdict == "unmeasured")

    @property
    def passed(self) -> bool:
        """Return ``True`` only when every layer was measured and every one passed."""
        return not self.failing and not self.unmeasured

    @property
    def verdict(self) -> str:
        """Return ``"pass"``, ``"fail"`` or ``"incomplete"``."""
        if self.failing:
            return "fail"
        if self.unmeasured:
            return "incomplete"
        return "pass"


def _number(evidence: Mapping[str, Any], key: str) -> Optional[float]:
    """Read a numeric field from one layer's evidence.

    Args:
        evidence: The layer's evidence mapping.
        key: The field to read.

    Returns:
        The value as a float, or ``None`` when it is absent or not a number —
        a missing count makes the layer unmeasurable, never zero.
    """
    value = evidence.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _unmeasured(layer: str, metric: str, floor: float, unit: str, reason: str) -> GateResult:
    """Build a gate result for a layer that produced no measurement.

    Args:
        layer: Layer name.
        metric: What would have been measured.
        floor: The floor it would have been compared against.
        unit: Unit suffix for printing.
        reason: Why no measurement exists.

    Returns:
        An unmeasured :class:`GateResult`.
    """
    return GateResult(
        layer=layer,
        metric=metric,
        measured=None,
        floor=floor,
        comparator=AT_MOST if floor == ZERO_TOLERANCE else AT_LEAST,
        unit=unit,
        detail=reason,
    )


def _static_gate(evidence: Mapping[str, Any]) -> GateResult:
    """Judge the static layer: flake8 violations plus black reformat requests.

    Args:
        evidence: Mapping with ``flake8_violations`` and ``black_unformatted``.

    Returns:
        The layer result. Either field missing makes the layer unmeasured,
        because a lint run that did not happen is not a clean lint run.
    """
    flake8 = _number(evidence, "flake8_violations")
    black = _number(evidence, "black_unformatted")
    if flake8 is None or black is None:
        return _unmeasured(
            "static", "violations", ZERO_TOLERANCE, "", "flake8 or black did not run"
        )
    total = flake8 + black
    return GateResult(
        layer="static",
        metric="violations",
        measured=total,
        floor=ZERO_TOLERANCE,
        comparator=AT_MOST,
        unit="",
        detail=f"flake8 {int(flake8)} + black {int(black)}",
    )


def _assertion_gate(evidence: Mapping[str, Any]) -> GateResult:
    """Judge the assertion layer against the Day 15 budget.

    Args:
        evidence: Mapping with ``tests`` and ``assertions`` counts.

    Returns:
        The layer result, or an unmeasured one when the count is absent.
    """
    asserts = _number(evidence, "assertions")
    tests = _number(evidence, "tests")
    if asserts is None or tests is None:
        return _unmeasured(
            "assertions", "assertions", ASSERTION_FLOOR, "", "assertion census did not run"
        )
    return GateResult(
        layer="assertions",
        metric="assertions",
        measured=asserts,
        floor=ASSERTION_FLOOR,
        comparator=AT_LEAST,
        unit="",
        detail=f"{int(tests)} test functions",
    )


def _isolation_gate(evidence: Mapping[str, Any]) -> GateResult:
    """Judge the isolation layer: real-I/O smells in the test suite.

    Args:
        evidence: Mapping with a ``violations`` count.

    Returns:
        The layer result, or an unmeasured one when the audit did not run.
    """
    violations = _number(evidence, "violations")
    if violations is None:
        return _unmeasured("isolation", "violations", ZERO_TOLERANCE, "", "io audit did not run")
    return GateResult(
        layer="isolation",
        metric="violations",
        measured=violations,
        floor=ZERO_TOLERANCE,
        comparator=AT_MOST,
        unit="",
        detail="imports, wall clock, home directory",
    )


def _property_gate(evidence: Mapping[str, Any]) -> GateResult:
    """Judge the property layer by the number of Hypothesis failures.

    Args:
        evidence: Mapping with ``tests`` and ``failures`` counts.

    Returns:
        The layer result, or an unmeasured one when the run is absent.
    """
    failures = _number(evidence, "failures")
    tests = _number(evidence, "tests")
    if failures is None:
        return _unmeasured(
            "properties", "failures", ZERO_TOLERANCE, "", "property tests did not run"
        )
    detail = f"{int(tests)} property tests" if tests is not None else "property tests"
    return GateResult(
        layer="properties",
        metric="failures",
        measured=failures,
        floor=ZERO_TOLERANCE,
        comparator=AT_MOST,
        unit="",
        detail=detail,
    )


def _fuzz_gate(evidence: Mapping[str, Any]) -> GateResult:
    """Judge the fuzz layer by the number of crashing cases.

    Args:
        evidence: Mapping with ``cases`` and ``failures`` counts.

    Returns:
        The layer result, or an unmeasured one when the campaign is absent.
    """
    failures = _number(evidence, "failures")
    cases = _number(evidence, "cases")
    if failures is None:
        return _unmeasured("fuzz", "crashes", ZERO_TOLERANCE, "", "fuzz campaign did not run")
    detail = f"{int(cases)} cases" if cases is not None else "fuzz corpus"
    return GateResult(
        layer="fuzz",
        metric="crashes",
        measured=failures,
        floor=ZERO_TOLERANCE,
        comparator=AT_MOST,
        unit="",
        detail=detail,
    )


def _mutation_gate(evidence: Mapping[str, Any]) -> GateResult:
    """Judge the mutation layer by the mutation score.

    Args:
        evidence: Mapping with ``mutants``, ``killed`` and ``score``.

    Returns:
        The layer result, or an unmeasured one when there is no mutmut cache —
        an absent cache is a run that never happened, not a perfect score.
    """
    score = _number(evidence, "score")
    total = _number(evidence, "mutants")
    if score is None or total is None or total <= 0:
        return _unmeasured(
            "mutation",
            "score",
            MUTATION_FLOOR,
            "%",
            "no mutmut cache; run python tools/mutation.py",
        )
    killed = _number(evidence, "killed") or 0.0
    return GateResult(
        layer="mutation",
        metric="score",
        measured=score,
        floor=MUTATION_FLOOR,
        comparator=AT_LEAST,
        unit="%",
        detail=f"{int(killed)} of {int(total)} mutants killed",
    )


def _coverage_gate(evidence: Mapping[str, Any]) -> GateResult:
    """Judge the coverage layer against the Day 20 floor.

    Args:
        evidence: Mapping with ``percent`` and ``statements``.

    Returns:
        The layer result, or an unmeasured one when no report could be read.
    """
    percent = _number(evidence, "percent")
    statements = _number(evidence, "statements")
    if percent is None:
        return _unmeasured(
            "coverage", "percent", COVERAGE_FLOOR, "%", "no readable coverage report"
        )
    detail = f"{int(statements)} statements" if statements is not None else "line coverage"
    return GateResult(
        layer="coverage",
        metric="percent",
        measured=percent,
        floor=COVERAGE_FLOOR,
        comparator=AT_LEAST,
        unit="%",
        detail=detail,
    )


#: One judge per layer, keyed by layer name.
_JUDGES = {
    "static": _static_gate,
    "assertions": _assertion_gate,
    "isolation": _isolation_gate,
    "properties": _property_gate,
    "fuzz": _fuzz_gate,
    "mutation": _mutation_gate,
    "coverage": _coverage_gate,
}


def build_report(evidence: Mapping[str, Any], generated: str = "") -> QualityReport:
    """Turn an evidence mapping into a judged report.

    Args:
        evidence: A mapping shaped like :func:`collect`'s output — a ``layers``
            mapping from layer name to that layer's measurements. An absent
            layer, or one whose measurements are incomplete, becomes an
            unmeasured gate rather than a pass.
        generated: ISO date to stamp on the report. Defaults to today.

    Returns:
        The report, with one gate per layer in :data:`LAYER_ORDER`.
    """
    layers = evidence.get("layers")
    if not isinstance(layers, Mapping):
        layers = {}
    gates: List[GateResult] = []
    for layer in LAYER_ORDER:
        entry = layers.get(layer)
        if not isinstance(entry, Mapping):
            entry = {}
        gates.append(_JUDGES[layer](entry))
    stamp = generated or str(evidence.get("generated") or "")
    return QualityReport(gates=tuple(gates), generated=stamp)


def _format_measurement(gate: GateResult) -> str:
    """Render a gate's measured value for the table.

    Args:
        gate: The gate to render.

    Returns:
        The number with its unit, or ``"—"`` when unmeasured.
    """
    if gate.measured is None:
        return "—"
    if gate.unit == "%":
        return f"{gate.measured:.2f}%"
    return f"{gate.measured:.0f}"


def _format_floor(gate: GateResult) -> str:
    """Render a gate's floor with its comparator for the table.

    Args:
        gate: The gate to render.

    Returns:
        For example ``">= 200"`` or ``"<= 0"``.
    """
    symbol = _COMPARATOR_SYMBOL.get(gate.comparator, ">=")
    if gate.unit == "%":
        return f"{symbol} {gate.floor:g}%"
    return f"{symbol} {gate.floor:g}"


def render(report: QualityReport) -> str:
    """Render the report as a fixed-width table plus a verdict.

    Args:
        report: The report to render.

    Returns:
        The rendered report, ending in a newline.
    """
    width = max(len(gate.layer) for gate in report.gates) if report.gates else 5
    lines = ["Quality report"]
    if report.generated:
        lines.append(f"  generated : {report.generated}")
    lines.append("")
    lines.append(f"  {'layer':<{width}}  {'metric':<12}{'measured':>10}  {'floor':<9}verdict")
    lines.append(f"  {'-' * width}  {'-' * 12}{'-' * 10}  {'-' * 9}{'-' * 10}")
    for gate in report.gates:
        lines.append(
            f"  {gate.layer:<{width}}  {gate.metric:<12}"
            f"{_format_measurement(gate):>10}  {_format_floor(gate):<9}{gate.verdict}"
        )
    lines.append("")

    for gate in report.failing:
        lines.append(f"  FAIL {gate.layer}: {gate.detail}")
    for gate in report.unmeasured:
        lines.append(f"  ?    {gate.layer}: {gate.detail}")

    lines.append("")
    if report.verdict == "pass":
        lines.append("PASS — every layer was measured and every floor was met.")
    elif report.verdict == "fail":
        lines.append(f"FAIL — {len(report.failing)} layer(s) breached their floor.")
    else:
        lines.append(
            f"INCOMPLETE — {len(report.unmeasured)} layer(s) could not be measured; "
            "an unmeasured layer is not a passing one."
        )
    return "\n".join(lines) + "\n"


def report_to_dict(report: QualityReport) -> Dict[str, Any]:
    """Convert a report into a JSON-serialisable mapping.

    Args:
        report: The report to convert.

    Returns:
        A mapping with the verdict, the counts and one row per layer.
    """
    return {
        "generated": report.generated,
        "verdict": report.verdict,
        "passed": report.passed,
        "failing": [gate.layer for gate in report.failing],
        "unmeasured": [gate.layer for gate in report.unmeasured],
        "layers": [
            {
                "layer": gate.layer,
                "metric": gate.metric,
                "measured": gate.measured,
                "floor": gate.floor,
                "comparator": gate.comparator,
                "unit": gate.unit,
                "verdict": gate.verdict,
                "detail": gate.detail,
            }
            for gate in report.gates
        ],
    }


def _run(
    command: Sequence[str], cwd: Path = ROOT, timeout: int = 900
) -> Optional[subprocess.CompletedProcess]:
    """Run a command, returning ``None`` when it cannot be launched at all.

    Args:
        command: The command as an argument list.
        cwd: Directory to run it in.
        timeout: Seconds to allow before the process is killed.

    Returns:
        The completed process, or ``None`` when the executable is missing — the
        caller reports that layer as unmeasurable rather than as a failure.
    """
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def collect_static(root: Path = ROOT) -> Dict[str, Any]:
    """Measure the static layer by running flake8 and black.

    Args:
        root: Repository root to lint.

    Returns:
        ``{"flake8_violations": int, "black_unformatted": int}``, or an empty
        mapping when either tool is unavailable.
    """
    flake8 = _run([sys.executable, "-m", "flake8", "."], cwd=root)
    black = _run([sys.executable, "-m", "black", "--check", "."], cwd=root)
    if flake8 is None or black is None:
        return {}
    if "No module named" in flake8.stderr or "No module named" in black.stderr:
        return {}
    violations = len([line for line in flake8.stdout.splitlines() if line.strip()])
    unformatted = len(
        [line for line in black.stdout.splitlines() if line.startswith("would reformat")]
    )
    return {"flake8_violations": violations, "black_unformatted": unformatted}


def collect_assertions(root: Path = ROOT) -> Dict[str, Any]:
    """Measure the assertion layer with the Day 15 census.

    Args:
        root: Repository root holding ``tests/``.

    Returns:
        ``{"tests": int, "assertions": int}``.
    """
    rows = assertion_census.census(root / "tests", root=root)
    tests, asserts = assertion_census.totals(rows)
    return {"tests": tests, "assertions": asserts}


def collect_isolation(root: Path = ROOT) -> Dict[str, Any]:
    """Measure the isolation layer with the Day 16 audit.

    Args:
        root: Repository root holding ``tests/``.

    Returns:
        ``{"violations": int}``.
    """
    paths = sorted((root / "tests").glob("test_*.py"))
    return {"violations": len(io_audit.audit_paths(paths))}


def collect_properties(root: Path = ROOT) -> Dict[str, Any]:
    """Measure the property layer by running the Hypothesis module.

    Args:
        root: Repository root.

    Returns:
        ``{"tests": int, "failures": int}``, or an empty mapping when pytest
        could not be launched.
    """
    completed = _run([sys.executable, "-m", "pytest", "-q", "tests/test_properties.py"], cwd=root)
    if completed is None:
        return {}
    rows = assertion_census.census(root / "tests", root=root)
    declared = sum(row.tests for row in rows if row.path.endswith("test_properties.py"))
    match = _PYTEST_COUNT_RE.search(completed.stdout)
    passed = int(match.group(1)) if match else 0
    return {"tests": declared or passed, "failures": 0 if completed.returncode == 0 else 1}


def collect_fuzz(
    root: Path = ROOT, cases: int = DEFAULT_FUZZ_CASES, seed: int = DEFAULT_FUZZ_SEED
) -> Dict[str, Any]:
    """Measure the fuzz layer by running a short campaign.

    Args:
        root: Repository root.
        cases: Cases per campaign.
        seed: Corpus seed, kept fixed so the layer is reproducible.

    Returns:
        ``{"cases": int, "failures": int}``, or an empty mapping when the
        driver could not be launched or its JSON could not be read.
    """
    completed = _run(
        [
            sys.executable,
            "tools/fuzz.py",
            "--cases",
            str(cases),
            "--seed",
            str(seed),
            "--json",
        ],
        cwd=root,
    )
    if completed is None:
        return {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return {"cases": payload.get("cases", 0), "failures": payload.get("failures", 0)}


def collect_mutation(root: Path = ROOT) -> Dict[str, Any]:
    """Measure the mutation layer from mutmut's on-disk cache.

    Args:
        root: Repository root holding ``mutants/``.

    Returns:
        ``{"mutants": int, "killed": int, "score": float}``, or an empty
        mapping when the cache is absent — the report says so rather than
        assuming a clean run.
    """
    report = mutation.load_report(root / "mutants")
    if report.total == 0:
        return {}
    return {
        "mutants": report.total,
        "killed": report.killed,
        "score": round(report.score, 2),
    }


def collect_coverage(
    root: Path = ROOT,
    minimum: float = COVERAGE_FLOOR,
    skip_slow: bool = False,
) -> Dict[str, Any]:
    """Measure the coverage layer from the Day 20 report.

    Args:
        root: Repository root.
        minimum: Floor handed to the gate when it runs the suite itself.
        skip_slow: Reuse an existing ``coverage.json`` instead of re-running
            the suite. Falls back to the existing report if the gate cannot run.

    Returns:
        ``{"statements": int, "percent": float}``, or an empty mapping when no
        report exists or none can be parsed.
    """
    report_path = root / DEFAULT_COVERAGE_JSON.name
    if not skip_slow:
        _run(
            [sys.executable, "tools/coverage_gate.py", "--minimum", f"{minimum:g}"],
            cwd=root,
        )
    if not report_path.exists():
        return {}
    try:
        summary = coverage_gate.load_coverage_json(report_path)
    except (OSError, ValueError):
        return {}
    return {"statements": summary.statements, "percent": round(summary.percent, 2)}


def collect(
    root: Path = ROOT,
    *,
    skip_slow: bool = False,
    cases: int = DEFAULT_FUZZ_CASES,
    seed: int = DEFAULT_FUZZ_SEED,
    minimum_coverage: float = COVERAGE_FLOOR,
) -> Dict[str, Any]:
    """Collect one measurement per layer.

    Args:
        root: Repository root.
        skip_slow: Skip the property run and reuse the existing coverage
            report. Mutation is always read from the cache, never re-run.
        cases: Cases per fuzz campaign.
        seed: Fuzz corpus seed.
        minimum_coverage: Coverage floor passed to the gate.

    Returns:
        An evidence mapping of the shape :func:`build_report` accepts.
    """
    layers: Dict[str, Any] = {
        "static": collect_static(root),
        "assertions": collect_assertions(root),
        "isolation": collect_isolation(root),
        "mutation": collect_mutation(root),
        "coverage": collect_coverage(root, minimum_coverage, skip_slow=skip_slow),
    }
    if skip_slow:
        layers["properties"] = {}
        layers["fuzz"] = {}
    else:
        layers["properties"] = collect_properties(root)
        layers["fuzz"] = collect_fuzz(root, cases=cases, seed=seed)
    return {"generated": date.today().isoformat(), "layers": layers}


def load_evidence(path: Path) -> Dict[str, Any]:
    """Read a saved evidence file.

    Args:
        path: The file written by ``--write-json``.

    Returns:
        The decoded evidence mapping.

    Raises:
        ValueError: If the file is not JSON or not a mapping.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} does not contain an evidence mapping")
    return dict(payload)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the QA report.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="qa_report.py",
        description="Judge every Week 3 quality layer in one report.",
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        metavar="PATH",
        help="judge a saved evidence file instead of collecting fresh evidence",
    )
    parser.add_argument(
        "--write-json",
        type=Path,
        default=None,
        metavar="PATH",
        help="save the collected evidence so a later run can re-judge it",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON instead of a table",
    )
    parser.add_argument(
        "--skip-slow",
        action="store_true",
        help="skip the property run and reuse the existing coverage report",
    )
    parser.add_argument(
        "--cases",
        type=int,
        default=DEFAULT_FUZZ_CASES,
        metavar="N",
        help=f"fuzz cases per campaign (default: {DEFAULT_FUZZ_CASES})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_FUZZ_SEED,
        metavar="N",
        help=f"fuzz corpus seed (default: {DEFAULT_FUZZ_SEED})",
    )
    parser.add_argument(
        "--minimum-coverage",
        type=float,
        default=COVERAGE_FLOOR,
        metavar="PERCENT",
        help=f"coverage floor handed to the gate (default: {COVERAGE_FLOOR:g})",
    )
    parser.add_argument(
        "--allow-unmeasured",
        action="store_true",
        help="exit 0/1 on the measured layers instead of 2 when a layer is missing",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Collect evidence, judge it, print the report, and return an exit code.

    Args:
        argv: Command-line arguments without the program name. Defaults to
            :data:`sys.argv` without the program name.

    Returns:
        ``0`` when every layer was measured and passed, ``1`` when a measured
        layer breached its floor, and ``2`` when the report could not be
        completed — an unmeasurable layer, or unreadable evidence.
    """
    args = build_parser().parse_args(None if argv is None else list(argv))

    if args.from_json is not None:
        try:
            evidence = load_evidence(Path(args.from_json))
        except (OSError, ValueError) as exc:
            print(f"qa-report: cannot read evidence: {exc}", file=sys.stderr)
            return EXIT_UNMEASURABLE
    else:
        evidence = collect(
            ROOT,
            skip_slow=args.skip_slow,
            cases=args.cases,
            seed=args.seed,
            minimum_coverage=args.minimum_coverage,
        )

    if args.write_json is not None:
        Path(args.write_json).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    report = build_report(evidence)

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print(render(report))

    if report.verdict == "fail":
        return 1
    if report.verdict == "incomplete" and not args.allow_unmeasured:
        return EXIT_UNMEASURABLE
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
