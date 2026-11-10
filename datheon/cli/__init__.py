"""Console-script shims for the five Datheon tools (Day 22).

Importing this package is deliberately cheap: it pulls in no project module and
starts no logging. Each shim module does its own import of the tool it fronts,
so ``hangman --version`` never pays for loading the portfolio tracker.

The tools are installed as console scripts through the ``[project.scripts]``
table in ``pyproject.toml``::

    hangman   = "datheon.cli.hangman:main"
    portfolio = "datheon.cli.portfolio:main"
    organizer = "datheon.cli.organizer:main"
    assistant = "datheon.cli.assistant:main"
    inventory = "datheon.cli.inventory:main"

:data:`datheon.cli._registry.TOOLS` is the single list those five modules, the
loader and the tests all agree on.
"""

from __future__ import annotations

__all__: list[str] = []
