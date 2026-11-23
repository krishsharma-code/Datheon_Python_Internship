"""Fixtures shared by the benchmark modules.

The benchmark suite is deliberately *outside* ``testpaths`` in
``pyproject.toml``: ``pytest`` at the repository root runs the hermetic unit
suite and nothing else, and ``pytest benchmarks/`` runs the timings. That split
is what keeps a timing from ever failing a correctness run — and a correctness
run from ever being slowed down by one.

The fixtures here only locate the bundled datasets. Every path is resolved from
this file rather than from the working directory, because the root ``conftest``
gives each test its own temporary working directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: Repository root, resolved from this file so the suite runs from any directory.
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def words_path() -> Path:
    """Return the bundled hangman word list.

    Returns:
        Path to ``projects/01_hangman_game/data/words.txt``.
    """
    path = ROOT / "projects" / "01_hangman_game" / "data" / "words.txt"
    if not path.is_file():  # pragma: no cover - the dataset ships with the repository
        pytest.skip(f"word list missing: {path}")
    return path


@pytest.fixture(scope="session")
def constituents_path() -> Path:
    """Return the bundled S&P 500 constituents table.

    Returns:
        Path to ``projects/02_portfolio_tracker/data/constituents.csv``.
    """
    path = ROOT / "projects" / "02_portfolio_tracker" / "data" / "constituents.csv"
    if not path.is_file():  # pragma: no cover - the dataset ships with the repository
        pytest.skip(f"constituents table missing: {path}")
    return path
