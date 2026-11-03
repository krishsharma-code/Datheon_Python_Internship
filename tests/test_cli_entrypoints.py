"""In-process tests for the five CLI entry points (Day 15).

Days 3–14 covered the *libraries* under ``projects/`` thoroughly, but the
``main.py`` wiring in front of them — roughly 1,200 lines of argument parsing,
option precedence, demo setup and report printing — was only ever exercised
through a handful of subprocess smoke tests. A subprocess run proves the tool
starts; it does not prove that ``--workers`` still folds into ``--concurrency``,
that ``--borrow-date`` reaches the item, or that a blank line still ends the
interactive prompt loop.

These tests import each entry point as a module and call ``main(argv)``
directly, so every branch of the CLI layer runs under the same coverage
instrumentation as the rest of the suite. Two rules keep them honest:

* **Hermetic.** ``DATHEON_LOG_DIR`` is redirected into ``tmp_path`` and every
  path a tool would write (summary CSV, activity log, conversation log) is
  passed explicitly, so a test run never touches the repository or the user's
  home directory.
* **Faithful to the process boundary.** Where ``main`` is expected to fail, the
  call is wrapped in :func:`cli_contract.run_cli` — the same shim the
  ``if __name__ == "__main__"`` block uses — so the assertions check the exit
  code a user actually sees rather than an internal exception type.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Iterator, List

import pytest

from cli_contract import ExitCode, run_cli

ROOT = Path(__file__).resolve().parents[1]

#: ``(program name, project directory)`` for each of the five tools.
PROJECTS = [
    ("hangman", "01_hangman_game"),
    ("portfolio", "02_portfolio_tracker"),
    ("organizer", "03_file_organizer_automation"),
    ("assistant", "04_terminal_assistant"),
    ("inventory", "05_inventory_oop"),
]

PROJECT_IDS = [name for name, _ in PROJECTS]


@lru_cache(maxsize=None)
def _entrypoint(project_dir: str) -> ModuleType:
    """Import a project's ``main.py`` under a unique, non-colliding module name.

    The project folders use numeric prefixes and each one ships a module called
    ``main``, so a plain ``import main`` would collide. Loading by file path
    under a namespaced alias keeps the five entry points side by side.

    Args:
        project_dir: Directory name under ``projects/``.

    Returns:
        The imported entry-point module.
    """
    path = ROOT / "projects" / project_dir / "main.py"
    alias = f"datheon_entry_{project_dir}"
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def _main(project_dir: str) -> object:
    """Return the ``main`` callable of a project's entry point.

    Args:
        project_dir: Directory name under ``projects/``.

    Returns:
        The module's ``main`` function.
    """
    return _entrypoint(project_dir).main


@pytest.fixture(autouse=True)
def hermetic_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every tool's log directory at ``tmp_path`` for the test's lifetime.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Fixture used to set the environment variable.

    Yields:
        The log directory the tools will use.
    """
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("DATHEON_LOG_DIR", str(log_dir))
    yield log_dir


def _code(project: str, argv: List[str]) -> int:
    """Run an entry point in-process and normalise the exit code.

    Most paths return an :class:`~cli_contract.ExitCode`, but a closed input
    stream deliberately raises ``SystemExit(0)`` from inside the interactive
    loops (the Day 5 edge-case policy). Calling through this helper keeps each
    test about behaviour rather than about which of the two shapes was used.

    Args:
        project: Directory name under ``projects/``.
        argv: Argument vector passed to the tool's ``main``.

    Returns:
        The process exit code the tool would have produced.
    """
    try:
        return int(_main(project)(argv))
    except SystemExit as exc:
        return int(exc.code or 0)


def _stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Replace ``sys.stdin`` with a scripted stream.

    An empty string makes every ``input()`` raise ``EOFError``, which is how the
    tools detect a closed stream.

    Args:
        monkeypatch: Fixture used to swap the stream.
        text: The scripted input, newline-separated.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


# --- module surface ----------------------------------------------------
@pytest.mark.parametrize("name,project", PROJECTS, ids=PROJECT_IDS)
def test_entrypoint_exposes_main_and_prog(name: str, project: str) -> None:
    module = _entrypoint(project)
    assert callable(module.main)
    assert module.PROG == name
    assert module.__doc__, f"{project}/main.py must keep its module docstring"


@pytest.mark.parametrize("name,project", PROJECTS, ids=PROJECT_IDS)
def test_entrypoint_version_banner_exits_zero(name: str, project: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _main(project)(["--version"])
    assert excinfo.value.code == int(ExitCode.OK)


# --- 01 hangman --------------------------------------------------------
def test_hangman_closed_stdin_ends_session(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _stdin(monkeypatch, "")
    code = _code("01_hangman_game", [])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Welcome to Hangman" in out
    assert "Input stream closed" in out
    # The stream closes inside the guess loop, so the summary banner is never
    # reached — the run stops exactly where the player stopped.
    assert "Final record" not in out


def test_hangman_closed_stdin_raises_system_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A closed stream ends the game with ``SystemExit(0)``, not a traceback.

    ``play_round`` stops the process from inside the guess loop (Day 5 edge-case
    policy); the helper above swallows that into a code, so this test pins the
    raw behaviour too.
    """
    words = ROOT / "projects" / "01_hangman_game" / "data" / "words.txt"
    _stdin(monkeypatch, "")
    with pytest.raises(SystemExit) as excinfo:
        _main("01_hangman_game")(["--words-file", str(words)])
    assert int(excinfo.value.code) == int(ExitCode.OK)
    assert "Input stream closed" in capsys.readouterr().out


def test_hangman_categories_mode_prints_category_label(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _stdin(monkeypatch, "")
    code = _code("01_hangman_game", ["--categories"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "category" in out


def test_hangman_words_file_mode_reports_word_count(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    words = ROOT / "projects" / "01_hangman_game" / "data" / "words.txt"
    _stdin(monkeypatch, "")
    code = _code("01_hangman_game", ["--words-file", str(words)])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "official word list" in out


def test_hangman_validate_accepts_the_shipped_dataset(capsys) -> None:
    code = _code("01_hangman_game", ["--validate"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "OK:" in out


def test_hangman_validate_rejects_a_corrupt_dataset(tmp_path: Path, capsys) -> None:
    corrupt = tmp_path / "words.txt"
    corrupt.write_bytes(b"\xff\xfe\x00not-a-word-list")
    code = _code(
        "01_hangman_game", ["--validate", "--words-file", str(corrupt), "--min-length", "4"]
    )
    out = capsys.readouterr().out
    assert code == int(ExitCode.DATA_ERROR)
    assert "malformed word list" in out


def test_hangman_non_positive_min_length_is_a_data_error() -> None:
    code = run_cli(lambda: _code("01_hangman_game", ["--min-length", "0"]))
    assert code == int(ExitCode.DATA_ERROR)


def test_hangman_max_length_below_min_length_is_a_data_error() -> None:
    code = run_cli(lambda: _code("01_hangman_game", ["--min-length", "8", "--max-length", "3"]))
    assert code == int(ExitCode.DATA_ERROR)


# --- 02 portfolio ------------------------------------------------------
def test_portfolio_demo_writes_summary_csv(tmp_path: Path, capsys) -> None:
    output = tmp_path / "summary.csv"
    code = _code("02_portfolio_tracker", ["--demo", "--output", str(output)])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Summary written to" in out
    assert output.is_file()
    assert "AAPL" in output.read_text(encoding="utf-8")


def test_portfolio_demo_prints_report_header(tmp_path: Path, capsys) -> None:
    code = _code("02_portfolio_tracker", ["--demo", "--output", str(tmp_path / "s.csv")])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "AAPL" in out
    assert "RELIANCE.NS" in out


def test_portfolio_validates_against_the_constituents_table(tmp_path: Path, capsys) -> None:
    constituents = ROOT / "projects" / "02_portfolio_tracker" / "data" / "constituents.csv"
    code = _code(
        "02_portfolio_tracker",
        [
            "--demo",
            "--output",
            str(tmp_path / "s.csv"),
            "--constituents",
            str(constituents),
        ],
    )
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Validated against" in out


def test_portfolio_interactive_position_is_added(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    _stdin(monkeypatch, "AAPL 10 150\n\n")
    code = _code("02_portfolio_tracker", ["--output", str(tmp_path / "s.csv")])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "+ Added AAPL" in out


def test_portfolio_short_line_is_reported_and_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    _stdin(monkeypatch, "AAPL 10\n\n")
    code = _code("02_portfolio_tracker", ["--output", str(tmp_path / "s.csv")])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "expected SYMBOL SHARES BUY_PRICE" in out
    assert not (tmp_path / "s.csv").exists()


def test_portfolio_invalid_number_is_reported_and_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    _stdin(monkeypatch, "AAPL ten 150\n\n")
    code = _code("02_portfolio_tracker", ["--output", str(tmp_path / "s.csv")])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "!" in out


def test_portfolio_closed_stdin_ends_the_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stdin(monkeypatch, "")
    code = _code("02_portfolio_tracker", ["--output", str(tmp_path / "s.csv")])
    assert code == int(ExitCode.OK)


def test_portfolio_strict_unknown_ticker_fails(tmp_path: Path) -> None:
    universe = tmp_path / "universe.csv"
    universe.write_text("Symbol\nMSFT\n", encoding="utf-8")
    code = run_cli(
        lambda: _code(
            "02_portfolio_tracker",
            [
                "--demo",
                "--strict",
                "--output",
                str(tmp_path / "s.csv"),
                "--constituents",
                str(universe),
            ],
        )
    )
    assert code == int(ExitCode.DATA_ERROR)


def test_portfolio_validate_mode_accepts_the_sample_prices(capsys) -> None:
    code = _code("02_portfolio_tracker", ["--validate"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "OK:" in out


# --- 03 organizer ------------------------------------------------------
def _seed(directory: Path) -> List[str]:
    """Create a small mixed file set for the organizer to sort.

    Args:
        directory: Directory to populate.

    Returns:
        The file names created.
    """
    names = ["report.pdf", "photo.jpg", "notes.txt"]
    for name in names:
        (directory / name).write_text("x", encoding="utf-8")
    return names


def test_organizer_moves_files_into_category_folders(tmp_path: Path, capsys) -> None:
    target = tmp_path / "inbox"
    target.mkdir()
    names = _seed(target)
    code = _code("03_file_organizer_automation", [str(target)])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Categories:" in out
    assert not any((target / name).exists() for name in names)
    assert any(child.is_dir() for child in target.iterdir())


def test_organizer_dry_run_leaves_files_in_place(tmp_path: Path, capsys) -> None:
    target = tmp_path / "inbox"
    target.mkdir()
    names = _seed(target)
    code = _code("03_file_organizer_automation", [str(target), "--dry-run"])
    capsys.readouterr()
    assert code == int(ExitCode.OK)
    assert all((target / name).exists() for name in names)


def test_organizer_deprecated_workers_alias_still_works(tmp_path: Path, capsys) -> None:
    target = tmp_path / "inbox"
    target.mkdir()
    _seed(target)
    code = _code("03_file_organizer_automation", [str(target), "--workers", "2", "--dry-run"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "--workers is deprecated" in out


def test_organizer_writes_json_activity_log(tmp_path: Path, capsys) -> None:
    target = tmp_path / "inbox"
    target.mkdir()
    _seed(target)
    log = tmp_path / "activity.json"
    code = _code("03_file_organizer_automation", [str(target), "--log", str(log)])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Activity log written to" in out
    assert log.is_file()


def test_organizer_detects_duplicates(tmp_path: Path, capsys) -> None:
    target = tmp_path / "inbox"
    target.mkdir()
    (target / "one.txt").write_text("same", encoding="utf-8")
    (target / "two.txt").write_text("same", encoding="utf-8")
    code = _code("03_file_organizer_automation", [str(target), "--duplicates"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "duplicate" in out.lower()


def test_organizer_zero_concurrency_is_a_data_error(tmp_path: Path) -> None:
    code = run_cli(
        lambda: _code("03_file_organizer_automation", [str(tmp_path), "--concurrency", "0"])
    )
    assert code == int(ExitCode.DATA_ERROR)


def test_organizer_missing_directory_is_a_data_error(tmp_path: Path) -> None:
    code = run_cli(lambda: _code("03_file_organizer_automation", [str(tmp_path / "absent")]))
    assert code == int(ExitCode.DATA_ERROR)


# --- 04 assistant ------------------------------------------------------
def test_assistant_answers_then_exits(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _stdin(monkeypatch, "hello\nbye\n")
    code = _code("04_terminal_assistant", ["--no-log"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "bot >" in out
    assert "Terminal Assistant" in out or "assistant" in out.lower()


def test_assistant_closed_stdin_exits_cleanly(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _stdin(monkeypatch, "")
    code = _code("04_terminal_assistant", ["--no-log"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "(interrupted)" in out


def test_assistant_writes_conversation_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    log = tmp_path / "conversation.jsonl"
    _stdin(monkeypatch, "hello\nbye\n")
    code = _code("04_terminal_assistant", ["--log", str(log)])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Conversation saved to" in out
    assert log.is_file()


def test_assistant_faq_dataset_mode(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    faq = ROOT / "projects" / "04_terminal_assistant" / "sample_faq.json"
    _stdin(monkeypatch, "bye\n")
    code = _code("04_terminal_assistant", ["--no-log", "--faq", str(faq)])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Loaded FAQ dataset" in out


def test_assistant_validate_mode_accepts_shipped_intents(capsys) -> None:
    code = _code("04_terminal_assistant", ["--validate"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "is valid" in out


def test_assistant_missing_intents_file_is_a_data_error(tmp_path: Path) -> None:
    code = run_cli(
        lambda: _code(
            "04_terminal_assistant", ["--no-log", "--intents", str(tmp_path / "absent.json")]
        )
    )
    assert code == int(ExitCode.DATA_ERROR)


# --- 05 inventory ------------------------------------------------------
def test_inventory_demo_runs_every_section(capsys) -> None:
    code = _code("05_inventory_oop", [])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Initial inventory" in out
    assert "Demonstrating error handling" in out
    assert "Final audit" in out
    assert "Dunder method showcase" in out


def test_inventory_borrower_path_checks_out(capsys) -> None:
    code = _code("05_inventory_oop", ["--borrower", "Krish"])
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Krish borrowed B001" in out


def test_inventory_borrower_path_accepts_days_and_date(capsys) -> None:
    code = _code(
        "05_inventory_oop", ["--borrower", "Aisha", "--days", "14", "--borrow-date", "2026-01-05"]
    )
    out = capsys.readouterr().out
    assert code == int(ExitCode.OK)
    assert "Aisha borrowed B001 for 14 days" in out


def test_inventory_zero_days_is_a_data_error() -> None:
    code = run_cli(lambda: _code("05_inventory_oop", ["--borrower", "Krish", "--days", "0"]))
    assert code == int(ExitCode.DATA_ERROR)


def test_inventory_bad_borrow_date_is_a_data_error() -> None:
    code = run_cli(
        lambda: _code("05_inventory_oop", ["--borrower", "Krish", "--borrow-date", "not-a-date"])
    )
    assert code == int(ExitCode.DATA_ERROR)


def test_inventory_strict_loan_period_is_capped() -> None:
    code = run_cli(
        lambda: _code("05_inventory_oop", ["--borrower", "Krish", "--days", "500", "--strict"])
    )
    assert code == int(ExitCode.DATA_ERROR)


# --- cross-cutting -----------------------------------------------------
@pytest.mark.parametrize("name,project", PROJECTS, ids=PROJECT_IDS)
def test_entrypoint_accepts_json_logging_flags(
    name: str, project: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Every CLI must accept the shared structured-logging switches.

    The demo path differs per tool, so the assertion is deliberately narrow:
    the flags parse, the run succeeds and a log file appears in the redirected
    log directory.
    """
    _stdin(monkeypatch, "")
    args = ["--log-level", "DEBUG", "--json-logs"]
    if project == "02_portfolio_tracker":
        args += ["--demo", "--output", str(tmp_path / "s.csv")]
    elif project == "03_file_organizer_automation":
        target = tmp_path / "inbox"
        target.mkdir()
        args.append(str(target))
    elif project == "04_terminal_assistant":
        args.append("--no-log")
    code = _code(project, args)
    capsys.readouterr()
    assert code == int(ExitCode.OK)
    assert (tmp_path / "logs" / f"{name}.log").is_file()
