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
    """
    try:
        return int(main())
    except BrokenPipeError:
        guard_broken_pipe()
        return int(ExitCode.OK)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return int(ExitCode.OK)
    except DatheonError as exc:
        print(exc.render(), file=sys.stderr)
        return int(exc.exit_code)
    except (ValueError, OSError) as exc:
        print(f"! {exc}", file=sys.stderr)
        return int(ExitCode.DATA_ERROR)
