"""Tests for the shared input-validation layer (Day 4).

Covers every validator in :mod:`validators` — the lenient and strict modes, the
actionable error messages, and the boundary cases — plus the end-to-end
``--strict`` behaviour of all five CLIs (exit code ``1`` and a single clear
message).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from validators import (
    MAX_PRICE,
    MAX_SYMBOL_LENGTH,
    SYMBOL_PATTERN,
    ValidationError,
    add_strict_flag,
    validate_date,
    validate_int,
    validate_letter,
    validate_message,
    validate_name,
    validate_path,
    validate_price,
    validate_quantity,
    validate_symbol,
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

CONSTITUENTS = ROOT / "projects" / "02_portfolio_tracker" / "data" / "constituents.csv"


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


# --- ValidationError type ---------------------------------------------
def test_validation_error_is_value_error():
    assert issubclass(ValidationError, ValueError)


# --- validate_symbol ----------------------------------------------------
def test_symbol_lenient_trims_and_uppercases():
    assert validate_symbol("  aapl ") == "AAPL"


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_symbol_rejects_empty(bad):
    with pytest.raises(ValidationError):
        validate_symbol(bad)


def test_symbol_strict_accepts_valid_tickers():
    assert validate_symbol("aapl", strict=True) == "AAPL"
    assert validate_symbol("reliance.ns", strict=True) == "RELIANCE.NS"
    assert validate_symbol("brk-b", strict=True) == "BRK-B"


def test_symbol_strict_rejects_bad_shape():
    with pytest.raises(ValidationError):
        validate_symbol("9LIVES", strict=True)
    with pytest.raises(ValidationError):
        validate_symbol("AAPL US", strict=True)


def test_symbol_strict_rejects_overlong():
    with pytest.raises(ValidationError):
        validate_symbol("A" * (MAX_SYMBOL_LENGTH + 1), strict=True)


def test_symbol_pattern_matches_examples():
    assert SYMBOL_PATTERN.fullmatch("AAPL")
    assert not SYMBOL_PATTERN.fullmatch("aapl")


# --- validate_quantity --------------------------------------------------
def test_quantity_accepts_numbers():
    assert validate_quantity("10") == 10.0
    assert validate_quantity(2.5) == 2.5


@pytest.mark.parametrize("bad", [0, -1, "0", "-3"])
def test_quantity_must_be_positive(bad):
    with pytest.raises(ValidationError):
        validate_quantity(bad)


def test_quantity_allow_zero():
    assert validate_quantity(0, allow_zero=True) == 0.0
    with pytest.raises(ValidationError):
        validate_quantity(-1, allow_zero=True)


def test_quantity_rejects_non_number():
    with pytest.raises(ValidationError):
        validate_quantity("ten")


def test_quantity_rejects_non_finite():
    with pytest.raises(ValidationError):
        validate_quantity(float("nan"))


def test_quantity_strict_rejects_separators():
    with pytest.raises(ValidationError):
        validate_quantity("1,000", strict=True)


# --- validate_price -----------------------------------------------------
def test_price_accepts_zero_by_default():
    assert validate_price("0") == 0.0
    assert validate_price(150.0) == 150.0


def test_price_rejects_negative():
    with pytest.raises(ValidationError):
        validate_price(-0.01)


def test_price_allow_zero_false():
    with pytest.raises(ValidationError):
        validate_price(0, allow_zero=False)


def test_price_strict_rejects_currency_symbol():
    with pytest.raises(ValidationError):
        validate_price("₹150", strict=True)


def test_price_strict_rejects_above_max():
    with pytest.raises(ValidationError):
        validate_price(MAX_PRICE + 1, strict=True)


def test_price_message_names_field_and_value():
    with pytest.raises(ValidationError) as excinfo:
        validate_price("abc", field="buy price")
    assert "buy price" in str(excinfo.value)
    assert "abc" in str(excinfo.value)


# --- validate_int -------------------------------------------------------
def test_int_accepts_and_bounds():
    assert validate_int("4") == 4
    with pytest.raises(ValidationError):
        validate_int("0", minimum=1)
    with pytest.raises(ValidationError):
        validate_int("40", maximum=32)


def test_int_rejects_non_integer():
    with pytest.raises(ValidationError):
        validate_int("4.0")


def test_int_strict_rejects_underscores():
    with pytest.raises(ValidationError):
        validate_int("1_000", strict=True)
    assert validate_int("1000", strict=True) == 1000


# --- validate_path ------------------------------------------------------
def test_path_returns_path(tmp_path):
    assert validate_path(tmp_path) == tmp_path


def test_path_must_exist(tmp_path):
    with pytest.raises(ValidationError):
        validate_path(tmp_path / "nope", must_exist=True)


def test_path_must_be_file(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")
    assert validate_path(target, must_be_file=True) == target
    with pytest.raises(ValidationError):
        validate_path(tmp_path, must_be_file=True)


def test_path_must_be_dir(tmp_path):
    assert validate_path(tmp_path, must_be_dir=True) == tmp_path
    with pytest.raises(ValidationError):
        validate_path(tmp_path / "missing", must_be_dir=True)


def test_path_strict_requires_existing_parent(tmp_path):
    with pytest.raises(ValidationError):
        validate_path(tmp_path / "ghost" / "f.txt", strict=True)


# --- validate_letter ----------------------------------------------------
def test_letter_normalises():
    assert validate_letter(" A ") == "a"


@pytest.mark.parametrize("bad", ["", "ab", "1", None])
def test_letter_rejects_non_letters(bad):
    with pytest.raises(ValidationError):
        validate_letter(bad)


def test_letter_strict_rejects_non_ascii():
    assert validate_letter("é") == "é"
    with pytest.raises(ValidationError):
        validate_letter("é", strict=True)


# --- validate_date ------------------------------------------------------
def test_date_parses_iso():
    assert validate_date("2026-01-02") == date(2026, 1, 2)
    assert validate_date(date(2026, 1, 2)) == date(2026, 1, 2)


def test_date_rejects_bad_format():
    with pytest.raises(ValidationError):
        validate_date("02/01/2026")


def test_date_rejects_future():
    future = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        validate_date(future)
    assert validate_date(future, allow_future=True) == date.today() + timedelta(days=1)


# --- validate_name ------------------------------------------------------
def test_name_strips():
    assert validate_name("  Krish ") == "Krish"


def test_name_rejects_empty():
    with pytest.raises(ValidationError):
        validate_name("   ")


def test_name_strict_rejects_overlong_and_control():
    with pytest.raises(ValidationError):
        validate_name("x" * 61, strict=True)
    with pytest.raises(ValidationError):
        validate_name("a\x00b", strict=True)


# --- validate_message ---------------------------------------------------
def test_message_lenient_allows_empty():
    assert validate_message("") == ""
    assert validate_message(None) == ""


def test_message_strict_rejects_empty_and_overlong():
    with pytest.raises(ValidationError):
        validate_message("   ", strict=True)
    with pytest.raises(ValidationError):
        validate_message("x" * 501, strict=True)
    assert validate_message("hello", strict=True) == "hello"


# --- add_strict_flag ----------------------------------------------------
def test_add_strict_flag_registers_switch():
    import argparse

    parser = add_strict_flag(argparse.ArgumentParser())
    assert parser.parse_args(["--strict"]).strict is True
    assert parser.parse_args([]).strict is False


# --- CLI-level: --strict is advertised everywhere -----------------------
@pytest.mark.parametrize("name,script", CLIS, ids=CLI_IDS)
def test_strict_flag_in_help(name, script):
    result = _run_cli(script, "--help")
    assert result.returncode == 0
    assert "--strict" in result.stdout


# --- CLI-level: strict rejects bad input with exit code 1 ---------------
def test_hangman_strict_min_length_rejected():
    result = _run_cli("projects/01_hangman_game/main.py", "--min-length", "0")
    assert result.returncode == 1
    assert "--min-length" in result.stderr


def test_hangman_max_below_min_rejected():
    result = _run_cli("projects/01_hangman_game/main.py", "--min-length", "5", "--max-length", "3")
    assert result.returncode == 1
    assert "--max-length" in result.stderr


def test_organizer_strict_workers_rejected():
    result = _run_cli("projects/03_file_organizer_automation/main.py", "--workers", "0")
    assert result.returncode == 1
    assert "--workers" in result.stderr


def test_portfolio_strict_missing_prices_rejected():
    result = _run_cli(
        "projects/02_portfolio_tracker/main.py",
        "--prices",
        str(ROOT / "nope.csv"),
        "--demo",
        "--strict",
    )
    assert result.returncode == 1
    assert "--prices" in result.stderr


def test_portfolio_lenient_missing_prices_falls_back():
    result = _run_cli(
        "projects/02_portfolio_tracker/main.py",
        "--prices",
        str(ROOT / "nope.csv"),
        "--demo",
    )
    assert result.returncode == 0


def test_portfolio_strict_unknown_symbol_rejected():
    result = _run_cli(
        "projects/02_portfolio_tracker/main.py",
        "--demo",
        "--constituents",
        str(CONSTITUENTS),
        "--strict",
    )
    assert result.returncode == 1
    assert "RELIANCE.NS" in result.stderr


def test_assistant_strict_missing_intents_rejected():
    result = _run_cli(
        "projects/04_terminal_assistant/main.py",
        "--strict",
        "--intents",
        str(ROOT / "nope.json"),
    )
    assert result.returncode == 1
    assert "--intents" in result.stderr


def test_inventory_single_checkout_success():
    result = _run_cli("projects/05_inventory_oop/main.py", "--borrower", "Krish", "--days", "7")
    assert result.returncode == 0
    assert "Krish borrowed B001 for 7 days" in result.stdout


def test_inventory_borrower_empty_rejected():
    result = _run_cli("projects/05_inventory_oop/main.py", "--borrower", "  ")
    assert result.returncode == 1
    assert "--borrower" in result.stderr


def test_inventory_days_zero_rejected():
    result = _run_cli("projects/05_inventory_oop/main.py", "--borrower", "Krish", "--days", "0")
    assert result.returncode == 1
    assert "--days" in result.stderr


def test_inventory_future_borrow_date_rejected():
    result = _run_cli(
        "projects/05_inventory_oop/main.py",
        "--borrower",
        "Krish",
        "--borrow-date",
        "2099-01-01",
    )
    assert result.returncode == 1
    assert "--borrow-date" in result.stderr


def test_inventory_demo_still_exits_zero():
    result = _run_cli("projects/05_inventory_oop/main.py")
    assert result.returncode == 0
    assert "Initial inventory" in result.stdout
