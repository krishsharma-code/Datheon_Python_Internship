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

Regenerate it after changing any class metadata by calling
``render_catalogue_markdown()`` and writing the result over
``docs/error_catalogue.md``; the regeneration command is repeated in the
document's own footer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

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
    PortfolioError,
    UnknownCategoryError,
    UsageError,
    ValidationError,
)

__all__ = [
    "ErrorEntry",
    "catalogue",
    "catalogue_codes",
    "describe_code",
    "error_class_for",
    "render_catalogue_markdown",
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



