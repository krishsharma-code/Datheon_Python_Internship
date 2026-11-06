"""Deterministic fuzz corpus and crash verdicts for the Datheon internship (Day 18).

Day 17 proved *properties* over well-formed input. Day 18 does the opposite: it
feeds the five CLIs, the three dataset loaders and the assistant's intent
engine input that no well-meaning user would ever type — truncated UTF-8, lone
control bytes, kilometre-long arguments, flags that do not exist, JSON that
opens and never closes — and asserts that nothing escapes as an unhandled
traceback.

Two design decisions make that a checkable claim rather than an anecdote.

**The corpus is a pure function of its coordinates.** :func:`generate_cases`
derives every case from ``(kind, target, seed, count)`` through a per-case
:class:`random.Random`. A failure therefore reproduces from a seed alone; there
are no binary fixtures to drift out of date and no "works on my machine" runs.
The default seed is :data:`DEFAULT_SEED`, so two invocations with the same
arguments produce byte-identical corpora.

**A verdict is data, not an exit status.** :func:`judge_process`,
:func:`judge_exit_code` and :func:`judge_exception` return a :class:`Verdict`
carrying the case, the outcome and a human-readable reason. A case passes when
the process exit code is one of the three the CLI contract allows
(:data:`VALID_EXIT_CODES`) and neither stream contains a Python traceback. The
caller decides what to do with a failure — :mod:`tools.fuzz` prints a report
and exits non-zero, the pytest suite asserts.

The corpus comes in three shapes, matching the three surfaces that take
untrusted input:

=============  ==================================================================
``kind``       Shape of one case
=============  ==================================================================
``"argv"``     A tuple of command-line tokens drawn from :data:`ARGV_TOKENS` plus
               occasionally a fuzzed string, so argument *shapes* are covered as
               well as argument *values*.
``"bytes"``    A file's worth of bytes: either random noise or a byte-level
               mutation of a real dataset passed in through ``seeds``.
``"text"``     A unicode string built from :data:`TEXT_FRAGMENTS`: control
               characters, bidi overrides, combining marks, format specifiers and
               other things a human keyboard rarely produces.
=============  ==================================================================

Everything here is standard-library only and side-effect free — no file is
opened, no process is started — which keeps it importable from the test suite,
the command-line driver and any future harness.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from errors import DatheonError

__all__ = [
    "ARGV_TOKENS",
    "DEFAULT_CASES",
    "DEFAULT_SEED",
    "FuzzCase",
    "TEXT_FRAGMENTS",
    "TOLERATED_EXCEPTIONS",
    "TRACEBACK_MARKERS",
    "VALID_EXIT_CODES",
    "Verdict",
    "generate_cases",
    "is_traceback",
    "judge_exception",
    "judge_exit_code",
    "judge_process",
    "mutate_bytes",
    "random_argv",
    "random_bytes",
    "random_text",
]

#: Seed used when the caller does not pick one. Any integer works; this one is
#: the day number, which makes a failing run easy to quote in a bug report.
DEFAULT_SEED: int = 18

#: Cases generated per target when the caller does not ask for a count.
DEFAULT_CASES: int = 25

#: The only exit codes the CLI contract permits (see :mod:`cli_contract`).
VALID_EXIT_CODES: frozenset = frozenset({0, 1, 2})

#: Substrings that only appear when an exception escaped the process boundary.
TRACEBACK_MARKERS: Tuple[str, ...] = (
    "Traceback (most recent call last)",
    "During handling of the above exception",
)

#: Exceptions the process boundary is expected to convert into an exit code.
#:
#: :func:`cli_contract.run_cli` handles every one of these, so seeing one is not
#: a crash — it is the contract doing its job. Anything else (``TypeError``,
#: ``KeyError``, ``RecursionError``, …) is a genuine defect.
TOLERATED_EXCEPTIONS: Tuple[type, ...] = (
    DatheonError,
    ValueError,
    OSError,
    SystemExit,
    KeyboardInterrupt,
)

#: Command-line tokens worth throwing at an :mod:`argparse` surface.
#:
#: The pool mixes three flavours: flags that exist somewhere in the suite,
#: values at the edge of each type (negative, zero, overflowing, non-numeric),
#: and shapes that break parsers (bare dashes, empty strings, NUL bytes,
#: kilometre-long tokens). Absolute paths are deliberately absent — a fuzz run
#: must never be able to point a tool at a real system directory.
ARGV_TOKENS: Tuple[str, ...] = (
    "--help",
    "--version",
    "--validate",
    "--strict",
    "--demo",
    "--dry-run",
    "--duplicates",
    "--no-log",
    "--log-level",
    "--workers",
    "--concurrency",
    "--min-length",
    "--max-length",
    "--words-file",
    "--prices",
    "--output",
    "--constituents",
    "--intents",
    "--faq",
    "--log",
    "--borrower",
    "--days",
    "--borrow-date",
    "DEBUG",
    "CRITICAL",
    "-1",
    "0",
    "1",
    "2",
    "999999999999999999999",
    "-",
    "--",
    "---",
    "-x",
    "--=x",
    "",
    " ",
    "\t",
    "\x00",
    "None",
    "nan",
    "inf",
    "NaN",
    "true",
    "..",
    ".",
    "missing.csv",
    "no/such/dir.csv",
    "/dev/null",
    "a" * 300,
    "Ω",
    "😀",
    "%s",
    "{0}",
)

#: Fragments assembled into fuzzed unicode text.
#:
#: Chosen to hit the parsing seams the suite cares about: whitespace handling in
#: :func:`chatbot.normalize`, word filtering in the hangman loader, JSON syntax
#: in the intent loader, and the format strings used when a tool echoes input
#: back to the terminal.
TEXT_FRAGMENTS: Tuple[str, ...] = (
    "hello",
    "Good morning",
    "bye",
    "exit",
    "quit",
    "price of RELIANCE",
    "",
    " ",
    "   ",
    "\t",
    "\n",
    "\r\n",
    "\x00",
    "\x1b[31m",
    "\x1b]0;title\x07",
    "\u202e",
    "\u0301",
    "a" * 64,
    "Ω",
    "日本語",
    "😀",
    "🇮🇳",
    "{}",
    "[]",
    "null",
    '{"name": ',
    "%s",
    "%d",
    "{0}",
    "{name}",
    "__import__('os')",
    "<script>alert(1)</script>",
    "../../",
    "..\\..\\",
    "; rm -rf /",
    "| cat",
    "SELECT * FROM x",
    "-1",
    "1e309",
    "Infinity",
    "NaN",
    "0x10",
    "1_000",
    "\u00a0",
    "\ufeff",
)


@dataclass(frozen=True)
class FuzzCase:
    """One generated input, reproducible from :attr:`seed`.

    Exactly one of :attr:`argv`, :attr:`data` or :attr:`text` carries the
    payload, chosen by :attr:`kind`; the other two keep their empty defaults so
    a case can be hashed, compared and serialised without special cases.

    Attributes:
        kind: ``"argv"``, ``"bytes"`` or ``"text"``.
        target: The surface the case is aimed at — a tool name, a loader name or
            a dataset flag.
        label: Human-readable name for the case within its target, for example
            ``"argv[3]"`` or ``"mutate[1]"``.
        seed: The integer that regenerates this exact payload.
        argv: Command-line tokens, for ``kind="argv"``.
        data: Raw file bytes, for ``kind="bytes"``.
        text: Unicode input, for ``kind="text"``.
    """

    kind: str
    target: str
    label: str
    seed: int
    argv: Tuple[str, ...] = ()
    data: bytes = b""
    text: str = ""

    def describe(self) -> str:
        """Render the payload as a single line safe to print.

        Returns:
            A short, escaped rendering of whichever payload field is populated,
            truncated so that a 300-character argument cannot flood a report.
        """
        if self.kind == "argv":
            rendered = " ".join(repr(token) for token in self.argv)
        elif self.kind == "bytes":
            rendered = repr(self.data)
        else:
            rendered = repr(self.text)
        if len(rendered) > 160:
            rendered = rendered[:157] + "..."
        return f"{self.target}/{self.label} seed={self.seed}: {rendered}"

    def to_dict(self) -> Dict[str, object]:
        """Serialise the case for a JSON report.

        Returns:
            A mapping with the payload encoded as text (bytes become ``repr``)
            so the result is always JSON-serialisable.
        """
        return {
            "kind": self.kind,
            "target": self.target,
            "label": self.label,
            "seed": self.seed,
            "argv": list(self.argv),
            "data": self.data.decode("latin-1") if self.kind == "bytes" else "",
            "text": self.text,
            "description": self.describe(),
        }


@dataclass(frozen=True)
class Verdict:
    """The outcome of running one :class:`FuzzCase`.

    Attributes:
        case: The case that was run.
        ok: ``True`` when the run stayed inside the contract.
        reason: Empty on success, otherwise why the case is a failure.
        exit_code: The exit code observed, when one was observed.
        detail: Optional captured output, truncated for printing.
    """

    case: FuzzCase
    ok: bool
    reason: str = ""
    exit_code: Optional[int] = None
    detail: str = field(default="")

    def to_dict(self) -> Dict[str, object]:
        """Serialise the verdict for a JSON report.

        Returns:
            A mapping with the case flattened alongside the outcome.
        """
        payload = self.case.to_dict()
        payload.update(
            {
                "ok": self.ok,
                "reason": self.reason,
                "exit_code": self.exit_code,
                "detail": self.detail,
            }
        )
        return payload


def is_traceback(text: str) -> bool:
    """Report whether a captured stream contains a Python traceback.

    Args:
        text: Captured stdout or stderr.

    Returns:
        ``True`` if any marker in :data:`TRACEBACK_MARKERS` occurs in ``text``.
    """
    return any(marker in text for marker in TRACEBACK_MARKERS)


def _tail(text: str, limit: int = 400) -> str:
    """Return the last ``limit`` characters of ``text``.

    Args:
        text: The captured stream.
        limit: Maximum number of characters to keep.

    Returns:
        The tail of the string, prefixed with an ellipsis when it was cut.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def judge_exit_code(case: FuzzCase, code: int) -> Verdict:
    """Judge an exit code observed in-process.

    Args:
        case: The case that produced the code.
        code: The exit code the tool returned.

    Returns:
        A passing :class:`Verdict` when ``code`` is in :data:`VALID_EXIT_CODES`,
        otherwise a failing one naming the offending value.
    """
    if code in VALID_EXIT_CODES:
        return Verdict(case, True, exit_code=code)
    reason = f"exit code {code} outside the contract {sorted(VALID_EXIT_CODES)}"
    return Verdict(case, False, reason, code)


def judge_process(
    case: FuzzCase,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> Verdict:
    """Judge a completed subprocess run.

    Args:
        case: The case that was run.
        returncode: The process exit code.
        stdout: Captured standard output.
        stderr: Captured standard error.

    Returns:
        A failing :class:`Verdict` if the exit code is outside the contract or
        either stream carries a traceback; a passing one otherwise.
    """
    if returncode not in VALID_EXIT_CODES:
        reason = f"exit code {returncode} outside the contract {sorted(VALID_EXIT_CODES)}"
        return Verdict(case, False, reason, returncode, _tail(stderr or stdout))
    for stream_name, stream in (("stderr", stderr), ("stdout", stdout)):
        if is_traceback(stream):
            return Verdict(case, False, f"traceback on {stream_name}", returncode, _tail(stream))
    return Verdict(case, True, exit_code=returncode)


def judge_exception(
    case: FuzzCase,
    exc: BaseException,
    tolerated: Sequence[type] = TOLERATED_EXCEPTIONS,
) -> Verdict:
    """Judge an exception raised while running a case in-process.

    Args:
        case: The case that raised.
        exc: The exception that escaped.
        tolerated: Exception types the caller considers handled. Defaults to
            :data:`TOLERATED_EXCEPTIONS`; pass a narrower tuple to demand that a
            layer converts a given failure into its own taxonomy type.

    Returns:
        A passing :class:`Verdict` when ``exc`` is an instance of ``tolerated``,
        otherwise a failing one naming the type and message.
    """
    if isinstance(exc, tuple(tolerated)):
        return Verdict(case, True, f"tolerated {type(exc).__name__}")
    reason = f"unhandled {type(exc).__name__}: {exc}"
    return Verdict(case, False, reason, detail=_tail(repr(exc)))


def random_text(rng: random.Random, *, max_fragments: int = 8) -> str:
    """Build a hostile unicode string from :data:`TEXT_FRAGMENTS`.

    Args:
        rng: Seeded generator, so the result is reproducible.
        max_fragments: Upper bound on how many fragments are concatenated.

    Returns:
        A string of between one and ``max_fragments`` fragments, optionally
        truncated mid-fragment to produce broken grapheme clusters.
    """
    count = rng.randint(1, max(1, max_fragments))
    text = "".join(rng.choice(TEXT_FRAGMENTS) for _ in range(count))
    if text and rng.random() < 0.25:
        text = text[: rng.randint(1, len(text))]
    return text


def random_bytes(rng: random.Random, *, max_length: int = 512) -> bytes:
    """Build a hostile byte string.

    Five shapes are drawn with equal probability so that a corpus exercises
    both "not a text file at all" and "a text file with one bad byte":

    * raw noise — every byte value, uniformly;
    * mangled UTF-8 — valid text with one byte replaced by ``0xFF``;
    * BOM-prefixed UTF-8 — a correct file with the marker the loaders strip;
    * NUL-embedded UTF-8 — text with ``\\x00`` inside a token;
    * Latin-1 text — a legacy 8-bit encoding the fallbacks must handle.

    Args:
        rng: Seeded generator.
        max_length: Hard ceiling on the payload size, in bytes.

    Returns:
        The generated bytes, never longer than ``max_length``. A text-shaped
        payload that exceeds the ceiling is cut mid-character, which leaves an
        invalid UTF-8 tail — itself a shape worth fuzzing.
    """
    ceiling = max(0, max_length)
    mode = rng.choice(("raw", "mangled", "bom", "nul", "latin-1"))

    if mode == "raw":
        length = rng.randint(0, ceiling)
        return bytes(rng.randrange(256) for _ in range(length))

    text = random_text(rng, max_fragments=12)

    if mode == "mangled":
        payload = bytearray(text.encode("utf-8", errors="replace"))
        if payload:
            payload[rng.randrange(len(payload))] = 0xFF
        return bytes(payload[:ceiling])
    if mode == "bom":
        return (b"\xef\xbb\xbf" + text.encode("utf-8"))[:ceiling]
    if mode == "nul":
        return text.encode("utf-8").replace(b" ", b"\x00")[:ceiling]
    return text.encode("latin-1", errors="replace")[:ceiling]


def mutate_bytes(rng: random.Random, base: bytes, *, max_edits: int = 6) -> bytes:
    """Apply a handful of byte-level edits to a real dataset.

    Random noise rarely reaches a parser's deeper branches: it usually fails the
    first decode. Starting from a file the tool accepts and then breaking *one
    thing* is what gets past the encoding check and into the row, schema and
    field validators. The edit vocabulary mirrors the defects the loaders
    already claim to survive — truncation, a stray BOM, CRLF translation,
    duplicated rows, an invalid byte mid-file.

    Args:
        rng: Seeded generator.
        base: The original file contents.
        max_edits: Upper bound on the number of edits applied.

    Returns:
        The mutated bytes. At least one edit is always applied, so the result
        differs from ``base`` whenever an edit is possible.
    """
    buffer = bytearray(base)
    edits = rng.randint(1, max(1, max_edits))

    for _ in range(edits):
        choice = rng.choice(
            ("delete", "insert", "replace", "duplicate", "truncate", "bom", "crlf", "junk")
        )
        size = len(buffer)

        if choice == "delete" and size:
            start = rng.randrange(size)
            del buffer[start : start + rng.randint(1, max(1, size // 8))]
        elif choice == "insert":
            position = rng.randint(0, size)
            blob = bytes(rng.randrange(256) for _ in range(rng.randint(1, 16)))
            buffer[position:position] = blob
        elif choice == "replace" and size:
            buffer[rng.randrange(size)] = rng.randrange(256)
        elif choice == "duplicate" and size:
            start = rng.randrange(size)
            chunk = bytes(buffer[start : start + rng.randint(1, max(1, size // 4))])
            buffer.extend(chunk)
        elif choice == "truncate" and size:
            del buffer[rng.randrange(size) :]
        elif choice == "bom":
            buffer[0:0] = b"\xef\xbb\xbf"
        elif choice == "crlf":
            buffer = bytearray(bytes(buffer).replace(b"\n", b"\r\n"))
        else:
            buffer.extend(rng.choice((b"\x00", b"\xff", b"\n\n", b",,,,", b"{}", b'{"x":')))

    if bytes(buffer) == base:
        buffer.extend(b"\x00")
    return bytes(buffer)


def random_argv(rng: random.Random, *, max_tokens: int = 6) -> List[str]:
    """Build a hostile argument vector.

    Most tokens come from :data:`ARGV_TOKENS`; roughly one in six is a fuzzed
    string, so the corpus covers both malformed *structure* (an unknown flag, a
    flag with no value) and malformed *content* (a value containing a NUL or a
    lone surrogate-free bidi override).

    Args:
        rng: Seeded generator.
        max_tokens: Upper bound on the vector length.

    Returns:
        A list of between zero and ``max_tokens`` tokens. An empty list is a
        legitimate case: it is how a user runs a tool with no arguments at all.
    """
    tokens: List[str] = []
    for _ in range(rng.randint(0, max(0, max_tokens))):
        if rng.random() < 1 / 6:
            tokens.append(random_text(rng, max_fragments=2))
        else:
            tokens.append(rng.choice(ARGV_TOKENS))
    return tokens


def generate_cases(
    kind: str,
    target: str,
    *,
    seed: int = DEFAULT_SEED,
    count: int = DEFAULT_CASES,
    seeds: Sequence[bytes] = (),
) -> List[FuzzCase]:
    """Generate a reproducible corpus for one target.

    Each case gets its own generator seeded from ``seed * 1000 + index``, so the
    corpus for a target is stable when ``count`` grows — case 7 is case 7
    whether you asked for eight cases or eight hundred — and a single case can
    be regenerated from the integer printed in a report.

    Args:
        kind: ``"argv"``, ``"bytes"`` or ``"text"``.
        target: Name of the surface, copied onto every case.
        seed: Master seed for the corpus.
        count: Number of cases to produce.
        seeds: Real dataset contents for ``kind="bytes"``. When supplied, each
            case is a :func:`mutate_bytes` of one of them (round-robin) instead
            of pure noise.

    Returns:
        A list of ``count`` cases.

    Raises:
        ValueError: If ``kind`` is not one of the three supported shapes, or if
            ``count`` is negative.
    """
    if kind not in {"argv", "bytes", "text"}:
        raise ValueError(f"unknown fuzz kind {kind!r}; expected 'argv', 'bytes' or 'text'")
    if count < 0:
        raise ValueError("count must be non-negative")

    cases: List[FuzzCase] = []
    for index in range(count):
        case_seed = seed * 1000 + index
        rng = random.Random(case_seed)

        if kind == "argv":
            cases.append(
                FuzzCase(
                    kind=kind,
                    target=target,
                    label=f"argv[{index}]",
                    seed=case_seed,
                    argv=tuple(random_argv(rng)),
                )
            )
        elif kind == "bytes":
            if seeds:
                base = seeds[index % len(seeds)]
                data = mutate_bytes(rng, base)
                label = f"mutate[{index}]"
            else:
                data = random_bytes(rng)
                label = f"bytes[{index}]"
            cases.append(FuzzCase(kind=kind, target=target, label=label, seed=case_seed, data=data))
        else:
            cases.append(
                FuzzCase(
                    kind=kind,
                    target=target,
                    label=f"text[{index}]",
                    seed=case_seed,
                    text=random_text(rng),
                )
            )

    return cases
