"""Robust, schema-aware dataset loading shared by the Datheon tools (Day 6).

The three dataset loaders — the Hangman word list, the portfolio CSV loaders,
and the assistant JSON / FAQ loaders — used to assume a clean, UTF-8, LF-only
file with a flawless schema. Real datasets are messier:

* they carry a **UTF-8 BOM** (Excel and several editors add one),
* they use **CRLF** line endings (anything authored on Windows),
* they arrive in a **legacy 8-bit encoding** (``cp1252`` / ``latin-1``), or
* they contain a handful of **malformed rows** that used to be dropped without
  a word.

This module centralises the hardening so a fix lands once:

* :func:`read_text_robust` decodes a file through a configurable encoding
  chain (``utf-8-sig`` → ``utf-8`` → ``cp1252`` → ``latin-1``), strips a
  leading BOM, and records the line-ending style;
* :class:`LoadReport` collects :class:`LoadIssue` records — each carrying the
  **line number** of the offending row (or the entry index, for JSON) — so a
  corrupt fixture is *reported*, never silently accepted;
* :func:`require_columns` performs CSV schema validation; and
* the failure classes come from the shared taxonomy in :mod:`errors` —
  :class:`~errors.MissingFileError`, :class:`~errors.AccessDeniedError`,
  :class:`~errors.DatasetEncodingError` and
  :class:`~errors.DatasetFormatError` — so every dataset failure derives from
  :class:`~errors.DatheonError` and maps onto the shared exit-code contract
  (code ``1``).

Severity defaults mirror each loader's historical behaviour — a soft failure
that used to be skipped is recorded as a *warning*, and the same failure under
``--strict`` / ``--validate`` is escalated to an *error* that aborts the load.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from errors import (
    AccessDeniedError,
    DatasetEncodingError,
    DatasetFormatError,
    MissingFileError,
)

__all__ = [
    "DEFAULT_ENCODINGS",
    "DatasetFormatError",
    "LoadIssue",
    "LoadReport",
    "TextSource",
    "detect_newline",
    "read_text_robust",
    "require_columns",
    "strip_bom",
]

#: Text encodings tried, in order, when decoding a dataset file.
#:
#: ``utf-8-sig`` decodes plain UTF-8 *and* transparently drops a leading BOM,
#: so it covers the common case; ``cp1252`` rescues Windows-authored files;
#: ``latin-1`` maps every byte and therefore always succeeds, which makes it the
#: terminal fallback. The reported :attr:`TextSource.encoding` names the winner,
#: so a silent mis-decode can still be surfaced.
DEFAULT_ENCODINGS: Tuple[str, ...] = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

#: Leading UTF-8 byte-order mark, as it appears in the raw bytes.
_UTF8_BOM = b"\xef\xbb\xbf"


def strip_bom(text: str) -> str:
    """Return ``text`` without a leading UTF-8 BOM character.

    Args:
        text: The decoded text.

    Returns:
        The text with a single leading ``U+FEFF`` removed, if one is present.
    """
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def detect_newline(text: str) -> str:
    """Return the dominant line-ending style in ``text``.

    Args:
        text: The decoded text.

    Returns:
        ``"crlf"``, ``"lf"``, ``"cr"``, ``"mixed"`` (when more than one style
        is present), or ``"none"`` when the text holds no line break at all.
    """
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    present = [name for name, count in (("crlf", crlf), ("lf", lf), ("cr", cr)) if count]
    if not present:
        return "none"
    if len(present) == 1:
        return present[0]
    return "mixed"


@dataclass(frozen=True)
class TextSource:
    """A decoded dataset file plus the metadata needed to describe it.

    Attributes:
        path: The file that was read.
        text: The decoded, BOM-stripped contents.
        encoding: The encoding that successfully decoded the bytes.
        newline: The dominant line-ending style (see :func:`detect_newline`).
        had_bom: ``True`` when the raw bytes started with a UTF-8 BOM.
    """

    path: Path
    text: str
    encoding: str
    newline: str
    had_bom: bool

    def describe(self) -> str:
        """Return a one-line human summary of the source."""
        bom = " +BOM" if self.had_bom else ""
        return f"{self.path.name}: encoding={self.encoding}{bom}, newlines={self.newline}"


def read_text_robust(
    path: Union[str, Path],
    *,
    encodings: Sequence[str] = DEFAULT_ENCODINGS,
    field: str = "file",
) -> TextSource:
    """Read a text file, tolerating a BOM, CRLF endings and legacy encodings.

    The raw bytes are decoded with the first encoding in ``encodings`` that
    succeeds, and a leading BOM is stripped from the result. Because ``latin-1``
    maps every byte, the default chain always yields *some* text; the reported
    :attr:`TextSource.encoding` tells the caller which one won.

    Args:
        path: The file to read.
        encodings: Candidate encodings, tried in order. Defaults to
            :data:`DEFAULT_ENCODINGS`.
        field: Field name used in error messages.

    Returns:
        A :class:`TextSource` with the decoded text and its metadata.

    Raises:
        MissingFileError: If ``path`` is not an existing file. Also a
            :class:`FileNotFoundError`, so older handlers still catch it.
        AccessDeniedError: If ``path`` cannot be read. Also a
            :class:`PermissionError`.
        DatasetEncodingError: If no encoding in ``encodings`` can decode the
            bytes (only reachable when ``latin-1`` is omitted from the chain).
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise MissingFileError(
            f"{field} not found: {file_path}",
            hint="check the spelling of the path; sample datasets live under each project's data/",
            context={"field": field, "path": str(file_path)},
        )
    try:
        raw = file_path.read_bytes()
    except PermissionError as exc:
        raise AccessDeniedError(
            f"{field} is not readable (permission denied): {file_path}",
            hint="fix the file's permissions, or run the tool as a user that may read it",
            context={"field": field, "path": str(file_path)},
        ) from exc

    had_bom = raw.startswith(_UTF8_BOM)
    last_error: Optional[UnicodeDecodeError] = None
    for encoding in encodings:
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        text = strip_bom(decoded)
        return TextSource(
            path=file_path,
            text=text,
            encoding=encoding,
            newline=detect_newline(text),
            had_bom=had_bom,
        )
    raise DatasetEncodingError(
        f"{field}: could not decode {file_path} with any of {', '.join(encodings)} ({last_error})",
        hint="re-save the file as UTF-8, or widen the encoding chain",
        context={"field": field, "path": str(file_path), "encodings": list(encodings)},
    )


def require_columns(
    headers: Sequence[str],
    candidates: Sequence[str],
    *,
    field: str,
    path: Optional[Union[str, Path]] = None,
) -> str:
    """Return the first header matching one of ``candidates`` (case-insensitive).

    Args:
        headers: The CSV header names, as read from the file.
        candidates: Acceptable spellings, most preferred first.
        field: Human name of the column, used in the error message.
        path: Optional source path, used in the error message.

    Returns:
        The original header spelling that matched.

    Raises:
        DatasetFormatError: If none of ``candidates`` is present in ``headers``.
    """
    lowered = {str(header).lower().strip(): header for header in headers}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    where = f" in {path}" if path is not None else ""
    raise DatasetFormatError(
        f"{field}: missing required column (one of {', '.join(candidates)}){where}; "
        f"found: {list(headers)}",
        hint="rename or add the column in the header row",
        context={"field": field, "candidates": list(candidates), "headers": list(headers)},
    )


@dataclass(frozen=True)
class LoadIssue:
    """A single problem found while loading a dataset.

    Attributes:
        message: What is wrong with the row.
        line: 1-based line number in the source file, when known. For JSON
            datasets this is ``None`` and the offending entry is named in
            ``message`` instead (for example ``intents[2]``).
        raw: The offending raw text, when available.
        severity: ``"error"`` (rejects the row and aborts in strict mode) or
            ``"warning"`` (the row was skipped).
    """

    message: str
    line: Optional[int] = None
    raw: str = ""
    severity: str = "error"

    def describe(self) -> str:
        """Return a single-line, human-readable rendering of the issue."""
        where = f"line {self.line}" if self.line is not None else "document"
        detail = f" — {self.raw!r}" if self.raw else ""
        return f"[{self.severity}] {where}: {self.message}{detail}"


class LoadReport:
    """Accumulates the issues found while loading one dataset.

    A report is threaded through a loader so that malformed rows are
    *collected* (with their line numbers) rather than silently dropped. In
    strict / ``--validate`` mode the caller turns the collected errors into a
    :class:`DatasetFormatError` via :meth:`raise_if_errors`.

    Args:
        path: The dataset path, used in :meth:`summary` and :meth:`render`.
    """

    def __init__(self, path: Optional[Union[str, Path]] = None) -> None:
        self.path: Optional[Path] = Path(path) if path is not None else None
        self.source: Optional[TextSource] = None
        self.rows_checked: int = 0
        self.rows_accepted: int = 0
        self._issues: List[LoadIssue] = []

    def add(
        self,
        message: str,
        *,
        line: Optional[int] = None,
        raw: str = "",
        severity: str = "error",
    ) -> LoadIssue:
        """Record one issue and return it.

        Args:
            message: What is wrong with the row.
            line: 1-based line number, when known.
            raw: The offending raw text.
            severity: ``"error"`` or ``"warning"``.

        Returns:
            The :class:`LoadIssue` that was appended.
        """
        issue = LoadIssue(message=message, line=line, raw=raw, severity=severity)
        self._issues.append(issue)
        return issue

    def error(
        self,
        message: str,
        *,
        line: Optional[int] = None,
        raw: str = "",
    ) -> LoadIssue:
        """Record an error-severity issue."""
        return self.add(message, line=line, raw=raw, severity="error")

    def warning(
        self,
        message: str,
        *,
        line: Optional[int] = None,
        raw: str = "",
    ) -> LoadIssue:
        """Record a warning-severity issue."""
        return self.add(message, line=line, raw=raw, severity="warning")

    @property
    def issues(self) -> List[LoadIssue]:
        """Every recorded issue, in the order it was found."""
        return list(self._issues)

    @property
    def errors(self) -> List[LoadIssue]:
        """The error-severity issues."""
        return [issue for issue in self._issues if issue.severity == "error"]

    @property
    def warnings(self) -> List[LoadIssue]:
        """The warning-severity issues."""
        return [issue for issue in self._issues if issue.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        """``True`` when at least one error-severity issue was recorded."""
        return any(issue.severity == "error" for issue in self._issues)

    @property
    def is_clean(self) -> bool:
        """``True`` when no issue at all was recorded."""
        return not self._issues

    def summary(self, what: str = "dataset") -> str:
        """Return a compact one-line summary suitable for an error message."""
        name = self.path.name if self.path is not None else what
        parts = [f"{name}: {self.rows_accepted}/{self.rows_checked} rows accepted"]
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        if self.warnings:
            parts.append(f"{len(self.warnings)} warning(s)")
        return ", ".join(parts)

    def render(self) -> str:
        """Return a multi-line report: the source line, then one line per issue."""
        lines: List[str] = []
        if self.source is not None:
            lines.append(self.source.describe())
        lines.append(self.summary())
        lines.extend(issue.describe() for issue in self._issues)
        return "\n".join(lines)

    def raise_if_errors(self, what: str = "dataset") -> None:
        """Raise :class:`DatasetFormatError` if any error was recorded.

        Args:
            what: Label used in the error message.

        Raises:
            DatasetFormatError: When :attr:`has_errors` is ``True``; the message
                carries the summary and the first few offending rows.
        """
        if not self.has_errors:
            return
        head = self.summary(what)
        detail = "; ".join(issue.describe() for issue in self.errors[:5])
        more = "" if len(self.errors) <= 5 else f"; (+{len(self.errors) - 5} more)"
        raise DatasetFormatError(
            f"malformed {what}: {head} — {detail}{more}",
            hint="open the file at the reported line and fix the row, or re-run with --validate",
            context={
                "dataset": what,
                "path": str(self.path) if self.path is not None else None,
                "errors": len(self.errors),
                "warnings": len(self.warnings),
            },
        )
