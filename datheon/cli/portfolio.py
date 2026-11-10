"""``portfolio`` console script — Stock portfolio tracker (Day 22).

Installed from the ``[project.scripts]`` table in ``pyproject.toml``::

    portfolio = "datheon.cli.portfolio:main"

Running ``portfolio`` is equivalent to ``python
projects/02_portfolio_tracker/main.py``: same argument parser, same exit codes, same
logging setup, same tests. The ``sys.path`` work that makes the numerically
prefixed project directory importable lives in
:mod:`datheon.cli._registry`.

Example:
    .. code-block:: console

        $ portfolio --version
        portfolio 1.0.0
        $ portfolio --help
"""

from __future__ import annotations

from typing import Optional, Sequence

from datheon.cli._registry import run

__all__ = ["main"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run Stock portfolio tracker.

    Args:
        argv: Command-line arguments excluding the program name. ``None`` reads
            :data:`sys.argv`, which is what the installed console script does.

    Returns:
        The process exit code: ``0`` on success, ``1`` for a user or data error,
        ``2`` for a malformed command line — the contract documented in
        :mod:`cli_contract`.
    """
    return run("portfolio", argv)


if __name__ == "__main__":
    raise SystemExit(main())
