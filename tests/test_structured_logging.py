"""Tests for the structured logging layer (Day 10).

The contract under test:

* ``JsonFormatter`` renders every record as one JSON object with a stable,
  versioned schema — timestamp, level, logger, message, source — and never
  emits a newline inside a record;
* caller-supplied fields are nested under ``fields`` and can therefore never
  shadow a reserved key; values that are not JSON-serialisable are rendered
  with ``str`` instead of dropping the record;
* a :class:`RunContext` stamps a ``run_id``, ``project``, ``started``,
  ``duration_ms`` and ``run_outcome`` on every record, and a contiguous ``seq``;
* ``--json-logs`` and ``--run-id`` reach the formatter from a parsed command
  line, and the text format stays the default;
* ``run_cli`` closes the run with the right outcome — ``success``, ``error`` or
  ``interrupted`` — and with the error payload a consumer wants;
* ``--json-logs --log-level DEBUG`` produces parseable JSON in *every* project.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

import pytest

from cli_contract import run_cli
from errors import ConfigurationError, ValidationError
from logging_config import (
    DEFAULT_LOG_DIR_ENV,
    LoggingSession,
    add_logging_flags,
    configure_logging,
    configure_logging_from_args,
    current_run,
    finish_active_runs,
    log_error,
    shutdown_logging,
    structured_extra,
)
from structured_logging import (
    FIELDS_ATTRIBUTE,
    JSON_SCHEMA_VERSION,
    OUTCOMES,
    RUN_ID_ENV,
    JsonFormatter,
    RunContext,
    begin_run,
    end_run,
    new_run_id,
)

ROOT = Path(__file__).resolve().parents[1]

#: ``(program name, script path relative to the repo root)`` for each CLI.
CLIS = [
    ("hangman", "projects/01_hangman_game/main.py"),
    ("portfolio", "projects/02_portfolio_tracker/main.py"),
    ("organizer", "projects/03_file_organizer_automation/main.py"),
    ("assistant", "projects/04_terminal_assistant/main.py"),
    ("inventory", "projects/05_inventory_oop/main.py"),
]

CLI_IDS = [name for name, _ in CLIS]

#: The shape :func:`new_run_id` guarantees.
RUN_ID_RE = re.compile(r"^run-\d{8}T\d{6}Z-[0-9a-f]{8}$")


@pytest.fixture(autouse=True)
def _isolated_logging(monkeypatch):
    """Keep every test hermetic: no inherited run ID or log dir, no leftovers."""
    monkeypatch.delenv(DEFAULT_LOG_DIR_ENV, raising=False)
    monkeypatch.delenv(RUN_ID_ENV, raising=False)
    yield
    shutdown_logging()
    end_run()


def _record(message: str = "hello", level: int = logging.INFO, **kwargs) -> logging.LogRecord:
    """Build a detached :class:`logging.LogRecord` for formatter tests.

    Args:
        message: The record message, possibly with ``%`` placeholders.
        level: The logging level.
        **kwargs: Extra attributes to set on the record (``exc_info``, …).

    Returns:
        A record that was never passed through a logger.
    """
    record = logging.LogRecord(
        name="demo",
        level=level,
        pathname="/repo/main.py",
        lineno=42,
        msg=message,
        args=kwargs.pop("args", ()),
        exc_info=kwargs.pop("exc_info", None),
        func="main",
    )
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


def _read_json_lines(path: Path) -> list[dict]:
    """Parse a JSON-lines log file.

    Args:
        path: The log file to read.

    Returns:
        One mapping per line; a malformed line fails the calling test with a
        ``json.JSONDecodeError``.
    """
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _run_cli(script: str, *args: str, log_dir: Path) -> subprocess.CompletedProcess:
    """Run a CLI script in a subprocess with a closed stdin and a temp log dir.

    Args:
        script: Path to the entry-point script, relative to the repo root.
        *args: Additional command-line arguments.
        log_dir: Directory exported as ``DATHEON_LOG_DIR`` for the child.

    Returns:
        The completed process, capturing stdout/stderr as text.
    """
    env = {**os.environ, DEFAULT_LOG_DIR_ENV: str(log_dir)}
    env.pop(RUN_ID_ENV, None)
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=str(ROOT),
        input="",
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


# --- run IDs ------------------------------------------------------------
def test_new_run_id_is_sortable_and_matches_the_documented_shape():
    run_id = new_run_id()
    assert RUN_ID_RE.match(run_id)
    assert new_run_id() != new_run_id()


def test_new_run_id_honours_a_prefix():
    assert new_run_id("cron").startswith("cron-")


def test_begin_run_makes_the_context_current_and_end_run_clears_it():
    context = begin_run("organizer")
    try:
        assert current_run() is context
        assert context.project == "organizer"
        assert context.outcome == "running"
        assert RUN_ID_RE.match(context.run_id)
    finally:
        end_run(context)
    assert current_run() is None


def test_begin_run_prefers_an_explicit_id_over_the_environment(monkeypatch):
    monkeypatch.setenv(RUN_ID_ENV, "from-env")
    assert begin_run("organizer").run_id == "from-env"
    assert begin_run("organizer", "explicit").run_id == "explicit"


def test_end_run_ignores_a_superseded_context():
    first = begin_run("organizer")
    second = begin_run("organizer")
    end_run(first)  # first is no longer current: must not clear second
    assert current_run() is second


def test_run_context_reports_a_monotonic_duration_and_fields():
    context = RunContext(run_id="run-x", project="hangman")
    first = context.elapsed_ms()
    second = context.elapsed_ms()
    assert first >= 0
    assert second >= first
    fields = context.finish("success").fields()
    assert fields["run_id"] == "run-x"
    assert fields["project"] == "hangman"
    assert fields["run_outcome"] == "success"
    assert fields["duration_ms"] >= 0
    datetime.fromisoformat(fields["started"])  # must be a valid ISO-8601 stamp


def test_documented_outcomes_are_the_ones_run_cli_produces():
    assert set(OUTCOMES) == {"success", "error", "interrupted", "incomplete"}


# --- JSON formatting ----------------------------------------------------
def test_json_record_carries_the_versioned_core_schema():
    payload = JsonFormatter().build_payload(_record("hello %s", args=("world",)))
    assert payload["schema_version"] == JSON_SCHEMA_VERSION
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["levelno"] == logging.INFO
    assert payload["logger"] == "demo"
    assert payload["source"] == "main.py:42"
    assert payload["function"] == "main"
    datetime.fromisoformat(payload["ts"])


def test_json_record_is_a_single_line():
    rendered = JsonFormatter().format(_record("multi\nline"))
    assert "\n" not in rendered
    assert json.loads(rendered)["message"] == "multi\nline"


def test_json_record_can_omit_the_source_location():
    payload = JsonFormatter(include_source=False).build_payload(_record())
    assert "source" not in payload
    assert "function" not in payload


def test_json_record_has_no_run_fields_without_a_context():
    payload = JsonFormatter().build_payload(_record())
    assert "run_id" not in payload
    assert "duration_ms" not in payload
    assert "seq" not in payload


def test_json_record_carries_the_run_context():
    context = begin_run("portfolio")
    try:
        payload = JsonFormatter().build_payload(_record())
    finally:
        end_run(context)
    assert payload["run_id"] == context.run_id
    assert payload["project"] == "portfolio"
    assert payload["run_outcome"] == "running"
    assert payload["duration_ms"] >= 0


def test_sequence_numbers_are_contiguous_and_shared_by_both_handlers():
    context = begin_run("inventory")
    try:
        formatter = JsonFormatter()
        record = _record()
        first = formatter.build_payload(record)["seq"]
        again = formatter.build_payload(record)["seq"]
        second = formatter.build_payload(_record())["seq"]
    finally:
        end_run(context)
    assert first == again == 1
    assert second == 2


def test_caller_fields_are_nested_and_cannot_shadow_reserved_keys():
    record = _record("real message", **structured_extra(processed=12, message="not mine"))
    payload = JsonFormatter().build_payload(record)
    assert payload["message"] == "real message"
    assert payload["fields"] == {"processed": 12, "message": "not mine"}


def test_unserialisable_field_values_are_rendered_as_text():
    record = _record("payload", **structured_extra(path=Path("/tmp/x"), size={1, 2}))
    payload = json.loads(JsonFormatter().format(record))
    assert payload["fields"]["path"] == "/tmp/x"
    assert "size" in payload["fields"]


def test_structured_extra_wraps_the_fields_mapping():
    assert structured_extra(a=1) == {FIELDS_ATTRIBUTE: {"a": 1}}
    assert structured_extra() == {FIELDS_ATTRIBUTE: {}}


def test_exception_details_are_captured_as_text():
    try:
        raise ValueError("kaboom")
    except ValueError:
        record = _record("failed", level=logging.ERROR, exc_info=sys.exc_info())
    payload = JsonFormatter().build_payload(record)
    assert "ValueError: kaboom" in payload["exception"]
    assert payload["level"] == "ERROR"


def test_log_error_uses_the_error_taxonomy_payload():
    logger = logging.getLogger("demo-taxonomy")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        log_error(logger, ValidationError("bad symbol", hint="use AAPL", context={"symbol": "??"}))
    finally:
        logger.removeHandler(handler)

    assert len(records) == 1
    payload = JsonFormatter().build_payload(records[0])
    assert payload["fields"]["error"]["code"].startswith("DT-")
    assert payload["fields"]["error"]["hint"] == "use AAPL"
    assert payload["fields"]["error"]["context"] == {"symbol": "??"}
    assert payload["message"].startswith("ValidationError: bad symbol")


def test_log_error_reduces_a_plain_exception_to_type_and_message():
    logger = logging.getLogger("demo-plain")
    logger.setLevel(logging.DEBUG)
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    try:
        log_error(logger, RuntimeError("plain boom"))
    finally:
        logger.removeHandler(handler)
    payload = json.loads(stream.getvalue().strip())
    assert payload["fields"]["error"] == {"type": "RuntimeError", "message": "plain boom"}


# --- configuration ------------------------------------------------------
def test_flags_default_to_text_and_no_run_id():
    parser = add_logging_flags(argparse.ArgumentParser())
    defaults = parser.parse_args([])
    assert defaults.json_logs is False
    assert defaults.run_id is None
    assert parser.parse_args(["--json-logs", "--run-id", "abc"]).json_logs is True
    assert parser.parse_args(["--json-logs", "--run-id", "abc"]).run_id == "abc"


def test_json_logs_writes_parseable_records_with_a_correlated_run(tmp_path):
    stream = StringIO()
    session = configure_logging(
        "demo", "INFO", tmp_path / "demo.log", stream=stream, json_logs=True
    )
    try:
        assert session.json_logs is True
        assert session.run_id is not None
        session.logger.info("middle", extra=structured_extra(processed=3))
        session.finish("success")
    finally:
        session.close()

    records = _read_json_lines(tmp_path / "demo.log")
    assert all(record["schema_version"] == JSON_SCHEMA_VERSION for record in records)
    assert {record["run_id"] for record in records} == {session.run_id}
    assert [
        record["fields"]["event"] for record in records if "event" in record.get("fields", {})
    ] == [
        "run_start",
        "run_finish",
    ]
    assert [record["seq"] for record in records] == list(range(1, len(records) + 1))
    assert records[-1]["run_outcome"] == "success"
    assert records[-1]["duration_ms"] >= 0
    assert records[-2]["fields"]["processed"] == 3

    console = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert console[0]["message"] == "run started"
    assert console[-1]["message"] == "run finished"


def test_text_format_is_still_the_default(tmp_path):
    session = configure_logging("demo", "INFO", tmp_path / "demo.log")
    try:
        session.logger.info("plain line")
    finally:
        session.close()
    first = (tmp_path / "demo.log").read_text(encoding="utf-8").splitlines()[0]
    assert not first.startswith("{")
    assert "INFO" in first
    assert "demo" in first


def test_json_logs_without_a_level_stays_off(tmp_path, monkeypatch):
    monkeypatch.setenv(DEFAULT_LOG_DIR_ENV, str(tmp_path))
    session = configure_logging("demo", None, None, json_logs=True)
    assert session.enabled is False
    assert session.run_id is None
    assert list(tmp_path.iterdir()) == []


def test_configure_from_args_reads_the_structured_flags(tmp_path):
    parser = add_logging_flags(argparse.ArgumentParser())
    args = parser.parse_args(
        ["--log-level", "debug", "--json-logs", "--run-id", "cron-42", "--log-file", "x.log"]
    )
    session = configure_logging_from_args("demo", args)
    try:
        assert session.json_logs is True
        assert session.run_id == "cron-42"
    finally:
        session.close()


def test_run_id_comes_from_the_environment_when_not_passed(monkeypatch, tmp_path):
    monkeypatch.setenv(RUN_ID_ENV, "env-run-7")
    session = configure_logging("demo", "INFO", tmp_path / "demo.log")
    try:
        assert session.run_id == "env-run-7"
    finally:
        session.close()


def test_reconfiguring_starts_a_fresh_run_and_closes_the_previous_one(tmp_path):
    first = configure_logging("demo", "INFO", tmp_path / "one.log", json_logs=True)
    first_id = first.run_id
    second = configure_logging("demo", "INFO", tmp_path / "two.log", json_logs=True)
    try:
        assert first.finished is True
        assert first_id is not None
        assert second.run_id != first_id
        records = _read_json_lines(tmp_path / "one.log")
        assert records[-1]["fields"]["event"] == "run_finish"
        assert records[-1]["run_outcome"] == "incomplete"
    finally:
        second.close()


def test_finish_is_idempotent(tmp_path):
    session = configure_logging("demo", "INFO", tmp_path / "demo.log", json_logs=True)
    try:
        session.finish("success")
        session.finish("error")
    finally:
        session.close()
    finishes = [
        record
        for record in _read_json_lines(tmp_path / "demo.log")
        if record.get("fields", {}).get("event") == "run_finish"
    ]
    assert len(finishes) == 1
    assert finishes[0]["run_outcome"] == "success"


def test_shutdown_marks_an_unfinished_run_incomplete(tmp_path):
    session = configure_logging("demo", "INFO", tmp_path / "demo.log", json_logs=True)
    assert session.finished is False
    shutdown_logging()
    records = _read_json_lines(tmp_path / "demo.log")
    assert records[-1]["run_outcome"] == "incomplete"
    assert records[-1]["fields"]["outcome"] == "incomplete"


def test_finish_active_runs_records_the_outcome_and_error(tmp_path):
    configure_logging("demo", "INFO", tmp_path / "demo.log", json_logs=True)
    finish_active_runs("error", error={"type": "OSError", "message": "denied"})
    shutdown_logging()
    last = _read_json_lines(tmp_path / "demo.log")[-1]
    assert last["run_outcome"] == "error"
    assert last["fields"]["error"] == {"type": "OSError", "message": "denied"}


def test_a_disabled_session_reports_no_run_id(tmp_path):
    session = configure_logging("demo", None, None)
    assert isinstance(session, LoggingSession)
    assert session.run_id is None
    session.finish("success")  # must be a no-op, not an error
    session.close()


# --- run_cli outcomes ---------------------------------------------------
def _finish_records(path: Path) -> list[dict]:
    """Return the ``run_finish`` records of a JSON-lines log.

    Args:
        path: The log file to read.

    Returns:
        Every record whose ``fields.event`` is ``run_finish``.
    """
    return [
        record
        for record in _read_json_lines(path)
        if record.get("fields", {}).get("event") == "run_finish"
    ]


def test_run_cli_records_a_successful_outcome(tmp_path):
    session = configure_logging("demo", "INFO", tmp_path / "cli.log", json_logs=True)
    assert run_cli(lambda: 0) == 0
    assert session.finished is True
    records = _finish_records(tmp_path / "cli.log")
    assert len(records) == 1
    assert records[0]["run_outcome"] == "success"
    assert "error" not in records[0]["fields"]


def test_run_cli_records_a_domain_error_with_its_payload(tmp_path, capsys):
    configure_logging("demo", "INFO", tmp_path / "cli.log", json_logs=True)

    def boom() -> int:
        raise ConfigurationError("bad config", hint="fix it", context={"key": "x"})

    assert run_cli(boom) == int(ConfigurationError("x").exit_code)
    assert "bad config" in capsys.readouterr().err
    record = _finish_records(tmp_path / "cli.log")[-1]
    assert record["run_outcome"] == "error"
    assert record["fields"]["error"]["hint"] == "fix it"
    assert record["fields"]["error"]["context"] == {"key": "x"}


def test_run_cli_records_a_bare_exception_as_an_error(tmp_path, capsys):
    configure_logging("demo", "INFO", tmp_path / "cli.log", json_logs=True)

    def boom() -> int:
        raise ValueError("bare")

    assert run_cli(boom) == 1
    assert "bare" in capsys.readouterr().err
    record = _finish_records(tmp_path / "cli.log")[-1]
    assert record["run_outcome"] == "error"
    assert record["fields"]["error"] == {"type": "ValueError", "message": "bare", "exit_code": 1}


def test_run_cli_records_an_interrupted_run(tmp_path, capsys):
    configure_logging("demo", "INFO", tmp_path / "cli.log", json_logs=True)

    def interrupted() -> int:
        raise KeyboardInterrupt

    assert run_cli(interrupted) == 0
    assert "Interrupted" in capsys.readouterr().err
    record = _finish_records(tmp_path / "cli.log")[-1]
    assert record["run_outcome"] == "interrupted"


def test_run_cli_records_a_non_zero_return_as_an_error(tmp_path):
    configure_logging("demo", "INFO", tmp_path / "cli.log", json_logs=True)
    assert run_cli(lambda: 1) == 1
    assert _finish_records(tmp_path / "cli.log")[-1]["run_outcome"] == "error"


# --- definition of done: JSON works in every project --------------------
@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_json_logs_are_parseable_in_every_project(name, script, tmp_path):
    log_dir = tmp_path / "logs"
    extra = [str(tmp_path / "work")] if name == "organizer" else []
    if extra:
        (tmp_path / "work").mkdir()
        (tmp_path / "work" / "note.txt").write_text("hello", encoding="utf-8")

    result = _run_cli(script, *extra, "--log-level", "DEBUG", "--json-logs", log_dir=log_dir)

    assert result.returncode == 0, result.stderr
    records = _read_json_lines(log_dir / f"{name}.log")
    assert len(records) >= 2
    assert {record["project"] for record in records} == {name}
    assert {record["run_id"] for record in records} == {records[0]["run_id"]}
    assert records[0]["fields"]["event"] == "run_start"
    assert records[-1]["fields"]["event"] == "run_finish"
    assert records[-1]["run_outcome"] == "success"
    assert records[-1]["duration_ms"] >= 0
    assert all(record["duration_ms"] >= 0 for record in records)
    assert all(
        record["level"] in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} for record in records
    )


@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_help_documents_the_structured_flags(name, script, tmp_path):
    result = _run_cli(script, "--help", log_dir=tmp_path / "logs")
    assert result.returncode == 0
    assert "--json-logs" in result.stdout
    assert "--run-id" in result.stdout


@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_run_id_flag_is_stamped_on_every_record(name, script, tmp_path):
    log_dir = tmp_path / "logs"
    extra = [str(tmp_path / "work")] if name == "organizer" else []
    if extra:
        (tmp_path / "work").mkdir()

    result = _run_cli(
        script,
        *extra,
        "--log-level",
        "INFO",
        "--json-logs",
        "--run-id",
        "cron-999",
        log_dir=log_dir,
    )

    assert result.returncode == 0, result.stderr
    records = _read_json_lines(log_dir / f"{name}.log")
    assert {record["run_id"] for record in records} == {"cron-999"}
