"""Tests for the assertion census and the Day 15 200+ assertion gate.

The plan's Day 15 deliverable is a number: the suite must cross **200+
assertions**. A number nobody measures is a claim, not a gate. These tests hold
the suite to the budget on every run and check the census tool that produces
it — including the ways the count could lie to us (an ``assert`` inside a
docstring, a comment, or a skipped module).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import List

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"

#: The Day 15 budget from ``docs/internship_milestones_plan.md``.
ASSERTION_BUDGET = 200

#: A test module smaller than this is probably a stub that never grew.
MIN_ASSERTS_PER_MODULE = 10

#: Each project must keep at least one dedicated test module.
PROJECT_TEST_MODULES = {
    "01_hangman_game": "tests/test_hangman.py",
    "02_portfolio_tracker": "tests/test_portfolio.py",
    "03_file_organizer_automation": "tests/test_organizer.py",
    "04_terminal_assistant": "tests/test_chatbot.py",
    "05_inventory_oop": "tests/test_inventory.py",
}


@lru_cache(maxsize=None)
def _census_module() -> ModuleType:
    """Import ``tools/assertion_census.py`` by path.

    Args:
        None.

    Returns:
        The imported census module.
    """
    path = ROOT / "tools" / "assertion_census.py"
    spec = importlib.util.spec_from_file_location("datheon_assertion_census", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["datheon_assertion_census"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rows() -> List[object]:
    """Census rows for the whole suite, computed once per module.

    Returns:
        The census rows.
    """
    return _census_module().census(TESTS_DIR, root=ROOT)


# --- the gate ----------------------------------------------------------
def test_suite_crosses_the_200_assertion_budget(rows: List[object]) -> None:
    _, total_asserts = _census_module().totals(rows)
    assert (
        total_asserts >= ASSERTION_BUDGET
    ), f"suite dropped to {total_asserts} assertions; the Day 15 budget is {ASSERTION_BUDGET}"


def test_suite_keeps_a_broad_test_function_count(rows: List[object]) -> None:
    total_tests, _ = _census_module().totals(rows)
    assert total_tests >= 150


def test_every_test_module_carries_real_assertions(rows: List[object]) -> None:
    thin = [row.path for row in rows if row.asserts < MIN_ASSERTS_PER_MODULE]
    assert thin == [], f"thin test modules: {thin}"


def test_every_module_reports_a_positive_test_count(rows: List[object]) -> None:
    assert [row.path for row in rows if row.tests == 0] == []


@pytest.mark.parametrize("project,module", sorted(PROJECT_TEST_MODULES.items()))
def test_each_project_has_a_dedicated_test_module(
    project: str, module: str, rows: List[object]
) -> None:
    paths = {row.path for row in rows}
    assert (ROOT / "projects" / project).is_dir()
    assert module in paths
    assert (ROOT / module).is_file()


def test_cli_entrypoints_are_covered_by_a_test_module(rows: List[object]) -> None:
    assert "tests/test_cli_entrypoints.py" in {row.path for row in rows}


# --- the census tool ---------------------------------------------------
def test_census_paths_are_repository_relative(rows: List[object]) -> None:
    assert rows, "census found no test modules"
    assert all(row.path.startswith("tests/") for row in rows)
    assert all(row.path.endswith(".py") for row in rows)


def test_count_node_ignores_docstrings_and_comments() -> None:
    tree = ast.parse(
        "def test_example():\n"
        '    """assert this is a docstring, not a statement."""\n'
        "    # assert inside a comment\n"
        '    value = "assert inside a string"\n'
        "    return value\n"
    )
    assert _census_module()._count_node(tree) == (1, 0)


def test_count_node_counts_nested_asserts() -> None:
    tree = ast.parse(
        "def test_example():\n"
        "    with open('x') as handle:\n"
        "        assert handle is not None\n"
        "    for item in (1, 2):\n"
        "        assert item\n"
    )
    assert _census_module()._count_node(tree) == (1, 2)


def test_render_prints_a_totals_line(rows: List[object]) -> None:
    report = _census_module().render(rows)
    total_tests, total_asserts = _census_module().totals(rows)
    assert "module" in report.splitlines()[0]
    assert "TOTAL" in report
    assert str(total_asserts) in report
    assert str(total_tests) in report


def test_main_passes_the_budget(capsys) -> None:
    assert _census_module().main(["--tests-dir", str(TESTS_DIR), "--minimum", "200"]) == 0
    out = capsys.readouterr().out
    assert "assertions." in out
    assert "TOTAL" in out


def test_main_fails_when_the_budget_is_missed(capsys) -> None:
    code = _census_module().main(["--tests-dir", str(TESTS_DIR), "--minimum", "1000000"])
    captured = capsys.readouterr()
    assert code == 1
    assert "assertion budget missed" in captured.err


def test_main_emits_parseable_json(capsys) -> None:
    assert _census_module().main(["--tests-dir", str(TESTS_DIR), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_asserts"] >= ASSERTION_BUDGET
    assert payload["total_tests"] == sum(row["tests"] for row in payload["modules"])
    assert payload["minimum"] is None
