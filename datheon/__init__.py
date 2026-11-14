"""Distribution package for the Datheon Python Internship toolkit (Day 22).

The repository's five tools live under ``projects/`` in directories with
numeric prefixes (``01_hangman_game`` …), which keeps them in task-pack order
but makes them impossible to import with an ``import`` statement. That layout is
fine for ``python projects/01_hangman_game/main.py`` and for the test suite,
which extends ``sys.path`` itself, but it is not something ``pip`` can turn into
commands on the user's ``PATH``.

This package is the thin, importable layer that sits on top of that layout. It
owns nothing but the wiring:

* :mod:`datheon.cli` — one module per tool, each exposing ``main()``, plus the
  shared loader in :mod:`datheon.cli._registry` that puts the project
  directories on ``sys.path`` and imports the right ``main`` module;
* the ``[project.scripts]`` table in ``pyproject.toml``, which maps the five
  console-script names onto those ``main`` functions.

Nothing in here re-implements a tool. ``hangman``, ``portfolio``, ``organizer``,
``assistant`` and ``inventory`` remain the same entry points the ``python
projects/.../main.py`` invocation has always used, so the exit-code contract,
the logging setup and the tests that cover them keep applying unchanged.

Attributes:
    __version__: Distribution version. It mirrors
        :data:`cli_contract.PROGRAM_VERSION` — the version every tool prints for
        ``--version`` — and ``tests/test_packaging.py`` fails if the two ever
        drift apart.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: Version of the distributed package; kept in lock-step with the CLI banner.
__version__ = "1.1.0"
