"""Tests for the shared logging architecture (Day 8).

The contract under test:

* ``--log-level`` and ``--log-file`` exist on every CLI and default to *off*;
* configuring a level installs a console handler on ``stderr`` **and** a
  rotating file handler whose path is ``<DATHEON_LOG_DIR>/<prog>.log``;
* ``--log-level DEBUG`` writes a log file in *every* project (the Day 8
  definition of done);
* an unwritable log path degrades to console-only instead of aborting a run;
* handlers are owned by a :class:`LoggingSession` and are closed exactly once,
  so repeated configuration never duplicates log lines.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from cli_contract import run_cli
from errors import ConfigurationError
from logging_config import (
    DEFAULT_BACKUP_COUNT,
    DEFAULT_LOG_DIR_ENV,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_BYTES,
    FILE_FORMAT,
    LOG_LEVELS,
    LoggingSession,
    add_logging_flags,
    configure_logging,
    configure_logging_from_args,
    default_log_dir,
    default_log_file,
    installed_handlers,
    resolve_level,
    shutdown_logging,
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

#: The ISO-8601 timestamp the file handler prefixes to every record.
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}")


@pytest.fixture(autouse=True)
def _isolated_logging(monkeypatch):
    """Keep every test hermetic: no inherited log dir, no leaked handlers."""
    monkeypatch.delenv(DEFAULT_LOG_DIR_ENV, raising=False)
    yield
    shutdown_logging()


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
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=str(ROOT),
        input="",
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


# --- level resolution ---------------------------------------------------
@pytest.mark.parametrize("name", LOG_LEVELS)
def test_resolve_level_accepts_every_documented_name(name):
    assert resolve_level(name) == int(logging.getLevelName(name))


def test_resolve_level_is_case_insensitive_and_accepts_numbers():
    assert resolve_level("debug") == logging.DEBUG
    assert resolve_level("  Warning ") == logging.WARNING
    assert resolve_level(logging.INFO) == logging.INFO
    assert resolve_level("20") == 20


def test_resolve_level_defaults_to_warning():
    assert resolve_level(None) == int(logging.getLevelName(DEFAULT_LOG_LEVEL))


@pytest.mark.parametrize("bad", ["LOUD", "", 99, -1, True])
def test_resolve_level_rejects_unknown_values(bad):
    with pytest.raises(ConfigurationError) as excinfo:
        resolve_level(bad)
    assert "--log-level" in str(excinfo.value)
    assert excinfo.value.hint is not None
    assert "DEBUG" in excinfo.value.hint


# --- paths --------------------------------------------------------------
def test_default_log_dir_honours_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv(DEFAULT_LOG_DIR_ENV, str(tmp_path / "logs"))
    assert default_log_dir() == tmp_path / "logs"


def test_default_log_dir_falls_back_to_home(monkeypatch):
    monkeypatch.delenv(DEFAULT_LOG_DIR_ENV, raising=False)
    assert default_log_dir() == Path.home() / ".datheon" / "logs"


def test_default_log_file_is_named_after_the_program():
    assert default_log_file("organizer").name == "organizer.log"
    assert default_log_file("organizer").parent == default_log_dir()


# --- flags --------------------------------------------------------------
def test_flags_default_to_off_and_uppercase_the_level():
    import argparse

    parser = add_logging_flags(argparse.ArgumentParser())
    defaults = parser.parse_args([])
    assert defaults.log_level is None
    assert defaults.log_file is None
    assert parser.parse_args(["--log-level", "debug"]).log_level == "DEBUG"
    assert parser.parse_args(["--log-file", "x.log"]).log_file == Path("x.log")


def test_unknown_level_is_a_usage_error():
    import argparse

    parser = add_logging_flags(argparse.ArgumentParser())
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--log-level", "LOUD"])
    assert excinfo.value.code == 2


# --- configuration ------------------------------------------------------
def test_configuring_nothing_installs_no_handlers(tmp_path):
    session = configure_logging("demo", None, None)
    assert isinstance(session, LoggingSession)
    assert session.enabled is False
    assert session.file_enabled is False
    assert session.handlers == []
    assert session.logger.name == "demo"
    assert installed_handlers(session.logger) == []
    assert session.logger.propagate is False
    assert list(tmp_path.iterdir()) == []


def test_debug_level_writes_a_rotating_file(tmp_path, monkeypatch):
    monkeypatch.setenv(DEFAULT_LOG_DIR_ENV, str(tmp_path))
    stream = StringIO()
    session = configure_logging("demo", "DEBUG", stream=stream)
    try:
        session.logger.debug("hello from %s", "the test")
        assert session.file_enabled is True
        assert session.log_file == tmp_path / "demo.log"
        text = (tmp_path / "demo.log").read_text(encoding="utf-8")
        assert "hello from the test" in text
        assert "DEBUG" in text
        assert "demo" in text
        assert TIMESTAMP_RE.search(text)
        assert "test_logging.py:" in text
        assert session.file_error is None
    finally:
        session.close()


def test_file_line_format_names_the_source_location():
    assert "%(asctime)s" in FILE_FORMAT
    assert "%(filename)s:%(lineno)d" in FILE_FORMAT


def test_console_handler_respects_the_level():
    stream = StringIO()
    session = configure_logging("demo", "WARNING", stream=stream)
    try:
        session.logger.info("quiet")
        session.logger.warning("loud")
    finally:
        session.close()
    captured = stream.getvalue()
    assert "loud" in captured
    assert "quiet" not in captured


def test_console_handler_defaults_to_stderr(capsys):
    session = configure_logging("demo", "INFO")
    try:
        session.logger.info("to stderr")
    finally:
        session.close()
    captured = capsys.readouterr()
    assert "to stderr" in captured.err
    assert captured.out == ""


def test_log_file_alone_enables_logging_at_the_default_level(tmp_path):
    session = configure_logging("demo", None, tmp_path / "explicit.log")
    try:
        assert session.file_enabled is True
        assert session.level == int(logging.getLevelName(DEFAULT_LOG_LEVEL))
        assert session.log_file == tmp_path / "explicit.log"
        assert (tmp_path / "explicit.log").is_file()
    finally:
        session.close()


def test_nested_log_directories_are_created(tmp_path):
    target = tmp_path / "deep" / "nested" / "demo.log"
    session = configure_logging("demo", "INFO", target)
    try:
        session.logger.info("nested")
        assert target.is_file()
        assert "nested" in target.read_text(encoding="utf-8")
    finally:
        session.close()


def test_file_rotates_once_the_threshold_is_crossed(tmp_path):
    target = tmp_path / "rotate.log"
    session = configure_logging("demo", "DEBUG", target, max_bytes=400, backup_count=2)
    try:
        for index in range(200):
            session.logger.debug("record %03d %s", index, "x" * 40)
        assert target.is_file()
        assert (tmp_path / "rotate.log.1").is_file()
        assert len(list(tmp_path.glob("rotate.log.*"))) <= DEFAULT_BACKUP_COUNT + 1
    finally:
        session.close()


def test_unwritable_log_path_degrades_to_console_only(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    stream = StringIO()
    session = configure_logging("demo", "DEBUG", blocker / "demo.log", stream=stream)
    try:
        assert session.enabled is True
        assert session.file_enabled is False
        assert session.file_error
        assert "file logging disabled" in stream.getvalue()
        session.logger.debug("still working")
        assert "still working" in stream.getvalue()
    finally:
        session.close()


def test_reconfiguring_replaces_the_previous_handlers(tmp_path):
    first = configure_logging("demo", "INFO", tmp_path / "one.log")
    first_handlers = list(first.handlers)
    second = configure_logging("demo", "DEBUG", tmp_path / "two.log")
    try:
        assert len(first_handlers) == 2
        assert len(second.handlers) == 2
        # The first generation is detached; only the new pair remains attached.
        assert installed_handlers(second.logger) == second.handlers
        assert not any(handler in installed_handlers(second.logger) for handler in first_handlers)
    finally:
        second.close()


def test_configure_from_args_reads_the_namespace(tmp_path):
    import argparse

    parser = add_logging_flags(argparse.ArgumentParser())
    args = parser.parse_args(["--log-level", "info", "--log-file", str(tmp_path / "args.log")])
    session = configure_logging_from_args("demo", args)
    try:
        assert session.level == logging.INFO
        assert session.log_file == tmp_path / "args.log"
    finally:
        session.close()


def test_configure_from_args_tolerates_a_namespace_without_the_flags():
    import argparse

    session = configure_logging_from_args("demo", argparse.Namespace())
    assert session.enabled is False


def test_session_close_is_idempotent_and_works_as_a_context_manager(tmp_path):
    session = configure_logging("demo", "INFO", tmp_path / "ctx.log")
    owned = list(session.handlers)
    with session as active:
        assert active is session
        active.logger.info("inside")
    assert session.handlers == []
    assert not any(handler in installed_handlers(session.logger) for handler in owned)
    session.close()  # second close must not raise
    assert "inside" in (tmp_path / "ctx.log").read_text(encoding="utf-8")


def test_run_cli_closes_the_session(tmp_path):
    session = configure_logging("demo", "INFO", tmp_path / "cli.log")
    owned = list(session.handlers)
    assert run_cli(lambda: 0) == 0
    assert session.handlers == []
    assert not any(handler in installed_handlers(session.logger) for handler in owned)


# --- definition of done: every project logs ----------------------------
@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_log_level_debug_writes_a_log_file_in_every_project(name, script, tmp_path):
    log_dir = tmp_path / "logs"
    extra = [str(tmp_path / "work")] if name == "organizer" else []
    if extra:
        (tmp_path / "work").mkdir()
        (tmp_path / "work" / "note.txt").write_text("hello", encoding="utf-8")

    result = _run_cli(script, *extra, "--log-level", "DEBUG", log_dir=log_dir)

    assert result.returncode == 0, result.stderr
    log_file = log_dir / f"{name}.log"
    assert log_file.is_file()
    contents = log_file.read_text(encoding="utf-8")
    assert f"{name} 1.0.0 starting" in contents
    assert "DEBUG" in contents
    assert TIMESTAMP_RE.search(contents)


@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_clis_stay_silent_on_disk_without_the_flag(name, script, tmp_path):
    log_dir = tmp_path / "logs"
    extra = [str(tmp_path / "work")] if name == "organizer" else []
    if extra:
        (tmp_path / "work").mkdir()
        (tmp_path / "work" / "note.txt").write_text("hello", encoding="utf-8")

    result = _run_cli(script, *extra, log_dir=log_dir)

    assert result.returncode == 0, result.stderr
    assert not log_dir.exists()


@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_help_documents_the_logging_flags(name, script, tmp_path):
    result = _run_cli(script, "--help", log_dir=tmp_path / "logs")
    assert result.returncode == 0
    assert "--log-level" in result.stdout
    assert "--log-file" in result.stdout


@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_bad_log_level_is_a_usage_error(name, script, tmp_path):
    result = _run_cli(script, "--log-level", "LOUD", log_dir=tmp_path / "logs")
    assert result.returncode == 2


def test_max_bytes_default_is_one_mebibyte():
    assert DEFAULT_MAX_BYTES == 1_048_576
    assert DEFAULT_BACKUP_COUNT == 3
