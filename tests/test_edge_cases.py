"""Regression tests for the shared edge-case handlers (Day 5).

Each test pins one of the failure modes the sweep closes: a closed or
interrupted input stream (``Ctrl-D``/``Ctrl-C``), an unreadable directory or
input file, an oversized input file, a name collision that cannot be resolved,
and a broken output pipe. The definition of done is that every case ends in a
clean exit or a single actionable error — never an unhandled traceback.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import chatbot
import edge_cases
import portfolio
import words
from cli_contract import ExitCode, run_cli
from edge_cases import (
    MAX_INPUT_FILE_BYTES,
    InputClosed,
    check_file_size,
    guard_broken_pipe,
    iter_files,
    safe_input,
)
from organizer import FileOrganizer, find_duplicates

ROOT = Path(__file__).resolve().parents[1]

#: True when the process can bypass filesystem permission bits (root).
_PRIVILEGED = hasattr(os, "geteuid") and os.geteuid() == 0

#: Permission bits are not enforceable on Windows.
requires_permissions = pytest.mark.skipif(
    _PRIVILEGED or sys.platform == "win32",
    reason="filesystem permission bits are not enforceable here",
)


def _run_cli(script: str, *args: str) -> subprocess.CompletedProcess:
    """Run a CLI script in a subprocess with a closed (EOF) stdin."""
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=str(ROOT),
        input="",
        capture_output=True,
        text=True,
        timeout=60,
    )


def _reject_oversized(path, *, max_bytes=0, field="file"):
    """Stand-in for :func:`edge_cases.check_file_size` that always rejects."""
    raise ValueError(f"{field} is too large")


# --- safe_input --------------------------------------------------------
def test_safe_input_returns_typed_line(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "hello")
    assert safe_input("> ") == "hello"


def test_safe_input_eof_without_default_raises(monkeypatch):
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(InputClosed):
        safe_input("> ")


def test_safe_input_eof_with_default_returns_default(monkeypatch):
    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert safe_input("> ", default="fallback") == "fallback"


def test_safe_input_ctrl_c_raises_input_closed(monkeypatch):
    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    with pytest.raises(InputClosed):
        safe_input("> ")


# --- check_file_size ---------------------------------------------------
def test_check_file_size_returns_size(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("12345", encoding="utf-8")
    assert check_file_size(path) == 5


def test_check_file_size_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        check_file_size(tmp_path / "nope.txt")


def test_check_file_size_rejects_oversized(tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("0123456789", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        check_file_size(path, max_bytes=4)
    assert "too large" in str(excinfo.value)


def test_default_input_limit_is_positive():
    assert MAX_INPUT_FILE_BYTES > 0


# --- iter_files --------------------------------------------------------
def test_iter_files_sorted_and_files_only(tmp_path):
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "A.txt").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    assert [p.name for p in iter_files(tmp_path)] == ["A.txt", "b.txt"]


def test_iter_files_empty_directory(tmp_path):
    assert iter_files(tmp_path) == []


def test_iter_files_not_a_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        iter_files(tmp_path / "nope")


@requires_permissions
def test_iter_files_permission_denied(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError) as excinfo:
            iter_files(locked)
        assert "permission denied" in str(excinfo.value)
    finally:
        locked.chmod(0o700)


# --- guard_broken_pipe -------------------------------------------------
def test_guard_broken_pipe_redirects_stdout(monkeypatch):
    seen: dict = {}

    def fake_open(path, flags):
        seen["path"] = path
        return 42

    def fake_dup2(src, dst):
        seen["src"] = src
        seen["dst"] = dst

    class FakeStdout:
        def fileno(self):
            return 7

    monkeypatch.setattr(edge_cases.os, "open", fake_open)
    monkeypatch.setattr(edge_cases.os, "dup2", fake_dup2)
    monkeypatch.setattr(edge_cases.sys, "stdout", FakeStdout())

    guard_broken_pipe()

    assert seen == {"path": os.devnull, "src": 42, "dst": 7}


def test_guard_broken_pipe_swallows_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no /dev/null here")

    monkeypatch.setattr(edge_cases.os, "open", boom)
    guard_broken_pipe()  # must not raise


# --- run_cli integration ----------------------------------------------
def test_run_cli_maps_broken_pipe_to_success(monkeypatch):
    monkeypatch.setattr("cli_contract.guard_broken_pipe", lambda: None)

    def boom() -> int:
        raise BrokenPipeError("consumer went away")

    assert run_cli(boom) == int(ExitCode.OK)


def test_run_cli_maps_keyboard_interrupt_to_success(capsys):
    def boom() -> int:
        raise KeyboardInterrupt

    assert run_cli(boom) == int(ExitCode.OK)


def test_run_cli_broken_pipe_end_to_end(tmp_path):
    script = tmp_path / "pipe_cli.py"
    script.write_text(
        textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from cli_contract import run_cli

            def boom():
                raise BrokenPipeError("closed")

            raise SystemExit(run_cli(boom))
            """),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == int(ExitCode.OK)
    assert "Traceback" not in result.stderr


# --- oversized input wiring -------------------------------------------
def test_load_words_surfaces_oversized(tmp_path, monkeypatch):
    path = tmp_path / "words.txt"
    path.write_text("hello\n", encoding="utf-8")
    monkeypatch.setattr(words, "check_file_size", _reject_oversized)
    with pytest.raises(ValueError):
        words.load_words_from_file(path)


def test_load_prices_surfaces_oversized(tmp_path, monkeypatch):
    path = tmp_path / "prices.csv"
    path.write_text("symbol,price\nAAPL,1\n", encoding="utf-8")
    monkeypatch.setattr(portfolio, "check_file_size", _reject_oversized)
    with pytest.raises(ValueError):
        portfolio.load_prices_from_csv(path)


def test_load_symbols_surfaces_oversized(tmp_path, monkeypatch):
    path = tmp_path / "constituents.csv"
    path.write_text("Symbol,Security\nAAPL,Apple\n", encoding="utf-8")
    monkeypatch.setattr(portfolio, "check_file_size", _reject_oversized)
    with pytest.raises(ValueError):
        portfolio.load_symbols_from_csv(path)


def test_intent_loader_surfaces_oversized(tmp_path, monkeypatch):
    path = tmp_path / "intents.json"
    path.write_text(json.dumps({"intents": [{"name": "x", "patterns": ["hi"]}]}), encoding="utf-8")
    monkeypatch.setattr(chatbot, "check_file_size", _reject_oversized)
    with pytest.raises(ValueError):
        chatbot.IntentEngine.from_json(path)


def test_faq_loader_surfaces_oversized(tmp_path, monkeypatch):
    path = tmp_path / "faq.json"
    path.write_text(
        json.dumps({"faqs": [{"question": "what hours", "answer": "9-5"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(chatbot, "check_file_size", _reject_oversized)
    with pytest.raises(ValueError):
        chatbot.IntentEngine.from_faq_json(path)


# --- permission-denied wiring -----------------------------------------
@requires_permissions
def test_load_words_permission_denied(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("hello\n", encoding="utf-8")
    path.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            words.load_words_from_file(path)
    finally:
        path.chmod(0o600)


@requires_permissions
def test_load_prices_permission_denied(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("symbol,price\nAAPL,1\n", encoding="utf-8")
    path.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            portfolio.load_prices_from_csv(path)
    finally:
        path.chmod(0o600)


@requires_permissions
def test_intent_loader_permission_denied(tmp_path):
    path = tmp_path / "intents.json"
    path.write_text(json.dumps({"intents": [{"name": "x", "patterns": ["hi"]}]}), encoding="utf-8")
    path.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            chatbot.IntentEngine.from_json(path)
    finally:
        path.chmod(0o600)


@requires_permissions
def test_organize_permission_denied_directory(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            FileOrganizer().organize(locked)
    finally:
        locked.chmod(0o700)


@requires_permissions
def test_find_duplicates_permission_denied_directory(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            find_duplicates(locked)
    finally:
        locked.chmod(0o700)


# --- CLI-level regressions --------------------------------------------
def test_hangman_eof_exits_cleanly():
    result = _run_cli("projects/01_hangman_game/main.py")
    assert result.returncode == int(ExitCode.OK)
    assert "Traceback" not in result.stderr


def test_hangman_categories_eof_exits_cleanly():
    result = _run_cli("projects/01_hangman_game/main.py", "--categories")
    assert result.returncode == int(ExitCode.OK)
    assert "Traceback" not in result.stderr


def test_assistant_eof_exits_cleanly():
    result = _run_cli("projects/04_terminal_assistant/main.py", "--no-log")
    assert result.returncode == int(ExitCode.OK)
    assert "Traceback" not in result.stderr


def test_portfolio_empty_input_exits_cleanly():
    result = _run_cli("projects/02_portfolio_tracker/main.py")
    assert result.returncode == int(ExitCode.OK)
    assert "Traceback" not in result.stderr


def test_organizer_empty_directory_exits_cleanly(tmp_path):
    result = _run_cli("projects/03_file_organizer_automation/main.py", str(tmp_path))
    assert result.returncode == int(ExitCode.OK)
    assert "No files found to organise" in result.stdout
