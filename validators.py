"""Strict, reusable input validators shared by the five Datheon CLIs.

Day 4 of the internship progression hardens every tool's input boundary: bad
input is rejected *early*, with a single, actionable message, and the process
exits with :data:`cli_contract.ExitCode.DATA_ERROR` (``1``). The validators in
this module are pure functions — they never print and never mutate global
state — so they can be unit tested directly and reused by both the interactive
shells and the pure cores.

Every validator offers two modes:

* **lenient** (the default) — coerce whatever can be coerced into a sane value:
  trim whitespace, upper-case ticker symbols, parse ``str`` numbers; and
* **strict** (``--strict`` on the command line) — additionally enforce the
  documented *shape* of the value (a well-formed ticker, a plain finite
  number, an existing path of the right kind, an ASCII letter, a non-future
  date) and refuse anything ambiguous.

Failures raise :class:`ValidationError`, a :class:`ValueError` subclass, so
:func:`cli_contract.run_cli` already maps them onto exit code ``1``. Messages
name the offending field, quote the offending value, and (where useful) show a
concrete example of valid input.
"""

from __future__ import annotations

import argparse
import math
import re
from datetime import date
from pathlib import Path

__all__ = [
    "MAX_PRICE",
    "MAX_SYMBOL_LENGTH",
    "SYMBOL_PATTERN",
    "ValidationError",
    "add_strict_flag",
    "validate_date",
    "validate_int",
    "validate_letter",
    "validate_message",
    "validate_name",
    "validate_path",
    "validate_price",
    "validate_quantity",
    "validate_symbol",
]

#: Maximum length of a ticker symbol accepted in strict mode.
MAX_SYMBOL_LENGTH: int = 15

#: Shape of a ticker symbol accepted in strict mode (e.g. ``AAPL``,
#: ``RELIANCE.NS``, ``BRK-B``).
SYMBOL_PATTERN = re.compile(r"[A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+)*")

#: Shape of a plain decimal number accepted in strict mode — no thousands
#: separators, currency symbols, or surrounding words.
_PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")

#: Shape of a plain whole number accepted in strict mode.
_PLAIN_INT = re.compile(r"[+-]?\d+")

#: Upper bound applied to prices in strict mode (one billion).
MAX_PRICE: float = 1_000_000_000.0


class ValidationError(ValueError):
    """Raised when user input fails validation.

    Subclasses :class:`ValueError` so that :func:`cli_contract.run_cli` maps an
    uncaught instance onto exit code ``1`` — a user or data error — instead of
    leaking a traceback.
    """


def _fail(field: str, message: str, example: str | None = None) -> None:
    """Raise a :class:`ValidationError` with an actionable, single-line message.

    Args:
        field: Name of the offending field or flag.
        message: What is wrong with the value.
        example: Optional concrete example of valid input.

    Raises:
        ValidationError: Always.
    """
    text = f"{field}: {message}"
    if example:
        text += f" — e.g. {example}"
    raise ValidationError(text)


def _require_text(raw: object, field: str, example: str) -> str:
    """Return ``raw`` as a non-empty, stripped string or fail."""
    if raw is None:
        _fail(field, "a value is required", example)
    text = str(raw).strip()
    if not text:
        _fail(field, "must not be empty", example)
    return text


def _to_number(raw: object, field: str, example: str, *, strict: bool) -> float:
    """Coerce ``raw`` to a float, applying the strict plain-number rule."""
    text = _require_text(raw, field, example)
    if strict and not _PLAIN_NUMBER.fullmatch(text):
        _fail(field, f"{text!r} is not a plain number", example)
    try:
        return float(text)
    except ValueError:
        _fail(field, f"{text!r} is not a number", example)
        raise  # pragma: no cover - unreachable, keeps type checkers happy


def validate_symbol(raw: object, *, strict: bool = False, field: str = "symbol") -> str:
    """Validate a ticker symbol.

    Args:
        raw: The candidate symbol.
        strict: When ``True``, require a well-formed ticker (see
            :data:`SYMBOL_PATTERN`) of at most :data:`MAX_SYMBOL_LENGTH`
            characters.
        field: Field name used in the error message.

    Returns:
        The upper-cased, stripped symbol.

    Raises:
        ValidationError: If the symbol is empty, or — in strict mode — does not
            match the ticker shape.
    """
    text = _require_text(raw, field, "AAPL")
    symbol = text.upper()
    if strict:
        if len(symbol) > MAX_SYMBOL_LENGTH:
            _fail(field, f"{symbol!r} is longer than {MAX_SYMBOL_LENGTH} characters", "AAPL")
        if not SYMBOL_PATTERN.fullmatch(symbol):
            _fail(field, f"{text!r} is not a valid ticker symbol", "AAPL, RELIANCE.NS")
    return symbol


def validate_quantity(
    raw: object,
    *,
    strict: bool = False,
    allow_zero: bool = False,
    field: str = "quantity",
) -> float:
    """Validate a share/quantity count.

    Args:
        raw: The candidate quantity.
        strict: When ``True``, require a plain finite decimal literal.
        allow_zero: When ``True``, ``0`` is accepted; otherwise the value must
            be strictly positive.
        field: Field name used in the error message.

    Returns:
        The quantity as a ``float``.

    Raises:
        ValidationError: If the value is not a number, not finite, or outside
            the accepted range.
    """
    value = _to_number(raw, field, "10", strict=strict)
    if not math.isfinite(value):
        _fail(field, f"{value!r} is not a finite number", "10")
    if allow_zero:
        if value < 0:
            _fail(field, f"must not be negative, got {value:g}", "0")
    elif value <= 0:
        _fail(field, f"must be greater than zero, got {value:g}", "10")
    return value


def validate_price(
    raw: object,
    *,
    strict: bool = False,
    allow_zero: bool = True,
    field: str = "price",
) -> float:
    """Validate a monetary price.

    Args:
        raw: The candidate price.
        strict: When ``True``, require a plain finite decimal literal within
            ``[0, MAX_PRICE]``.
        allow_zero: When ``True`` (the default), ``0`` is accepted.
        field: Field name used in the error message.

    Returns:
        The price as a ``float``.

    Raises:
        ValidationError: If the value is not a number, not finite, negative, or
            (in strict mode) above :data:`MAX_PRICE`.
    """
    value = _to_number(raw, field, "150.00", strict=strict)
    if not math.isfinite(value):
        _fail(field, f"{value!r} is not a finite number", "150.00")
    if value < 0:
        _fail(field, f"must not be negative, got {value:g}", "150.00")
    if not allow_zero and value == 0:
        _fail(field, "must be greater than zero", "150.00")
    if strict and value > MAX_PRICE:
        _fail(field, f"{value:g} exceeds the maximum of {MAX_PRICE:g}", "150.00")
    return value


def validate_int(
    raw: object,
    *,
    strict: bool = False,
    minimum: int | None = None,
    maximum: int | None = None,
    field: str = "value",
) -> int:
    """Validate a whole-number count against optional bounds.

    Args:
        raw: The candidate integer.
        strict: When ``True``, require a plain integer literal (no ``_``
            separators or other Python-only forms).
        minimum: Inclusive lower bound, if any.
        maximum: Inclusive upper bound, if any.
        field: Field name used in the error message.

    Returns:
        The parsed integer.

    Raises:
        ValidationError: If the value is not a whole number or violates a
            bound.
    """
    text = _require_text(raw, field, "1")
    if strict and not _PLAIN_INT.fullmatch(text):
        _fail(field, f"{text!r} is not a plain whole number", "1")
    try:
        value = int(text)
    except ValueError:
        _fail(field, f"{text!r} is not a whole number", "1")
        raise  # pragma: no cover - unreachable, keeps type checkers happy
    if minimum is not None and value < minimum:
        _fail(field, f"must be at least {minimum}, got {value}", str(minimum))
    if maximum is not None and value > maximum:
        _fail(field, f"must be at most {maximum}, got {value}", str(maximum))
    return value


def validate_path(
    raw: object,
    *,
    strict: bool = False,
    must_exist: bool = False,
    must_be_file: bool = False,
    must_be_dir: bool = False,
    field: str = "path",
) -> Path:
    """Validate a filesystem path.

    Args:
        raw: The candidate path.
        strict: When ``True``, additionally require the parent directory to
            exist, so obvious typos are caught before any I/O.
        must_exist: Require the path to exist.
        must_be_file: Require the path to be an existing file.
        must_be_dir: Require the path to be an existing directory.
        field: Field name used in the error message.

    Returns:
        The expanded :class:`~pathlib.Path`.

    Raises:
        ValidationError: If the path is empty or violates one of the requested
            constraints.
    """
    path = Path(_require_text(raw, field, "/path/to/file")).expanduser()
    if must_exist and not path.exists():
        _fail(field, f"{path} does not exist", "/path/to/file")
    if must_be_file and not path.is_file():
        _fail(field, f"{path} is not a file", "/path/to/file.csv")
    if must_be_dir and not path.is_dir():
        _fail(field, f"{path} is not a directory", "/path/to/dir")
    if strict and not path.parent.exists():
        _fail(field, f"parent directory {path.parent} does not exist", "/existing/dir/file.txt")
    return path


def validate_letter(raw: object, *, strict: bool = False, field: str = "letter") -> str:
    """Validate a single-letter guess.

    Args:
        raw: The candidate letter.
        strict: When ``True``, require an ASCII ``a``–``z`` letter.
        field: Field name used in the error message.

    Returns:
        The lower-cased letter.

    Raises:
        ValidationError: If the value is not exactly one letter.
    """
    text = _require_text(raw, field, "a single letter, e.g. 'a'")
    letter = text.lower()
    if len(letter) != 1 or not letter.isalpha():
        _fail(field, f"{text!r} is not a single letter", "'a'")
    if strict and not ("a" <= letter <= "z"):
        _fail(field, f"{text!r} is not an ASCII letter (a-z)", "'a'")
    return letter


def validate_date(
    raw: object,
    *,
    strict: bool = False,
    allow_future: bool = False,
    field: str = "date",
) -> date:
    """Validate a calendar date.

    Args:
        raw: A :class:`datetime.date` or an ISO ``YYYY-MM-DD`` string.
        strict: Accepted for API symmetry; the shape rule applies in both
            modes.
        allow_future: When ``True``, dates after today are accepted.
        field: Field name used in the error message.

    Returns:
        The parsed :class:`datetime.date`.

    Raises:
        ValidationError: If the value is not an ISO date or is in the future
            (when ``allow_future`` is ``False``).
    """
    if isinstance(raw, date):
        value = raw
    else:
        text = _require_text(raw, field, "2026-10-23")
        try:
            value = date.fromisoformat(text)
        except ValueError:
            _fail(field, f"{text!r} is not an ISO date (YYYY-MM-DD)", "2026-10-23")
            raise  # pragma: no cover - unreachable, keeps type checkers happy
    if not allow_future and value > date.today():
        _fail(field, f"{value.isoformat()} is in the future", "today or earlier")
    return value


def validate_name(
    raw: object,
    *,
    strict: bool = False,
    max_length: int = 60,
    field: str = "name",
) -> str:
    """Validate a short free-text name such as a borrower.

    Args:
        raw: The candidate name.
        strict: When ``True``, enforce ``max_length`` and reject control
            characters.
        max_length: Maximum accepted length in strict mode.
        field: Field name used in the error message.

    Returns:
        The stripped name.

    Raises:
        ValidationError: If the name is empty, or — in strict mode — too long
            or contains control characters.
    """
    text = _require_text(raw, field, "Krish")
    if strict:
        if len(text) > max_length:
            _fail(field, f"is longer than {max_length} characters", "Krish")
        if any(ord(ch) < 32 for ch in text):
            _fail(field, f"{text!r} contains control characters", "Krish")
    return text


def validate_message(
    raw: object,
    *,
    strict: bool = False,
    max_length: int = 500,
    field: str = "message",
) -> str:
    """Validate a free-text chat message.

    Args:
        raw: The candidate message.
        strict: When ``True``, reject empty messages and messages longer than
            ``max_length``.
        max_length: Maximum accepted length in strict mode.
        field: Field name used in the error message.

    Returns:
        The stripped message (possibly empty in lenient mode).

    Raises:
        ValidationError: In strict mode, if the message is empty or too long.
    """
    text = "" if raw is None else str(raw).strip()
    if strict and not text:
        _fail(field, "must not be empty", "hello")
    if strict and len(text) > max_length:
        _fail(field, f"is longer than {max_length} characters", "hello")
    return text


def add_strict_flag(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add the shared ``--strict`` switch to an argument parser.

    Args:
        parser: The parser to extend.

    Returns:
        The same parser, for fluent use.
    """
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Enable strict validation: reject ambiguous or out-of-range input "
            "with a single actionable error (exit code 1)."
        ),
    )
    return parser
