"""Unified exception taxonomy for the five Datheon internship tools (Day 7).

Before this module existed every project raised whatever Python primitive was
convenient — a bare :class:`ValueError` here, a :class:`KeyError` there, a
:class:`FileNotFoundError` somewhere else. A caller therefore could not tell a
*user typo* from a *corrupt dataset* from an *interrupted session* without
parsing English prose, and the command-line contract in :mod:`cli_contract` had
to guess the exit code from the exception's type.

This module gives every deliberate failure one root:

.. code-block:: text

    DatheonError                   DT-0000   exit 1
    ├── InputError                 DT-1000   exit 1
    │   ├── ValidationError        DT-1001   exit 1   (also ValueError)
    │   ├── InputClosed            DT-1002   exit 1
    │   ├── UsageError             DT-1003   exit 2   (bad command line)
    │   └── UnknownCategoryError   DT-1004   exit 1   (also KeyError)
    ├── FilesystemError            DT-2000   exit 1   (also OSError)
    │   ├── MissingFileError       DT-2001   exit 1   (also FileNotFoundError)
    │   ├── AccessDeniedError      DT-2002   exit 1   (also PermissionError)
    │   └── DirectoryExpectedError DT-2003   exit 1   (also NotADirectoryError)
    ├── DatasetError               DT-2100   exit 1   (also ValueError)
    │   ├── DatasetFormatError     DT-2101   exit 1
    │   └── DatasetEncodingError   DT-2102   exit 1   (also DatasetFormatError)
    ├── OperationError             DT-3000   exit 1
    │   ├── InventoryError         DT-3001   exit 1
    │   ├── OrganizerError         DT-3002   exit 1
    │   ├── PortfolioError         DT-3003   exit 1
    │   ├── AssistantError         DT-3004   exit 1
    │   ├── PersistenceError       DT-3005   exit 1   (also OSError)
    │   └── SchemaVersionError     DT-3006   exit 1
    └── ConfigurationError         DT-4000   exit 1

Three properties matter:

* **One root.** Every error a Datheon tool raises on purpose derives from
  :class:`DatheonError`, so ``except DatheonError`` is a complete safety net.
* **Backwards compatible.** The leaf classes also inherit the builtin they
  replace (``ValueError``, ``KeyError``, ``OSError``, ``FileNotFoundError``,
  ``PermissionError``, ``NotADirectoryError``), so the handlers and the tests
  written on Days 3–6 keep working unchanged.
* **Self-describing.** Each class carries a stable machine-readable ``code``, a
  process ``exit_code``, and catalogue metadata (title, summary, resolution).
  :meth:`DatheonError.render` turns an instance into the single, actionable
  message a shell user should see, and :mod:`error_catalogue` regenerates
  ``docs/error_catalogue.md`` from that metadata so the documentation can never
  drift from the code.

Typical use::

    from errors import DatheonError, exit_code_for

    try:
        ...
    except DatheonError as exc:
        print(exc.render(), file=sys.stderr)
        raise SystemExit(exit_code_for(exc))
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

__all__ = [
    "EXIT_DATA_ERROR",
    "EXIT_SUCCESS",
    "EXIT_USAGE_ERROR",
    "AccessDeniedError",
    "AssistantError",
    "ConfigurationError",
    "DatheonError",
    "DatasetEncodingError",
    "DatasetError",
    "DatasetFormatError",
    "DirectoryExpectedError",
    "FilesystemError",
    "InputClosed",
    "InputError",
    "InventoryError",
    "MissingFileError",
    "OperationError",
    "OrganizerError",
    "PersistenceError",
    "PortfolioError",
    "SchemaVersionError",
    "UnknownCategoryError",
    "UsageError",
    "ValidationError",
    "exit_code_for",
]

# -- process exit codes ----------------------------------------------------
#: The command completed successfully.
EXIT_SUCCESS: int = 0
#: A user or data error: bad input, a missing file, a corrupt dataset.
EXIT_DATA_ERROR: int = 1
#: A usage error: the command line itself was invalid.
EXIT_USAGE_ERROR: int = 2


class DatheonError(Exception):
    """Base class for every error a Datheon tool raises deliberately.

    Instances carry the human-facing message, an optional actionable ``hint``,
    an optional structured ``context`` mapping, and the class-level metadata
    (:attr:`code`, :attr:`exit_code`, :attr:`title`, :attr:`summary`,
    :attr:`resolution`) that the error catalogue is built from.

    Args:
        message: Human-readable description of what went wrong. Kept as
            :func:`str` output so existing message assertions stay valid.
        hint: Optional one-line suggestion of what the user can do next.
        context: Optional structured detail (field name, path, line number…)
            for logs and machine consumers.
        code: Override the class-level catalogue code, for the rare case where
            one class reports several distinct conditions.

    Attributes:
        message: The description passed to the constructor.
        hint: The optional suggestion passed to the constructor.
        context: A mutable copy of the structured detail.
        code: The stable machine-readable error code (e.g. ``"DT-1001"``).
        exit_code: The process exit code this error maps onto.
    """

    #: Stable machine-readable identifier, unique per concrete class.
    code: str = "DT-0000"
    #: Process exit code this class maps onto.
    exit_code: int = EXIT_DATA_ERROR
    #: Short human title used in the catalogue.
    title: str = "Unexpected error"
    #: One-sentence description of the condition, used in the catalogue.
    summary: str = "An unexpected error occurred inside a Datheon tool."
    #: What the user should do about it, used in the catalogue and in hints.
    resolution: str = "Re-run the command; if the error persists, report it with the message above."

    def __init__(
        self,
        message: str = "",
        *,
        hint: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
        code: Optional[str] = None,
    ) -> None:
        self.message: str = str(message)
        self.hint: Optional[str] = hint
        self.context: Dict[str, Any] = dict(context) if context else {}
        if code is not None:
            self.code = code
        super().__init__(self.message)

    # -- rendering ------------------------------------------------------
    def __str__(self) -> str:
        """Return the bare message, without the code or the hint."""
        return self.message

    def render(self, *, show_code: bool = True) -> str:
        """Return the user-facing, single-block rendering of this error.

        Args:
            show_code: When ``True`` (the default), prefix the message with the
                catalogue code so a user can quote it in a bug report.

        Returns:
            A block of the form ``"! [DT-1001] symbol: must not be empty"``,
            followed by a ``hint:`` line when a hint is available.
        """
        prefix = f"[{self.code}] " if show_code else ""
        text = f"! {prefix}{self.message}".rstrip()
        if self.hint:
            text += f"\n  hint: {self.hint}"
        return text

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable description of this error.

        Returns:
            A mapping with ``code``, ``type``, ``message``, ``hint``,
            ``context`` and ``exit_code`` keys — suitable for structured logs.
        """
        return {
            "code": self.code,
            "type": type(self).__name__,
            "message": self.message,
            "hint": self.hint,
            "context": dict(self.context),
            "exit_code": self.exit_code,
        }

    def __repr__(self) -> str:
        """Return an unambiguous debugging representation."""
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"

    # -- fluent helpers -------------------------------------------------
    def with_context(self, **extra: Any) -> "DatheonError":
        """Merge ``extra`` into :attr:`context` and return ``self``.

        Lets a caller add detail while re-raising without rebuilding the
        exception::

            except DatheonError as exc:
                raise exc.with_context(path=path)

        Args:
            **extra: Key/value pairs to merge into the context mapping.

        Returns:
            This same exception instance, for chaining.
        """
        self.context.update(extra)
        return self

    def with_hint(self, hint: str) -> "DatheonError":
        """Set the hint when the raiser had none, and return ``self``.

        Args:
            hint: The suggestion to show the user.

        Returns:
            This same exception instance, for chaining.
        """
        if not self.hint:
            self.hint = hint
        return self


# -- input errors ----------------------------------------------------------
class InputError(DatheonError):
    """Base class for errors caused by what the user typed or passed in."""

    code = "DT-1000"
    exit_code = EXIT_DATA_ERROR
    title = "Input error"
    summary = "The value supplied on the command line or at a prompt was rejected."
    resolution = "Correct the value and run the command again; `--help` lists the accepted form."


class ValidationError(InputError, ValueError):
    """Raised when user input fails validation.

    Also a :class:`ValueError`, so handlers and tests written before the
    taxonomy existed — including :func:`cli_contract.run_cli` — keep working.
    """

    code = "DT-1001"
    title = "Invalid input"
    summary = "A field, flag or prompt answer failed validation."
    resolution = (
        "Read the message: it names the field and quotes the offending value. "
        "Supply a value in the documented form, or relax the check by omitting `--strict`."
    )


class InputClosed(InputError):
    """Raised when an interactive input stream ends or is interrupted.

    The interactive shells catch this to finish the session cleanly — the
    terminal equivalent of ``Ctrl-D`` (EOF) or ``Ctrl-C`` — instead of letting
    :class:`EOFError` or :class:`KeyboardInterrupt` escape as a traceback.
    """

    code = "DT-1002"
    title = "Input stream closed"
    summary = "Standard input reached end-of-file, or the read was interrupted with Ctrl-C."
    resolution = "Nothing to fix: the session ended normally. Re-run the tool to start a new one."


class UsageError(InputError):
    """Raised when the command line itself is invalid.

    Maps onto exit code ``2``, matching :mod:`argparse` and POSIX ``sysexits``
    ``EX_USAGE``, so a script can distinguish "you called me wrong" from "the
    data was bad".
    """

    code = "DT-1003"
    exit_code = EXIT_USAGE_ERROR
    title = "Usage error"
    summary = "An unknown flag, a missing value or a malformed argument was supplied."
    resolution = "Run the tool with `--help` to see the accepted arguments."


class UnknownCategoryError(InputError, KeyError):
    """Raised when a named lookup key — such as a word-bank category — is absent.

    Also a :class:`KeyError`, preserving the behaviour callers relied on before
    the taxonomy existed.
    """

    code = "DT-1004"
    title = "Unknown category"
    summary = "A requested named group, such as a Hangman word-bank category, does not exist."
    resolution = "Pick one of the categories named in the error message."


# -- filesystem errors -----------------------------------------------------
class FilesystemError(DatheonError, OSError):
    """Base class for errors raised when a required path cannot be used.

    Also an :class:`OSError`, so ``except OSError`` in the project entry points
    keeps catching it.
    """

    code = "DT-2000"
    title = "Filesystem error"
    summary = "A file or directory the tool needs is missing, unreadable or the wrong kind."
    resolution = "Check the path passed on the command line and the permissions on it."


class MissingFileError(FilesystemError, FileNotFoundError):
    """Raised when a required path does not name an existing regular file.

    Also a :class:`FileNotFoundError`, so existing handlers keep catching it.
    """

    code = "DT-2001"
    title = "File not found"
    summary = "The path does not name an existing regular file."
    resolution = (
        "Check the spelling of the path; sample datasets live under each project's `data/`."
    )


class AccessDeniedError(FilesystemError, PermissionError):
    """Raised when a path exists but the process may not read it.

    Also a :class:`PermissionError`, so existing handlers keep catching it.
    """

    code = "DT-2002"
    title = "Permission denied"
    summary = "The path exists but the operating system refused access to it."
    resolution = "Fix the permissions on the path, or run the tool as a user that may read it."


class DirectoryExpectedError(FilesystemError, NotADirectoryError):
    """Raised when a path that must be a directory is something else.

    Also a :class:`NotADirectoryError`, so existing handlers keep catching it.
    """

    code = "DT-2003"
    title = "Not a directory"
    summary = "A command that walks a folder was given a path that is not a directory."
    resolution = "Pass the folder you want the tool to work on, not a file."


# -- dataset errors --------------------------------------------------------
class DatasetError(DatheonError, ValueError):
    """Base class for errors parsing a dataset shipped with, or supplied to, a tool.

    Also a :class:`ValueError` so the shared CLI contract maps it onto exit code
    ``1`` without special-casing.
    """

    code = "DT-2100"
    title = "Dataset error"
    summary = "A dataset file was read but violates its schema or row format."
    resolution = "Check the file against the documented schema; `--validate` reports bad rows."


class DatasetFormatError(DatasetError):
    """Raised when a dataset is present but structurally invalid."""

    code = "DT-2101"
    title = "Malformed dataset"
    summary = "The dataset was read successfully but violates its schema or row format."
    resolution = (
        "Open the file at the reported line and fix the row; `--validate` reports every "
        "offending line before anything is used."
    )


class DatasetEncodingError(DatasetFormatError):
    """Raised when no candidate encoding could decode a dataset's bytes.

    A subclass of :class:`DatasetFormatError` — bytes that cannot be decoded are
    a format problem — so handlers written against the broader class keep
    catching it.
    """

    code = "DT-2102"
    title = "Undecodable dataset"
    summary = "None of the configured text encodings could decode the file's bytes."
    resolution = "Re-save the file as UTF-8, or widen the encoding chain passed to the loader."


# -- operation errors ------------------------------------------------------
class OperationError(DatheonError):
    """Base class for errors raised by a tool's domain logic, not its inputs."""

    code = "DT-3000"
    title = "Operation failed"
    summary = "A domain operation could not be completed given the current state."
    resolution = "Read the message for the state that blocked the operation, then retry."


class InventoryError(OperationError):
    """Raised when an inventory operation is invalid."""

    code = "DT-3001"
    title = "Inventory operation refused"
    summary = "A checkout, return or registration violated the library's rules."
    resolution = "Check the item id and its current loan state before retrying."


class OrganizerError(OperationError):
    """Raised when the file organizer cannot complete a move."""

    code = "DT-3002"
    title = "File operation failed"
    summary = "A file could not be classified or moved into its category folder."
    resolution = "Check free space and permissions on the source and destination folders."


class PortfolioError(OperationError):
    """Raised when a portfolio operation cannot be completed."""

    code = "DT-3003"
    title = "Portfolio operation refused"
    summary = "A holding, price update or summary export violated the portfolio's rules."
    resolution = "Check the ticker and the price, then retry the command."


class AssistantError(OperationError):
    """Raised when the terminal assistant cannot build or use its intent model."""

    code = "DT-3004"
    title = "Assistant unavailable"
    summary = "The intent engine could not be constructed or queried."
    resolution = "Check that `intents.json` defines at least one valid intent."


# -- configuration ---------------------------------------------------------
class PersistenceError(OperationError, OSError):
    """Raised when a file could not be written atomically.

    Also an :class:`OSError`, so the project entry points — which already catch
    ``OSError`` — keep reporting a failed write without a traceback.
    """

    code = "DT-3005"
    title = "Write failed"
    summary = "A file could not be staged or published; the destination was left unchanged."
    resolution = (
        "Check that the destination directory is writable and has free space, and that no "
        "other program holds the file open, then re-run the command."
    )


class SchemaVersionError(PersistenceError):
    """Raised when a stored document's schema version is missing or unusable.

    A subclass of :class:`PersistenceError`, so a handler written against the
    broader class keeps catching it.
    """

    code = "DT-3006"
    title = "Unsupported document version"
    summary = "A saved document has no schema version, or one this tool cannot read."
    resolution = (
        "Re-create the document with the current tool. If it was written by a newer "
        "version, upgrade the Datheon tools first."
    )


class ConfigurationError(DatheonError):
    """Raised when the tool's own configuration is unusable."""

    code = "DT-4000"
    title = "Configuration error"
    summary = "A mapping, limit or setting the tool depends on is missing or invalid."
    resolution = "Review the configuration mentioned in the message and correct it."


def exit_code_for(exc: BaseException) -> int:
    """Return the process exit code appropriate for ``exc``.

    Args:
        exc: Any exception instance.

    Returns:
        ``exc.exit_code`` for a :class:`DatheonError`; the recorded code for a
        :class:`SystemExit`; otherwise :data:`EXIT_DATA_ERROR`, the standard
        shape of an unhandled user or data error.
    """
    if isinstance(exc, DatheonError):
        return exc.exit_code
    if isinstance(exc, SystemExit):
        return int(exc.code or EXIT_SUCCESS)
    return EXIT_DATA_ERROR
