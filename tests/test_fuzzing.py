"""Fuzz tests: hostile input must never escape as an unhandled exception (Day 18).

Days 15–17 made the suite *wide* (200+ assertions), *isolated* (no real I/O in
unit tests) and *property-based* (statements over every valid input). Day 18
adds the missing direction: input that is not valid at all.

Three ideas hold this module together.

**The corpus is generated, not stored.** Every case comes from
:func:`fuzzing.generate_cases`, seeded from a constant, so a failing case is
reproduced by quoting an integer rather than by attaching a 512-byte fixture.
The tests therefore cost a few hundred microseconds and cover inputs nobody
would think to hand-write.

**The verdict is shared with the CLI driver.** :mod:`tools.fuzz` and this module
judge a run through the same :func:`fuzzing.judge_process` /
:func:`fuzzing.judge_exception` helpers. A test that passes here is the same
assertion the nightly driver makes, which is what stops the two from drifting.

**The tolerance differs by layer.** A CLI may legitimately exit ``1`` with a
domain error — that *is* the contract. A loader has no such licence: it must
convert a malformed file into a :class:`~errors.DatheonError`, because anything
else reaches the user as a traceback. The loader tests therefore pass a
narrower ``tolerated`` tuple than the CLI tests.

The final section is a regression suite for the two defects this campaign
actually found, so that neither can come back.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Iterator, List

import pytest

from cli_contract import ExitCode, run_cli
from errors import DatheonError, ValidationError
from fuzzing import (
    ARGV_TOKENS,
    DEFAULT_SEED,
    FuzzCase,
    Verdict,
    generate_cases,
    is_traceback,
    judge_exception,
    judge_exit_code,
    judge_process,
    mutate_bytes,
    random_argv,
    random_bytes,
    random_text,
)

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

#: How many generated argument vectors each entry point is asked to survive.
ARGV_CASES_PER_PROJECT = 6

#: How many mutated files each loader is asked to survive.
LOADER_CASES_PER_TARGET = 8

#: How many hostile strings the assistant is asked to survive.
TEXT_CASES = 30


@lru_cache(maxsize=None)
def _entrypoint(project_dir: str) -> ModuleType:
    """Import a project's ``main.py`` under a unique, non-colliding name.

    Args:
        project_dir: Directory name under ``projects/``.

    Returns:
        The imported entry-point module.
    """
    path = ROOT / "projects" / project_dir / "main.py"
    alias = f"datheon_fuzz_entry_{project_dir}"
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def _fuzz_driver() -> ModuleType:
    """Import ``tools/fuzz.py`` as a module so its helpers can be unit-tested.

    Args:
        None.

    Returns:
        The imported driver module.
    """
    path = ROOT / "tools" / "fuzz.py"
    alias = "datheon_fuzz_driver"
    spec = importlib.util.spec_from_file_location(alias, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def hermetic_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Keep a fuzz run inside ``tmp_path``.

    Fuzzed argument vectors are hostile by construction, so the safety rails
    have to live in the fixture rather than in the corpus: the working
    directory moves to ``tmp_path``, the log directory is redirected, and the
    three entry points that write to a *default* path get that default pointed
    at the sandbox. Without this, a generated ``--demo`` would rewrite the
    repository's own ``portfolio_summary.csv``.

    Args:
        tmp_path: Pytest's per-test temporary directory.
        monkeypatch: Fixture used to set environment variables and attributes.

    Yields:
        The sandbox directory.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.chdir(sandbox)
    monkeypatch.setenv("DATHEON_LOG_DIR", str(tmp_path / "logs"))

    portfolio = _entrypoint("02_portfolio_tracker")
    monkeypatch.setattr(portfolio, "DEFAULT_OUTPUT", sandbox / "summary.csv", raising=False)

    assistant = _entrypoint("04_terminal_assistant")
    monkeypatch.setattr(assistant, "DEFAULT_LOG", sandbox / "conversation.jsonl", raising=False)

    yield sandbox


def _run_entrypoint(project_dir: str, argv: List[str]) -> int:
    """Run an entry point in-process against an empty stdin.

    Args:
        project_dir: Directory name under ``projects/``.
        argv: Argument vector for the tool.

    Returns:
        The exit code the process boundary would have produced, with
        :class:`SystemExit` (raised by ``--help`` and ``--version``) folded back
        into an integer.
    """
    module = _entrypoint(project_dir)
    stdin = io.StringIO("")
    original = sys.stdin
    sys.stdin = stdin
    try:
        return int(run_cli(lambda: module.main(argv)))
    except SystemExit as exc:  # --help / --version exit through argparse
        return int(exc.code or 0)
    finally:
        sys.stdin = original


# --------------------------------------------------------------------------- #
# The harness itself
# --------------------------------------------------------------------------- #


def test_generate_cases_is_deterministic() -> None:
    """The same coordinates must always produce the same corpus."""
    first = generate_cases("argv", "hangman", seed=3, count=4)
    second = generate_cases("argv", "hangman", seed=3, count=4)

    assert [case.argv for case in first] == [case.argv for case in second]
    assert [case.seed for case in first] == [case.seed for case in second]


def test_corpus_is_stable_when_the_count_grows() -> None:
    """Case *n* must not change when the run asks for more cases.

    Without this, a failure reported as "case 7" would be unreproducible at a
    different corpus size.
    """
    short = generate_cases("text", "chatbot", seed=DEFAULT_SEED, count=3)
    long = generate_cases("text", "chatbot", seed=DEFAULT_SEED, count=9)

    assert [case.text for case in short] == [case.text for case in long[:3]]


def test_different_seeds_produce_different_corpora() -> None:
    """A new seed must explore new input, or the seed is decorative."""
    a = generate_cases("argv", "inventory", seed=1, count=5)
    b = generate_cases("argv", "inventory", seed=2, count=5)

    assert [case.argv for case in a] != [case.argv for case in b]


def test_generate_cases_rejects_unknown_kind() -> None:
    """A typo in the campaign name must fail loudly, not silently fuzz nothing."""
    with pytest.raises(ValueError, match="unknown fuzz kind"):
        generate_cases("binary", "hangman", count=1)


def test_generate_cases_rejects_negative_count() -> None:
    """A negative corpus size is a caller bug."""
    with pytest.raises(ValueError, match="non-negative"):
        generate_cases("argv", "hangman", count=-1)


def test_empty_argv_is_a_legitimate_case() -> None:
    """Running a tool with no arguments at all must be inside the corpus."""
    corpus = generate_cases("argv", "portfolio", seed=DEFAULT_SEED, count=200)

    assert any(case.argv == () for case in corpus)


def test_random_argv_only_uses_the_declared_pool_or_fuzzed_text() -> None:
    """Every token must be a known shape — no absolute paths sneak in.

    The pool is deliberately free of ``/`` and ``../..`` so a fuzz run can never
    aim a tool at a real system directory.
    """
    for index in range(50):
        tokens = random_argv(__import__("random").Random(index))
        assert isinstance(tokens, list)
        assert all(isinstance(token, str) for token in tokens)
        assert not any(token.startswith("/") for token in tokens if token != "/dev/null")
    assert "/dev/null" in ARGV_TOKENS


def test_random_bytes_respects_the_length_bound() -> None:
    """A fuzz payload must never be unbounded: disk is not infinite."""
    for index in range(50):
        payload = random_bytes(__import__("random").Random(index), max_length=64)
        assert isinstance(payload, bytes)
        assert len(payload) <= 64


def test_mutate_bytes_always_changes_the_input() -> None:
    """A mutation that mutates nothing would waste a case."""
    base = b"symbol,price\nRELIANCE,2400.50\nINFY,1500.25\n"
    for index in range(50):
        mutated = mutate_bytes(__import__("random").Random(index), base)
        assert mutated != base
        assert isinstance(mutated, bytes)


def test_mutate_bytes_keeps_a_recognisable_prefix_sometimes() -> None:
    """Mutations must be surgical: most cases still start like a real dataset.

    If every mutation were catastrophic the campaign would only ever test the
    decoder, never the row and schema validators behind it.
    """
    base = b"symbol,price\nRELIANCE,2400.50\n"
    survivors = 0
    for index in range(50):
        mutated = mutate_bytes(__import__("random").Random(index), base)
        survivors += mutated.startswith(b"symbol") or mutated.startswith(b"\xef\xbb\xbfsymbol")

    assert survivors >= 25


def test_is_traceback_recognises_the_marker() -> None:
    """The traceback check is the whole definition of "crash" for a subprocess."""
    assert is_traceback("Traceback (most recent call last):\n  File ...")
    assert not is_traceback("usage: hangman [-h] [--version]\nhangman: error: bad flag")


def test_judge_exit_code_accepts_the_contract_and_rejects_the_rest() -> None:
    """``0``, ``1`` and ``2`` are legal; anything else is a crash."""
    case = FuzzCase("argv", "hangman", "argv[0]", 1, argv=("--version",))

    assert judge_exit_code(case, int(ExitCode.OK)).ok
    assert judge_exit_code(case, int(ExitCode.DATA_ERROR)).ok
    assert judge_exit_code(case, int(ExitCode.USAGE_ERROR)).ok

    crashed = judge_exit_code(case, 139)  # SIGSEGV as seen by a shell
    assert not crashed.ok
    assert "139" in crashed.reason


def test_judge_process_flags_a_traceback_even_on_a_legal_exit_code() -> None:
    """An exception swallowed after printing its traceback is still a defect."""
    case = FuzzCase("argv", "organizer", "argv[1]", 2, argv=())

    verdict = judge_process(case, 1, stderr="Traceback (most recent call last):\n  ...")
    assert not verdict.ok
    assert verdict.reason == "traceback on stderr"


def test_judge_process_accepts_a_clean_data_error() -> None:
    """A tool refusing bad input with exit 1 is the contract working."""
    case = FuzzCase("argv", "organizer", "argv[2]", 3, argv=("missing.csv",))

    verdict = judge_process(case, 1, stderr="organizer: error: directory not found")
    assert verdict.ok
    assert verdict.exit_code == 1


def test_judge_exception_narrows_to_the_requested_tolerance() -> None:
    """A loader must convert ``UnicodeDecodeError``, a CLI need not."""
    case = FuzzCase("bytes", "words", "mutate[0]", 4, data=b"\xff\xfe")

    assert judge_exception(case, UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")).ok
    strict = judge_exception(
        case, UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), tolerated=(DatheonError,)
    )
    assert not strict.ok
    assert "UnicodeDecodeError" in strict.reason


def test_fuzz_case_describe_truncates_long_payloads() -> None:
    """A report line must stay readable even for a 300-character argument."""
    case = FuzzCase("argv", "hangman", "argv[9]", 5, argv=("a" * 300,))

    described = case.describe()
    assert described.startswith("hangman/argv[9] seed=5:")
    assert len(described) < 200
    assert described.endswith("...")


def test_fuzz_case_to_dict_is_json_serialisable() -> None:
    """Reports are JSON: bytes must not reach the encoder raw."""
    case = FuzzCase("bytes", "prices", "mutate[1]", 6, data=b"\x00\xff\x10")

    payload = case.to_dict()
    assert json.loads(json.dumps(payload))["data"] == "\x00\xff\x10"
    assert payload["kind"] == "bytes"


def test_verdict_to_dict_flattens_the_case() -> None:
    """A JSON report needs one flat row per case."""
    case = FuzzCase("text", "chatbot", "text[0]", 7, text="hi")
    payload = Verdict(case, False, "boom", 1, "detail").to_dict()

    assert payload["target"] == "chatbot"
    assert payload["ok"] is False
    assert payload["reason"] == "boom"


# --------------------------------------------------------------------------- #
# Campaign 1: the five CLIs, in-process
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("project", PROJECT_IDS)
def test_cli_survives_generated_argument_vectors(project: str) -> None:
    """No fuzzed argument vector may escape the CLI contract.

    The organizer is always run with ``--dry-run`` for the same reason
    :mod:`tools.fuzz` injects it: fuzzing must never move a real file.
    """
    project_dir = dict(PROJECTS)[project]
    verdicts: List[Verdict] = []

    for case in generate_cases("argv", project, seed=DEFAULT_SEED, count=ARGV_CASES_PER_PROJECT):
        argv = list(case.argv)
        if project == "organizer" and "--dry-run" not in argv:
            argv.insert(0, "--dry-run")
        try:
            code = _run_entrypoint(project_dir, argv)
        except BaseException as exc:  # noqa: BLE001 - the point of the campaign
            verdicts.append(judge_exception(case, exc))
        else:
            verdicts.append(judge_exit_code(case, code))

    failures = [verdict for verdict in verdicts if not verdict.ok]
    assert not failures, "\n".join(f"{v.case.describe()} -> {v.reason}" for v in failures)


@pytest.mark.parametrize("project", PROJECT_IDS)
def test_cli_survives_random_stdin(project: str) -> None:
    """Hostile text typed at an interactive prompt must not crash the loop.

    The prompts are read through :func:`edge_cases.safe_input`, which turns EOF
    and ``Ctrl-C`` into a normal stop; this test feeds the *content* side of the
    same boundary — text that no prompt expects.
    """
    project_dir = dict(PROJECTS)[project]
    payload = "\n".join(random_text(__import__("random").Random(i)) for i in range(20))

    module = _entrypoint(project_dir)
    original = sys.stdin
    sys.stdin = io.StringIO(payload + "\n")
    try:
        code = int(run_cli(lambda: module.main([])))
    except SystemExit as exc:
        code = int(exc.code or 0)
    except BaseException as exc:  # noqa: BLE001 - the point of the campaign
        pytest.fail(f"{project} raised {type(exc).__name__}: {exc}")
    finally:
        sys.stdin = original

    assert code in {0, 1, 2}


# --------------------------------------------------------------------------- #
# Campaign 2: the dataset loaders, in-process
# --------------------------------------------------------------------------- #


def _load_words(path: Path) -> object:
    """Load a word list, bypassing the cache so each case is independent."""
    from words import load_words_from_file

    return load_words_from_file(path, use_cache=False)


def _load_prices(path: Path) -> object:
    """Load a price CSV."""
    from portfolio import load_prices_from_csv

    return load_prices_from_csv(path)


def _load_symbols(path: Path) -> object:
    """Load a constituents CSV, bypassing the cache."""
    from portfolio import load_symbols_from_csv

    return load_symbols_from_csv(path, use_cache=False)


def _load_intents(path: Path) -> object:
    """Load an intent JSON dataset."""
    from chatbot import IntentEngine

    return IntentEngine.from_json(path)


def _load_faq(path: Path) -> object:
    """Load a FAQ JSON dataset."""
    from chatbot import IntentEngine

    return IntentEngine.from_faq_json(path)


LOADERS = {
    "words": (_load_words, ROOT / "projects" / "01_hangman_game" / "data" / "words.txt"),
    "prices": (
        _load_prices,
        ROOT / "projects" / "02_portfolio_tracker" / "data" / "sample_prices.csv",
    ),
    "symbols": (
        _load_symbols,
        ROOT / "projects" / "02_portfolio_tracker" / "data" / "constituents.csv",
    ),
    "intents": (_load_intents, ROOT / "projects" / "04_terminal_assistant" / "intents.json"),
    "faq": (_load_faq, ROOT / "projects" / "04_terminal_assistant" / "sample_faq.json"),
}


@pytest.mark.parametrize("target", sorted(LOADERS))
def test_loader_converts_mutated_datasets_into_domain_errors(target: str, tmp_path: Path) -> None:
    """A corrupt dataset is a user error, never a traceback.

    The tolerance is a single type — :class:`~errors.DatheonError`. A loader that
    lets ``UnicodeDecodeError`` or ``re.error`` through would print a traceback
    at the terminal, which is exactly what Day 7's taxonomy exists to prevent.
    """
    load, seed_path = LOADERS[target]
    material = [seed_path.read_bytes()] if seed_path.is_file() else []
    verdicts: List[Verdict] = []

    for index, case in enumerate(
        generate_cases(
            "bytes",
            target,
            seed=DEFAULT_SEED,
            count=LOADER_CASES_PER_TARGET,
            seeds=material,
        )
    ):
        path = tmp_path / f"{target}_{index}.dat"
        path.write_bytes(case.data)
        try:
            load(path)
        except BaseException as exc:  # noqa: BLE001 - the point of the campaign
            verdicts.append(judge_exception(case, exc, tolerated=(DatheonError,)))
        else:
            verdicts.append(Verdict(case, True, "accepted"))

    failures = [verdict for verdict in verdicts if not verdict.ok]
    assert not failures, "\n".join(f"{v.case.describe()} -> {v.reason}" for v in failures)


@pytest.mark.parametrize("target", sorted(LOADERS))
def test_loader_survives_pure_noise(target: str, tmp_path: Path) -> None:
    """A file that was never text at all must also be a domain error."""
    load, _ = LOADERS[target]
    verdicts: List[Verdict] = []

    for index, case in enumerate(generate_cases("bytes", target, seed=99, count=6)):
        path = tmp_path / f"noise_{target}_{index}.dat"
        path.write_bytes(case.data)
        try:
            load(path)
        except BaseException as exc:  # noqa: BLE001 - the point of the campaign
            verdicts.append(judge_exception(case, exc, tolerated=(DatheonError,)))
        else:
            verdicts.append(Verdict(case, True, "accepted"))

    assert all(verdict.ok for verdict in verdicts), verdicts


# --------------------------------------------------------------------------- #
# Campaign 3: the assistant's text path
# --------------------------------------------------------------------------- #


def test_chatbot_answers_every_hostile_string_invariantly() -> None:
    """Whatever the user types, the reply must be well-formed.

    The invariants are the ones the REPL relies on to format a line:
    confidence inside ``0..1``, non-empty text, a boolean ``is_exit``.
    """
    from chatbot import IntentEngine
    from validators import validate_message

    engine = IntentEngine.from_json(ROOT / "projects" / "04_terminal_assistant" / "intents.json")
    accepted = 0

    for case in generate_cases("text", "chatbot", seed=DEFAULT_SEED, count=TEXT_CASES):
        try:
            message = validate_message(case.text, strict=True, field="input")
        except ValidationError:
            continue
        accepted += 1
        response = engine.respond(message)
        assert 0.0 <= response.confidence <= 1.0, case.describe()
        assert isinstance(response.text, str) and response.text.strip(), case.describe()
        assert isinstance(response.is_exit, bool), case.describe()

    assert accepted > 0, "the validator rejected the entire corpus — check the generator"


def test_chatbot_normalises_control_characters_without_raising() -> None:
    """``normalize`` is the first thing every turn touches; it must be total."""
    from chatbot import normalize

    for case in generate_cases("text", "chatbot", seed=5, count=40):
        result = normalize(case.text)
        assert isinstance(result, str)
        assert result == result.lower()


# --------------------------------------------------------------------------- #
# Regressions found by the campaign
# --------------------------------------------------------------------------- #


def test_intent_with_an_invalid_regex_is_a_dataset_error() -> None:
    """Regression: a mutated intent file once escaped as a bare ``re.error``.

    Fuzzing ``intents.json`` produced a pattern with an unterminated group, and
    :meth:`chatbot.Intent.from_dict` let :class:`re.error` through — past
    ``_parse_intents``' ``except ValueError``, past ``run_cli``, and out to the
    terminal as a traceback. The dataset is wrong, not the program, so the
    failure belongs in the taxonomy.
    """
    from chatbot import Intent

    with pytest.raises(ValidationError) as caught:
        Intent.from_dict({"name": "broken", "patterns": ["(unclosed"]})

    assert "invalid regular expression" in str(caught.value)
    assert caught.value.hint


def test_intent_with_a_non_string_pattern_is_a_dataset_error() -> None:
    """Regression: a JSON pattern may be a number or an object, not just text."""
    from chatbot import Intent

    with pytest.raises(ValidationError, match="non-string pattern"):
        Intent.from_dict({"name": "numeric", "patterns": [42]})


def test_intent_with_a_non_integer_priority_is_a_dataset_error() -> None:
    """Regression: ``int()`` on a JSON object raised an uncaught ``TypeError``."""
    from chatbot import Intent

    with pytest.raises(ValidationError, match="non-integer priority"):
        Intent.from_dict({"name": "odd", "patterns": ["hi"], "priority": {"a": 1}})


def test_assistant_validate_reports_an_invalid_regex_with_exit_code_1(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The user-visible half of the regression: exit 1, no traceback."""
    dataset = tmp_path / "intents.json"
    dataset.write_text(
        json.dumps(
            {
                "exit_commands": ["bye"],
                "fallbacks": ["Sorry?"],
                "intents": [{"name": "broken", "patterns": ["(unclosed"], "responses": ["hi"]}],
            }
        ),
        encoding="utf-8",
    )

    code = _run_entrypoint("04_terminal_assistant", ["--intents", str(dataset), "--validate"])
    captured = capsys.readouterr()

    assert code == int(ExitCode.DATA_ERROR)
    assert not is_traceback(captured.out + captured.err)
    assert "invalid regular expression" in captured.out + captured.err


def test_intent_engine_still_accepts_a_valid_pattern_after_the_guard() -> None:
    """The guard must not reject the well-formed case it sits next to."""
    from chatbot import Intent

    intent = Intent.from_dict({"name": "hours", "patterns": [r"\bhours?\b"], "priority": 3})

    assert intent.score("what are your hours") == 1
    assert intent.priority == 3


# --------------------------------------------------------------------------- #
# The driver in tools/fuzz.py
# --------------------------------------------------------------------------- #


def test_driver_hardens_the_organizer_with_a_dry_run() -> None:
    """A fuzzed directory argument must never move a real file."""
    driver = _fuzz_driver()

    assert driver._harden("organizer", [".."]) == ["--dry-run", ".."]
    assert driver._harden("organizer", ["--dry-run", ".."]) == ["--dry-run", ".."]
    assert driver._harden("hangman", ["--version"]) == ["--version"]


def test_driver_summarises_verdicts_per_target() -> None:
    """The table is the report; it must count crashes, not cases."""
    driver = _fuzz_driver()
    ok = Verdict(FuzzCase("argv", "hangman", "argv[0]", 1), True, exit_code=0)
    bad = Verdict(FuzzCase("argv", "hangman", "argv[1]", 2), False, "boom", 1)

    summary = driver._summarise([ok, bad])

    assert summary["hangman"] == {"cases": 2, "crashes": 1}


def test_driver_report_names_a_reproducing_seed() -> None:
    """A failure line must carry enough to reproduce the case."""
    driver = _fuzz_driver()
    case = FuzzCase("argv", "inventory", "argv[4]", 18004, argv=("--days", "x"))
    report = driver.render_report([Verdict(case, False, "exit code 3", 3)])

    assert "1 failure(s)" in report
    assert "seed=18004" in report
    assert "exit code 3" in report


def test_driver_report_confirms_a_clean_run() -> None:
    """A clean campaign must say so rather than printing an empty table."""
    driver = _fuzz_driver()
    report = driver.render_report([Verdict(FuzzCase("text", "chatbot", "text[0]", 1), True)])

    assert "No unhandled exceptions" in report


def test_driver_runs_a_campaign_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """End-to-end: the driver itself must exit 0 on a clean corpus."""
    driver = _fuzz_driver()

    code = driver.main(["--kind", "text", "--cases", "2", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["failures"] == 0
    assert payload["cases"] == 2


def test_driver_rejects_a_negative_case_count(capsys: pytest.CaptureFixture) -> None:
    """Misusing the driver is a usage error, not a silent empty run."""
    driver = _fuzz_driver()

    assert driver.main(["--cases", "-1"]) == 2
    assert "--cases must be non-negative" in capsys.readouterr().err


def test_driver_builds_an_environment_that_redirects_logs(tmp_path: Path) -> None:
    """The sandbox env must keep rotating log files out of the repository."""
    driver = _fuzz_driver()

    env = driver._sandbox_env(tmp_path)

    assert env["DATHEON_LOG_DIR"] == str(tmp_path)
    assert str(ROOT) in env["PYTHONPATH"].split(__import__("os").pathsep)
