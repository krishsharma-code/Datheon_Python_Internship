"""Tests for the Day 21 QA report (``tools/qa_report.py``).

The report is only worth its verdict if two things hold: a layer that misses
its floor makes the report *fail*, and a layer that could not be measured makes
the report *incomplete* rather than green. Both halves are tested here against
synthetic evidence, so the suite proves the judgement without running a
six-minute campaign or depending on today's real numbers — a test asserting
"coverage is 95%" would have to be edited every time an honest test is added.

The collectors are tested separately, with ``subprocess`` stubbed out, so the
parsing of tool output is covered without paying for the tools.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import assertion_census, coverage_gate, mutation, qa_report  # noqa: E402


def evidence(**layers: dict) -> dict:
    """Build a full evidence mapping, one entry per layer.

    Args:
        **layers: Layer name to that layer's measurements. A layer left out is
            reported as unmeasured.

    Returns:
        An evidence mapping shaped like :func:`qa_report.collect`'s output.
    """
    defaults = {
        "static": {"flake8_violations": 0, "black_unformatted": 0},
        "assertions": {"tests": 830, "assertions": 1570},
        "isolation": {"violations": 0},
        "properties": {"tests": 31, "failures": 0},
        "fuzz": {"cases": 48, "failures": 0},
        "mutation": {"mutants": 182, "killed": 182, "score": 100.0},
        "coverage": {"statements": 3292, "percent": 95.76},
    }
    defaults.update(layers)
    return {"generated": "2026-11-09", "layers": defaults}


def gate(report: qa_report.QualityReport, layer: str) -> qa_report.GateResult:
    """Return one layer's result from a report.

    Args:
        report: The report to search.
        layer: The layer name.

    Returns:
        The matching gate result.
    """
    return next(result for result in report.gates if result.layer == layer)


def completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a stand-in for :class:`subprocess.CompletedProcess`.

    Args:
        stdout: Captured standard output.
        stderr: Captured standard error.
        returncode: The process exit code.

    Returns:
        An object with the three attributes the collectors read.
    """
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


# --------------------------------------------------------------------------- #
# The judgement itself
# --------------------------------------------------------------------------- #


def test_full_evidence_passes_with_one_gate_per_layer() -> None:
    """A complete, healthy evidence mapping yields a passing report."""
    report = qa_report.build_report(evidence())

    assert [result.layer for result in report.gates] == list(qa_report.LAYER_ORDER)
    assert len(report.gates) == 7
    assert report.verdict == "pass"
    assert report.passed is True
    assert report.failing == ()
    assert report.unmeasured == ()


def test_report_stamps_the_generation_date() -> None:
    """The date on the evidence becomes the date on the report."""
    report = qa_report.build_report(evidence())

    assert report.generated == "2026-11-09"


def test_a_measurement_exactly_on_its_floor_passes() -> None:
    """The floor is inclusive: 200 assertions and 90% coverage both clear."""
    report = qa_report.build_report(
        evidence(
            assertions={"tests": 100, "assertions": 200},
            coverage={"statements": 3000, "percent": 90.0},
            mutation={"mutants": 40, "killed": 38, "score": 95.0},
        )
    )

    assert gate(report, "assertions").verdict == "pass"
    assert gate(report, "coverage").verdict == "pass"
    assert gate(report, "mutation").verdict == "pass"
    assert report.verdict == "pass"


@pytest.mark.parametrize(
    ("layer", "payload", "detail_fragment"),
    [
        ("assertions", {"tests": 100, "assertions": 199}, "assertions"),
        ("coverage", {"statements": 3000, "percent": 89.99}, "percent"),
        ("mutation", {"mutants": 40, "killed": 37, "score": 92.5}, "score"),
        ("static", {"flake8_violations": 1, "black_unformatted": 0}, "flake8 1"),
        ("isolation", {"violations": 2}, "violations"),
        ("properties", {"tests": 31, "failures": 1}, "failures"),
        ("fuzz", {"cases": 48, "failures": 3}, "crashes"),
    ],
)
def test_a_breach_fails_that_layer_and_the_report(
    layer: str, payload: dict, detail_fragment: str
) -> None:
    """One layer under its floor fails the layer and the whole report."""
    report = qa_report.build_report(evidence(**{layer: payload}))

    result = gate(report, layer)
    assert result.verdict == "fail"
    assert result.passed is False
    assert report.verdict == "fail"
    assert report.passed is False
    assert result.metric in detail_fragment or layer in ("static", "fuzz")
    assert layer in [failed.layer for failed in report.failing]


@pytest.mark.parametrize("layer", list(qa_report.LAYER_ORDER))
def test_a_missing_layer_is_unmeasured_not_passing(layer: str) -> None:
    """Dropping a layer makes the report incomplete, never green."""
    payload = evidence()
    del payload["layers"][layer]

    report = qa_report.build_report(payload)

    result = gate(report, layer)
    assert result.verdict == "unmeasured"
    assert result.passed is None
    assert result.measured is None
    assert report.verdict == "incomplete"
    assert report.passed is False
    assert layer in [missing.layer for missing in report.unmeasured]


def test_an_empty_mutmut_cache_is_unmeasured_rather_than_perfect() -> None:
    """A run that never happened must not score 100%."""
    report = qa_report.build_report(evidence(mutation={"mutants": 0, "killed": 0, "score": 100.0}))

    result = gate(report, "mutation")
    assert result.verdict == "unmeasured"
    assert "tools/mutation.py" in result.detail


@pytest.mark.parametrize("value", [None, "0", True, [], {}])
def test_a_non_numeric_measurement_is_unmeasurable(value: object) -> None:
    """Booleans and strings are not measurements; they make the layer unknown."""
    report = qa_report.build_report(evidence(coverage={"percent": value}))

    result = gate(report, "coverage")
    assert result.verdict == "unmeasured"
    assert report.verdict == "incomplete"


def test_a_layer_with_only_half_its_fields_is_unmeasured() -> None:
    """flake8 without black is a partial run, so the static layer stays unknown."""
    report = qa_report.build_report(evidence(static={"flake8_violations": 0}))

    assert gate(report, "static").verdict == "unmeasured"


def test_evidence_without_a_layers_mapping_measures_nothing() -> None:
    """A malformed evidence file produces seven unmeasured layers, not a crash."""
    report = qa_report.build_report({"generated": "2026-11-09"})

    assert len(report.gates) == 7
    assert len(report.unmeasured) == 7
    assert report.verdict == "incomplete"


def test_failing_beats_incomplete_in_the_verdict() -> None:
    """A real breach outranks a missing measurement when both are present."""
    payload = evidence(coverage={"percent": 61.0})
    del payload["layers"]["fuzz"]

    report = qa_report.build_report(payload)

    assert report.verdict == "fail"
    assert [failed.layer for failed in report.failing] == ["coverage"]
    assert [missing.layer for missing in report.unmeasured] == ["fuzz"]


def test_floors_agree_with_the_modules_that_own_them() -> None:
    """The report cannot drift from the gates it reports on."""
    assert qa_report.ASSERTION_FLOOR == assertion_census.ASSERTION_BUDGET
    assert qa_report.MUTATION_FLOOR == mutation.DEFAULT_MINIMUM_SCORE
    assert qa_report.COVERAGE_FLOOR == coverage_gate.DEFAULT_MINIMUM


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_render_lists_every_layer_and_a_pass_line() -> None:
    """A passing report names each layer and says so in one closing line."""
    text = qa_report.render(qa_report.build_report(evidence()))

    for layer in qa_report.LAYER_ORDER:
        assert layer in text
    assert "95.76%" in text
    assert "PASS — every layer was measured" in text


def test_render_names_the_failing_layer_and_its_floor() -> None:
    """A failing report explains which layer broke and by how much."""
    text = qa_report.render(qa_report.build_report(evidence(coverage={"percent": 84.5})))

    assert "FAIL coverage" in text
    assert "FAIL — 1 layer(s) breached their floor." in text
    assert ">= 90%" in text


def test_render_marks_unmeasured_layers_and_refuses_to_call_them_passed() -> None:
    """The incomplete line states the rule the report exists to enforce."""
    payload = evidence()
    del payload["layers"]["mutation"]
    text = qa_report.render(qa_report.build_report(payload))

    assert "?    mutation" in text
    assert "INCOMPLETE — 1 layer(s) could not be measured" in text
    assert "is not a passing one" in text


def test_report_to_dict_is_json_serialisable_and_round_trips() -> None:
    """The JSON form carries the verdict and one row per layer."""
    payload = qa_report.report_to_dict(qa_report.build_report(evidence()))

    assert json.loads(json.dumps(payload)) == payload
    assert payload["verdict"] == "pass"
    assert payload["passed"] is True
    assert [row["layer"] for row in payload["layers"]] == list(qa_report.LAYER_ORDER)
    assert payload["layers"][0]["comparator"] == qa_report.AT_MOST


def test_report_to_dict_lists_failing_and_unmeasured_layers() -> None:
    """The two lists are what a CI log should be able to grep for."""
    data = evidence(fuzz={"cases": 48, "failures": 1})
    del data["layers"]["properties"]
    payload = qa_report.report_to_dict(qa_report.build_report(data))

    assert payload["verdict"] == "fail"
    assert payload["failing"] == ["fuzz"]
    assert payload["unmeasured"] == ["properties"]


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #


def test_collect_assertions_matches_the_census() -> None:
    """The live collector reports the same totals as the Day 15 tool."""
    collected = qa_report.collect_assertions(ROOT)
    tests, asserts = assertion_census.totals(assertion_census.census(ROOT / "tests", root=ROOT))

    assert collected == {"tests": tests, "assertions": asserts}
    assert collected["assertions"] > 0


def test_collect_isolation_finds_nothing_to_flag() -> None:
    """The committed suite is isolated; a regression here is a real finding."""
    assert qa_report.collect_isolation(ROOT) == {"violations": 0}


def test_collect_mutation_reports_an_absent_cache_as_nothing(
    tmp_path: Path,
) -> None:
    """No ``mutants/`` directory means no evidence, not a clean run."""
    assert qa_report.collect_mutation(tmp_path) == {}


def test_collect_coverage_reads_an_existing_report(tmp_path: Path) -> None:
    """A saved report is parsed without running the suite again."""
    (tmp_path / "coverage.json").write_text(
        json.dumps(
            {
                "totals": {
                    "num_statements": 100,
                    "missing_lines": 4,
                    "percent_covered": 96.0,
                },
                "files": {},
            }
        ),
        encoding="utf-8",
    )

    assert qa_report.collect_coverage(tmp_path, skip_slow=True) == {
        "statements": 100,
        "percent": 96.0,
    }


def test_collect_coverage_survives_a_missing_or_broken_report(tmp_path: Path) -> None:
    """Nothing to read is reported as nothing, never as a perfect score."""
    assert qa_report.collect_coverage(tmp_path, skip_slow=True) == {}

    (tmp_path / "coverage.json").write_text("{not json", encoding="utf-8")
    assert qa_report.collect_coverage(tmp_path, skip_slow=True) == {}


def test_collect_coverage_falls_back_to_the_saved_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the gate cannot run, an existing report is still usable."""
    (tmp_path / "coverage.json").write_text(
        json.dumps(
            {
                "totals": {
                    "num_statements": 50,
                    "missing_lines": 5,
                    "percent_covered": 90.0,
                },
                "files": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(qa_report, "_run", lambda *args, **kwargs: None)

    assert qa_report.collect_coverage(tmp_path, skip_slow=False)["percent"] == 90.0


def test_collect_static_counts_violations_and_reformats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One flake8 line per violation; one black line per unformatted file."""
    responses = {
        "flake8": completed(stdout="a.py:1:1: E501 line too long\nb.py:2:1: F401 unused\n"),
        "black": completed(stdout="would reformat /repo/c.py\n", returncode=1),
    }

    def fake_run(command, cwd=None, timeout=None):
        name = "black" if "black" in command else "flake8"
        return responses[name]

    monkeypatch.setattr(qa_report, "_run", fake_run)

    assert qa_report.collect_static() == {"flake8_violations": 2, "black_unformatted": 1}


def test_collect_static_reports_nothing_when_a_tool_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing linter makes the layer unmeasurable rather than clean."""
    monkeypatch.setattr(qa_report, "_run", lambda *args, **kwargs: None)

    assert qa_report.collect_static() == {}


def test_collect_static_ignores_a_module_that_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``No module named`` stderr is a missing tool, not a clean run."""
    monkeypatch.setattr(
        qa_report,
        "_run",
        lambda *args, **kwargs: completed(stderr="No module named flake8", returncode=1),
    )

    assert qa_report.collect_static() == {}


def test_collect_fuzz_parses_the_json_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    """The driver's JSON is the measurement, not its exit code."""
    payload = json.dumps({"seed": 18, "cases": 48, "failures": 2})
    monkeypatch.setattr(qa_report, "_run", lambda *args, **kwargs: completed(payload))

    assert qa_report.collect_fuzz() == {"cases": 48, "failures": 2}


def test_collect_fuzz_reports_nothing_when_the_driver_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreadable output is unmeasurable, not zero crashes."""
    monkeypatch.setattr(qa_report, "_run", lambda *args, **kwargs: None)
    assert qa_report.collect_fuzz() == {}

    monkeypatch.setattr(qa_report, "_run", lambda *args, **kwargs: completed("<html>"))
    assert qa_report.collect_fuzz() == {}

    monkeypatch.setattr(qa_report, "_run", lambda *args, **kwargs: completed("[]"))
    assert qa_report.collect_fuzz() == {}


@pytest.mark.parametrize(("returncode", "failures"), [(0, 0), (1, 1)])
def test_collect_properties_turns_the_exit_code_into_a_failure_count(
    monkeypatch: pytest.MonkeyPatch, returncode: int, failures: int
) -> None:
    """A red property run is one failure; a green one is none."""
    monkeypatch.setattr(
        qa_report,
        "_run",
        lambda *args, **kwargs: completed(stdout="31 passed in 2.4s", returncode=returncode),
    )

    collected = qa_report.collect_properties(ROOT)

    assert collected["failures"] == failures
    assert collected["tests"] > 0


def test_collect_properties_reports_nothing_when_pytest_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pytest, no property evidence."""
    monkeypatch.setattr(qa_report, "_run", lambda *args, **kwargs: None)

    assert qa_report.collect_properties() == {}


def test_collect_skip_slow_leaves_the_expensive_layers_unmeasured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--skip-slow`` trades completeness for speed, and says so."""
    monkeypatch.setattr(qa_report, "collect_static", lambda root=ROOT: {})
    monkeypatch.setattr(qa_report, "collect_mutation", lambda root=ROOT: {})
    monkeypatch.setattr(qa_report, "collect_coverage", lambda root=ROOT, *a, **k: {"percent": 95.0})

    payload = qa_report.collect(ROOT, skip_slow=True)
    report = qa_report.build_report(payload)

    assert payload["layers"]["properties"] == {}
    assert payload["layers"]["fuzz"] == {}
    assert gate(report, "properties").verdict == "unmeasured"
    assert gate(report, "coverage").verdict == "pass"


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def write_evidence(tmp_path: Path, payload: dict) -> Path:
    """Write an evidence mapping to a temporary file.

    Args:
        tmp_path: Directory to write into.
        payload: The evidence mapping.

    Returns:
        The path written.
    """
    path = tmp_path / "qa_evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_main_returns_zero_for_a_passing_report(tmp_path: Path, capsys) -> None:
    """A green dashboard is exit 0."""
    path = write_evidence(tmp_path, evidence())

    assert qa_report.main(["--from-json", str(path)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_main_returns_one_when_a_layer_breaches(tmp_path: Path, capsys) -> None:
    """A breach is exit 1, the same code the owning gate would use."""
    path = write_evidence(tmp_path, evidence(assertions={"tests": 10, "assertions": 42}))

    assert qa_report.main(["--from-json", str(path)]) == 1
    assert "FAIL assertions" in capsys.readouterr().out


def test_main_returns_two_when_a_layer_is_unmeasured(tmp_path: Path) -> None:
    """An incomplete report refuses to claim success."""
    payload = evidence()
    del payload["layers"]["mutation"]
    path = write_evidence(tmp_path, payload)

    assert qa_report.main(["--from-json", str(path)]) == qa_report.EXIT_UNMEASURABLE


def test_main_can_be_told_to_accept_an_incomplete_report(tmp_path: Path) -> None:
    """``--allow-unmeasured`` judges the layers that were measured."""
    payload = evidence()
    del payload["layers"]["mutation"]
    path = write_evidence(tmp_path, payload)

    assert qa_report.main(["--from-json", str(path), "--allow-unmeasured"]) == 0


def test_main_reports_an_unreadable_evidence_file(tmp_path: Path, capsys) -> None:
    """A file that is not JSON is exit 2 with a message, not a traceback."""
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    assert qa_report.main(["--from-json", str(path)]) == qa_report.EXIT_UNMEASURABLE
    assert "cannot read evidence" in capsys.readouterr().err


def test_main_emits_json_and_saves_the_evidence(tmp_path: Path, capsys) -> None:
    """``--json`` and ``--write-json`` are both honoured on a saved run."""
    path = write_evidence(tmp_path, evidence())
    out = tmp_path / "report.json"

    assert qa_report.main(["--from-json", str(path), "--json", "--write-json", str(out)]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["verdict"] == "pass"
    assert json.loads(out.read_text(encoding="utf-8"))["layers"]["coverage"]["percent"] == 95.76


def test_run_returns_none_when_the_command_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing executable is a missing measurement, not an exception."""

    def explode(*args, **kwargs):
        raise FileNotFoundError("no such tool")

    monkeypatch.setattr(subprocess, "run", explode)

    assert qa_report._run(["definitely-not-a-real-command"]) is None


def test_run_returns_none_when_the_command_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout is handled like a missing tool: no evidence."""

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="slow", timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)

    assert qa_report._run(["slow-command"], timeout=1) is None
