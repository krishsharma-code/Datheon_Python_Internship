"""Packaging and console-entry-point tests (Day 22).

Day 22's definition of done is narrow and testable: *``pip install -e .`` exposes
``hangman``, ``portfolio``, ``organizer``, ``assistant`` and ``inventory``*. The
way that claim rots is quietly — a rename under ``projects/``, a shim deleted
from ``datheon/cli/``, a ``[project.scripts]`` row pointing at a function that
no longer exists — and none of it shows up until a user types the command.

So the assertions here are structural rather than anecdotal:

* **The declaration.** ``pyproject.toml`` is parsed with :mod:`tomllib` and the
  ``[project.scripts]`` table is compared with :data:`datheon.cli._registry.TOOLS`
  in both directions, so a tool cannot be added to one without the other.
* **The wiring.** Every declared target is imported and its ``main`` called, so
  a broken entry point fails here instead of on a user's ``PATH``. The
  ``--version`` cases drive the whole chain — shim, loader, project module,
  parser — and assert the banner and the exit code.
* **The layout.** The shared core modules shipped as ``py-modules`` must match
  the ``*.py`` files actually sitting at the repository root, and the registry
  must name every directory under ``projects/``.

Nothing here installs anything or starts a subprocess: the installed script is a
two-line stub that calls the same ``main`` these tests call directly, so calling
``main`` is the faithful check and it stays hermetic. A separate assertion
inspects :mod:`importlib.metadata` and skips itself when the distribution is not
installed, which is the case in a plain ``pytest`` run from a checkout.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Dict, List, Optional

import pytest

from cli_contract import PROGRAM_VERSION
from datheon import __version__ as package_version
from datheon.cli import _registry

try:  # Python 3.11+ has tomllib in the standard library; 3.10 needs tomli.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the 3.10 CI cell
    import tomli as tomllib  # type: ignore[no-redef]

from datheon.cli._registry import (  # noqa: E402  (path set up by conftest)
    TOOL_NAMES,
    TOOLS,
    TOOLS_BY_NAME,
    load_entrypoint,
    project_directory,
    projects_root,
    run,
)

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
PROJECTS_DIR = ROOT / "projects"

#: Distribution name declared in ``pyproject.toml``.
DIST_NAME = "datheon-python-internship"


@pytest.fixture(scope="module")
def pyproject() -> Dict[str, object]:
    """Return the parsed ``pyproject.toml``.

    Returns:
        The whole document as nested dictionaries.
    """
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _scripts(pyproject: Dict[str, object]) -> Dict[str, str]:
    """Extract the ``[project.scripts]`` table.

    Args:
        pyproject: Parsed ``pyproject.toml``.

    Returns:
        Console-script name to ``"module:callable"`` target.
    """
    project = pyproject["project"]
    assert isinstance(project, dict)
    scripts = project["scripts"]
    assert isinstance(scripts, dict)
    return scripts


# ---------------------------------------------------------------------------
# Versioning — one number, two places
# ---------------------------------------------------------------------------


def test_package_version_matches_the_cli_banner() -> None:
    """``datheon.__version__`` must equal the ``--version`` string's version."""
    assert package_version == PROGRAM_VERSION


def test_pyproject_version_matches_the_package(pyproject: Dict[str, object]) -> None:
    """The metadata version, the package attribute and the banner all agree."""
    project = pyproject["project"]
    assert isinstance(project, dict)
    assert project["version"] == package_version


# ---------------------------------------------------------------------------
# The declaration: pyproject.toml
# ---------------------------------------------------------------------------


def test_build_system_is_declared(pyproject: Dict[str, object]) -> None:
    """A PEP 517 build backend must be present for ``pip install`` to work."""
    build_system = pyproject["build-system"]
    assert isinstance(build_system, dict)
    assert build_system["build-backend"] == "setuptools.build_meta"
    requires = build_system["requires"]
    assert any(requirement.startswith("setuptools") for requirement in requires)


def test_project_metadata_is_pep_621_complete(pyproject: Dict[str, object]) -> None:
    """The fields a consumer of the distribution sees are all filled in."""
    project = pyproject["project"]
    assert isinstance(project, dict)
    assert project["name"] == DIST_NAME
    assert project["readme"] == "README.md"
    assert project["requires-python"].startswith(">=3.10")
    assert project["license"] == "MIT"
    assert project["dependencies"] == []
    authors = project["authors"]
    assert isinstance(authors, list) and authors[0]["name"] == "Krish Sharma"


def test_console_scripts_cover_every_tool_exactly_once(
    pyproject: Dict[str, object],
) -> None:
    """``[project.scripts]`` and the registry list the same five names."""
    assert set(_scripts(pyproject)) == set(TOOL_NAMES)
    assert len(_scripts(pyproject)) == len(TOOL_NAMES) == 5


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_console_script_target_is_the_matching_shim(
    pyproject: Dict[str, object], name: str
) -> None:
    """Each command points at its own shim's ``main``."""
    assert _scripts(pyproject)[name] == f"datheon.cli.{name}:main"


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_console_script_target_resolves_to_a_callable(name: str) -> None:
    """Every declared target imports and yields a callable ``main``."""
    module_path, _, attribute = f"datheon.cli.{name}:main".partition(":")
    module = importlib.import_module(module_path)
    target = getattr(module, attribute)
    assert callable(target)


def test_py_modules_are_exactly_the_root_modules(pyproject: Dict[str, object]) -> None:
    """The ``py-modules`` list tracks the shared core files at the root.

    ``conftest.py`` is test-only and deliberately not shipped.
    """
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    setuptools = tool["setuptools"]
    assert isinstance(setuptools, dict)
    declared = set(setuptools["py-modules"])
    on_disk = {path.stem for path in ROOT.glob("*.py")} - {"conftest"}
    assert declared == on_disk


def test_package_data_covers_the_bundled_datasets(pyproject: Dict[str, object]) -> None:
    """The word list and the sample CSVs are declared for inclusion."""
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    setuptools = tool["setuptools"]
    assert isinstance(setuptools, dict)
    package_data = setuptools["package-data"]
    assert isinstance(package_data, dict)
    patterns = package_data["*"]
    assert "data/*.txt" in patterns
    assert "data/*.csv" in patterns


def test_packages_find_includes_the_shim_package(pyproject: Dict[str, object]) -> None:
    """Package discovery must pick up ``datheon`` and the five tools."""
    tool = pyproject["tool"]
    assert isinstance(tool, dict)
    setuptools = tool["setuptools"]
    assert isinstance(setuptools, dict)
    find = setuptools["packages"]["find"]
    assert "datheon*" in find["include"]
    assert "projects*" in find["include"]


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_registry_names_every_project_directory() -> None:
    """Each ``projects/<dir>`` is registered, and nothing phantom is."""
    on_disk = {
        path.name
        for path in PROJECTS_DIR.iterdir()
        if path.is_dir() and (path / "main.py").is_file()
    }
    assert {spec.package for spec in TOOLS} == on_disk


def test_registry_names_are_unique_and_ordered() -> None:
    """No duplicate console-script names, and ``TOOL_NAMES`` follows ``TOOLS``."""
    assert len(set(TOOL_NAMES)) == len(TOOLS)
    assert TOOL_NAMES == tuple(spec.name for spec in TOOLS)
    assert set(TOOLS_BY_NAME) == set(TOOL_NAMES)


def test_every_spec_has_a_summary() -> None:
    """The one-line summaries are used by the docs, so they cannot be empty."""
    assert all(spec.summary.strip() for spec in TOOLS)


def test_projects_root_holds_the_five_packages() -> None:
    """The loader resolves ``projects/`` to the repository's own directory."""
    root = projects_root()
    assert root.is_dir()
    assert root.name == "projects"
    for spec in TOOLS:
        assert (root / spec.package / "main.py").is_file()


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_project_directory_is_absolute_and_inside_the_repo(name: str) -> None:
    """``project_directory`` never returns a relative or foreign path."""
    directory = project_directory(TOOLS_BY_NAME[name])
    assert directory.is_absolute()
    assert directory.parent == projects_root()


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_load_entrypoint_returns_the_project_main_module(name: str) -> None:
    """The loaded module is the tool's own ``main.py``, with a callable ``main``."""
    module = load_entrypoint(name)
    assert isinstance(module, ModuleType)
    assert callable(module.main)
    assert Path(module.__file__).resolve() == project_directory(TOOLS_BY_NAME[name]) / "main.py"


def test_load_entrypoint_is_cached_by_importlib() -> None:
    """Loading twice hands back the identical module object, not a copy."""
    assert load_entrypoint("hangman") is load_entrypoint("hangman")


def test_load_entrypoint_unknown_tool_lists_the_valid_names() -> None:
    """An unknown name is a programming error, reported with the alternatives."""
    with pytest.raises(KeyError) as excinfo:
        load_entrypoint("solitaire")
    message = str(excinfo.value)
    assert "unknown tool 'solitaire'" in message
    for name in TOOL_NAMES:
        assert name in message


def test_ensure_importable_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-adding a path that is already present does not duplicate it."""
    import sys

    with monkeypatch.context() as patch:
        patch.setattr(sys, "path", list(sys.path))
        before = list(sys.path)
        _registry._ensure_importable(ROOT)
        _registry._ensure_importable(ROOT)
        assert sys.path.count(str(ROOT)) == before.count(str(ROOT))


# ---------------------------------------------------------------------------
# run(): the shared dispatch
# ---------------------------------------------------------------------------


def _fake_module(recorded: List[Optional[List[str]]], code: int = 0) -> ModuleType:
    """Build a stand-in ``main`` module that records the argv it was given.

    Args:
        recorded: List the fake appends its received ``argv`` to.
        code: Exit code the fake ``main`` returns.

    Returns:
        A module object exposing ``main``.
    """

    def main(argv: Optional[List[str]] = None) -> int:
        recorded.append(argv)
        return code

    return SimpleNamespace(main=main)  # type: ignore[return-value]


def test_run_returns_the_project_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run`` forwards to the tool and hands back its exit code unchanged."""
    recorded: List[Optional[List[str]]] = []
    monkeypatch.setattr(_registry, "load_entrypoint", lambda name: _fake_module(recorded, 3))
    assert run("hangman", ["--version"]) == 3
    assert recorded == [["--version"]]


def test_run_forwards_none_so_the_tool_reads_sys_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A console script passes no ``argv``, so ``None`` must reach the tool."""
    recorded: List[Optional[List[str]]] = []
    monkeypatch.setattr(_registry, "load_entrypoint", lambda name: _fake_module(recorded))
    assert run("portfolio") == 0
    assert recorded == [None]


def test_run_copies_the_argument_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tuple or any sequence is normalised to a list the tool can mutate."""
    recorded: List[Optional[List[str]]] = []
    monkeypatch.setattr(_registry, "load_entrypoint", lambda name: _fake_module(recorded))
    run("organizer", ("--dry-run", "."))
    assert recorded == [["--dry-run", "."]]


def test_run_maps_a_bare_value_error_onto_the_data_error_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``run`` goes through ``run_cli``, so an uncaught error exits 1, not 1 with a traceback."""

    def explode(argv: Optional[List[str]] = None) -> int:
        raise ValueError("bad portfolio row")

    monkeypatch.setattr(_registry, "load_entrypoint", lambda name: SimpleNamespace(main=explode))
    assert run("portfolio", []) == 1
    assert "bad portfolio row" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The shims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_shim_forwards_argv_to_the_registry(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each shim is a one-line delegation — nothing more, nothing less."""
    shim = importlib.import_module(f"datheon.cli.{name}")
    recorded: List[object] = []

    def fake_run(tool: str, argv: object = None) -> int:
        recorded.append((tool, argv))
        return 42

    monkeypatch.setattr(shim, "run", fake_run)
    assert shim.main(["--flag"]) == 42
    assert recorded == [(name, ["--flag"])]


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_installed_command_prints_its_version_and_exits_zero(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """``<tool> --version`` prints the banner and exits 0 — end to end.

    This drives shim → loader → project ``main`` → parser, which is exactly the
    chain a console script started by a shell follows.
    """
    shim = importlib.import_module(f"datheon.cli.{name}")
    with pytest.raises(SystemExit) as excinfo:
        shim.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"{name} {PROGRAM_VERSION}"


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_installed_command_rejects_a_bad_flag_with_exit_two(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown flag exits 2 from the console script, as the contract says."""
    shim = importlib.import_module(f"datheon.cli.{name}")
    with pytest.raises(SystemExit) as excinfo:
        shim.main(["--definitely-not-a-flag"])
    assert excinfo.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_hangman_shim_help_mentions_the_exit_code_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--help`` reaches the tool's own parser, not the shim's."""
    shim = importlib.import_module("datheon.cli.hangman")
    with pytest.raises(SystemExit) as excinfo:
        shim.main(["--help"])
    assert excinfo.value.code == 0
    assert "exit codes" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Installed metadata (skipped in a bare checkout)
# ---------------------------------------------------------------------------


def test_distribution_declares_the_five_console_scripts() -> None:
    """When the package is installed, its metadata carries the five scripts."""
    from importlib import metadata

    try:
        distribution = metadata.distribution(DIST_NAME)
    except metadata.PackageNotFoundError:  # pragma: no cover - depends on install
        pytest.skip(f"{DIST_NAME} is not installed in this environment")

    declared = {
        entry.name for entry in distribution.entry_points if entry.group == "console_scripts"
    }
    assert set(TOOL_NAMES) <= declared
