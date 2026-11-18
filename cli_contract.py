"""Shared command-line contract for the five Datheon internship CLIs.

Every entry point under ``projects/`` builds its argument parser through
:func:`build_parser` so that the five tools present one consistent surface:

* ``-h`` / ``--help`` is available everywhere;
* ``--version`` prints ``"<prog> <version>"`` and exits with code ``0``;
* a malformed command line (unknown flag, missing value, bad type) exits with
  code ``2`` — the :data:`ExitCode.USAGE_ERROR` convention also used by
  :mod:`argparse` and POSIX ``sysexits`` ``EX_USAGE``;
* a user or data error (missing file, invalid input) exits with code ``1`` —
  :data:`ExitCode.DATA_ERROR`.

The codes are exposed as :class:`ExitCode` so callers can ``return
int(ExitCode.DATA_ERROR)`` instead of writing magic numbers, and
:func:`run_cli` maps uncaught domain errors onto the contract for the
``if __name__ == "__main__"`` blocks.

Since Day 7 the mapping is driven by the exception taxonomy in :mod:`errors`:
any :class:`~errors.DatheonError` knows its own ``exit_code`` and renders its
own user-facing message, so ``run_cli`` no longer has to infer the exit code
from the exception's builtin type.

The module also owns the shared ``--json`` output contract (maintenance-mode
backlog item). :func:`add_json_flag` puts the flag on every parser,
:func:`human_output` pushes the human-readable chatter to ``stderr`` while it
is active, and :class:`JsonEmitter` writes the one document that lands on
``stdout``. Exit codes are identical with and without the flag, so a script
can key off ``$?`` and still parse the payload.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager, redirect_stdout
from enum import IntEnum
from typing import Any, Callable, Dict, Iterator, Mapping, Optional

from edge_cases import guard_broken_pipe
from errors import EXIT_DATA_ERROR, EXIT_SUCCESS, EXIT_USAGE_ERROR, DatheonError
from logging_config import finish_active_runs, shutdown_logging

__all__ = [
    "JSON_SCHEMA_VERSION",
    "PROGRAM_VERSION",
    "EXIT_CODE_EPILOG",
    "ExitCode",
    "JsonEmitter",
    "add_json_flag",
    "build_parser",
    "human_output",
    "json_requested",
    "run_cli",
    "version_string",
]

#: Semantic version shared by every tool in the repository.
PROGRAM_VERSION = "1.1.0"

#: Version of the ``--json`` document schema.
#:
#: The envelope gains fields additively, so a consumer that reads
#: ``schema``/``ok``/``data`` keeps working when a tool adds a key. A bump
#: means a field was removed or changed meaning.
JSON_SCHEMA_VERSION = 1

#: Help epilog documenting the exit-code contract, appended to every parser.
EXIT_CODE_EPILOG = (
    "exit codes:\n"
    "  0  success\n"
    "  1  user or data error\n"
    "  2  usage error (invalid command line)\n"
)


class ExitCode(IntEnum):
    """Process exit codes shared by all five command-line tools.

    Attributes:
        OK: The command completed successfully.
        DATA_ERROR: A user or data error occurred (e.g. a missing input file).
        USAGE_ERROR: The command line itself was invalid.
    """

    OK = EXIT_SUCCESS
    DATA_ERROR = EXIT_DATA_ERROR
    USAGE_ERROR = EXIT_USAGE_ERROR


def version_string(prog: str) -> str:
    """Return the ``--version`` banner for a program.

    Args:
        prog: The program name as shown in help output.

    Returns:
        A ``"<prog> <version>"`` string, for example ``"hangman 1.1.0"``.
    """
    return f"{prog} {PROGRAM_VERSION}"


def build_parser(
    prog: str,
    description: str,
    *,
    epilog: Optional[str] = EXIT_CODE_EPILOG,
) -> argparse.ArgumentParser:
    """Create a parser pre-wired with the shared command-line contract.

    The returned parser always exposes ``-h/--help`` and ``--version`` and
    inherits :mod:`argparse`'s behaviour of exiting with code
    :data:`ExitCode.USAGE_ERROR` (``2``) when the command line is malformed.

    Args:
        prog: Program name used in the help and version output.
        description: One-line summary shown at the top of ``--help``.
        epilog: Trailing help text; defaults to the exit-code contract.

    Returns:
        A configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=version_string(prog),
        help="Show the program version and exit.",
    )
    return parser


def add_json_flag(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--json`` flag to a tool's parser.

    The flag is deliberately uniform across the five tools: it selects a
    machine-readable run, never a different code path through the domain
    logic. Use :func:`json_requested` to read it and :class:`JsonEmitter` to
    write the document.

    Args:
        parser: The parser returned by :func:`build_parser`.
    """
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help=(
            "Emit one machine-readable JSON document on stdout; human-readable "
            "output is redirected to stderr."
        ),
    )


def json_requested(args: argparse.Namespace) -> bool:
    """Return whether ``--json`` was passed.

    Args:
        args: Parsed command-line arguments.

    Returns:
        ``True`` when the parsed namespace carries ``--json``.
    """
    return bool(getattr(args, "json", False))


@contextmanager
def human_output(enabled: bool) -> Iterator[None]:
    """Send human-readable output to ``stderr`` while ``--json`` is active.

    A ``--json`` run writes exactly one document to ``stdout`` and nothing
    else, so the caller's banners, tables and prompts are redirected to
    ``stderr`` for the duration of the block. Diagnostics already go there:
    the logger and every ``run_cli`` error message use ``stderr``.

    Args:
        enabled: ``True`` for a ``--json`` run; ``False`` leaves ``stdout``
            untouched, which is what every non-JSON invocation gets.

    Yields:
        ``None``; the block runs with ``stdout`` either redirected or not.
    """
    if not enabled:
        yield
        return
    with redirect_stdout(sys.stderr):
        yield


class JsonEmitter:
    """Writes the single JSON document a ``--json`` run produces.

    The envelope is identical for every tool, so a caller can parse the output
    of ``hangman``, ``portfolio``, ``organizer``, ``assistant`` and
    ``inventory`` with one piece of code::

        {
          "tool": "organizer",
          "schema": 1,
          "version": "1.1.0",
          "command": "organize",
          "ok": true,
          "data": {"directory": "...", "summary": {"moved": 3}}
        }

    A failed run replaces ``data`` with ``error`` and sets ``ok`` to
    ``false``; the process exit code is unchanged, so ``$?`` stays the
    authoritative signal alongside the document.

    Args:
        tool: Program name, as shown by ``--version``.
        enabled: ``True`` when ``--json`` was passed. When ``False`` every
            method is a no-op, which keeps the entry points free of branches.
    """

    def __init__(self, tool: str, enabled: bool) -> None:
        self.tool: str = tool
        self.enabled: bool = enabled

    def success(
        self,
        command: str,
        data: Mapping[str, Any],
        *,
        code: int = int(ExitCode.OK),
    ) -> int:
        """Emit a success document and return the exit code to use.

        Args:
            command: The sub-command that ran (``"play"``, ``"validate"`` …).
            data: The tool-specific payload; must be JSON-serialisable.
            code: Exit code to return, so a caller can report a partial
                success (for example ``--validate`` on a corrupt dataset)
                while still emitting a well-formed document.

        Returns:
            ``code``, unchanged.
        """
        if self.enabled:
            self._write(self._document(command, ok=True, data=dict(data)))
        return int(code)

    def failure(self, command: str, exc: BaseException) -> None:
        """Emit a failure document for an exception the tool is about to raise.

        The entry point calls this from its ``except`` block and re-raises, so
        :func:`run_cli` still prints the human message on ``stderr`` and
        returns the documented exit code. A :class:`~errors.DatheonError`
        contributes its catalogue code, hint and context; any other exception
        contributes its type and message.

        Args:
            command: The sub-command that was running.
            exc: The exception that aborted the run.
        """
        if not self.enabled:
            return
        if isinstance(exc, DatheonError):
            error: Dict[str, Any] = exc.to_dict()
        else:
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "exit_code": int(ExitCode.DATA_ERROR),
            }
        self._write(self._document(command, ok=False, error=error))

    def _document(self, command: str, *, ok: bool, **payload: Any) -> Dict[str, Any]:
        """Build the envelope for one run.

        Args:
            command: The sub-command that ran.
            ok: Whether the run succeeded.
            **payload: ``data`` or ``error``, merged into the envelope.

        Returns:
            The JSON-ready envelope.
        """
        document: Dict[str, Any] = {
            "tool": self.tool,
            "schema": JSON_SCHEMA_VERSION,
            "version": PROGRAM_VERSION,
            "command": command,
            "ok": ok,
        }
        document.update(payload)
        return document

    @staticmethod
    def _write(document: Mapping[str, Any]) -> None:
        """Print one document, keys sorted so two runs diff cleanly.

        Args:
            document: The envelope to serialise.
        """
        print(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))


def run_cli(main: Callable[[], int]) -> int:
    """Run a CLI entry point, mapping domain and pipe errors onto the contract.

    Args:
        main: A zero-argument callable that returns a process exit code.

    Returns:
        The exit code returned by ``main``; :data:`ExitCode.OK` (``0``) when the
        user interrupted the program with ``Ctrl-C`` or the output pipe was
        closed early (for example ``organizer . | head``); the error's own
        :attr:`~errors.DatheonError.exit_code` when ``main`` raised a
        :class:`~errors.DatheonError` — printing its catalogue code and hint
        instead of a bare message; or :data:`ExitCode.DATA_ERROR` (``1``) if
        ``main`` raised a bare :class:`ValueError` or :class:`OSError` that was
        never converted into the taxonomy.

    Note:
        :class:`BrokenPipeError` is a subclass of :class:`OSError`, so it is
        caught first and treated as a normal early exit rather than a data
        error. :class:`~errors.DatheonError` is checked before the generic
        ``OSError`` branch because several taxonomy leaves — for instance
        :class:`~errors.MissingFileError` — are also :class:`OSError`
        subclasses.

        Whatever the outcome, the ``finally`` block first calls
        :func:`logging_config.finish_active_runs` and then
        :func:`logging_config.shutdown_logging`, so the rotating file handler a
        tool opened for ``--log-level`` is flushed and closed exactly once, at
        the process boundary, on the success path as well as on every error
        path.

        Since Day 10 the same ``finally`` block also closes the structured run:
        the outcome (``success``, ``error`` or ``interrupted``) and the total
        ``duration_ms`` are written as one ``run finished`` record that shares
        its ``run_id`` with every other record of the invocation, and a
        :class:`~errors.DatheonError` contributes its full
        :meth:`~errors.DatheonError.to_dict` payload. Nothing here changes a
        printed line or an exit code — only the log records gain structure.
    """
    outcome = "success"
    error_payload = None
    try:
        code = int(main())
    except BrokenPipeError:
        outcome = "interrupted"
        guard_broken_pipe()
        code = int(ExitCode.OK)
    except KeyboardInterrupt:
        outcome = "interrupted"
        print("\nInterrupted.", file=sys.stderr)
        code = int(ExitCode.OK)
    except DatheonError as exc:
        outcome = "error"
        error_payload = exc.to_dict()
        print(exc.render(), file=sys.stderr)
        code = int(exc.exit_code)
    except (ValueError, OSError) as exc:
        outcome = "error"
        error_payload = {
            "type": type(exc).__name__,
            "message": str(exc),
            "exit_code": int(ExitCode.DATA_ERROR),
        }
        print(f"! {exc}", file=sys.stderr)
        code = int(ExitCode.DATA_ERROR)
    else:
        if code != int(ExitCode.OK):
            outcome = "error"
    finally:
        finish_active_runs(outcome, error=error_payload)
        shutdown_logging()
    return code
