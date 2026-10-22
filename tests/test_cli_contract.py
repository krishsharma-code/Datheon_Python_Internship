"""Tests for the shared command-line contract (Day 3).

Every one of the five CLIs must expose a consistent surface: ``--help`` and
``--version`` succeed with exit code ``0``, a malformed command line fails with
the usage exit code ``2``, and a user/data error fails with exit code ``1``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cli_contract import (
    EXIT_CODE_EPILOG,
    PROGRAM_VERSION,
    ExitCode,
    build_parser,
    run_cli,
    version_string,
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


def _run_cli(script: str, *args: str) -> subprocess.CompletedProcess:
    """Run a CLI script in a subprocess with a closed (EOF) stdin.

    Args:
        script: Path to the entry-point script, relative to the repo root.
        *args: Additional command-line arguments.

    Returns:
        The completed process, capturing stdout/stderr as text.
    """
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=str(ROOT),
        input="",
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- contract primitives ----------------------------------------------
def test_exit_code_values():
    assert ExitCode.OK == 0
    assert ExitCode.DATA_ERROR == 1
    assert ExitCode.USAGE_ERROR == 2


def test_version_string_format():
    assert version_string("hangman") == f"hangman {PROGRAM_VERSION}"


def test_build_parser_registers_version_and_epilog():
    parser = build_parser("demo", "A demo program.")
    assert parser.prog == "demo"
    assert parser.epilog == EXIT_CODE_EPILOG
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == int(ExitCode.OK)


def test_build_parser_unknown_option_is_usage_error():
    parser = build_parser("demo", "A demo program.")
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--nope"])
    assert excinfo.value.code == int(ExitCode.USAGE_ERROR)


def test_run_cli_maps_domain_errors_to_data_error(capsys):
    def boom() -> int:  # pragma: no cover - exercised via run_cli
        raise ValueError("bad input")

    assert run_cli(boom) == int(ExitCode.DATA_ERROR)
    assert "bad input" in capsys.readouterr().err


def test_run_cli_passes_through_success():
    assert run_cli(lambda: 0) == int(ExitCode.OK)


# --- per-CLI exit codes -------------------------------------------------
@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_version_exits_zero(name, script):
    result = _run_cli(script, "--version")
    assert result.returncode == int(ExitCode.OK)
    assert f"{name} {PROGRAM_VERSION}" in result.stdout


@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_help_exits_zero_and_documents_exit_codes(name, script):
    result = _run_cli(script, "--help")
    assert result.returncode == int(ExitCode.OK)
    assert "usage" in result.stdout.lower()
    assert "exit codes" in result.stdout.lower()


@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_unknown_option_is_usage_error(name, script):
    result = _run_cli(script, "--definitely-not-a-flag")
    assert result.returncode == int(ExitCode.USAGE_ERROR)


def test_hangman_missing_word_file_is_data_error():
    result = _run_cli(
        "projects/01_hangman_game/main.py",
        "--words-file",
        str(ROOT / "does-not-exist.txt"),
    )
    assert result.returncode == int(ExitCode.DATA_ERROR)


def test_organizer_missing_directory_is_data_error():
    result = _run_cli(
        "projects/03_file_organizer_automation/main.py",
        str(ROOT / "no-such-directory"),
    )
    assert result.returncode == int(ExitCode.DATA_ERROR)


def test_assistant_missing_intents_is_data_error():
    result = _run_cli(
        "projects/04_terminal_assistant/main.py",
        "--intents",
        str(ROOT / "no-such-intents.json"),
    )
    assert result.returncode == int(ExitCode.DATA_ERROR)


def test_portfolio_malformed_prices_is_data_error(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("name,value\nfoo,bar\n", encoding="utf-8")
    result = _run_cli(
        "projects/02_portfolio_tracker/main.py",
        "--prices",
        str(bad),
        "--demo",
    )
    assert result.returncode == int(ExitCode.DATA_ERROR)


def test_inventory_demo_exits_zero():
    result = _run_cli("projects/05_inventory_oop/main.py")
    assert result.returncode == int(ExitCode.OK)
    assert "Initial inventory" in result.stdout
