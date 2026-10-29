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
"""

from __future__ import annotations

import argparse
import sys
from enum import IntEnum
from typing import Callable, Optional

from edge_cases import guard_broken_pipe
from errors import EXIT_DATA_ERROR, EXIT_SUCCESS, EXIT_USAGE_ERROR, DatheonError
from logging_config import finish_active_runs, shutdown_logging

__all__ = [
    "PROGRAM_VERSION",
    "EXIT_CODE_EPILOG",
    "ExitCode",
    "build_parser",
    "run_cli",
    "version_string",
]

#: Semantic version shared by every tool in the repository.
PROGRAM_VERSION = "1.0.0"

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
        A ``"<prog> <version>"`` string, for example ``"hangman 1.0.0"``.
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
