"""Loader shared by the five console-script shims (Day 22).

Why a loader at all
-------------------
The five tools live in ``projects/01_hangman_game`` … ``projects/05_inventory_oop``.
Those directories are not valid Python identifiers, and each tool's ``main.py``
imports its siblings by plain module name (``from hangman import HangmanGame``),
because that is what makes ``python projects/01_hangman_game/main.py`` work from
any directory. A console script cannot rely on either of those things: it is a
generated stub in the interpreter's ``bin`` directory, started from whatever the
user's working directory happens to be.

So this module reproduces, in one place, the two path manipulations the test
suite's ``conftest.py`` performs:

1. the distribution root goes on ``sys.path``, so the shared core modules
   (``cli_contract``, ``errors``, ``validators`` …) import by name;
2. the tool's own project directory goes on ``sys.path``, so its ``main.py`` can
   import ``hangman``/``portfolio``/``organizer``/``chatbot``/``models``.

Then it imports ``projects.<package>.main`` and calls it through
:func:`cli_contract.run_cli`, which is the same wrapper the ``if __name__ ==
"__main__"`` block uses. The result is that ``hangman --version``, ``hangman
--help`` and ``hangman`` behave identically to the ``python
projects/01_hangman_game/main.py`` invocation — same parser, same exit codes,
same logging setup, same tests.

Example:
    >>> spec = TOOLS_BY_NAME["organizer"]
    >>> spec.package
    '03_file_organizer_automation'
    >>> run("organizer", ["--help"])  # doctest: +SKIP
    0
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "TOOLS",
    "TOOLS_BY_NAME",
    "TOOL_NAMES",
    "ToolSpec",
    "load_entrypoint",
    "project_directory",
    "projects_root",
    "run",
]

#: Distribution root: the repository root in a checkout or editable install,
#: and ``site-packages`` in a wheel install. Either way it is the directory that
#: must be importable for ``cli_contract`` and ``projects`` to resolve.
_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ToolSpec:
    """One installable console script.

    Attributes:
        name: The console-script name — the word the user types in a shell, and
            the stem of the shim module in :mod:`datheon.cli`.
        package: Directory under ``projects/`` holding the tool.
        summary: One-line description, used by ``docs/packaging.md`` and the
            packaging tests.
    """

    name: str
    package: str
    summary: str


#: The five tools, in task-pack order. This tuple is the single source of truth
#: for the shims, the loader and ``tests/test_packaging.py``.
TOOLS: Tuple[ToolSpec, ...] = (
    ToolSpec("hangman", "01_hangman_game", "Console Hangman word game"),
    ToolSpec("portfolio", "02_portfolio_tracker", "Stock portfolio tracker"),
    ToolSpec("organizer", "03_file_organizer_automation", "Automated file organizer"),
    ToolSpec("assistant", "04_terminal_assistant", "Rule-based terminal assistant"),
    ToolSpec("inventory", "05_inventory_oop", "OOP inventory & asset system"),
)

#: Console-script name to spec lookup.
TOOLS_BY_NAME: Dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}

#: Console-script names in declaration order.
TOOL_NAMES: Tuple[str, ...] = tuple(spec.name for spec in TOOLS)


def _ensure_importable(*paths: Path) -> None:
    """Put each path on ``sys.path`` if it is not already there.

    The paths are inserted in reverse so the first argument ends up first on the
    path, ahead of anything an earlier call or the caller's environment added.

    Args:
        *paths: Directories to make importable, highest priority first.
    """
    for path in reversed(paths):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def projects_root() -> Path:
    """Return the directory that contains the five project packages.

    Returns:
        The absolute path of ``projects/``, resolved from the imported package
        so it is correct for a source checkout and for an installed wheel alike.
    """
    _ensure_importable(_ROOT)
    import projects  # noqa: PLC0415 - deferred so the root is importable first

    return Path(projects.__file__).resolve().parent


def project_directory(spec: ToolSpec) -> Path:
    """Return the directory holding one tool's source.

    Args:
        spec: The tool to locate.

    Returns:
        The absolute path of ``projects/<package>``.
    """
    return projects_root() / spec.package


def load_entrypoint(name: str) -> ModuleType:
    """Import the ``main`` module of one tool.

    Both the distribution root and the tool's project directory are added to
    ``sys.path`` before the import, which is what lets the tool's bare sibling
    imports resolve. The import is cached by :mod:`importlib`, so calling this
    twice is cheap.

    Args:
        name: Console-script name, one of :data:`TOOL_NAMES`.

    Returns:
        The imported ``projects.<package>.main`` module.

    Raises:
        KeyError: If ``name`` is not a known tool. The message lists the valid
            names, because this is a programming error rather than user input.
    """
    try:
        spec = TOOLS_BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown tool {name!r}; expected one of {', '.join(TOOL_NAMES)}") from None

    project_dir = project_directory(spec)
    _ensure_importable(_ROOT, project_dir)
    return importlib.import_module(f"projects.{spec.package}.main")


def run(name: str, argv: Optional[Sequence[str]] = None) -> int:
    """Run one tool and return the exit code a shell should see.

    The call is wrapped in :func:`cli_contract.run_cli` — exactly as the tool's
    own ``if __name__ == "__main__"`` block does — so ``Ctrl-C``, a closed pipe
    and every :class:`~errors.DatheonError` map onto the documented exit codes
    instead of a traceback.

    Args:
        name: Console-script name, one of :data:`TOOL_NAMES`.
        argv: Argument list to hand the tool, excluding the program name. When
            ``None`` the tool parses :data:`sys.argv`, which is what a console
            script started by a shell wants.

    Returns:
        ``0`` on success, ``1`` for a user or data error, ``2`` for a malformed
        command line — the contract documented in :mod:`cli_contract`.
    """
    from cli_contract import run_cli  # deferred: keeps import cost off --version

    module = load_entrypoint(name)
    args: Optional[List[str]] = None if argv is None else list(argv)
    return int(run_cli(lambda: module.main(args)))
