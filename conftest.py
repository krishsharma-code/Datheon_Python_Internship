"""Pytest configuration for the Datheon internship repository.

The project folders use numeric prefixes (``01_hangman_game``, …) which are not
valid Python identifiers and therefore cannot be imported as packages. This
conftest adds the repository root and every project directory to ``sys.path``
so that the shared ``cli_contract`` module and the per-project modules
(``hangman``, ``portfolio``, ``organizer``, ``chatbot``, ``models``,
``inventory``) can be imported by their plain module names from the tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"

# Repository root first, so the shared CLI-contract module imports by name.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if PROJECTS_DIR.is_dir():
    for project in sorted(PROJECTS_DIR.iterdir()):
        if project.is_dir():
            entry = str(project)
            if entry not in sys.path:
                sys.path.insert(0, entry)
