"""Tests for the Day 20 coverage gate (``tools/coverage_gate.py``).

A quality gate is only worth its exit code if two things are true: it *fails*
when the number drops, and it *refuses to pass* when it cannot measure. Both
halves are tested here against synthetic reports, so the suite proves the gate
without spawning a second pytest and without depending on today's real
coverage — a test that asserts "coverage is 94%" would have to be edited every
time an honest test is added.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import coverage_gate  # noqa: E402  (path set up above)


def report(
    percent: float,
    statements: int = 1000,
    files: dict | None = None,
) -> dict:
    """Build a coverage JSON payload shaped like ``pytest-cov``'s.

    Args:
        percent: Overall covered percentage.
        statements: Total executable statements.
        files: Optional ``path -> (statements, missing, percent)`` mapping.

    Returns:
        A decoded-report-shaped mapping.
    """
    covered = int(round(statements * percent / 100.0))
    payload: dict = {
        "meta": {"version": "7.0"},
        "totals": {
            "num_statements": statements,
            "covered_lines": covered,
            "missing_lines": statements - covered,
            "percent_covered": percent,
        },
        "files": {},
    }
    for path, (file_statements, file_missing, file_percent) in (files or {}).items():
        payload["files"][path] = {
            "summary": {
                "num_statements": file_statements,
                "covered_lines": file_statements - file_missing,
                "missing_lines": file_missing,
                "percent_covered": file_percent,
            }
        }
    return payload


# --- percentage arithmetic ---------------------------------------------
def test_percent_computes_the_covered_share():
    assert coverage_gate.percent(94, 100) == pytest.approx(94.0)


def test_percent_treats_an_empty_module_as_fully_covered():
    assert coverage_gate.percent(0, 0) == 100.0


def test_percent_handles_a_fully_covered_module():
    assert coverage_gate.percent(12, 12) == 100.0


# --- parsing -----------------------------------------------------------
def test_parse_reads_the_totals():
    summary = coverage_gate.parse_coverage_json(report(93.5, statements=200))
    assert summary.statements == 200
    assert summary.missing == 13
    assert summary.covered == 187
    assert summary.percent == pytest.approx(93.5)


def test_parse_sorts_files_worst_first():
    payload = report(
        90.0,
        files={
            "projects/a.py": (10, 0, 100.0),
            "projects/b.py": (10, 5, 50.0),
            "projects/c.py": (10, 2, 80.0),
        },
    )
    summary = coverage_gate.parse_coverage_json(payload)
    assert [row.path for row in summary.files] == [
        "projects/b.py",
        "projects/c.py",
        "projects/a.py",
    ]


def test_parse_normalises_windows_separators():
    payload = report(95.0, files={"projects\\win.py": (4, 0, 100.0)})
    assert coverage_gate.parse_coverage_json(payload).files[0].path == "projects/win.py"


def test_parse_skips_malformed_file_entries():
    payload = report(95.0)
    payload["files"] = {
        "ok.py": {"summary": {"num_statements": 2, "missing_lines": 0, "percent_covered": 100}},
        "broken.py": "not a mapping",
        "no-summary.py": {"executed_lines": [1, 2]},
    }
    summary = coverage_gate.parse_coverage_json(payload)
    assert [row.path for row in summary.files] == ["ok.py"]


def test_parse_rejects_a_payload_without_totals():
    with pytest.raises(ValueError, match="no 'totals' mapping"):
        coverage_gate.parse_coverage_json({"files": {}})


def test_parse_rejects_totals_without_numbers():
    with pytest.raises(ValueError, match="num_statements"):
        coverage_gate.parse_coverage_json({"totals": {"missing_lines": 0}})


def test_parse_rejects_a_boolean_where_a_number_belongs():
    payload = {"totals": {"num_statements": True, "missing_lines": 0, "percent_covered": 90}}
    with pytest.raises(ValueError, match="num_statements"):
        coverage_gate.parse_coverage_json(payload)


# --- loading -----------------------------------------------------------
def test_load_reads_a_report_from_disk(tmp_path: Path):
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report(91.25, statements=400)), encoding="utf-8")
    assert coverage_gate.load_coverage_json(path).percent == pytest.approx(91.25)


def test_load_reports_a_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        coverage_gate.load_coverage_json(tmp_path / "absent.json")


def test_load_rejects_a_json_list(tmp_path: Path):
    path = tmp_path / "coverage.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="does not contain a coverage report"):
        coverage_gate.load_coverage_json(path)


# --- the verdict -------------------------------------------------------
def test_evaluate_passes_at_the_floor():
    verdict = coverage_gate.evaluate(coverage_gate.parse_coverage_json(report(90.0)), minimum=90.0)
    assert verdict.passed is True
    assert verdict.failures == ()


def test_evaluate_passes_above_the_floor():
    verdict = coverage_gate.evaluate(coverage_gate.parse_coverage_json(report(97.4)), minimum=90.0)
    assert verdict.passed is True


def test_evaluate_fails_below_the_floor():
    verdict = coverage_gate.evaluate(coverage_gate.parse_coverage_json(report(89.9)), minimum=90.0)
    assert verdict.passed is False
    assert [failure.kind for failure in verdict.failures] == ["total"]
    assert verdict.failures[0].minimum == 90.0
    assert verdict.failures[0].actual == pytest.approx(89.9)


def test_the_failure_message_names_the_numbers():
    summary = coverage_gate.parse_coverage_json(report(61.0, statements=1000))
    message = coverage_gate.evaluate(summary, minimum=90.0).failures[0].message
    assert "61.00%" in message
    assert "90%" in message
    assert "390 of 1000 statements unexecuted" in message


def test_default_floor_is_ninety():
    assert coverage_gate.DEFAULT_MINIMUM == 90.0


def test_per_file_floor_flags_every_laggard():
    payload = report(
        95.0,
        files={
            "projects/good.py": (50, 0, 100.0),
            "projects/main_a.py": (40, 12, 70.0),
            "projects/main_b.py": (20, 6, 70.0),
        },
    )
    verdict = coverage_gate.evaluate(
        coverage_gate.parse_coverage_json(payload), minimum=90.0, per_file_minimum=80.0
    )
    assert verdict.passed is False
    assert [failure.kind for failure in verdict.failures] == ["file", "file"]
    assert [failure.path for failure in verdict.failures] == [
        "projects/main_a.py",
        "projects/main_b.py",
    ]


def test_per_file_floor_exempts_a_module_with_nothing_to_execute():
    payload = report(95.0, files={"projects/empty.py": (0, 0, 100.0)})
    verdict = coverage_gate.evaluate(
        coverage_gate.parse_coverage_json(payload), minimum=90.0, per_file_minimum=99.0
    )
    assert verdict.passed is True


def test_total_breach_is_reported_before_file_breaches():
    payload = report(50.0, files={"projects/bad.py": (10, 10, 0.0)})
    verdict = coverage_gate.evaluate(
        coverage_gate.parse_coverage_json(payload), minimum=90.0, per_file_minimum=80.0
    )
    assert [failure.kind for failure in verdict.failures] == ["total", "file"]


# --- rendering ---------------------------------------------------------
def test_render_passes_and_names_the_floor():
    summary = coverage_gate.parse_coverage_json(report(96.0))
    text = coverage_gate.render_summary(summary, coverage_gate.evaluate(summary), minimum=90.0)
    assert "PASS" in text
    assert "96.00%" in text
    assert "floor    : 90%" in text


def test_render_fails_and_lists_the_breach():
    summary = coverage_gate.parse_coverage_json(report(42.0))
    text = coverage_gate.render_summary(summary, coverage_gate.evaluate(summary), minimum=90.0)
    assert "FAIL" in text
    assert "below the 90% floor" in text


def test_render_lists_only_files_under_the_floor():
    payload = report(
        92.0,
        files={
            "projects/weak.py": (10, 5, 50.0),
            "projects/strong.py": (10, 0, 100.0),
        },
    )
    summary = coverage_gate.parse_coverage_json(payload)
    text = coverage_gate.render_summary(summary, coverage_gate.evaluate(summary))
    assert "projects/weak.py" in text
    assert "projects/strong.py" not in text


def test_render_can_hide_the_file_table():
    summary = coverage_gate.parse_coverage_json(
        report(92.0, files={"projects/weak.py": (10, 5, 50.0)})
    )
    text = coverage_gate.render_summary(
        summary, coverage_gate.evaluate(summary), show_missing=False
    )
    assert "projects/weak.py" not in text


def test_json_payload_carries_the_verdict():
    payload = report(88.0, statements=500, files={"projects/x.py": (10, 1, 90.0)})
    summary = coverage_gate.parse_coverage_json(payload)
    verdict = coverage_gate.evaluate(summary, minimum=90.0)
    body = coverage_gate.summary_to_dict(summary, verdict, minimum=90.0)
    assert body["passed"] is False
    assert body["percent"] == pytest.approx(88.0)
    assert body["minimum"] == 90.0
    assert body["failures"][0]["kind"] == "total"
    assert body["files"][0]["path"] == "projects/x.py"
    assert json.loads(json.dumps(body))["covered"] == body["covered"]


# --- the pytest command ------------------------------------------------
def test_pytest_command_asks_for_json_at_the_requested_path(tmp_path: Path):
    command = coverage_gate.build_pytest_command(tmp_path / "cov.json")
    assert command[:3] == [sys.executable, "-m", "pytest"]
    assert "--cov" in command
    assert f"--cov-report=json:{tmp_path / 'cov.json'}" in command


def test_pytest_command_switches_off_pytest_covs_own_floor():
    """The gate owns the verdict; pytest-cov must not exit 1 on a breach."""
    assert "--cov-fail-under=0" in coverage_gate.build_pytest_command(Path("cov.json"))


def test_pytest_command_appends_extra_arguments():
    command = coverage_gate.build_pytest_command(Path("cov.json"), ["-k", "gate"])
    assert command[-2:] == ["-k", "gate"]


def test_run_pytest_returns_the_subprocess_exit_code(monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}

    class Completed:
        returncode = 3

    def fake_run(command, cwd=None, check=False):
        seen["command"] = command
        seen["cwd"] = cwd
        return Completed()

    monkeypatch.setattr(coverage_gate.subprocess, "run", fake_run)
    assert coverage_gate.run_pytest(Path("cov.json")) == 3
    assert seen["cwd"] == str(coverage_gate.ROOT)


# --- the CLI -----------------------------------------------------------
def write_report(tmp_path: Path, percent: float, statements: int = 1000) -> Path:
    """Write a synthetic coverage report and return its path.

    Args:
        tmp_path: Directory to write into.
        percent: Overall covered percentage.
        statements: Total executable statements.

    Returns:
        The path of the written report.
    """
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report(percent, statements)), encoding="utf-8")
    return path


def test_main_passes_above_the_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = write_report(tmp_path, 94.0)
    code = coverage_gate.main(["--report-only", "--from-json", str(path)])
    assert code == 0
    assert "PASS" in capsys.readouterr().out


def test_main_fails_below_the_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = write_report(tmp_path, 71.5)
    code = coverage_gate.main(["--report-only", "--from-json", str(path)])
    captured = capsys.readouterr()
    assert code == 1
    assert "71.50%" in captured.err


def test_main_honours_a_stricter_minimum(tmp_path: Path):
    path = write_report(tmp_path, 94.0)
    assert coverage_gate.main(["--report-only", "--from-json", str(path), "--minimum", "95"]) == 1


def test_main_emits_json_when_asked(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = write_report(tmp_path, 91.0, statements=250)
    code = coverage_gate.main(["--report-only", "--from-json", str(path), "--json"])
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["passed"] is True
    assert body["statements"] == 250


def test_main_refuses_to_pass_without_a_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    code = coverage_gate.main(["--report-only", "--from-json", str(tmp_path / "absent.json")])
    assert code == coverage_gate.EXIT_UNMEASURABLE
    assert "cannot read a coverage report" in capsys.readouterr().err


def test_main_refuses_to_pass_when_pytest_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = write_report(tmp_path, 99.0)
    monkeypatch.setattr(coverage_gate, "run_pytest", lambda json_path, extra: 1)
    code = coverage_gate.main(["--from-json", str(path)])
    assert code == coverage_gate.EXIT_UNMEASURABLE


def test_main_runs_pytest_then_gates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = write_report(tmp_path, 92.0)
    called: dict = {}

    def fake_run(json_path, extra_args):
        called["json_path"] = json_path
        called["extra"] = list(extra_args)
        return 0

    monkeypatch.setattr(coverage_gate, "run_pytest", fake_run)
    code = coverage_gate.main(["--from-json", str(path), "--pytest-arg=-x"])
    assert code == 0
    assert called["json_path"] == path
    assert called["extra"] == ["-x"]


def test_main_reports_the_breach_rather_than_calling_it_unmeasurable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """A green suite with a red report is a coverage failure (1), not a crash (2)."""
    path = write_report(tmp_path, 84.3)
    monkeypatch.setattr(coverage_gate, "run_pytest", lambda json_path, extra: 0)
    code = coverage_gate.main(["--from-json", str(path)])
    captured = capsys.readouterr()
    assert code == 1
    assert "84.30%" in captured.out
    assert "FAIL" in captured.out


def test_main_can_hide_the_file_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = write_report(tmp_path, 92.0)
    assert coverage_gate.main(["--report-only", "--from-json", str(path), "--no-missing"]) == 0
    assert "file" not in capsys.readouterr().out


# --- the gate is actually wired in -------------------------------------
def test_ci_runs_the_coverage_gate():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "tools/coverage_gate.py" in workflow
    assert "--minimum 90" in workflow


def test_pyproject_carries_the_same_floor():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.coverage.report]" in pyproject
    assert "fail_under = 90" in pyproject


def test_pyproject_scopes_coverage_to_shipped_code():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for omitted in ('"tests/*"', '"tools/*"', '"conftest.py"'):
        assert omitted in pyproject


def test_the_scope_excludes_tests_and_tooling():
    """The measured scope must not be flattered by the test files themselves."""
    from coverage import Coverage

    cov = Coverage(config_file=str(ROOT / "pyproject.toml"))
    cov.load()
    measured = cov.get_data()  # noqa: F841 - exercises the config load path
    omitted = str(cov.config.get_option("run:omit") or "")
    assert "tests" in omitted
