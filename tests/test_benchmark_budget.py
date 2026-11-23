"""Tests for the Day 30 benchmark regression gate (``tools/benchmark_budget.py``).

Two things have to be proven separately, as for every gate in this repository:
that each verdict *fires* when the defect is present, and that the committed
budget currently grades clean. The first half runs against synthetic documents
written to ``tmp_path`` — small enough to read, and complete enough that every
branch of the comparison is exercised without timing anything, because a test
that measures the machine is a test that fails when the machine is busy. The
second half runs the loader against the real ``benchmarks/budget.json``; the
declaration audit that pairs it with the harness lives in
``tests/test_benchmarks.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import benchmark_budget as bb  # noqa: E402  (path set up above)


def _write_results(path: Path, medians: Dict[str, float]) -> Path:
    """Write a pytest-benchmark report whose medians are in microseconds.

    Args:
        path: Destination file.
        medians: Canonical benchmark name to median in microseconds.

    Returns:
        The path written.
    """
    document = {
        "machine_info": {"node": "test"},
        "benchmarks": [
            {
                "name": f"test_{name}",
                "fullname": f"benchmarks/bench_core.py::test_{name}",
                "stats": {
                    "min": median / 1_000_000.0,
                    "max": median / 1_000_000.0,
                    "mean": median / 1_000_000.0,
                    "median": median / 1_000_000.0,
                    "stddev": 0.0,
                    "rounds": 7,
                    "iterations": 1,
                },
            }
            for name, median in medians.items()
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _budget_document(
    medians: Dict[str, float],
    *,
    calibration_us: float = 1000.0,
    headroom: float = 2.0,
    tolerance: float = 0.0,
) -> Dict[str, object]:
    """Build a budget document that mirrors what ``--update`` writes.

    Args:
        medians: Canonical benchmark name to recorded median in microseconds.
        calibration_us: The calibration median to store.
        headroom: Multiplier between each median and its ceiling.
        tolerance: Extra slack stored alongside the ceilings.

    Returns:
        The budget document, with the calibration stored once as the scale rather
        than as a benchmark of its own — which is what ``--update`` writes.
    """
    return {
        "schema": bb.BUDGET_SCHEMA,
        "recorded": "2026-11-23",
        "python": "3.12.0",
        "headroom": headroom,
        "tolerance": tolerance,
        "calibration_us": calibration_us,
        "benchmarks": {
            name: {
                "median_us": median,
                "max_median_us": median * headroom,
            }
            for name, median in medians.items()
            if name != bb.CALIBRATION_NAME
        },
    }


def _write_budget(path: Path, document: Dict[str, object]) -> Path:
    """Write a budget document.

    Args:
        path: Destination file.
        document: The document to write.

    Returns:
        The path written.
    """
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture()
def pair(tmp_path: Path):
    """Return a matching ``(results, budget)`` pair on a 1x machine.

    Yields:
        A callable ``(medians, **budget_kwargs)`` returning ``(results, budget)``
        paths for the given recorded medians.
    """

    def build(medians: Dict[str, float], **budget_kwargs) -> tuple:
        results = _write_results(tmp_path / "results.json", medians)
        budget = _write_budget(tmp_path / "budget.json", _budget_document(medians, **budget_kwargs))
        return results, budget

    return build


# --- names -----------------------------------------------------------------


def test_canonical_name_strips_the_pytest_prefix() -> None:
    """The budget key names the benchmark, not the test function."""
    assert bb.canonical_name("test_bench_word_list_cold") == "bench_word_list_cold"


def test_canonical_name_leaves_an_unprefixed_name_alone() -> None:
    """A report written by another harness still keys the same way."""
    assert bb.canonical_name("bench_cache_lookup") == "bench_cache_lookup"


@pytest.mark.parametrize("raw", ["", "   ", None, 7])
def test_canonical_name_rejects_an_unusable_name(raw: object) -> None:
    """A name is what ties a measurement to a ceiling, so it must exist."""
    with pytest.raises(bb.BudgetError):
        bb.canonical_name(raw)


def test_canonical_name_keeps_a_bare_prefix() -> None:
    """``test_`` alone is a name, not a prefix with nothing after it."""
    assert bb.canonical_name("test_") == "test_"


# --- reading a report ------------------------------------------------------


def test_load_results_converts_seconds_to_microseconds(tmp_path: Path) -> None:
    """pytest-benchmark reports seconds; a human budget reads microseconds."""
    path = _write_results(tmp_path / "results.json", {"bench_calibration": 2500.0})
    samples = bb.load_results(path)
    assert samples["bench_calibration"].median_us == pytest.approx(2500.0)
    assert samples["bench_calibration"].rounds == 7


def test_load_results_missing_file_is_unmeasurable(tmp_path: Path) -> None:
    """A gate with no report must say so, not report success."""
    with pytest.raises(bb.BudgetError, match="not found"):
        bb.load_results(tmp_path / "absent.json")


def test_load_results_rejects_invalid_json(tmp_path: Path) -> None:
    """A truncated upload is not an empty run."""
    path = tmp_path / "results.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(bb.BudgetError, match="not valid JSON"):
        bb.load_results(path)


def test_load_results_rejects_a_document_without_benchmarks(tmp_path: Path) -> None:
    """A pytest report and a benchmark report are different documents."""
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"duration": 1.0}), encoding="utf-8")
    with pytest.raises(bb.BudgetError, match="no 'benchmarks' list"):
        bb.load_results(path)


def test_load_results_rejects_an_empty_run(tmp_path: Path) -> None:
    """Zero benchmarks is not a fast run; it is no run at all."""
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"benchmarks": []}), encoding="utf-8")
    with pytest.raises(bb.BudgetError, match="contains no benchmarks"):
        bb.load_results(path)


def test_load_results_rejects_a_missing_stats_block(tmp_path: Path) -> None:
    """Without stats there is no median to compare."""
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"benchmarks": [{"name": "test_bench_x"}]}), encoding="utf-8")
    with pytest.raises(bb.BudgetError, match="no 'stats'"):
        bb.load_results(path)


def test_load_results_rejects_a_non_positive_median(tmp_path: Path) -> None:
    """A zero median would make every ratio infinite."""
    document = {"benchmarks": [{"name": "test_bench_x", "stats": {"median": 0}}]}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(bb.BudgetError, match="must be positive"):
        bb.load_results(path)


def test_load_results_rejects_a_duplicated_benchmark(tmp_path: Path) -> None:
    """A repeated name would silently shadow the first measurement."""
    document = {
        "benchmarks": [
            {"name": "test_bench_x", "stats": {"median": 0.001}},
            {"name": "test_bench_x", "stats": {"median": 0.002}},
        ]
    }
    path = tmp_path / "results.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(bb.BudgetError, match="lists bench_x twice"):
        bb.load_results(path)


# --- reading a budget ------------------------------------------------------


def test_load_budget_reads_the_committed_document() -> None:
    """The budget in the repository parses, and carries a calibration."""
    budget = bb.load_budget(bb.DEFAULT_BUDGET)
    assert budget.schema == bb.BUDGET_SCHEMA
    assert budget.calibration_us > 0
    assert budget.benchmarks


def test_load_budget_rejects_a_wrong_schema(tmp_path: Path) -> None:
    """A document this gate cannot read is a finding, not a default."""
    document = _budget_document({"bench_x": 10.0})
    document["schema"] = 99
    path = _write_budget(tmp_path / "budget.json", document)
    with pytest.raises(bb.BudgetError, match="schema 99"):
        bb.load_budget(path)


def test_load_budget_rejects_a_negative_tolerance(tmp_path: Path) -> None:
    """A negative allowance would tighten the ceiling behind the reader's back."""
    path = _write_budget(
        tmp_path / "budget.json", _budget_document({"bench_x": 10.0}, tolerance=-0.1)
    )
    with pytest.raises(bb.BudgetError, match="tolerance"):
        bb.load_budget(path)


def test_load_budget_rejects_a_headroom_below_one(tmp_path: Path) -> None:
    """A headroom under 1 writes a ceiling below the median it recorded."""
    path = _write_budget(
        tmp_path / "budget.json", _budget_document({"bench_x": 10.0}, headroom=0.5)
    )
    with pytest.raises(bb.BudgetError, match="headroom"):
        bb.load_budget(path)


def test_load_budget_rejects_a_ceiling_under_its_median(tmp_path: Path) -> None:
    """The recorded run itself must pass; otherwise the gate fails on day one."""
    document = _budget_document({"bench_x": 10.0})
    document["benchmarks"]["bench_x"]["max_median_us"] = 5.0  # type: ignore[index]
    path = _write_budget(tmp_path / "budget.json", document)
    with pytest.raises(bb.BudgetError, match="below median_us"):
        bb.load_budget(path)


def test_load_budget_rejects_an_empty_benchmark_map(tmp_path: Path) -> None:
    """A budget that budgets nothing cannot fail anything."""
    document = _budget_document({"bench_x": 10.0})
    document["benchmarks"] = {}
    path = _write_budget(tmp_path / "budget.json", document)
    with pytest.raises(bb.BudgetError, match="no 'benchmarks' object"):
        bb.load_budget(path)


def test_load_budget_rejects_a_missing_calibration(tmp_path: Path) -> None:
    """Without a calibration nothing can be normalised to this machine."""
    document = _budget_document({"bench_x": 10.0})
    del document["calibration_us"]
    path = _write_budget(tmp_path / "budget.json", document)
    with pytest.raises(bb.BudgetError, match="calibration_us"):
        bb.load_budget(path)


# --- machine normalisation -------------------------------------------------


def test_machine_factor_is_one_on_the_reference_machine(pair) -> None:
    """A run that matches the budget scales nothing."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    factor, notes = bb.machine_factor(samples, bb.load_budget(budget_path))
    assert factor == pytest.approx(1.0)
    assert notes == []


def test_machine_factor_scales_a_slower_runner(pair) -> None:
    """A runner twice as slow gets twice the budget, not a false regression."""
    results, budget_path = pair({"bench_calibration": 2000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    factor, notes = bb.machine_factor(samples, bb.load_budget(budget_path))
    assert factor == pytest.approx(2.0)
    assert notes == []


def test_machine_factor_clamps_and_says_so(pair) -> None:
    """Beyond the accepted band the verdict is approximate, and admits it."""
    results, budget_path = pair({"bench_calibration": 100_000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    factor, notes = bb.machine_factor(samples, bb.load_budget(budget_path))
    assert factor == pytest.approx(bb.FACTOR_MAX)
    assert len(notes) == 1
    assert "clamped" in notes[0]


def test_machine_factor_requires_a_calibration_sample(pair) -> None:
    """No calibration means no comparison worth trusting."""
    results, budget_path = pair({"bench_x": 10.0})
    samples = bb.load_results(results)
    with pytest.raises(bb.BudgetError, match="no bench_calibration sample"):
        bb.machine_factor(samples, bb.load_budget(budget_path))


# --- the comparison --------------------------------------------------------


def test_a_median_at_the_ceiling_passes(pair) -> None:
    """The ceiling is inclusive: a benchmark may sit exactly on it."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    budget = bb.load_budget(budget_path)
    verdicts = bb.compare(budget, samples)
    assert [verdict.status for verdict in verdicts] == ["pass"]
    assert verdicts[0].ratio == pytest.approx(0.5)


def test_a_median_over_the_ceiling_is_a_regression(pair) -> None:
    """Doubling the cost of a benchmark fails the gate."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    samples["bench_x"] = bb.Sample("bench_x", 21.0, 5, 1)
    verdicts = bb.compare(bb.load_budget(budget_path), samples)
    assert [verdict.status for verdict in verdicts] == ["regression"]
    assert verdicts[0].ok is False
    assert "over" in verdicts[0].detail


def test_a_slow_machine_is_not_a_regression(pair) -> None:
    """The same code on a 3x slower runner measures 3x slower and still passes."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    samples = {
        "bench_calibration": bb.Sample("bench_calibration", 3000.0, 5, 1),
        "bench_x": bb.Sample("bench_x", 25.0, 5, 1),
    }
    verdicts = bb.compare(bb.load_budget(budget_path), samples)
    assert [verdict.status for verdict in verdicts] == ["pass"]


def test_a_budgeted_benchmark_that_did_not_run_is_missing(pair) -> None:
    """A stale ceiling is a gate measuring less than it claims."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    budget = bb.load_budget(budget_path)
    budget = bb.Budget(
        schema=budget.schema,
        calibration_us=budget.calibration_us,
        benchmarks={**budget.benchmarks, "bench_gone": budget.benchmarks["bench_x"]},
        tolerance=budget.tolerance,
        headroom=budget.headroom,
        recorded=budget.recorded,
        python=budget.python,
    )
    verdicts = bb.compare(budget, samples)
    assert [verdict.status for verdict in verdicts] == ["missing", "pass"]
    assert verdicts[0].measured_us is None
    assert "not measured in this run" in verdicts[0].detail


def test_a_benchmark_without_a_ceiling_is_unbudgeted(pair) -> None:
    """A new benchmark must arrive with a ceiling, not slip in unmeasured."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    samples["bench_new"] = bb.Sample("bench_new", 5.0, 5, 1)
    verdicts = bb.compare(bb.load_budget(budget_path), samples)
    statuses = {verdict.name: verdict.status for verdict in verdicts}
    assert statuses == {"bench_x": "pass", "bench_new": "unbudgeted"}


def test_the_calibration_is_never_reported_as_unbudgeted(pair) -> None:
    """The calibration is the scale, not a benchmark being judged."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    verdicts = bb.compare(bb.load_budget(budget_path), bb.load_results(results))
    assert [verdict.name for verdict in verdicts] == ["bench_x"]


def test_tolerance_widens_the_ceiling(pair) -> None:
    """An explicit allowance is applied on top of the scaled ceiling."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    samples["bench_x"] = bb.Sample("bench_x", 25.0, 5, 1)
    budget = bb.load_budget(budget_path)
    assert bb.compare(budget, samples)[0].status == "regression"
    assert bb.compare(budget, samples, tolerance=0.5)[0].status == "pass"


def test_a_negative_tolerance_is_rejected(pair) -> None:
    """A caller cannot tighten a gate by accident."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    with pytest.raises(bb.BudgetError, match="tolerance"):
        bb.compare(bb.load_budget(budget_path), bb.load_results(results), tolerance=-1.0)


# --- recording a budget ----------------------------------------------------


def test_build_budget_records_a_ceiling_with_headroom() -> None:
    """Each ceiling is the recorded median times the headroom."""
    samples = {
        "bench_calibration": bb.Sample("bench_calibration", 1000.0, 5, 1),
        "bench_x": bb.Sample("bench_x", 40.0, 5, 1),
    }
    document = bb.build_budget(samples, headroom=2.0, recorded="2026-11-23", python="3.12.0")
    assert document["calibration_us"] == pytest.approx(1000.0)
    assert document["benchmarks"]["bench_x"]["median_us"] == pytest.approx(40.0)
    assert document["benchmarks"]["bench_x"]["max_median_us"] == pytest.approx(80.0)


def test_build_budget_excludes_the_calibration_from_the_map() -> None:
    """The calibration is stored once, as the scale, not as a benchmark."""
    samples = {
        "bench_calibration": bb.Sample("bench_calibration", 1000.0, 5, 1),
        "bench_x": bb.Sample("bench_x", 40.0, 5, 1),
    }
    document = bb.build_budget(samples, recorded="2026-11-23")
    assert "bench_calibration" not in document["benchmarks"]


def test_build_budget_defaults_to_today() -> None:
    """An un-stamped budget would claim no date at all."""
    samples = {
        "bench_calibration": bb.Sample("bench_calibration", 1000.0, 5, 1),
        "bench_x": bb.Sample("bench_x", 40.0, 5, 1),
    }
    document = bb.build_budget(samples)
    assert document["recorded"]


def test_build_budget_requires_a_calibration() -> None:
    """A budget with nothing to normalise it would only be true on one machine."""
    samples = {"bench_x": bb.Sample("bench_x", 40.0, 5, 1)}
    with pytest.raises(bb.BudgetError, match="without a bench_calibration"):
        bb.build_budget(samples)


def test_build_budget_requires_a_benchmark() -> None:
    """A calibration alone is a budget that protects nothing."""
    samples = {"bench_calibration": bb.Sample("bench_calibration", 1000.0, 5, 1)}
    with pytest.raises(bb.BudgetError, match="no benchmarks"):
        bb.build_budget(samples)


def test_build_budget_rejects_a_headroom_below_one() -> None:
    """A headroom under 1 would record a ceiling below the median."""
    samples = {
        "bench_calibration": bb.Sample("bench_calibration", 1000.0, 5, 1),
        "bench_x": bb.Sample("bench_x", 40.0, 5, 1),
    }
    with pytest.raises(bb.BudgetError, match="headroom"):
        bb.build_budget(samples, headroom=0.9)


def test_write_budget_round_trips_through_the_loader(tmp_path: Path) -> None:
    """What ``--update`` writes is what the gate reads back."""
    samples = {
        "bench_calibration": bb.Sample("bench_calibration", 1000.0, 5, 1),
        "bench_x": bb.Sample("bench_x", 40.0, 5, 1),
    }
    document = bb.build_budget(samples, recorded="2026-11-23")
    target = bb.write_budget(document, tmp_path / "nested" / "budget.json")
    assert target.read_text(encoding="utf-8").endswith("\n")
    budget = bb.load_budget(target)
    assert budget.recorded == "2026-11-23"
    assert budget.ceiling_us("bench_x", 1.0) == pytest.approx(80.0)


# --- the report ------------------------------------------------------------


def test_render_table_names_every_benchmark(pair) -> None:
    """A table a reader cannot scan is a table nobody reads."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    budget = bb.load_budget(budget_path)
    table = bb.render_table(budget, bb.compare(budget, bb.load_results(results)), 1.0)
    assert "bench_x" in table
    assert "all 1 benchmarks within budget" in table
    assert "1.00x" in table


def test_render_table_lists_the_findings(pair) -> None:
    """A failure says which benchmark and by how much."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    samples = bb.load_results(results)
    samples["bench_x"] = bb.Sample("bench_x", 100.0, 5, 1)
    budget = bb.load_budget(budget_path)
    table = bb.render_table(budget, bb.compare(budget, samples), 1.0)
    assert "1 finding(s)" in table
    assert "regression" in table


# --- the command line ------------------------------------------------------


def test_cli_list_prints_the_budget(capsys: pytest.CaptureFixture) -> None:
    """``--list`` answers "what am I held to" without a benchmark run."""
    assert bb.main(["--list", "--budget", str(bb.DEFAULT_BUDGET)]) == bb.EXIT_OK
    out = capsys.readouterr().out
    assert "bench_word_list_cold" in out
    assert "calibration" in out


def test_cli_passes_a_run_within_budget(pair, capsys: pytest.CaptureFixture) -> None:
    """The happy path exits 0 and prints the table."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    code = bb.main(["--results", str(results), "--budget", str(budget_path)])
    assert code == bb.EXIT_OK
    assert "within budget" in capsys.readouterr().out


def test_cli_fails_a_regression(pair, capsys: pytest.CaptureFixture) -> None:
    """A regression exits 1 so CI fails on it."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    _write_results(results, {"bench_calibration": 1000.0, "bench_x": 500.0})
    code = bb.main(["--results", str(results), "--budget", str(budget_path)])
    assert code == bb.EXIT_REGRESSION
    assert "regression" in capsys.readouterr().out


def test_cli_json_payload_is_machine_readable(pair, capsys: pytest.CaptureFixture) -> None:
    """The JSON mode carries the verdicts, the factor and the notes."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    code = bb.main(["--results", str(results), "--budget", str(budget_path), "--json"])
    assert code == bb.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["machine_factor"] == pytest.approx(1.0)
    assert payload["verdicts"][0]["name"] == "bench_x"


def test_cli_update_writes_a_budget(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """``--update`` is how a budget is cut, and it records the calibration."""
    results = _write_results(
        tmp_path / "results.json", {"bench_calibration": 1000.0, "bench_x": 40.0}
    )
    target = tmp_path / "budget.json"
    code = bb.main(
        ["--results", str(results), "--budget", str(target), "--update", "--recorded", "2026-11-23"]
    )
    assert code == bb.EXIT_OK
    budget = bb.load_budget(target)
    assert budget.recorded == "2026-11-23"
    assert budget.benchmarks["bench_x"].max_median_us == pytest.approx(80.0)
    assert "recorded 1 benchmarks" in capsys.readouterr().out


def test_cli_missing_report_is_unmeasurable(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """A gate that cannot measure never reports success."""
    code = bb.main(["--results", str(tmp_path / "absent.json")])
    assert code == bb.EXIT_UNMEASURABLE
    assert "could not reach a verdict" in capsys.readouterr().err


def test_cli_unreadable_budget_is_unmeasurable(pair, tmp_path: Path, capsys) -> None:
    """A budget this gate cannot read is exit 2, not a silent default."""
    results, _ = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    broken = tmp_path / "broken.json"
    broken.write_text("[]", encoding="utf-8")
    assert bb.main(["--results", str(results), "--budget", str(broken)]) == bb.EXIT_UNMEASURABLE
    assert "not a JSON object" in capsys.readouterr().err


def test_cli_notes_go_to_stderr(pair, capsys: pytest.CaptureFixture) -> None:
    """A clamped factor is a warning, so it must not pollute the table on stdout."""
    results, budget_path = pair({"bench_calibration": 1000.0, "bench_x": 10.0})
    _write_results(results, {"bench_calibration": 100_000.0, "bench_x": 10.0})
    bb.main(["--results", str(results), "--budget", str(budget_path)])
    captured = capsys.readouterr()
    assert "clamped" in captured.err
    assert "clamped" not in captured.out
