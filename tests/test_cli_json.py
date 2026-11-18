"""Tests for the shared ``--json`` output mode.

The maintenance-mode backlog asks for a machine-readable result on every tool.
The contract lives in :mod:`cli_contract` — one envelope, one document, the
same exit code — so these tests check three things rather than five:

* the **envelope** is identical for all five tools (``tool``, ``schema``,
  ``version``, ``command``, ``ok``, and ``data`` or ``error``);
* ``stdout`` carries **exactly one** document and nothing else, with the
  human-readable report moved to ``stderr``;
* the **exit code is unchanged** by the flag, including on the failure path,
  where the document reports the :class:`~errors.DatheonError` catalogue code.

Every run goes through :func:`cli_contract.run_cli`, the same shim the
``if __name__ == "__main__"`` blocks use, so a failure test asserts the code a
user's shell would see rather than an internal exception type.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Tuple

import pytest

from cli_contract import (
    JSON_SCHEMA_VERSION,
    PROGRAM_VERSION,
    ExitCode,
    JsonEmitter,
    human_output,
    run_cli,
)
from errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]

#: ``(program name, project directory)`` for each of the five tools.
PROJECTS: List[Tuple[str, str]] = [
    ("hangman", "01_hangman_game"),
    ("portfolio", "02_portfolio_tracker"),
    ("organizer", "03_file_organizer_automation"),
    ("assistant", "04_terminal_assistant"),
    ("inventory", "05_inventory_oop"),
]

PROJECT_IDS = [name for name, _ in PROJECTS]

#: The keys every successful document carries, in addition to ``data``.
ENVELOPE_KEYS = {"tool", "schema", "version", "command", "ok"}


@lru_cache(maxsize=None)
def _entrypoint(project_dir: str) -> ModuleType:
    """Import a project's ``main.py`` under a unique, non-colliding name.

    Args:
        project_dir: Directory name under ``projects/``.

    Returns:
        The imported entry-point module.
    """
    path = ROOT / "projects" / project_dir / "main.py"
    alias = f"datheon_json_entry_{project_dir}"
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def _invoke(project: str, argv: List[str], capsys) -> Tuple[int, str, str]:
    """Run a tool in-process and return its exit code and both streams.

    Args:
        project: Directory name under ``projects/``.
        argv: Argument vector passed to the tool's ``main``.
        capsys: Pytest's stream capturer, read once the call returns.

    Returns:
        A ``(exit_code, stdout, stderr)`` triple.
    """
    try:
        code = run_cli(lambda: _entrypoint(project).main(argv))
    except SystemExit as exc:
        # The interactive loops stop the process from inside a prompt (Day 5
        # edge-case policy), so a closed stream arrives as SystemExit(0).
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _document(stdout: str) -> Dict[str, Any]:
    """Parse the single JSON document a ``--json`` run wrote.

    Args:
        stdout: The captured standard output.

    Returns:
        The parsed envelope.

    Raises:
        AssertionError: If ``stdout`` holds anything other than one object.
    """
    text = stdout.strip()
    assert text.startswith("{"), f"stdout is not a JSON document: {text[:80]!r}"
    assert text.endswith("}"), f"stdout has trailing output after the document: {text[-80:]!r}"
    document = json.loads(text)
    assert isinstance(document, dict), "the document must be a JSON object"
    return document


def _stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Replace ``sys.stdin`` with a scripted stream.

    Args:
        monkeypatch: Fixture used to swap the stream.
        text: The scripted input, newline-separated.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


# --- the emitter on its own -------------------------------------------
def test_emitter_is_silent_when_the_flag_is_absent(capsys) -> None:
    emitter = JsonEmitter("hangman", enabled=False)
    code = emitter.success("play", {"wins": 3})
    assert code == int(ExitCode.OK)
    assert capsys.readouterr().out == ""


def test_emitter_success_writes_the_envelope_and_returns_the_code(capsys) -> None:
    emitter = JsonEmitter("hangman", enabled=True)
    code = emitter.success("play", {"wins": 3}, code=int(ExitCode.DATA_ERROR))
    document = _document(capsys.readouterr().out)
    assert code == int(ExitCode.DATA_ERROR)
    assert document["tool"] == "hangman"
    assert document["command"] == "play"
    assert document["ok"] is True
    assert document["data"] == {"wins": 3}
    assert document["schema"] == JSON_SCHEMA_VERSION
    assert document["version"] == PROGRAM_VERSION


def test_emitter_failure_reports_the_catalogue_code(capsys) -> None:
    emitter = JsonEmitter("portfolio", enabled=True)
    emitter.failure(
        "report",
        ValidationError(
            "price must not be negative", hint="use 0 or more", context={"field": "price"}
        ),
    )
    document = _document(capsys.readouterr().out)
    assert document["ok"] is False
    assert "data" not in document
    assert document["error"]["code"] == ValidationError.code
    assert document["error"]["hint"] == "use 0 or more"
    assert document["error"]["context"] == {"field": "price"}
    assert document["error"]["exit_code"] == int(ExitCode.DATA_ERROR)


def test_emitter_failure_handles_a_plain_exception(capsys) -> None:
    emitter = JsonEmitter("organizer", enabled=True)
    emitter.failure("organize", ValueError("boom"))
    document = _document(capsys.readouterr().out)
    assert document["ok"] is False
    assert document["error"]["type"] == "ValueError"
    assert document["error"]["message"] == "boom"
    assert document["error"]["exit_code"] == int(ExitCode.DATA_ERROR)


def test_emitter_failure_is_silent_when_disabled(capsys) -> None:
    JsonEmitter("organizer", enabled=False).failure("organize", ValueError("boom"))
    assert capsys.readouterr().out == ""


def test_human_output_moves_prints_to_stderr(capsys) -> None:
    with human_output(True):
        print("banner")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "banner" in captured.err


def test_human_output_leaves_stdout_alone_when_disabled(capsys) -> None:
    with human_output(False):
        print("banner")
    assert "banner" in capsys.readouterr().out


# --- the flag on every parser -----------------------------------------
@pytest.mark.parametrize("name,project", PROJECTS, ids=PROJECT_IDS)
def test_every_parser_advertises_the_json_flag(name: str, project: str, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _entrypoint(project).main(["--help"])
    assert excinfo.value.code == int(ExitCode.OK)
    assert "--json" in capsys.readouterr().out


# --- 01 hangman --------------------------------------------------------
def test_hangman_json_play_reports_the_session(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    words = ROOT / "projects" / "01_hangman_game" / "data" / "words.txt"
    _stdin(monkeypatch, "")
    code, out, err = _invoke("01_hangman_game", ["--json", "--words-file", str(words)], capsys)
    document = _document(out)
    assert code == int(ExitCode.OK)
    assert document["ok"] is True
    assert document["command"] == "play"
    assert document["data"]["source"] == "word_list"
    assert document["data"]["word_count"] > 0
    assert document["data"]["rounds_played"] == 0
    # The banner the player sees is still produced, just on stderr.
    assert "Welcome to Hangman" in err


def test_hangman_json_validate_reports_the_dataset(capsys) -> None:
    code, out, _err = _invoke("01_hangman_game", ["--json", "--validate"], capsys)
    document = _document(out)
    assert code == int(ExitCode.OK)
    assert document["command"] == "validate"
    assert document["data"]["valid"] is True
    assert document["data"]["rows_accepted"] > 0
    assert document["data"]["issues"] == []


def test_hangman_json_validate_reports_a_corrupt_dataset(tmp_path: Path, capsys) -> None:
    corrupt = tmp_path / "words.txt"
    corrupt.write_text("python\n12345\nvariable\n", encoding="utf-8")
    code, out, _err = _invoke(
        "01_hangman_game", ["--json", "--validate", "--words-file", str(corrupt)], capsys
    )
    document = _document(out)
    assert code == int(ExitCode.DATA_ERROR)
    assert document["ok"] is True
    assert document["data"]["valid"] is False
    assert document["data"]["issues"], "a rejected dataset must list its issues"
    assert document["data"]["issues"][0]["line"] == 2


def test_hangman_without_json_keeps_stdout_human(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    words = ROOT / "projects" / "01_hangman_game" / "data" / "words.txt"
    _stdin(monkeypatch, "")
    code, out, _err = _invoke("01_hangman_game", ["--words-file", str(words)], capsys)
    assert code == int(ExitCode.OK)
    assert "Welcome to Hangman" in out
    assert not out.lstrip().startswith("{")


# --- 02 portfolio ------------------------------------------------------
def test_portfolio_json_demo_reports_the_positions(tmp_path: Path, capsys) -> None:
    summary = tmp_path / "summary.csv"
    code, out, err = _invoke(
        "02_portfolio_tracker", ["--json", "--demo", "--output", str(summary)], capsys
    )
    document = _document(out)
    assert code == int(ExitCode.OK)
    assert document["command"] == "report"
    assert document["data"]["demo"] is True
    assert len(document["data"]["positions"]) == 4
    assert document["data"]["positions"][0]["symbol"] == "AAPL"
    assert set(document["data"]["totals"]) == {
        "total_cost",
        "total_value",
        "total_gain",
        "total_gain_pct",
    }
    assert document["data"]["summary_csv"] == str(summary)
    assert summary.is_file()
    assert "SYMBOL" in err


def test_portfolio_json_validate_reports_each_dataset(capsys) -> None:
    code, out, _err = _invoke("02_portfolio_tracker", ["--json", "--validate"], capsys)
    document = _document(out)
    assert code == int(ExitCode.OK)
    assert document["command"] == "validate"
    assert document["data"]["valid"] is True
    assert document["data"]["datasets"][0]["label"] == "price file"
    assert document["data"]["datasets"][0]["rows_accepted"] > 0


def test_portfolio_json_failure_reports_a_data_error(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "absent.csv"
    code, out, err = _invoke(
        "02_portfolio_tracker",
        ["--json", "--demo", "--strict", "--prices", str(missing)],
        capsys,
    )
    document = _document(out)
    assert code == int(ExitCode.DATA_ERROR)
    assert document["ok"] is False
    assert document["command"] == "report"
    assert document["error"]["exit_code"] == int(ExitCode.DATA_ERROR)
    assert document["error"]["code"], "a taxonomy error must carry its catalogue code"
    assert "absent.csv" in document["error"]["message"]
    # The human message still reaches the operator on stderr.
    assert "--prices" in err


# --- 03 organizer ------------------------------------------------------
def test_organizer_json_dry_run_reports_every_plan(tmp_path: Path, capsys) -> None:
    workdir = tmp_path / "downloads"
    workdir.mkdir()
    (workdir / "notes.txt").write_text("a", encoding="utf-8")
    (workdir / "photo.png").write_text("b", encoding="utf-8")
    code, out, err = _invoke(
        "03_file_organizer_automation", ["--json", "--dry-run", str(workdir)], capsys
    )
    document = _document(out)
    data = document["data"]
    assert code == int(ExitCode.OK)
    assert document["command"] == "organize"
    assert data["dry_run"] is True
    assert data["summary"]["planned"] == 2
    assert data["summary"]["moved"] == 0
    assert {record["status"] for record in data["records"]} == {"planned"}
    assert data["log_path"] is None
    assert (workdir / "notes.txt").is_file(), "a dry run must not move anything"
    assert "Organising:" in err


def test_organizer_json_records_the_activity_log_path(tmp_path: Path, capsys) -> None:
    workdir = tmp_path / "downloads"
    workdir.mkdir()
    (workdir / "notes.txt").write_text("a", encoding="utf-8")
    log = tmp_path / "run.json"
    code, out, _err = _invoke(
        "03_file_organizer_automation",
        ["--json", "--dry-run", "--log", str(log), str(workdir)],
        capsys,
    )
    document = _document(out)
    assert code == int(ExitCode.OK)
    assert document["data"]["log_path"] == str(log)
    assert log.is_file()


# --- 04 assistant ------------------------------------------------------
def test_assistant_json_chat_reports_every_turn(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _stdin(monkeypatch, "hello\nbye\n")
    code, out, err = _invoke("04_terminal_assistant", ["--json", "--no-log"], capsys)
    document = _document(out)
    turns = document["data"]["turns"]
    assert code == int(ExitCode.OK)
    assert document["command"] == "chat"
    assert document["data"]["turns_count"] == len(turns) == 4
    assert [turn["role"] for turn in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[1]["intent"] == "greeting"
    assert turns[3]["intent"] == "exit"
    assert document["data"]["log_path"] is None
    assert "Rule-Based Terminal Assistant" in err


def test_assistant_json_validate_reports_the_intents(capsys) -> None:
    code, out, _err = _invoke("04_terminal_assistant", ["--json", "--validate"], capsys)
    document = _document(out)
    assert code == int(ExitCode.OK)
    assert document["command"] == "validate"
    assert document["data"]["label"] == "intent file"
    assert document["data"]["valid"] is True
    assert document["data"]["rows_accepted"] > 0


# --- 05 inventory ------------------------------------------------------
def test_inventory_json_demo_reports_the_audit(capsys) -> None:
    code, out, err = _invoke("05_inventory_oop", ["--json"], capsys)
    document = _document(out)
    data = document["data"]
    assert code == int(ExitCode.OK)
    assert document["command"] == "demo"
    assert data["summary"]["items"] == 4
    assert len(data["items"]) == 4
    assert len(data["checkouts"]) == 2
    assert len(data["rejected"]) == 3
    assert data["summary"]["revenue"] > 0
    assert "Initial inventory" in err


def test_inventory_json_single_checkout_reports_the_record(capsys) -> None:
    code, out, _err = _invoke(
        "05_inventory_oop", ["--json", "--borrower", "Krish", "--days", "7"], capsys
    )
    document = _document(out)
    record = document["data"]["record"]
    assert code == int(ExitCode.OK)
    assert document["command"] == "checkout"
    assert record["item_id"] == "B001"
    assert record["borrower"] == "Krish"
    assert record["days"] == 7
    assert record["fee"] >= 0
    assert document["data"]["summary"]["on_loan"] == 1


# --- one envelope across all five tools -------------------------------
def test_all_five_tools_share_one_envelope(tmp_path: Path, capsys) -> None:
    workdir = tmp_path / "downloads"
    workdir.mkdir()
    (workdir / "notes.txt").write_text("a", encoding="utf-8")
    commands = {
        "01_hangman_game": ["--json", "--validate"],
        "02_portfolio_tracker": ["--json", "--validate"],
        "03_file_organizer_automation": ["--json", "--dry-run", str(workdir)],
        "04_terminal_assistant": ["--json", "--validate"],
        "05_inventory_oop": ["--json"],
    }
    for project, argv in commands.items():
        code, out, _err = _invoke(project, argv, capsys)
        document = _document(out)
        assert code == int(ExitCode.OK), f"{project} failed its smoke command"
        assert ENVELOPE_KEYS <= set(document), f"{project} is missing envelope keys"
        assert document["tool"] == _entrypoint(project).PROG
        assert document["schema"] == JSON_SCHEMA_VERSION
        assert document["version"] == PROGRAM_VERSION
        assert document["ok"] is True
