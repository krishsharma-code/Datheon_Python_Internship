"""``organizer`` console script — Automated file organizer (Day 22).

Installed from the ``[project.scripts]`` table in ``pyproject.toml``::

    organizer = "datheon.cli.organizer:main"

Running ``organizer`` is equivalent to ``python
projects/03_file_organizer_automation/main.py``: same argument parser, same exit codes, same
logging setup, same tests. The ``sys.path`` work that makes the numerically
prefixed project directory importable lives in
:mod:`datheon.cli._registry`.

Example:
    .. code-block:: console

        $ organizer --version
        organizer 1.1.0
        $ organizer --help
"""

from __future__ import annotations

from typing import Optional, Sequence

from datheon.cli._registry import run

__all__ = ["main"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run Automated file organizer.

    Args:
        argv: Command-line arguments excluding the program name. ``None`` reads
            :data:`sys.argv`, which is what the installed console script does.

    Returns:
        The process exit code: ``0`` on success, ``1`` for a user or data error,
        ``2`` for a malformed command line — the contract documented in
        :mod:`cli_contract`.
    """
    return run("organizer", argv)


if __name__ == "__main__":
    raise SystemExit(main())
