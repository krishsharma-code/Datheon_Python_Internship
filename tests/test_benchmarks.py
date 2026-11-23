"""Audit the benchmark harness against the declarations around it (Day 30).

A benchmark suite rots differently from code. Nothing breaks when a benchmark
is added without a ceiling, when the gate stops being called by the workflow,
when the docs page falls out of the navigation or when the plugin drops out of
the dev extra — the suite still runs, and the numbers still look fine. What is
lost is the *guarantee*, quietly.

So this module checks the declaration against the repository, in the same shape
as every other gate here: the harness must time the real hot paths, every
benchmark must carry a ceiling and a docstring, the workflow must actually call
the gate and keep its report, and the guide must be a page on the site. Each
check is written as a small function that takes the parsed document, so the
last section can prove the check *fires* on a doctored copy instead of only
passing on the good one.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Sequence, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import benchmark_budget as bb  # noqa: E402  (path set up above)

yaml = pytest.importorskip("yaml", reason="PyYAML is in requirements.txt")

#: The harness, the budget it is graded against, and the workflow that runs it.
BENCH_DIR = ROOT / "benchmarks"
BUDGET_PATH = ROOT / "benchmarks" / "budget.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "benchmarks.yml"
DOCS_PAGE = ROOT / "docs" / "benchmarking.md"
MKDOCS = ROOT / "mkdocs.yml"
PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "requirements.txt"
INDEX = ROOT / "docs" / "index.md"

#: The fewest benchmarks the harness may carry: the four hot paths Day 12
#: profiled, the calibration, and one more so a single removal is visible.
MINIMUM_BENCHMARKS = 6

#: Commands the workflow must run. Each one is a step of the gate, so a workflow
#: missing one has stopped being a gate even though it still passes.
REQUIRED_COMMANDS: Tuple[str, ...] = (
    "actions/checkout@",
    "actions/setup-python@",
    "pip install -r requirements.txt",
    "pytest benchmarks/",
    "tools/benchmark_budget.py",
    "actions/upload-artifact@",
)


def _benchmark_modules() -> List[Path]:
    """Return the benchmark modules, sorted by name.

    Returns:
        Paths of ``benchmarks/bench_*.py``.
    """
    return sorted(BENCH_DIR.glob("bench_*.py"))


def _benchmark_functions() -> Dict[str, ast.FunctionDef]:
    """Parse every benchmark module and return its benchmark functions.

    Returns:
        Function definitions keyed by canonical benchmark name.

    Raises:
        AssertionError: If a module cannot be parsed.
    """
    found: Dict[str, ast.FunctionDef] = {}
    for path in _benchmark_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_bench_"):
                found[bb.canonical_name(node.name)] = node
    return found


def _argument_names(node: ast.FunctionDef) -> List[str]:
    """Return a function's positional and keyword argument names.

    Args:
        node: The function definition.

    Returns:
        Argument names, in declaration order.
    """
    args = node.args
    return [arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)]


def _flatten_nav(entries: Any) -> Iterator[str]:
    """Yield every page path in a parsed MkDocs ``nav``.

    Args:
        entries: A nav list, or one entry of it.

    Yields:
        Page paths as written in ``mkdocs.yml``.
    """
    if isinstance(entries, str):
        yield entries
    elif isinstance(entries, list):
        for entry in entries:
            yield from _flatten_nav(entry)
    elif isinstance(entries, Mapping):
        for value in entries.values():
            yield from _flatten_nav(value)


def _workflow_findings(document: Any) -> List[str]:
    """Return one message per guarantee a benchmark workflow is missing.

    Args:
        document: The parsed workflow.

    Returns:
        Findings; empty when the workflow keeps every guarantee.
    """
    findings: List[str] = []
    if not isinstance(document, Mapping):
        return ["the workflow is not a mapping"]

    # PyYAML resolves the bare key `on` to the boolean True under YAML 1.1, so
    # both spellings are accepted here and in the file itself.
    triggers = document.get("on", document.get(True))
    if not isinstance(triggers, Mapping):
        return ["the workflow has no `on` block"]
    for trigger in ("pull_request", "schedule", "workflow_dispatch"):
        if trigger not in triggers:
            findings.append(f"the workflow does not trigger on {trigger}")

    permissions = document.get("permissions")
    if not isinstance(permissions, Mapping) or permissions.get("contents") != "read":
        findings.append("the workflow does not declare `permissions: contents: read`")

    jobs = document.get("jobs")
    if not isinstance(jobs, Mapping) or not jobs:
        return findings + ["the workflow declares no jobs"]

    for job_name, job in jobs.items():
        if not isinstance(job, Mapping):
            findings.append(f"job {job_name!r} is not a mapping")
            continue
        if job.get("runs-on") != "ubuntu-latest":
            findings.append(f"job {job_name!r} does not run on ubuntu-latest")
        if "timeout-minutes" not in job:
            findings.append(f"job {job_name!r} has no timeout")
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            findings.append(f"job {job_name!r} has no steps")
            continue
        text = "\n".join(
            str(step.get("run", "")) + " " + str(step.get("uses", ""))
            for step in steps
            if isinstance(step, Mapping)
        )
        for command in REQUIRED_COMMANDS:
            if command not in text:
                findings.append(f"job {job_name!r} never runs {command!r}")
        uploads = [
            step
            for step in steps
            if isinstance(step, Mapping) and "upload-artifact" in str(step.get("uses", ""))
        ]
        if uploads and not any(str(step.get("with", {})).find("path") >= 0 for step in uploads):
            findings.append(f"job {job_name!r} uploads an artefact with no path")
    return findings


def _unbudgeted(names: Sequence[str], budget: bb.Budget) -> List[str]:
    """Return the benchmark names the budget does not hold to a ceiling.

    Args:
        names: Benchmark names from the harness.
        budget: The committed budget.

    Returns:
        Names with no ceiling, sorted. The calibration is exempt: it is budgeted
        once, as ``calibration_us``, because it is the scale and not a benchmark
        being judged.
    """
    return sorted(set(names) - set(budget.benchmarks) - {bb.CALIBRATION_NAME})


def _stale(names: Sequence[str], budget: bb.Budget) -> List[str]:
    """Return the ceilings that no longer match a benchmark in the harness.

    Args:
        names: Benchmark names from the harness.
        budget: The committed budget.

    Returns:
        Budget keys with no benchmark behind them, sorted.
    """
    return sorted(set(budget.benchmarks) - set(names))


# --- the harness -----------------------------------------------------------


def test_the_harness_times_the_real_hot_paths() -> None:
    """A benchmark suite with nothing in it protects nothing."""
    assert _benchmark_modules(), "benchmarks/ has no bench_*.py module"
    assert len(_benchmark_functions()) >= MINIMUM_BENCHMARKS


def test_the_calibration_benchmark_is_present() -> None:
    """Without it, a ceiling recorded on one machine means nothing on another."""
    assert bb.CALIBRATION_NAME in _benchmark_functions()


def test_every_benchmark_takes_the_benchmark_fixture() -> None:
    """A function named like a benchmark but not timed is a test in disguise."""
    for name, node in _benchmark_functions().items():
        assert "benchmark" in _argument_names(node), f"{name} does not take `benchmark`"


def test_every_benchmark_is_documented_and_annotated() -> None:
    """The repository's own standard: a docstring and full type hints."""
    for name, node in _benchmark_functions().items():
        assert ast.get_docstring(node), f"{name} has no docstring"
        assert node.returns is not None, f"{name} has no return annotation"
        for arg in node.args.args:
            if arg.arg != "self":
                assert arg.annotation is not None, f"{name} does not annotate {arg.arg!r}"


def test_the_harness_measures_code_it_imports() -> None:
    """A benchmark that re-implements the code measures a program nobody runs."""
    source = "\n".join(path.read_text(encoding="utf-8") for path in _benchmark_modules())
    for module in ("caching", "persistence", "validators", "error_catalogue"):
        assert f"import {module}" in source or f"from {module} import" in source


# --- the budget ------------------------------------------------------------


def test_the_committed_budget_parses() -> None:
    """The gate reads this file; a reader should be able to as well."""
    budget = bb.load_budget(BUDGET_PATH)
    assert budget.calibration_us > 0
    assert budget.headroom >= 1
    assert budget.tolerance >= 0


def test_every_benchmark_carries_a_ceiling() -> None:
    """A new benchmark must arrive with a budget, not slip in unmeasured."""
    budget = bb.load_budget(BUDGET_PATH)
    assert _unbudgeted(list(_benchmark_functions()), budget) == []


def test_the_budget_has_no_stale_ceilings() -> None:
    """A ceiling for a benchmark that no longer exists measures less each month."""
    budget = bb.load_budget(BUDGET_PATH)
    assert _stale(list(_benchmark_functions()), budget) == []


def test_every_ceiling_sits_above_its_recorded_median() -> None:
    """The run that cut the budget must pass the budget it cut."""
    budget = bb.load_budget(BUDGET_PATH)
    for name, entry in budget.benchmarks.items():
        assert entry.max_median_us >= entry.median_us, name


def test_the_budget_records_where_it_was_cut() -> None:
    """A ceiling with no date and no interpreter is a number with no provenance."""
    budget = bb.load_budget(BUDGET_PATH)
    assert budget.recorded
    assert budget.python


# --- the declarations ------------------------------------------------------


def test_pyproject_declares_the_plugin_and_the_filenames() -> None:
    """The suite is only runnable if the extra installs the plugin it needs."""
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "pytest-benchmark>=4.0" in text
    assert '"bench_*.py"' in text
    assert 'testpaths = ["tests"]' in text


def test_requirements_lists_the_plugin() -> None:
    """CI installs requirements.txt, not the extra."""
    assert "pytest-benchmark>=4.0" in REQUIREMENTS.read_text(encoding="utf-8")


def test_the_workflow_exists_and_parses() -> None:
    """A workflow that does not parse is a gate that never runs."""
    assert WORKFLOW_PATH.is_file()
    assert isinstance(yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8")), Mapping)


def test_the_workflow_keeps_every_guarantee() -> None:
    """Triggers, least privilege, a timeout, the gate itself and the report."""
    document = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert _workflow_findings(document) == []


def test_the_docs_page_is_a_page_on_the_site() -> None:
    """Every ``.md`` on disk is in the nav — the docs audit enforces the rest."""
    assert DOCS_PAGE.is_file()
    config = yaml.safe_load(MKDOCS.read_text(encoding="utf-8"))
    pages = list(_flatten_nav(config.get("nav")))
    assert "benchmarking.md" in pages


def test_the_docs_page_documents_the_contract() -> None:
    """A guide that omits the exit codes leaves the reader to guess."""
    text = DOCS_PAGE.read_text(encoding="utf-8")
    for phrase in (
        "tools/benchmark_budget.py",
        "benchmarks/budget.json",
        "--update",
        "Exit codes",
        "calibration",
    ):
        assert phrase in text, phrase


def test_the_index_lists_the_benchmark_gate() -> None:
    """The quality-gates table is the repository's own claim list."""
    text = INDEX.read_text(encoding="utf-8")
    assert "tools/benchmark_budget.py" in text


# --- the audits are not vacuous --------------------------------------------


def test_the_ceiling_audit_fires_on_a_missing_budget_entry() -> None:
    """Deleting a ceiling must be visible to the audit, not just to the gate."""
    budget = bb.load_budget(BUDGET_PATH)
    names = list(_benchmark_functions())
    target = next(name for name in names if name != bb.CALIBRATION_NAME)
    trimmed = bb.Budget(
        schema=budget.schema,
        calibration_us=budget.calibration_us,
        benchmarks={name: entry for name, entry in budget.benchmarks.items() if name != target},
        tolerance=budget.tolerance,
        headroom=budget.headroom,
        recorded=budget.recorded,
        python=budget.python,
    )
    assert _unbudgeted(names, trimmed) == [target]


def test_the_stale_audit_fires_on_an_invented_ceiling() -> None:
    """A ceiling nobody can measure is a finding, not a harmless leftover."""
    budget = bb.load_budget(BUDGET_PATH)
    padded = bb.Budget(
        schema=budget.schema,
        calibration_us=budget.calibration_us,
        benchmarks={
            **budget.benchmarks,
            "bench_deleted": budget.benchmarks[sorted(budget.benchmarks)[0]],
        },
        tolerance=budget.tolerance,
        headroom=budget.headroom,
        recorded=budget.recorded,
        python=budget.python,
    )
    assert _stale(list(_benchmark_functions()), padded) == ["bench_deleted"]


def test_the_workflow_audit_fires_on_a_doctored_workflow() -> None:
    """A workflow that stops calling the gate must fail the audit."""
    document = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    job_name = next(iter(document["jobs"]))
    document["jobs"][job_name]["steps"] = [
        step
        for step in document["jobs"][job_name]["steps"]
        if "benchmark_budget.py" not in str(step.get("run", ""))
    ]
    findings = _workflow_findings(document)
    assert any("tools/benchmark_budget.py" in finding for finding in findings)
