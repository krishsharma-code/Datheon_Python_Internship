"""Property-based tests for the validators, loaders and text pipeline (Day 17).

Days 1–16 proved correctness with hand-picked examples: a bad symbol, a BOM
file, a CRLF file, an over-long name. Examples show that the cases we thought
of work; they say nothing about the cases we did not think of. Day 17 states
the *rules* and lets Hypothesis hunt for a counter-example:

* **validators** — strict mode is a subset of lenient mode; every accepted
  symbol round-trips through the documented shape; every rejected value raises
  :class:`errors.ValidationError` and nothing else; bounds are inclusive.
* **dataset loader** — decoding is lossless across the encoding chain, a BOM is
  stripped exactly once, newline detection matches the style that was written,
  and column matching is case-insensitive.
* **assistant text pipeline** — keyword extraction is deduplicated, ordered,
  lower-cased, stop-word-filtered and monotone in the length floor;
  :func:`chatbot.normalize` is idempotent.

The strategies live in :mod:`tests.strategies`; the profile (and therefore the
seed) is chosen with ``HYPOTHESIS_PROFILE`` — CI runs the ``ci`` profile, which
derandomises the seed so a failure is reproducible.
"""

from __future__ import annotations

import re
import string
from datetime import date
from pathlib import Path
from typing import Callable, List

import pytest

pytest.importorskip("hypothesis", reason="the Day 17 property suite needs hypothesis")

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from tests import strategies  # noqa: E402
from chatbot import STOPWORDS, _keywords, normalize  # noqa: E402
from dataset_loader import (  # noqa: E402
    DEFAULT_ENCODINGS,
    DatasetFormatError,
    detect_newline,
    read_text_robust,
    require_columns,
    strip_bom,
)
from errors import ValidationError  # noqa: E402
from validators import (  # noqa: E402
    MAX_PRICE,
    MAX_SYMBOL_LENGTH,
    SYMBOL_PATTERN,
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

#: Shape the strict-mode number rules accept, restated here so the tests do not
#: simply re-import the implementation they are checking.
PLAIN_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


# --- symbols -----------------------------------------------------------
@given(symbol=strategies.symbol_texts())
def test_strict_symbols_round_trip_through_the_documented_shape(symbol: str) -> None:
    """A generated well-formed ticker is accepted, upper-cased and unchanged."""
    result = validate_symbol(symbol, strict=True)
    assert result == symbol.upper()
    assert SYMBOL_PATTERN.fullmatch(result) is not None
    assert len(result) <= MAX_SYMBOL_LENGTH


@given(text=strategies.names())
def test_lenient_symbols_trim_and_upper_case_any_non_blank_text(text: str) -> None:
    """Lenient mode normalises rather than rejects, for every non-blank input."""
    if not text.strip():
        with pytest.raises(ValidationError):
            validate_symbol(text)
        return
    assert validate_symbol(f"  {text}  ") == text.strip().upper()
    assert validate_symbol(text) == validate_symbol(f" {text} ")


@given(text=strategies.names())
def test_strict_symbol_acceptance_implies_lenient_acceptance(text: str) -> None:
    """Strict mode only ever narrows what lenient mode already accepts."""
    try:
        strict_value = validate_symbol(text, strict=True)
    except ValidationError:
        return
    assert validate_symbol(text) == strict_value


@given(blank=strategies.blank_texts())
def test_blank_text_is_rejected_with_an_actionable_hint(blank: str) -> None:
    """Whitespace-only input fails every text validator, with a hint attached."""
    for call in (validate_symbol, validate_name, validate_letter):
        with pytest.raises(ValidationError) as excinfo:
            call(blank)
        assert excinfo.value.hint, f"{call.__name__} rejected a blank value without a hint"


# --- numbers -----------------------------------------------------------
@given(price=strategies.price_values())
def test_prices_inside_the_window_are_accepted_verbatim(price: float) -> None:
    """Every finite price in ``[0, MAX_PRICE]`` survives both modes unchanged."""
    assert validate_price(price, strict=True) == price
    assert validate_price(repr(price), strict=True) == price
    assert validate_price(price) == price


def test_the_price_cap_itself_is_inclusive() -> None:
    """``MAX_PRICE`` is inside the strict window; one ulp above is not."""
    assert validate_price(MAX_PRICE, strict=True) == MAX_PRICE
    with pytest.raises(ValidationError):
        validate_price(MAX_PRICE * 2, strict=True)


@given(price=strategies.oversize_prices())
def test_prices_above_the_cap_need_strict_mode_to_fail(price: float) -> None:
    """The upper bound is a strict-mode rule, not a lenient one."""
    with pytest.raises(ValidationError):
        validate_price(price, strict=True)
    assert validate_price(price) == price


@given(price=strategies.quantity_values())
def test_negative_numbers_are_rejected_by_price_and_quantity(price: float) -> None:
    """A negative price or quantity is a data error in either mode."""
    with pytest.raises(ValidationError):
        validate_price(-price)
    with pytest.raises(ValidationError):
        validate_quantity(-price, allow_zero=True)


@given(quantity=strategies.quantity_values())
def test_quantities_are_positive_and_round_trip(quantity: float) -> None:
    """A positive quantity is accepted as-is, and zero is refused by default."""
    assert validate_quantity(quantity) == quantity
    assert validate_quantity(repr(quantity), strict=True) == quantity
    assert validate_quantity("0", allow_zero=True) == 0
    with pytest.raises(ValidationError):
        validate_quantity(0)


@given(value=strategies.whole_numbers())
def test_integer_bounds_are_inclusive(value: int) -> None:
    """``minimum`` and ``maximum`` include their own boundary values."""
    assert validate_int(value, minimum=value, maximum=value) == value
    assert validate_int(str(value), strict=True) == value
    with pytest.raises(ValidationError):
        validate_int(value, minimum=value + 1)
    with pytest.raises(ValidationError):
        validate_int(value, maximum=value - 1)


@given(text=strategies.names())
def test_strict_number_rules_reject_non_plain_literals(text: str) -> None:
    """Anything strict mode accepts is a plain literal, and lenient mode agrees."""
    try:
        strict_value = validate_price(text, strict=True)
    except ValidationError:
        return
    assert PLAIN_NUMBER.fullmatch(text.strip()) is not None
    assert validate_price(text) == strict_value


@given(literal=strategies.plain_number_texts())
def test_plain_number_literals_parse_back_to_their_own_value(literal: str) -> None:
    """The generated literals really are plain, so the domain is not vacuous."""
    assert PLAIN_NUMBER.fullmatch(literal) is not None
    assert validate_price(literal, strict=True) == float(literal)


# --- letters, names, messages, dates, paths ----------------------------
@given(letter=strategies.letters())
def test_letters_round_trip_in_lower_case(letter: str) -> None:
    """A single ASCII letter is accepted in either case and returned lower-cased."""
    assert validate_letter(letter, strict=True) == letter.lower()
    assert validate_letter(letter.upper(), strict=True) == letter.lower()
    with pytest.raises(ValidationError):
        validate_letter(letter + letter, strict=True)


@given(name=strategies.names())
def test_strict_names_enforce_the_length_budget(name: str) -> None:
    """Names are accepted exactly when they are non-blank and within 60 chars."""
    stripped = name.strip()
    if not stripped or len(stripped) > 60:
        with pytest.raises(ValidationError):
            validate_name(stripped, strict=True)
    else:
        assert validate_name(stripped, strict=True) == stripped


@given(message=strategies.messages())
def test_strict_messages_enforce_the_length_budget(message: str) -> None:
    """Messages are accepted exactly when non-blank and within 500 characters."""
    stripped = message.strip()
    if not stripped or len(stripped) > 500:
        with pytest.raises(ValidationError):
            validate_message(stripped, strict=True)
    else:
        assert validate_message(stripped, strict=True) == stripped


@given(day=strategies.past_dates())
def test_iso_dates_round_trip(day: date) -> None:
    """A past date survives both its ``date`` form and its ISO spelling."""
    assert validate_date(day) == day
    assert validate_date(day.isoformat()) == day
    assert validate_date(day.isoformat(), strict=True) == day


@given(day=strategies.future_dates())
def test_future_dates_need_the_explicit_opt_in(day: date) -> None:
    """Dates far ahead are rejected by default and accepted on request."""
    with pytest.raises(ValidationError):
        validate_date(day)
    assert validate_date(day, allow_future=True) == day


@given(name=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8))
def test_existing_files_validate_as_files(name: str, tmp_path: Path) -> None:
    """A real file passes ``must_be_file`` in strict mode and comes back as a Path."""
    directory = tmp_path / "files"
    directory.mkdir(exist_ok=True)
    target = directory / name
    target.write_text("payload", encoding="utf-8")
    resolved = validate_path(target, strict=True, must_be_file=True)
    assert isinstance(resolved, Path)
    assert resolved == target
    assert validate_path(target, must_exist=True) == target


@given(name=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8))
def test_absent_paths_fail_must_exist(name: str, tmp_path: Path) -> None:
    """A path that was never created fails ``must_exist`` instead of passing."""
    target = tmp_path / "absent" / name
    with pytest.raises(ValidationError):
        validate_path(target, must_exist=True)


@given(text=strategies.unicode_texts())
def test_text_validators_never_leak_a_foreign_exception(text: str) -> None:
    """Arbitrary input either validates or raises ``ValidationError`` — nothing else."""
    calls: List[Callable[[], object]] = [
        lambda: validate_symbol(text),
        lambda: validate_symbol(text, strict=True),
        lambda: validate_quantity(text),
        lambda: validate_price(text, strict=True),
        lambda: validate_int(text),
        lambda: validate_letter(text),
        lambda: validate_name(text, strict=True),
        lambda: validate_message(text, strict=True),
        lambda: validate_date(text),
    ]
    assert len(calls) == 9
    for call in calls:
        try:
            call()
        except ValidationError:
            continue


# --- dataset loader ----------------------------------------------------
@given(text=strategies.unicode_texts())
def test_utf8_decoding_is_lossless_and_strips_one_bom(text: str, tmp_path: Path) -> None:
    """A UTF-8 file decodes to its own text, minus at most one leading BOM."""
    path = strategies.write_dataset(tmp_path / "data.txt", text)
    source = read_text_robust(path)
    assert source.text == strip_bom(text)
    assert source.had_bom == text.startswith("\ufeff")
    assert source.encoding in ("utf-8-sig", "utf-8")
    assert source.path == path


@given(text=strategies.unicode_texts())
def test_strip_bom_removes_at_most_one_leading_marker(text: str) -> None:
    """``strip_bom`` is prefix-safe and idempotent for arbitrary text."""
    stripped = strip_bom(text)
    assert not stripped.startswith("\ufeff")
    assert stripped == (text[1:] if text.startswith("\ufeff") else text)
    assert strip_bom(stripped) == stripped


@given(lines=strategies.documents(), style=strategies.newline_styles())
def test_newline_detection_matches_the_style_that_was_written(lines: List[str], style: str) -> None:
    """Every recognised line-ending style is reported as itself."""
    text = strategies.NEWLINE_BY_STYLE[style].join(lines)
    assert detect_newline(text) == style


@given(first=strategies.line_texts(), second=strategies.line_texts(), third=strategies.line_texts())
def test_mixed_line_endings_are_reported_as_mixed(first: str, second: str, third: str) -> None:
    """Two different separators in one document are neither of them alone."""
    assert detect_newline(f"{first}\r\n{second}\n{third}") == "mixed"
    assert detect_newline(f"{first}\r{second}\r\n{third}") == "mixed"


@given(lines=strategies.documents())
def test_bom_and_crlf_survive_the_round_trip(lines: List[str], tmp_path: Path) -> None:
    """A Windows-authored, BOM-prefixed file loads with its text intact."""
    path = strategies.write_document(tmp_path / "data.csv", lines, newline="crlf", bom=True)
    source = read_text_robust(path)
    assert source.had_bom is True
    assert source.newline == "crlf"
    assert source.text == "\r\n".join(lines)
    assert source.text.startswith(lines[0])


@given(text=strategies.latin1_texts())
def test_legacy_encoded_files_decode_losslessly(text: str, tmp_path: Path) -> None:
    """The cp1252/latin-1 fallback chain returns the original characters."""
    path = strategies.write_dataset(tmp_path / "legacy.txt", text, encoding="latin-1")
    source = read_text_robust(path)
    assert source.text == text
    assert source.encoding in DEFAULT_ENCODINGS
    assert source.had_bom is False


@given(headers=strategies.headers(), index=st.integers(min_value=0, max_value=8))
def test_required_columns_match_case_insensitively(headers: List[str], index: int) -> None:
    """A column is found by its lower-cased, stripped spelling and returned as written."""
    chosen = headers[index % len(headers)]
    assert require_columns(headers, [chosen.lower().strip()], field="col") == chosen


@given(headers=strategies.headers(), missing=strategies.header_texts())
def test_absent_columns_raise_a_dataset_format_error(headers: List[str], missing: str) -> None:
    """A candidate that is not present fails loudly instead of silently."""
    if missing.lower().strip() in {header.lower().strip() for header in headers}:
        return
    with pytest.raises(DatasetFormatError) as excinfo:
        require_columns(headers, [missing], field="col")
    assert "missing required column" in str(excinfo.value)
    assert excinfo.value.hint


# --- assistant text pipeline -------------------------------------------
@given(text=strategies.unicode_texts(), min_length=st.integers(min_value=1, max_value=8))
def test_keywords_are_lower_case_unique_and_ordered(text: str, min_length: int) -> None:
    """Extraction keeps first-seen order and drops repeats, short tokens and stop words."""
    keywords = _keywords(text, min_length=min_length)
    expected: List[str] = []
    for token in strategies.keyword_tokens(text):
        if len(token) < min_length or token in STOPWORDS or token in expected:
            continue
        expected.append(token)
    assert keywords == expected
    assert len(keywords) == len(set(keywords))
    assert all(keyword == keyword.lower() for keyword in keywords)
    assert all(re.fullmatch(r"[a-z0-9]+", keyword) for keyword in keywords)
    assert strategies.is_subsequence(keywords, strategies.keyword_tokens(text))


@given(text=strategies.unicode_texts(), floor=st.integers(min_value=1, max_value=4))
def test_raising_the_length_floor_only_removes_keywords(text: str, floor: int) -> None:
    """A stricter length floor yields a subset of the looser run."""
    wider = set(_keywords(text, min_length=floor))
    narrower = set(_keywords(text, min_length=floor + 1))
    assert narrower <= wider


@given(text=strategies.unicode_texts())
def test_normalize_is_idempotent_and_collapses_whitespace(text: str) -> None:
    """Normalising twice changes nothing, and the result is single-spaced lowercase."""
    once = normalize(text)
    assert normalize(once) == once
    assert once == once.strip()
    assert once == once.lower()
    assert "  " not in once
    assert "\t" not in once and "\n" not in once
