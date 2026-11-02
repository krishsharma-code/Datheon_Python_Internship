"""Tests for the Day 14 observability report.

Two groups:

* the reader — :func:`observability.read_json_records`,
  :func:`observability.discover_log_files` — including the lines a killed
  process leaves behind (truncated JSON, blank lines, a JSON array instead of an
  object) and the paths that should be refused;
* the reduction — :func:`observability.summarize_runs`,
  :class:`observability.ProjectSummary`, :func:`observability.percentile`,
  :func:`observability.duration_stats`, :func:`observability.build_report` and
  the CLI — asserted on record counts and derived figures rather than on
  wall-clock time, so nothing here can flake on a busy machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import observability
from errors import AccessDeniedError, MissingFileError, ValidationError
from logging_config import DEFAULT_LOG_DIR_ENV
from structured_logging import OUTCOMES

ROOT = Path(__file__).resolve().parents[1]


# --- fixtures -----------------------------------------------------------


def _start(run_id: str, project: str = "organizer", **extra: object) -> dict:
    """Build the ``run_start`` record of a run."""
    record = {
        "schema_version": 1,
        "ts": "2026-11-02T09:00:00.000+05:30",
        "level": "INFO",
        "levelno": 20,
        "logger": project,
        "message": "run started",
        "source": "logging_config.py:538",
        "function": "configure_logging",
        "run_id": run_id,
        "project": project,
        "duration_ms": 0.7,
        "run_outcome": "running",
        "seq": 1,
        "fields": {"event": "run_start", "project": project},
    }
    record.update(extra)
    return record


def _finish(
    run_id: str,
    project: str = "organizer",
    outcome: str = "success",
    duration_ms: float = 41.2,
    error: dict | None = None,
) -> dict:
    """Build the ``run_finish`` record of a run."""
    fields: dict = {"event": "run_finish", "outcome": outcome}
    if error is not None:
        fields["error"] = error
    return {
        "schema_version": 1,
        "ts": "2026-11-02T09:00:00.041+05:30",
        "level": "INFO" if outcome == "success" else "ERROR",
        "levelno": 20 if outcome == "success" else 40,
        "logger": project,
        "message": "run finished",
        "source": "logging_config.py:598",
        "function": "shutdown_logging",
        "run_id": run_id,
        "project": project,
        "duration_ms": duration_ms,
        "run_outcome": outcome,
        "seq": 2,
        "fields": fields,
    }


def _write_log(path: Path, records: list[dict]) -> Path:
    """Write records to ``path`` as JSON lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _run_cli(script: str, *args: str, log_dir: Path) -> subprocess.CompletedProcess:
    """Run a CLI script in a subprocess with a closed stdin and a temp log dir."""
    env = {**os.environ, DEFAULT_LOG_DIR_ENV: str(log_dir)}
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=str(ROOT),
        input="",
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


# --- read_json_records --------------------------------------------------


def test_read_json_records_parses_one_object_per_line(tmp_path):
    path = _write_log(tmp_path / "organizer.log", [_start("run-a"), _finish("run-a")])

    records, malformed = observability.read_json_records(path)

    assert [record["run_id"] for record in records] == ["run-a", "run-a"]
    assert malformed == []


def test_read_json_records_skips_blank_lines(tmp_path):
    path = tmp_path / "organizer.log"
    path.write_text("\n" + json.dumps(_start("run-a")) + "\n\n\n", encoding="utf-8")

    records, malformed = observability.read_json_records(path)

    assert len(records) == 1
    assert malformed == []


def test_read_json_records_reports_invalid_json_with_its_line_number(tmp_path):
    path = tmp_path / "organizer.log"
    path.write_text(
        json.dumps(_start("run-a")) + "\n" + '{"ts": "truncated' + "\n",
        encoding="utf-8",
    )

    records, malformed = observability.read_json_records(path)

    assert len(records) == 1
    assert len(malformed) == 1
    assert malformed[0].line_number == 2
    assert malformed[0].reason.startswith("invalid JSON")
    assert '{"ts": "truncated' in malformed[0].preview


def test_read_json_records_rejects_json_that_is_not_an_object(tmp_path):
    path = tmp_path / "organizer.log"
    path.write_text('[1, 2, 3]\n"just a string"\n', encoding="utf-8")

    records, malformed = observability.read_json_records(path)

    assert records == []
    assert [line.reason for line in malformed] == [
        "not a JSON object (list)",
        "not a JSON object (str)",
    ]


def test_read_json_records_keeps_reading_after_a_bad_line(tmp_path):
    path = tmp_path / "organizer.log"
    path.write_text(
        "not json at all\n" + json.dumps(_start("run-a")) + "\nstill not json\n",
        encoding="utf-8",
    )

    records, malformed = observability.read_json_records(path)

    assert len(records) == 1
    assert [line.line_number for line in malformed] == [1, 3]


def test_read_json_records_truncates_the_preview(tmp_path):
    path = tmp_path / "organizer.log"
    path.write_text("x" * 500 + "\n", encoding="utf-8")

    _, malformed = observability.read_json_records(path)

    assert len(malformed[0].preview) == observability.PREVIEW_CHARS


def test_read_json_records_missing_file_raises_access_denied(tmp_path):
    with pytest.raises(AccessDeniedError):
        observability.read_json_records(tmp_path / "absent.log")


def test_malformed_line_serialises_and_describes_itself(tmp_path):
    path = tmp_path / "organizer.log"
    path.write_text("oops\n", encoding="utf-8")
    _, malformed = observability.read_json_records(path)

    payload = malformed[0].to_dict()

    assert payload["line"] == 1
    assert payload["path"].endswith("organizer.log")
    assert "organizer.log:1" in malformed[0].describe()


# --- discover_log_files -------------------------------------------------


def test_discover_log_files_expands_a_directory_including_rotations(tmp_path):
    _write_log(tmp_path / "logs" / "organizer.log", [_start("run-a")])
    _write_log(tmp_path / "logs" / "organizer.log.1", [_start("run-b")])
    (tmp_path / "logs" / "notes.txt").write_text("ignored", encoding="utf-8")

    found = observability.discover_log_files([tmp_path / "logs"])

    assert [path.name for path in found] == ["organizer.log", "organizer.log.1"]


def test_discover_log_files_accepts_an_explicit_file(tmp_path):
    path = _write_log(tmp_path / "custom.jsonl", [_start("run-a")])

    assert observability.discover_log_files([path]) == [path]


def test_discover_log_files_deduplicates_a_repeated_path(tmp_path):
    path = _write_log(tmp_path / "logs" / "organizer.log", [_start("run-a")])

    found = observability.discover_log_files([path, tmp_path / "logs", path])

    assert found == [path]


def test_discover_log_files_missing_path_is_a_data_error(tmp_path):
    with pytest.raises(MissingFileError):
        observability.discover_log_files([tmp_path / "nowhere"])


def test_discover_log_files_empty_directory_is_a_data_error(tmp_path):
    (tmp_path / "logs").mkdir()

    with pytest.raises(MissingFileError):
        observability.discover_log_files([tmp_path / "logs"])


# --- percentile / duration_stats ---------------------------------------


def test_percentile_is_nearest_rank():
    samples = [1.0, 2.0, 3.0, 4.0, 5.0]

    assert observability.percentile(samples, 0.5) == 3.0
    assert observability.percentile(samples, 0.95) == 5.0
    assert observability.percentile(samples, 1.0) == 5.0
    assert observability.percentile(samples, 0.2) == 1.0


def test_percentile_returns_a_sample_never_an_average():
    samples = [10.0, 20.0, 30.0, 40.0]

    assert observability.percentile(samples, 0.75) == 30.0
    assert observability.percentile(samples, 0.75) in samples


def test_percentile_rejects_an_empty_sample():
    with pytest.raises(ValidationError):
        observability.percentile([], 0.5)


@pytest.mark.parametrize("quantile", [0.0, -0.1, 1.5])
def test_percentile_rejects_an_out_of_range_quantile(quantile):
    with pytest.raises(ValidationError):
        observability.percentile([1.0, 2.0], quantile)


def test_duration_stats_of_nothing_is_zeroed():
    assert observability.duration_stats([]) == {
        "count": 0,
        "p50_ms": 0.0,
        "p95_ms": 0.0,
        "max_ms": 0.0,
        "mean_ms": 0.0,
    }


def test_duration_stats_summarises_the_samples():
    stats = observability.duration_stats([10.0, 20.0, 30.0, 40.0])

    assert stats["count"] == 4
    assert stats["p50_ms"] == 20.0
    assert stats["p95_ms"] == 40.0
    assert stats["max_ms"] == 40.0
    assert stats["mean_ms"] == 25.0


# --- summarize_runs -----------------------------------------------------


def test_summarize_runs_takes_outcome_and_duration_from_the_finish_record():
    runs = observability.summarize_runs([_start("run-a"), _finish("run-a", duration_ms=41.2)])

    assert len(runs) == 1
    run = runs[0]
    assert run.run_id == "run-a"
    assert run.project == "organizer"
    assert run.outcome == "success"
    assert run.duration_ms == 41.2
    assert run.records == 2


def test_summarize_runs_marks_an_unfinished_run_incomplete():
    runs = observability.summarize_runs([_start("run-a", duration_ms=5.0)])

    assert runs[0].outcome == "incomplete"
    assert runs[0].duration_ms == 5.0


def test_summarize_runs_extracts_the_error_code_and_type():
    error = {
        "code": "DT-1001",
        "type": "ValidationError",
        "message": "invalid symbol",
        "hint": "use a ticker like AAPL",
        "context": {"symbol": "??"},
        "exit_code": 1,
    }

    runs = observability.summarize_runs(
        [_start("run-a"), _finish("run-a", outcome="error", error=error)]
    )

    assert runs[0].outcome == "error"
    assert runs[0].error_code == "DT-1001"
    assert runs[0].error_type == "ValidationError"


def test_summarize_runs_ignores_records_without_a_run_id():
    runs = observability.summarize_runs([{"message": "loose"}, _start("run-a")])

    assert [run.run_id for run in runs] == ["run-a"]
    assert runs[0].records == 1


def test_summarize_runs_keeps_runs_in_first_seen_order():
    runs = observability.summarize_runs(
        [_start("run-b"), _start("run-a"), _finish("run-a"), _finish("run-b")]
    )

    assert [run.run_id for run in runs] == ["run-b", "run-a"]


def test_summarize_runs_prefers_the_terminal_duration_over_a_larger_offset():
    late = _start("run-a", duration_ms=900.0)
    runs = observability.summarize_runs([late, _finish("run-a", duration_ms=41.2)])

    assert runs[0].duration_ms == 41.2


def test_run_summary_serialises_itself():
    run = observability.summarize_runs([_start("run-a"), _finish("run-a")])[0]

    payload = run.to_dict()

    assert payload["run_id"] == "run-a"
    assert payload["outcome"] == "success"
    assert payload["records"] == 2


# --- build_report -------------------------------------------------------


def _three_run_log(tmp_path: Path) -> Path:
    """Write one log holding a success, a failure and an interrupted run."""
    records = [
        _start("run-1"),
        _finish("run-1", duration_ms=10.0),
        _start("run-2"),
        _finish(
            "run-2",
            outcome="error",
            duration_ms=30.0,
            error={"code": "DT-1001", "type": "ValidationError", "message": "bad"},
        ),
        _start("run-3", project="assistant"),
        _finish("run-3", project="assistant", outcome="interrupted", duration_ms=20.0),
        {"message": "no run id here"},
    ]
    return _write_log(tmp_path / "logs" / "organizer.log", records)


def test_build_report_counts_runs_outcomes_and_records(tmp_path):
    path = _three_run_log(tmp_path)

    report = observability.build_report([path], caches={})

    assert report.files == (path,)
    assert report.records == 7
    assert len(report.runs) == 3
    assert report.unattributed == 1
    assert report.outcome_totals() == {
        "success": 1,
        "error": 1,
        "interrupted": 1,
        "incomplete": 0,
    }


def test_build_report_totals_are_flat_and_complete(tmp_path):
    report = observability.build_report([_three_run_log(tmp_path)], caches={})

    totals = report.totals()

    assert totals["runs"] == 3
    assert totals["files"] == 1
    assert totals["malformed_lines"] == 0
    assert totals["unattributed_records"] == 1
    assert totals["p50_ms"] == 20.0
    assert totals["max_ms"] == 30.0
    assert totals["success_rate"] == round(1 / 3, 4)
    assert totals["error_codes"] == {"DT-1001": 1}


def test_build_report_groups_projects_busiest_first(tmp_path):
    report = observability.build_report([_three_run_log(tmp_path)], caches={})

    projects = report.projects()

    assert [summary.project for summary in projects] == ["organizer", "assistant"]
    assert projects[0].runs == 2
    assert projects[0].failures == 1
    assert projects[0].success_rate == 0.5
    assert projects[1].error_codes == {}


def test_build_report_counts_malformed_lines_across_files(tmp_path):
    _write_log(tmp_path / "logs" / "a.log", [_start("run-a"), _finish("run-a")])
    (tmp_path / "logs" / "b.log").write_text("{broken\n", encoding="utf-8")

    report = observability.build_report([tmp_path / "logs"], caches={})

    assert report.records == 2
    assert len(report.malformed) == 1
    assert len(report.files) == 2


def test_build_report_embeds_the_cache_snapshot(tmp_path):
    caches = {
        "word_list": {
            "name": "word_list",
            "hits": 9,
            "misses": 1,
            "requests": 10,
            "hit_rate": 0.9,
            "size": 1,
            "maxsize": 16,
        }
    }

    report = observability.build_report([_three_run_log(tmp_path)], caches=caches)

    assert report.caches["word_list"]["hits"] == 9
    assert "word_list" in report.render()


def test_build_report_with_no_cache_snapshot_says_so(tmp_path):
    report = observability.build_report([_three_run_log(tmp_path)], caches={})

    assert "(no live caches)" in report.render()


def test_report_to_dict_is_json_serialisable(tmp_path):
    report = observability.build_report([_three_run_log(tmp_path)], caches={})

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["totals"]["runs"] == 3
    assert len(payload["projects"]) == 2
    assert payload["projects"][0]["project"] == "organizer"
    assert payload["malformed"] == []


def test_report_render_lists_projects_outcomes_and_codes(tmp_path):
    report = observability.build_report([_three_run_log(tmp_path)], caches={})

    text = report.render()

    assert "3 run(s)" in text
    assert "organizer" in text
    assert "outcomes: success=1  error=1  interrupted=1  incomplete=0" in text
    assert "DT-1001" in text
    assert "malformed lines: 0" in text


def test_report_render_of_an_empty_report_is_still_a_table():
    report = observability.ObservabilityReport()

    text = report.render()

    assert "(no runs recorded)" in text
    assert "(none)" in text


def test_report_render_limits_the_project_table(tmp_path):
    report = observability.build_report([_three_run_log(tmp_path)], caches={})

    text = report.render(top=1)

    assert "organizer" in text
    assert "assistant" not in text


@pytest.mark.parametrize("top", [0, -3])
def test_report_rejects_a_non_positive_top(tmp_path, top):
    report = observability.build_report([_three_run_log(tmp_path)], caches={})

    with pytest.raises(ValidationError):
        report.render(top=top)
    with pytest.raises(ValidationError):
        report.to_dict(top=top)


def test_project_summary_always_carries_every_outcome_key(tmp_path):
    report = observability.build_report([_three_run_log(tmp_path)], caches={})

    for summary in report.projects():
        assert set(summary.outcomes) == set(OUTCOMES)


def test_error_codes_are_ranked_by_frequency(tmp_path):
    records = []
    for index, code in enumerate(["DT-1001", "DT-1002", "DT-1001"]):
        run_id = f"run-{index}"
        records.append(_start(run_id))
        records.append(
            _finish(
                run_id,
                outcome="error",
                error={"code": code, "type": "ValidationError", "message": "bad"},
            )
        )
    report = observability.build_report([_write_log(tmp_path / "x.log", records)], caches={})

    assert list(report.error_codes()) == ["DT-1001", "DT-1002"]
    assert report.error_codes()["DT-1001"] == 2


# --- CLI ----------------------------------------------------------------


def test_cli_emits_json(tmp_path, capsys):
    path = _three_run_log(tmp_path)

    code = observability.main([str(path), "--json", "--no-caches"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["runs"] == 3
    assert payload["caches"] == {}


def test_cli_emits_a_table_by_default(tmp_path, capsys):
    path = _three_run_log(tmp_path)

    code = observability.main([str(path)])

    assert code == 0
    output = capsys.readouterr().out
    assert "observability report" in output
    assert "organizer" in output


def test_cli_accepts_a_log_dir(tmp_path, capsys):
    _three_run_log(tmp_path)

    code = observability.main(["--log-dir", str(tmp_path / "logs"), "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["totals"]["runs"] == 3


def test_cli_rejects_paths_and_log_dir_together(tmp_path):
    with pytest.raises(ValidationError):
        observability.main([str(tmp_path / "logs"), "--log-dir", str(tmp_path / "logs")])


def test_cli_missing_path_is_a_data_error(tmp_path):
    with pytest.raises(MissingFileError):
        observability.main([str(tmp_path / "nowhere")])


def test_cli_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        observability.main(["--version"])

    assert excinfo.value.code == 0
    assert "observability" in capsys.readouterr().out


def test_cli_reports_real_tool_logs(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    result = _run_cli(
        "projects/05_inventory_oop/main.py",
        "--log-level",
        "INFO",
        "--json-logs",
        log_dir=log_dir,
    )
    assert result.returncode == 0, result.stderr

    code = observability.main(["--log-dir", str(log_dir), "--json", "--no-caches"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["runs"] == 1
    assert payload["projects"][0]["project"] == "inventory"
    assert payload["projects"][0]["outcomes"]["success"] == 1
    assert payload["totals"]["malformed_lines"] == 0
    assert payload["totals"]["max_ms"] >= 0
