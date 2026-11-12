"""Pytest configuration for the Datheon internship repository.

The project folders use numeric prefixes (``01_hangman_game``, …) which are not
valid Python identifiers and therefore cannot be imported as packages. This
conftest adds the repository root and every project directory to ``sys.path``
so that the shared ``cli_contract`` module and the per-project modules
(``hangman``, ``portfolio``, ``organizer``, ``chatbot``, ``models``,
``inventory``) can be imported by their plain module names from the tests.

It also enforces the **Day 16 external-I/O isolation policy** for every test in
the suite, through three autouse fixtures:

* :func:`isolated_home` — ``HOME``, ``USERPROFILE``, the temp directories and
  ``DATHEON_LOG_DIR`` point at ``tmp_path``, so ``Path.home()`` and
  ``expanduser()`` can never reach the developer's real home directory;
* :func:`isolated_cwd` — the working directory is a per-test temp directory, so
  a stray relative write lands in the sandbox instead of the repository;
* :func:`forbid_network` — every socket entry point raises
  :class:`tests.io_fakes.RealIOAttempted`.

Clock isolation is opt-in rather than autouse: a frozen clock is only
meaningful relative to the module under test, so tests call
:func:`tests.io_fakes.install_fake_clock` (or the ``frozen_today`` fixture
below) themselves. ``tools/io_audit.py`` statically rejects any test that reads
the wall clock directly, which is what keeps that opt-in honest.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Iterator, List

import pytest

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


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Redirect every home- and temp-directory lookup into a private sandbox.

    The sandbox is a sibling of ``tmp_path`` rather than a child, so tests that
    assert "nothing was written" against their own ``tmp_path`` keep working.

    Args:
        tmp_path_factory: Pytest's directory factory.
        monkeypatch: Pytest's environment patcher.

    Returns:
        The fake home directory, for tests that want to assert on it.
    """
    sandbox = tmp_path_factory.mktemp("isolation")
    home = sandbox / "home"
    scratch = sandbox / "tmp"
    home.mkdir()
    scratch.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(scratch))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("DATHEON_LOG_DIR", str(scratch / "datheon-logs"))
    return home


@pytest.fixture(autouse=True)
def isolated_cwd(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Run every test from a per-test working directory.

    Args:
        tmp_path_factory: Pytest's directory factory.
        monkeypatch: Pytest's chdir helper.

    Yields:
        The directory the test runs in.
    """
    workdir = tmp_path_factory.mktemp("cwd")
    workdir.mkdir(exist_ok=True)
    monkeypatch.chdir(workdir)
    yield workdir


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> "object":
    """Block every socket entry point for the duration of a test.

    Args:
        monkeypatch: Pytest's attribute patcher.

    Returns:
        The installed :class:`tests.io_fakes.NetworkGuard`, whose ``attempts``
        list records blocked calls.
    """
    from tests.io_fakes import NetworkGuard

    return NetworkGuard().install(monkeypatch)


@pytest.fixture
def frozen_today(monkeypatch: pytest.MonkeyPatch) -> date:
    """Freeze ``date.today()`` inside the modules that read it.

    Patching the two modules that consult the calendar — ``validators`` and the
    inventory ``models`` — keeps date-boundary tests stable no matter what day
    the suite runs on.

    Args:
        monkeypatch: Pytest's attribute patcher.

    Returns:
        The frozen date, so a test can build offsets from it.
    """
    from tests.io_fakes import install_fake_date

    frozen = date(2026, 11, 4)
    patched: List[str] = []
    for name in ("validators", "models"):
        module = sys.modules.get(name)
        if module is not None:
            install_fake_date(monkeypatch, module, frozen)
            patched.append(name)
    assert patched, "frozen_today needs validators or models to be imported first"
    return frozen
