"""Count test functions and assertion statements across the pytest suite (Day 15).

Day 15 of the internship plan sets a concrete, checkable quality target: the
suite must cross **200+ assertions**. Until now that number lived only in the
plan document, which meant it could drift silently — a refactor could delete a
dozen assertions and nothing would notice.

This module turns the target into a measurement. It parses every ``test_*.py``
file with :mod:`ast` (so assertions inside helpers, parametrised bodies and
nested ``with`` blocks are all counted, and nothing is fooled by the word
``assert`` appearing inside a string or a comment) and reports, per module, how
many ``assert`` statements and how many test functions it contains.

Run it directly::

    python tools/assertion_census.py                 # human-readable table
    python tools/assertion_census.py --json          # machine-readable
    python tools/assertion_census.py --minimum 200   # exit 1 if below budget

The ``--minimum`` form is what a CI step or the Day 20 coverage gate can use:
the process exit code is the verdict, so a shrinking suite fails loudly instead
of quietly.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: Directory scanned by default.
DEFAULT_TESTS_DIR = ROOT / "tests"

#: The Day 15 budget from ``docs/internship_milestones_plan.md``.
ASSERTION_BUDGET = 200


@dataclass(frozen=True)
class ModuleCensus:
    """Assertion and test counts for a single test module.

    Attributes:
        path: Path of the module relative to the repository root, POSIX-style.
        tests: Number of ``def test_*`` functions defined in the module.
        asserts: Number of ``assert`` statements anywhere in the module.
    """

    path: str
    tests: int
    asserts: int


def _count_node(node: ast.AST) -> tuple[int, int]:
    """Count test functions and assert statements in one syntax tree.

    Args:
        node: The module (or subtree) to walk.

    Returns:
        A ``(tests, asserts)`` pair. A nested function whose name starts with
        ``test_`` counts as a test, which matches how pytest collects module
        level definitions.
    """
    tests = 0
    asserts = 0
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name.startswith("test_"):
                tests += 1
        elif isinstance(child, ast.Assert):
            asserts += 1
    return tests, asserts


def census(tests_dir: Path = DEFAULT_TESTS_DIR, *, root: Path = ROOT) -> List[ModuleCensus]:
    """Measure every ``test_*.py`` module under ``tests_dir``.

    Args:
        tests_dir: Directory holding the pytest modules.
        root: Repository root used to make reported paths relative.

    Returns:
        One :class:`ModuleCensus` per module, sorted by path. Files that fail
        to parse are skipped rather than crashing the census; a broken test
        module already fails the suite on its own.
    """
    results: List[ModuleCensus] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):  # pragma: no cover - defensive
            continue
        tests, asserts = _count_node(tree)
        try:
            label = path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - tests dir outside the repo
            label = path.as_posix()
        results.append(ModuleCensus(path=label, tests=tests, asserts=asserts))
    return results


def totals(rows: Iterable[ModuleCensus]) -> tuple[int, int]:
    """Sum the census rows.

    Args:
        rows: Census rows to aggregate.

    Returns:
        A ``(total_tests, total_asserts)`` pair.
    """
    rows = list(rows)
    return sum(row.tests for row in rows), sum(row.asserts for row in rows)


def render(rows: Sequence[ModuleCensus]) -> str:
    """Render a census as a fixed-width table plus a totals line.

    Args:
        rows: Census rows to render.

    Returns:
        The formatted report, without a trailing newline.
    """
    width = max((len(row.path) for row in rows), default=4)
    lines = [f"{'module'.ljust(width)}  tests  asserts", f"{'-' * width}  -----  -------"]
    for row in rows:
        lines.append(f"{row.path.ljust(width)}  {row.tests:>5}  {row.asserts:>7}")
    total_tests, total_asserts = totals(rows)
    lines.append(f"{'-' * width}  -----  -------")
    lines.append(f"{'TOTAL'.ljust(width)}  {total_tests:>5}  {total_asserts:>7}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the census tool.

    Returns:
        A parser accepting ``--tests-dir``, ``--json`` and ``--minimum``.
    """
    parser = argparse.ArgumentParser(
        prog="assertion_census",
        description="Count pytest test functions and assertion statements.",
    )
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=DEFAULT_TESTS_DIR,
        help="Directory holding the test modules (default: <repo>/tests).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    parser.add_argument(
        "--minimum",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Exit 1 when the total assertion count is below N "
            f"(the plan's Day 15 budget is {ASSERTION_BUDGET})."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the census and report the verdict through the exit code.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        ``0`` when the census passes (or no minimum was given), ``1`` when the
        total assertion count falls below ``--minimum``.
    """
    args = build_parser().parse_args(argv)
    rows = census(args.tests_dir)
    total_tests, total_asserts = totals(rows)

    if args.json:
        payload = {
            "modules": [asdict(row) for row in rows],
            "total_tests": total_tests,
            "total_asserts": total_asserts,
            "minimum": args.minimum,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render(rows))
        print(f"\n{total_tests} test functions, {total_asserts} assertions.")

    if args.minimum is not None and total_asserts < args.minimum:
        print(
            f"! assertion budget missed: {total_asserts} < {args.minimum}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
