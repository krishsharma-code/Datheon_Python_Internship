"""Fuzz driver for the five CLIs, the dataset loaders and the assistant (Day 18).

Day 18's deliverable is a sentence with a number in it: *random and malformed
input to the CLIs, loaders and chatbot, with zero unhandled exceptions*. This
script is what turns that sentence into a measurement.

It runs four campaigns, all driven by the deterministic corpus in
:mod:`fuzzing`:

===============  ==============================================================
Campaign         What is exercised
===============  ==============================================================
``argv``         Each of the five entry points, as a real subprocess, with a
                 fuzzed argument vector and a closed stdin.
``dataset``      The three dataset-aware tools, again as subprocesses, with
                 ``--validate`` pointed at a byte-mutated copy of a real
                 dataset — the path a corrupt download actually takes.
``loader``       The six loader entry points in-process, so an exception that
                 the CLI would have masked is still visible.
``text``         The intent engine's ``respond`` plus the strict message
                 validator, over hostile unicode.
===============  ==============================================================

Run it directly::

    python tools/fuzz.py                        # all campaigns, default corpus
    python tools/fuzz.py --cases 100 --seed 7   # a deeper, different run
    python tools/fuzz.py --kind argv --json     # one campaign, machine-readable

The exit code is the verdict: ``0`` when every case stayed inside the contract,
``1`` when any case crashed or the tool itself was misused. That makes the
script usable as a CI step — a regression shows up as a red build, not as a
paragraph in a report nobody reads.

Nothing here is destructive by design. Every subprocess runs with its working
directory inside a temporary sandbox, log output is redirected there, absolute
paths are absent from the argument pool, and the file organizer is always given
``--dry-run`` so a fuzzed directory argument can never move a real file.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for _project in sorted((ROOT / "projects").iterdir()):
    if _project.is_dir() and str(_project) not in sys.path:
        sys.path.insert(0, str(_project))

from errors import DatheonError  # noqa: E402  (import after sys.path is prepared)
from fuzzing import (  # noqa: E402  (import after sys.path is prepared)
    DEFAULT_CASES,
    DEFAULT_SEED,
    FuzzCase,
    Verdict,
    generate_cases,
    judge_exception,
    judge_process,
)
from validators import ValidationError, validate_message  # noqa: E402

#: ``(name, entry-point path)`` for each of the five tools.
CLI_TARGETS: Dict[str, Path] = {
    "hangman": ROOT / "projects" / "01_hangman_game" / "main.py",
    "portfolio": ROOT / "projects" / "02_portfolio_tracker" / "main.py",
    "organizer": ROOT / "projects" / "03_file_organizer_automation" / "main.py",
    "assistant": ROOT / "projects" / "04_terminal_assistant" / "main.py",
    "inventory": ROOT / "projects" / "05_inventory_oop" / "main.py",
}

#: ``target name -> (CLI name, dataset flag, mutation seed name)`` for the
#: ``--validate`` campaign.
DATASET_TARGETS: Dict[str, Tuple[str, str, str]] = {
    "hangman/words-file": ("hangman", "--words-file", "words"),
    "portfolio/prices": ("portfolio", "--prices", "prices"),
    "portfolio/constituents": ("portfolio", "--constituents", "constituents"),
    "assistant/intents": ("assistant", "--intents", "intents"),
    "assistant/faq": ("assistant", "--faq", "faq"),
}

#: Real datasets, used as the starting point for byte-level mutation.
SEED_FILES: Dict[str, Path] = {
    "words": ROOT / "projects" / "01_hangman_game" / "data" / "words.txt",
    "prices": ROOT / "projects" / "02_portfolio_tracker" / "data" / "sample_prices.csv",
    "constituents": ROOT / "projects" / "02_portfolio_tracker" / "data" / "constituents.csv",
    "intents": ROOT / "projects" / "04_terminal_assistant" / "intents.json",
    "faq": ROOT / "projects" / "04_terminal_assistant" / "sample_faq.json",
}

#: ``loader name -> mutation seed name``; the symbols loader reads the same
#: constituents file the portfolio tool does.
LOADER_SEEDS: Dict[str, str] = {
    "words": "words",
    "prices": "prices",
    "symbols": "constituents",
    "intents": "intents",
    "faq": "faq",
}

#: Wall-clock ceiling for one subprocess case, in seconds.
CASE_TIMEOUT: float = 30.0


def _read_seed(name: str) -> List[bytes]:
    """Load a dataset as mutation material, tolerating a missing file.

    Args:
        name: Key into :data:`SEED_FILES`.

    Returns:
        A single-element list holding the file's bytes, or an empty list when
        the dataset is absent so the campaign falls back to random noise.
    """
    path = SEED_FILES[name]
    return [path.read_bytes()] if path.is_file() else []


def _sandbox_env(log_dir: Path) -> Dict[str, str]:
    """Build the environment a fuzzed subprocess runs under.

    Args:
        log_dir: Directory the tools may write rotating log files into.

    Returns:
        A copy of the current environment with ``DATHEON_LOG_DIR`` redirected
        and the repository plus every project directory on ``PYTHONPATH``, so a
        subprocess resolves the same modules the test suite does.
    """
    env = dict(os.environ)
    env["DATHEON_LOG_DIR"] = str(log_dir)
    roots = [str(ROOT)] + [str(p) for p in sorted((ROOT / "projects").iterdir()) if p.is_dir()]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(roots + ([existing] if existing else []))
    env.pop("PYTHONHASHSEED", None)
    return env


def _harden(target: str, argv: Sequence[str]) -> List[str]:
    """Make a fuzzed argument vector safe to run.

    The organizer moves files, so a fuzzed positional directory is only ever
    previewed: ``--dry-run`` is injected when the case does not already carry
    it. Every other tool is read-only or writes only to the sandbox.

    Args:
        target: CLI name from :data:`CLI_TARGETS`.
        argv: The fuzzed tokens.

    Returns:
        The tokens to pass to the entry point.
    """
    tokens = list(argv)
    if target == "organizer" and "--dry-run" not in tokens:
        tokens.insert(0, "--dry-run")
    return tokens


def run_process_case(
    case: FuzzCase,
    script: Path,
    argv: Sequence[str],
    *,
    workdir: Path,
    env: Dict[str, str],
    timeout: float = CASE_TIMEOUT,
) -> Verdict:
    """Run one CLI case as a subprocess and judge the outcome.

    Args:
        case: The case being run, used for the verdict.
        script: Path to the entry point's ``main.py``.
        argv: Argument vector for the tool.
        workdir: Sandbox directory the process runs in.
        env: Environment for the process (see :func:`_sandbox_env`).
        timeout: Seconds before the case is declared hung.

    Returns:
        A passing :class:`Verdict` when the process exits ``0``, ``1`` or ``2``
        without printing a traceback; otherwise a failing one. A vector that
        carries a NUL byte is reported as skipped, because the operating system
        cannot pass it to a child process at all.
    """
    # A NUL byte cannot cross the process boundary: no kernel will accept it in
    # an argv entry, so such a case is unreachable rather than a crash. The
    # in-process suite still exercises it, because ``main(argv)`` takes any
    # string.
    if any("\x00" in token for token in argv):
        return Verdict(case, True, "skipped: NUL cannot cross the process boundary")

    try:
        completed = subprocess.run(
            [sys.executable, str(script), *argv],
            cwd=str(workdir),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Verdict(case, False, f"timed out after {timeout:.0f}s", None, "")
    except OSError as exc:  # pragma: no cover - only if the interpreter vanishes
        return Verdict(case, False, f"could not start the process: {exc}", None, "")

    return judge_process(case, completed.returncode, completed.stdout, completed.stderr)


def run_argv_campaign(
    *,
    seed: int,
    cases: int,
    workdir: Path,
    timeout: float = CASE_TIMEOUT,
    targets: Sequence[str] = tuple(CLI_TARGETS),
) -> List[Verdict]:
    """Fuzz every entry point's argument parser.

    Args:
        seed: Master seed for the corpus.
        cases: Number of argument vectors per target.
        workdir: Sandbox directory, also the tools' working directory.
        timeout: Seconds before one case is declared hung.
        targets: Subset of :data:`CLI_TARGETS` to exercise.

    Returns:
        One :class:`Verdict` per case, in target-then-index order.
    """
    env = _sandbox_env(workdir / "logs")
    verdicts: List[Verdict] = []
    for target in targets:
        script = CLI_TARGETS[target]
        for case in generate_cases("argv", target, seed=seed, count=cases):
            verdicts.append(
                run_process_case(
                    case,
                    script,
                    _harden(target, case.argv),
                    workdir=workdir,
                    env=env,
                    timeout=timeout,
                )
            )
    return verdicts


def run_dataset_campaign(
    *,
    seed: int,
    cases: int,
    workdir: Path,
    timeout: float = CASE_TIMEOUT,
    targets: Sequence[str] = tuple(DATASET_TARGETS),
) -> List[Verdict]:
    """Fuzz the ``--validate`` path of every dataset-aware tool.

    Each case writes a mutated copy of a real dataset into the sandbox and
    points the tool at it, so the whole chain — decode, schema check, row
    parsing, error rendering — sees the corruption.

    Args:
        seed: Master seed for the corpus.
        cases: Number of mutated files per target.
        workdir: Sandbox directory.
        timeout: Seconds before one case is declared hung.
        targets: Subset of :data:`DATASET_TARGETS` to exercise.

    Returns:
        One :class:`Verdict` per case.
    """
    env = _sandbox_env(workdir / "logs")
    verdicts: List[Verdict] = []

    for target in targets:
        cli_name, flag, seed_name = DATASET_TARGETS[target]
        material = _read_seed(seed_name)
        for index, case in enumerate(
            generate_cases("bytes", target, seed=seed, count=cases, seeds=material)
        ):
            path = workdir / f"case_{target.replace('/', '_')}_{index}.dat"
            path.write_bytes(case.data)
            verdicts.append(
                run_process_case(
                    case,
                    CLI_TARGETS[cli_name],
                    [flag, str(path), "--validate"],
                    workdir=workdir,
                    env=env,
                    timeout=timeout,
                )
            )
    return verdicts


def _loader_callables() -> Dict[str, Callable[[Path], object]]:
    """Return the six loader entry points under test.

    Imports are deferred to call time so that a failure in one project does not
    prevent the other campaigns from running.

    Returns:
        A mapping from loader name to a callable taking the fuzzed file path.
    """
    from chatbot import IntentEngine
    from portfolio import load_prices_from_csv, load_symbols_from_csv
    from words import load_words_from_file

    return {
        "words": lambda path: load_words_from_file(path, use_cache=False),
        "prices": load_prices_from_csv,
        "symbols": lambda path: load_symbols_from_csv(path, use_cache=False),
        "intents": IntentEngine.from_json,
        "faq": IntentEngine.from_faq_json,
    }


def run_loader_campaign(
    *,
    seed: int,
    cases: int,
    workdir: Path,
    targets: Sequence[str] = ("words", "prices", "symbols", "intents", "faq"),
) -> List[Verdict]:
    """Fuzz the loaders directly, so their own exceptions are observable.

    A loader is expected to convert every malformed input into a
    :class:`~dataset_loader.DatasetFormatError` (a :class:`~errors.DatheonError`);
    the process boundary would hide a leak, so the tolerance here is narrower
    than the CLI campaigns'.

    Args:
        seed: Master seed for the corpus.
        cases: Number of files per loader.
        workdir: Sandbox directory.
        targets: Subset of the loader names to exercise.

    Returns:
        One :class:`Verdict` per case.
    """
    loaders = _loader_callables()
    verdicts: List[Verdict] = []

    for target in targets:
        load = loaders[target]
        material = _read_seed(LOADER_SEEDS.get(target, target))
        for index, case in enumerate(
            generate_cases("bytes", target, seed=seed, count=cases, seeds=material)
        ):
            path = workdir / f"loader_{target}_{index}.dat"
            path.write_bytes(case.data)
            try:
                load(path)
            except BaseException as exc:  # noqa: BLE001 - the point of the campaign
                verdicts.append(judge_exception(case, exc, tolerated=(DatheonError,)))
            else:
                verdicts.append(Verdict(case, True, "loaded or rejected cleanly"))
    return verdicts


def run_text_campaign(*, seed: int, cases: int, workdir: Path) -> List[Verdict]:
    """Fuzz the assistant's message validator and intent engine.

    Args:
        seed: Master seed for the corpus.
        cases: Number of hostile strings.
        workdir: Sandbox directory (the engine reads only its own dataset).

    Returns:
        One :class:`Verdict` per case. A case passes when the validator either
        rejects the text with a :class:`~validators.ValidationError` or accepts
        it and the engine returns a well-formed reply.
    """
    from chatbot import IntentEngine

    engine = IntentEngine.from_json(SEED_FILES["intents"])
    verdicts: List[Verdict] = []

    for case in generate_cases("text", "chatbot", seed=seed, count=cases):
        try:
            try:
                message = validate_message(case.text, strict=True, field="input")
            except ValidationError:
                verdicts.append(Verdict(case, True, "rejected by the validator"))
                continue
            response = engine.respond(message)
            problems = []
            if not 0.0 <= response.confidence <= 1.0:
                problems.append(f"confidence {response.confidence} outside 0..1")
            if not isinstance(response.text, str) or not response.text.strip():
                problems.append("empty response text")
            if not isinstance(response.is_exit, bool):
                problems.append("is_exit is not a bool")
            if problems:
                verdicts.append(Verdict(case, False, "; ".join(problems)))
            else:
                verdicts.append(Verdict(case, True, f"intent={response.intent}"))
        except BaseException as exc:  # noqa: BLE001 - the point of the campaign
            verdicts.append(judge_exception(case, exc))

    return verdicts


def _summarise(verdicts: Sequence[Verdict]) -> Dict[str, Dict[str, int]]:
    """Aggregate verdicts per target.

    Args:
        verdicts: The verdicts to summarise.

    Returns:
        ``{target: {"cases": n, "crashes": n}}``, ordered by first appearance.
    """
    summary: Dict[str, Dict[str, int]] = {}
    for verdict in verdicts:
        bucket = summary.setdefault(verdict.case.target, {"cases": 0, "crashes": 0})
        bucket["cases"] += 1
        if not verdict.ok:
            bucket["crashes"] += 1
    return summary


def render_report(verdicts: Sequence[Verdict]) -> str:
    """Render a human-readable report of a fuzz run.

    Args:
        verdicts: The verdicts to report on.

    Returns:
        A multi-line report: a per-target table, then the failures with their
        seed and payload so they can be reproduced.
    """
    summary = _summarise(verdicts)
    failures = [v for v in verdicts if not v.ok]
    width = max((len(name) for name in summary), default=10)

    lines = [
        f"Fuzz run — {len(verdicts)} case(s) across {len(summary)} target(s)",
        "",
        f"{'target'.ljust(width)}  cases  crashes",
        f"{'-' * width}  -----  -------",
    ]
    for name, bucket in summary.items():
        lines.append(f"{name.ljust(width)}  {bucket['cases']:>5}  {bucket['crashes']:>7}")

    if failures:
        lines.extend(["", f"{len(failures)} failure(s):", ""])
        for verdict in failures:
            lines.append(f"  ! {verdict.case.describe()}")
            lines.append(f"    {verdict.reason}")
            if verdict.detail:
                lines.append(f"    output: {verdict.detail}")
    else:
        lines.extend(["", "No unhandled exceptions: every case stayed inside the CLI contract."])

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the driver's argument parser.

    Returns:
        A parser exposing the campaign selector, corpus size, seed and output
        switches.
    """
    parser = argparse.ArgumentParser(
        prog="fuzz",
        description="Fuzz the Datheon CLIs, dataset loaders and assistant (Day 18).",
    )
    parser.add_argument(
        "--kind",
        choices=("all", "argv", "dataset", "loader", "text"),
        default="all",
        help="Which campaign to run (default: all).",
    )
    parser.add_argument(
        "--cases",
        type=int,
        default=DEFAULT_CASES,
        help=f"Cases per target (default: {DEFAULT_CASES}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=(
            "Corpus seed; the same seed always produces the same corpus "
            f"(default: {DEFAULT_SEED})."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=CASE_TIMEOUT,
        help=f"Seconds before one subprocess case is declared hung (default: {CASE_TIMEOUT:.0f}).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit a JSON report instead of a table."
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the selected campaigns and report.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        ``0`` when every case stayed inside the contract, ``1`` when any case
        crashed, ``2`` on a usage error.
    """
    args = build_parser().parse_args(argv)
    if args.cases < 0:
        print("fuzz: --cases must be non-negative", file=sys.stderr)
        return 2

    verdicts: List[Verdict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="datheon-fuzz-") as sandbox:
            workdir = Path(sandbox)
            (workdir / "logs").mkdir(exist_ok=True)

            if args.kind in ("all", "argv"):
                verdicts.extend(
                    run_argv_campaign(
                        seed=args.seed, cases=args.cases, workdir=workdir, timeout=args.timeout
                    )
                )
            if args.kind in ("all", "dataset"):
                verdicts.extend(
                    run_dataset_campaign(
                        seed=args.seed, cases=args.cases, workdir=workdir, timeout=args.timeout
                    )
                )
            if args.kind in ("all", "loader"):
                verdicts.extend(
                    run_loader_campaign(seed=args.seed, cases=args.cases, workdir=workdir)
                )
            if args.kind in ("all", "text"):
                verdicts.extend(
                    run_text_campaign(seed=args.seed, cases=args.cases, workdir=workdir)
                )
    except Exception:  # noqa: BLE001 - a driver bug must not look like a clean run
        traceback.print_exc()
        print("fuzz: the driver itself failed", file=sys.stderr)
        return 1

    failures = [v for v in verdicts if not v.ok]

    if args.json:
        print(
            json.dumps(
                {
                    "seed": args.seed,
                    "cases": len(verdicts),
                    "failures": len(failures),
                    "summary": _summarise(verdicts),
                    "verdicts": [v.to_dict() for v in verdicts],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(render_report(verdicts))

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
