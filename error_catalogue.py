"""The user-facing error catalogue, generated from the :mod:`errors` taxonomy.

The taxonomy in :mod:`errors` is the source of truth; this module turns it into
something a person can read. It offers three things:

* :func:`catalogue` — one :class:`ErrorEntry` per class, parents before
  children, built by reflecting over the class metadata;
* :func:`describe_code` / :func:`error_class_for` — look an entry or its class
  up by the stable code a user quotes in a bug report; and
* :func:`render_catalogue_markdown` — the Markdown body of
  ``docs/error_catalogue.md``.

The document is generated rather than hand-written, and
``tests/test_errors.py`` asserts that the committed file matches this
module's output byte for byte, so the catalogue cannot drift away from the code.

Regenerate it after changing any class metadata by running the module from the
repository root — it rewrites ``docs/error_catalogue.md`` in place::

    python error_catalogue.py

A different destination can be passed as the first argument.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from errors import (
    AccessDeniedError,
    AssistantError,
    ConfigurationError,
    DatheonError,
    DatasetEncodingError,
    DatasetError,
    DatasetFormatError,
    DirectoryExpectedError,
    FilesystemError,
    InputClosed,
    InputError,
    InventoryError,
    MissingFileError,
    OperationError,
    OrganizerError,
    PersistenceError,
    PortfolioError,
    SchemaVersionError,
    UnknownCategoryError,
    UsageError,
    ValidationError,
)
from persistence import atomic_write_text

__all__ = [
    "ErrorEntry",
    "catalogue",
    "catalogue_codes",
    "describe_code",
    "error_class_for",
    "main",
    "render_catalogue_markdown",
    "write_catalogue",
]


@dataclass(frozen=True)
class ErrorEntry:
    """One row of the user-facing error catalogue.

    Attributes:
        code: Stable machine-readable identifier, e.g. ``"DT-1001"``.
        name: The exception class name.
        title: Short human title.
        summary: One-sentence description of the condition.
        resolution: What the user should do about it.
        exit_code: The process exit code the error maps onto.
        bases: Names of the builtin exception types the class also inherits.
    """

    code: str
    name: str
    title: str
    summary: str
    resolution: str
    exit_code: int
    bases: Tuple[str, ...] = ()

    def as_row(self) -> Tuple[str, str, str, str, str]:
        """Return the cells used in the Markdown catalogue table."""
        return (self.code, self.name, self.title, self.summary, self.resolution)


#: Every concrete error class, in catalogue order (parents before children).
_ERROR_CLASSES: Tuple[type, ...] = (
    DatheonError,
    InputError,
    ValidationError,
    InputClosed,
    UsageError,
    UnknownCategoryError,
    FilesystemError,
    MissingFileError,
    AccessDeniedError,
    DirectoryExpectedError,
    DatasetError,
    DatasetFormatError,
    DatasetEncodingError,
    OperationError,
    InventoryError,
    OrganizerError,
    PortfolioError,
    AssistantError,
    PersistenceError,
    SchemaVersionError,
    ConfigurationError,
)

#: Builtin exception types a taxonomy class is also an instance of.
_BUILTIN_BASES: Tuple[type, ...] = (
    ValueError,
    KeyError,
    OSError,
    FileNotFoundError,
    PermissionError,
    NotADirectoryError,
)


def catalogue() -> Tuple[ErrorEntry, ...]:
    """Return the full error catalogue, parents before children.

    Returns:
        One :class:`ErrorEntry` per class in :data:`_ERROR_CLASSES`.
    """
    entries: List[ErrorEntry] = []
    for cls in _ERROR_CLASSES:
        bases = tuple(base.__name__ for base in _BUILTIN_BASES if issubclass(cls, base))
        entries.append(
            ErrorEntry(
                code=cls.code,
                name=cls.__name__,
                title=cls.title,
                summary=cls.summary,
                resolution=cls.resolution,
                exit_code=cls.exit_code,
                bases=bases,
            )
        )
    return tuple(entries)


def catalogue_codes() -> Tuple[str, ...]:
    """Return every catalogue code, in catalogue order."""
    return tuple(entry.code for entry in catalogue())


def describe_code(code: str) -> ErrorEntry:
    """Look up one catalogue entry by its code.

    Args:
        code: A code such as ``"DT-1001"``.

    Returns:
        The matching :class:`ErrorEntry`.

    Raises:
        UnknownCategoryError: If ``code`` is not in the catalogue.
    """
    for entry in catalogue():
        if entry.code == code:
            return entry
    raise UnknownCategoryError(
        f"Unknown error code {code!r}.",
        hint=f"Known codes: {', '.join(catalogue_codes())}",
        context={"code": code},
    )


def error_class_for(code: str) -> type:
    """Return the exception class registered under ``code``.

    Args:
        code: A code such as ``"DT-2101"``.

    Returns:
        The matching exception class.

    Raises:
        UnknownCategoryError: If ``code`` is not in the catalogue.
    """
    for cls in _ERROR_CLASSES:
        if cls.code == code:
            return cls
    raise UnknownCategoryError(
        f"Unknown error code {code!r}.",
        hint=f"Known codes: {', '.join(catalogue_codes())}",
        context={"code": code},
    )


def render_catalogue_markdown() -> str:
    """Render the error catalogue as the Markdown body of ``docs/error_catalogue.md``.

    The generated text is committed verbatim, and a test asserts that the file
    on disk matches this function's output — so the documentation cannot drift
    away from the code.

    Returns:
        A Markdown document describing every error class.
    """
    lines: List[str] = [
        "# Error catalogue",
        "",
        "Every error a Datheon tool raises on purpose derives from `DatheonError`,",
        "so `except DatheonError` catches all of them. Each class carries a stable",
        "machine-readable code, a process exit code, and the guidance below.",
        "",
        "Exit codes: `0` success · `1` user or data error · `2` usage error.",
        "",
        "| Code | Class | Title | Meaning | What to do |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in catalogue():
        rendered = " | ".join(cell.replace("|", "\\|") for cell in entry.as_row())
        lines.append(f"| {rendered} |")
    lines.extend(
        [
            "",
            "## Builtin compatibility",
            "",
            "The leaf classes also inherit the builtin exception they replace, so",
            "handlers written before this taxonomy existed keep working:",
            "",
            "| Class | Also catchable as |",
            "| --- | --- |",
        ]
    )
    for entry in catalogue():
        if entry.bases:
            names = ", ".join(f"`{base}`" for base in entry.bases)
            lines.append(f"| `{entry.name}` | {names} |")
    lines.extend(
        [
            "",
            "## Rendering an error for the user",
            "",
            "`DatheonError.render()` produces the block the CLIs print, and",
            "`DatheonError.to_dict()` produces the structured form for logs:",
            "",
            "```python",
            "from errors import ValidationError",
            "",
            "exc = ValidationError('symbol: must not be empty', hint='e.g. AAPL')",
            "print(exc.render())",
            "# ! [DT-1001] symbol: must not be empty",
            "#   hint: e.g. AAPL",
            "print(exc.to_dict())",
            "# {'code': 'DT-1001', 'type': 'ValidationError', 'message': 'symbol: ...', ...}",
            "```",
            "",
            "## Regenerating this document",
            "",
            "The tables above are generated from the classes in `errors.py`. Edit the",
            "class metadata, never this file, then rewrite it from the repository",
            "root:",
            "",
            "```bash",
            "python error_catalogue.py",
            "```",
            "",
            "A different destination path may be passed as the first argument.",
            "",
        ]
    )
    return "\n".join(lines)


def write_catalogue(path: Union[str, Path] = "docs/error_catalogue.md") -> Path:
    """Write the generated catalogue to ``path`` and return the path written.

    Args:
        path: Destination file. Defaults to ``docs/error_catalogue.md``.

    Returns:
        The path that was written, for logging.
    """
    target = Path(path)
    atomic_write_text(target, render_catalogue_markdown())
    return target


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Regenerate the catalogue file, then exit with code ``0``.

    Args:
        argv: Command-line arguments; the first, when present, is the
            destination path. Defaults to :data:`sys.argv` without the program
            name.

    Returns:
        :data:`errors.EXIT_SUCCESS`.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    written = write_catalogue(args[0] if args else "docs/error_catalogue.md")
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
