"""Reusable Hypothesis strategies for the Datheon property suite (Day 17).

Day 17 replaces hand-picked examples with *properties*: statements that must
hold for every input in a domain, checked by Hypothesis against hundreds of
generated values per test. The generators live here rather than inside the test
module so that

* one definition of "a well-formed ticker" serves the validator tests, the
  loader tests and any future test that needs a symbol;
* the domains are expressed in terms of the production constants
  (:data:`validators.SYMBOL_PATTERN`, :data:`validators.MAX_PRICE`,
  :data:`validators.MAX_SYMBOL_LENGTH`) instead of duplicated magic numbers, so
  changing a bound changes the generator with it; and
* the fixture writers (:func:`write_dataset`, :func:`write_document`) keep the
  encoding / newline / BOM plumbing out of the tests, which then read as a
  statement of intent.

Every strategy here is a *pure function of its arguments* — none of them reads
the clock, the filesystem or the network — which is what lets the suite honour
the Day 16 isolation policy while still generating hundreds of cases per test.

Profiles are registered at import time and selected by the ``HYPOTHESIS_PROFILE``
environment variable:

===========  ==========================================================
Profile      Behaviour
===========  ==========================================================
``dev``      (default) 50 examples, random seed — fast local feedback.
``ci``       150 examples, ``derandomize=True`` — a **fixed seed**, so a red
             CI run is reproducible and a green one is not a coincidence.
``nightly``  1,000 examples, fixed seed — the deep soak.
===========  ==========================================================

``deadline`` is disabled and the function-scoped-fixture health check is
suppressed for every profile: the suite writes real files into ``tmp_path``
(which is function-scoped by pytest's design), and a per-example wall-clock
budget would make the verdict depend on how loaded the machine is.
"""

from __future__ import annotations

import os
import re
import string
from datetime import date
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st

from validators import MAX_PRICE, MAX_SYMBOL_LENGTH, SYMBOL_PATTERN

__all__ = [
    "PROFILE_NAMES",
    "active_profile",
    "blank_texts",
    "documents",
    "future_dates",
    "header_texts",
    "headers",
    "latin1_texts",
    "letters",
    "line_texts",
    "messages",
    "names",
    "newline_styles",
    "oversize_prices",
    "past_dates",
    "plain_number_texts",
    "price_values",
    "quantity_values",
    "symbol_texts",
    "unicode_texts",
    "whole_numbers",
    "write_dataset",
    "write_document",
]

#: Hypothesis profiles this repository defines.
PROFILE_NAMES: Tuple[str, ...] = ("dev", "ci", "nightly")

#: Characters that make up a dataset line: everything a CSV cell may hold
#: except a line break, so a constructed document has a known newline style.
LINE_ALPHABET: str = string.ascii_letters + string.digits + " ,.;:-_"

#: Printable ASCII, the alphabet used for "arbitrary user input" domains.
PRINTABLE: str = "".join(chr(code) for code in range(32, 127))

#: Latin-1 code points that ``cp1252`` maps identically to ``latin-1``, so a
#: legacy-encoded round trip is well defined.
_LATIN1_SAFE = st.characters(min_codepoint=0x20, max_codepoint=0x7E) | st.characters(
    min_codepoint=0xE0, max_codepoint=0xFF
)

#: Regular-expression source for a strict-mode ticker, taken from the validator
#: itself so the generator cannot drift from the rule it is generating for.
SYMBOL_REGEX: str = SYMBOL_PATTERN.pattern

#: The ``"\n"`` separator used by :func:`write_document` for each style name.
NEWLINE_BY_STYLE = {"lf": "\n", "crlf": "\r\n", "cr": "\r"}


def _common_settings(**overrides: object) -> settings:
    """Build a :class:`hypothesis.settings` with the shared, non-negotiable bits.

    Args:
        **overrides: Profile-specific settings to merge in.

    Returns:
        The composed settings object.
    """
    options: dict = {
        "deadline": None,
        "suppress_health_check": [
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    }
    options.update(overrides)
    return settings(**options)


settings.register_profile("dev", _common_settings(max_examples=50))
settings.register_profile("ci", _common_settings(max_examples=150, derandomize=True))
settings.register_profile("nightly", _common_settings(max_examples=1000, derandomize=True))


def active_profile() -> str:
    """Return the profile selected by ``HYPOTHESIS_PROFILE``.

    Returns:
        One of :data:`PROFILE_NAMES`; ``"dev"`` when the variable is unset or
        names an unknown profile.
    """
    requested = os.environ.get("HYPOTHESIS_PROFILE", "dev").strip().lower()
    return requested if requested in PROFILE_NAMES else "dev"


settings.load_profile(active_profile())


# --- text domains ------------------------------------------------------
def unicode_texts(max_size: int = 200) -> st.SearchStrategy[str]:
    """Return arbitrary text, including non-ASCII characters.

    Args:
        max_size: Maximum length of a generated string.

    Returns:
        A strategy for :class:`str`.
    """
    return st.text(max_size=max_size)


def blank_texts() -> st.SearchStrategy[str]:
    """Return strings that are empty once stripped.

    Returns:
        A strategy for whitespace-only (or empty) strings.
    """
    return st.text(alphabet=" \t\r\n\f\v", max_size=6)


def names() -> st.SearchStrategy[str]:
    """Return short free-text names built from printable ASCII.

    Returns:
        A strategy for :class:`str` of up to 120 printable characters.
    """
    return st.text(alphabet=PRINTABLE, max_size=120)


def messages() -> st.SearchStrategy[str]:
    """Return free-text chat messages, some longer than the 500-char limit.

    Returns:
        A strategy for :class:`str` of up to 800 characters.
    """
    return st.text(max_size=800)


def letters() -> st.SearchStrategy[str]:
    """Return a single ASCII letter, in either case.

    Returns:
        A strategy for a one-character :class:`str`.
    """
    return st.sampled_from(string.ascii_letters)


# --- numeric domains ---------------------------------------------------
def price_values() -> st.SearchStrategy[float]:
    """Return finite prices inside the strict-mode window ``[0, MAX_PRICE]``.

    Returns:
        A strategy for :class:`float`.
    """
    return st.floats(min_value=0.0, max_value=MAX_PRICE, allow_nan=False, allow_infinity=False)


def oversize_prices() -> st.SearchStrategy[float]:
    """Return finite prices above :data:`validators.MAX_PRICE`.

    Returns:
        A strategy for :class:`float`.
    """
    return st.floats(
        min_value=MAX_PRICE * 2.0,
        max_value=MAX_PRICE * 1000.0,
        allow_nan=False,
        allow_infinity=False,
    )


def quantity_values() -> st.SearchStrategy[float]:
    """Return finite, strictly positive quantities.

    Returns:
        A strategy for :class:`float`.
    """
    return st.floats(min_value=1e-6, max_value=1e9, allow_nan=False, allow_infinity=False)


def whole_numbers() -> st.SearchStrategy[int]:
    """Return integers across a wide, inclusive range.

    Returns:
        A strategy for :class:`int` in ``[-10**6, 10**6]``.
    """
    return st.integers(min_value=-(10**6), max_value=10**6)


def plain_number_texts() -> st.SearchStrategy[str]:
    """Return non-negative textual literals that strict mode accepts as "plain".

    ``repr`` of a finite float is always a plain decimal or exponent form, and
    integers stringify to plain digits, so both spellings satisfy the strict
    ``_PLAIN_NUMBER`` rule. The domain is kept non-negative because it is used
    against :func:`validators.validate_price`, which rejects a leading minus.

    Returns:
        A strategy for :class:`str` number literals in ``[0, MAX_PRICE]``.
    """
    return st.one_of(st.integers(min_value=0, max_value=10**6).map(str), price_values().map(repr))


# --- symbols, headers, dates ------------------------------------------
def symbol_texts() -> st.SearchStrategy[str]:
    """Return well-formed ticker symbols, at most ``MAX_SYMBOL_LENGTH`` long.

    Returns:
        A strategy for :class:`str` accepted by ``validate_symbol(strict=True)``.
    """
    return st.from_regex(SYMBOL_REGEX, fullmatch=True).filter(
        lambda value: len(value) <= MAX_SYMBOL_LENGTH
    )


def header_texts() -> st.SearchStrategy[str]:
    """Return a plausible CSV column header.

    Returns:
        A strategy for :class:`str` of 1–10 letters, spaces or underscores.
    """
    return st.text(alphabet=string.ascii_letters + " _", min_size=1, max_size=10)


def headers() -> st.SearchStrategy[List[str]]:
    """Return CSV headers that stay distinct once lower-cased and stripped.

    The uniqueness key mirrors how :func:`dataset_loader.require_columns`
    indexes the header row, so a generated list can never be ambiguous.

    Returns:
        A strategy for a non-empty list of header strings.
    """
    return st.lists(
        header_texts(),
        min_size=1,
        max_size=6,
        unique_by=lambda header: header.lower().strip(),
    )


def past_dates() -> st.SearchStrategy[date]:
    """Return dates comfortably in the past, without reading the clock.

    Returns:
        A strategy for :class:`datetime.date` in 1990–2020.
    """
    return st.dates(min_value=date(1990, 1, 1), max_value=date(2020, 12, 31))


def future_dates() -> st.SearchStrategy[date]:
    """Return dates far enough ahead to be future on any run date.

    Returns:
        A strategy for :class:`datetime.date` in 2090–2999.
    """
    return st.dates(min_value=date(2090, 1, 1), max_value=date(2999, 12, 31))


# --- documents ---------------------------------------------------------
def line_texts() -> st.SearchStrategy[str]:
    """Return a single line of dataset text, free of line-break characters.

    Returns:
        A strategy for :class:`str` of up to 20 characters.
    """
    return st.text(alphabet=LINE_ALPHABET, max_size=20)


def documents() -> st.SearchStrategy[List[str]]:
    """Return a list of lines that make up a small dataset file.

    Returns:
        A strategy for a list of 2–5 lines.
    """
    return st.lists(line_texts(), min_size=2, max_size=5)


def newline_styles() -> st.SearchStrategy[str]:
    """Return one of the line-ending style names the loader recognises.

    Returns:
        A strategy for ``"lf"``, ``"crlf"`` or ``"cr"``.
    """
    return st.sampled_from(sorted(NEWLINE_BY_STYLE))


def latin1_texts() -> st.SearchStrategy[str]:
    """Return text restricted to code points ``cp1252`` and ``latin-1`` agree on.

    Returns:
        A strategy for :class:`str` of up to 80 characters.
    """
    return st.text(alphabet=_LATIN1_SAFE, max_size=80)


# --- fixture writers ---------------------------------------------------
def write_dataset(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    bom: bool = False,
) -> Path:
    """Write ``text`` to ``path`` byte-for-byte, optionally behind a UTF-8 BOM.

    No newline translation happens here: the caller decides the exact text, so
    a round-trip assertion is comparing the loader against the same bytes it
    would see in a real fixture.

    Args:
        path: Destination file; parent directories must already exist.
        text: The exact text to encode.
        encoding: Encoding used to produce the bytes.
        bom: When ``True``, prefix the payload with a UTF-8 BOM.

    Returns:
        ``path``, so the call can be used inline.
    """
    payload = text.encode(encoding)
    if bom:
        payload = b"\xef\xbb\xbf" + payload
    path.write_bytes(payload)
    return path


def write_document(
    path: Path,
    lines: Sequence[str],
    *,
    newline: str = "lf",
    encoding: str = "utf-8",
    bom: bool = False,
) -> Path:
    """Write ``lines`` joined by a named line-ending style.

    Args:
        path: Destination file; parent directories must already exist.
        lines: The lines to join; each must be free of line breaks.
        newline: One of the :data:`NEWLINE_BY_STYLE` names.
        encoding: Encoding used to produce the bytes.
        bom: When ``True``, prefix the payload with a UTF-8 BOM.

    Returns:
        ``path``, so the call can be used inline.
    """
    separator = NEWLINE_BY_STYLE[newline]
    return write_dataset(path, separator.join(lines), encoding=encoding, bom=bom)


def keyword_tokens(text: str) -> List[str]:
    """Return the tokens the assistant's keyword extractor would consider.

    Args:
        text: The source text.

    Returns:
        Lower-cased alphanumeric tokens, in first-seen order, with repeats.
    """
    return re.findall(r"[a-z0-9]+", text.lower())


def is_subsequence(subset: Iterable[str], sequence: Sequence[str]) -> bool:
    """Return ``True`` when ``subset`` appears in ``sequence`` in order.

    Args:
        subset: The candidate ordered subset.
        sequence: The superset to search.

    Returns:
        Whether every element of ``subset`` appears, in order, in ``sequence``.
    """
    iterator = iter(sequence)
    return all(item in iterator for item in subset)
