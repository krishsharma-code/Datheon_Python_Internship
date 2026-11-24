"""Interactive terminal entry point for the rule-based assistant.

Run with::

    python projects/04_terminal_assistant/main.py

The session reads input line by line, matches intent via :mod:`chatbot`, prints
the reply with its confidence, and appends every turn to ``conversation.jsonl``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Make the repository root importable so every CLI shares one exit-code
# contract module regardless of the directory the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chatbot import ConversationLogger, IntentEngine  # noqa: E402
from cli_contract import (  # noqa: E402
    EXIT_CODE_EPILOG,
    PROGRAM_VERSION,
    ExitCode,
    JsonEmitter,
    add_json_flag,
    build_parser,
    human_output,
    json_requested,
    run_cli,
)
from dataset_loader import LoadReport  # noqa: E402
from errors import DatheonError  # noqa: E402
from edge_cases import InputClosed, safe_input  # noqa: E402
from logging_config import add_logging_flags, configure_logging_from_args  # noqa: E402
from validators import (  # noqa: E402
    ValidationError,
    add_strict_flag,
    validate_message,
    validate_path,
)

#: Program name, shared by the parser, the version banner and the logger.
PROG = "assistant"

HERE = Path(__file__).resolve().parent
DEFAULT_INTENTS = HERE / "intents.json"
DEFAULT_FAQ = HERE / "sample_faq.json"
DEFAULT_LOG = HERE / "conversation.jsonl"

BANNER = r"""
  ____        _   _   _
 |  _ \  __ _| |_| | | | ___  ___  _ __
 | | | |/ _` | __| |_| |/ _ \/ _ \| '_ \
 | |_| | (_| | |_|  _  |  __/ (_) | | | |
 |____/ \__,_|\__|_| |_|\___|\___/|_| |_|
 Rule-Based Terminal Assistant — Datheon Task 4
"""


def _log_turn(
    logger: ConversationLogger,
    role: str,
    text: str,
    *,
    intent: str = "",
    confidence: float = 0.0,
) -> Optional[ConversationLogger]:
    """Append one turn to the conversation log, degrading gracefully on failure.

    A full disk or an unwritable log path must not abort the session: the
    failure is reported once and logging is switched off for the rest of the
    run.

    Args:
        logger: The active :class:`ConversationLogger`.
        role: ``"user"`` or ``"assistant"``.
        text: The turn's text.
        intent: Matched intent, forwarded to :meth:`ConversationLogger.log`.
        confidence: Match confidence, forwarded to the same call.

    Returns:
        The logger to keep using, or ``None`` once it has been disabled.
    """
    try:
        logger.log(role, text, intent=intent, confidence=confidence)
    except OSError as exc:
        print(f"  ! could not write the conversation log ({exc}); logging disabled.")
        return None
    return logger


def _run_validation(args: argparse.Namespace) -> Tuple[str, Dict[str, Any], int]:
    """Validate the intent or FAQ dataset and exit, without starting the REPL.

    Args:
        args: Parsed command-line arguments (``--intents`` or ``--faq``).

    Returns:
        A ``(command, payload, exit_code)`` triple: code ``0`` when the dataset
        is valid, ``1`` when it is missing or corrupt.
    """
    # The two loaders are different classmethods with different defaults, so the
    # variable is annotated with the shape the loop relies on: path in, engine out.
    loader: Callable[..., IntentEngine]
    if args.faq is not None:
        label, path, loader = "FAQ file", args.faq, IntentEngine.from_faq_json
    else:
        label, path, loader = "intent file", args.intents, IntentEngine.from_json

    report = LoadReport(path)
    try:
        loader(path, strict=True, report=report)
    except (OSError, ValueError) as exc:
        print(f"! {label}: {exc}")
        return (
            "validate",
            _validation_payload(label, path, report, valid=False),
            int(ExitCode.DATA_ERROR),
        )
    print(report.render())
    print(f"OK: {label} {path} is valid ({report.rows_accepted} entries).")
    return "validate", _validation_payload(label, path, report, valid=True), int(ExitCode.OK)


def _validation_payload(
    label: str,
    path: Path,
    report: LoadReport,
    *,
    valid: bool,
) -> Dict[str, Any]:
    """Describe one ``--validate`` run as a JSON-ready mapping.

    Args:
        label: Human name for the dataset (``"FAQ file"``, ``"intent file"``).
        path: The dataset that was checked.
        report: The :class:`~dataset_loader.LoadReport` collected while loading.
        valid: Whether the dataset passed every check.

    Returns:
        A mapping with the label, path, verdict, entry counts and issues.
    """
    return {
        "label": label,
        "path": str(path),
        "valid": valid,
        "rows_checked": report.rows_checked,
        "rows_accepted": report.rows_accepted,
        "issues": [
            {"severity": issue.severity, "line": issue.line, "message": issue.message}
            for issue in report.issues
        ],
    }


def main(argv: List[str] | None = None) -> int:
    """Run the assistant REPL.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``1`` if intents fail to load
        (see :mod:`cli_contract`). With ``--json`` the same code is returned
        and one document is written to ``stdout``.
    """
    parser = build_parser(
        PROG,
        "Rule-based terminal assistant.",
        epilog=EXIT_CODE_EPILOG,
    )
    parser.add_argument(
        "--intents", type=Path, default=DEFAULT_INTENTS, help="JSON intent dataset."
    )
    parser.add_argument(
        "--faq",
        type=Path,
        default=None,
        help="Load intents from a Datheon-style FAQ JSON dataset instead.",
    )
    parser.add_argument(
        "--log", type=Path, default=DEFAULT_LOG, help="JSONL conversation log path."
    )
    parser.add_argument("--no-log", action="store_true", help="Disable conversation logging.")
    add_strict_flag(parser)
    add_logging_flags(parser)
    add_json_flag(parser)
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the intent/FAQ dataset (schema, encoding, entries) and exit.",
    )
    args = parser.parse_args(argv)

    emitter = JsonEmitter(PROG, json_requested(args))
    try:
        with human_output(emitter.enabled):
            command, payload, code = _run(args)
    except DatheonError as exc:
        emitter.failure("validate" if args.validate else "chat", exc)
        raise
    return emitter.success(command, payload, code=code)


def _run(args: argparse.Namespace) -> Tuple[str, Dict[str, Any], int]:
    """Run one assistant session (or a dataset validation) and report it.

    The JSON document is emitted by :func:`main` once this returns, so the
    transcript and the machine-readable payload never interleave on ``stdout``.

    Args:
        args: Parsed command-line arguments.

    Returns:
        A ``(command, payload, exit_code)`` triple for the emitter.
    """
    session = configure_logging_from_args(PROG, args)
    logger = session.logger
    logger.debug("%s %s starting", PROG, PROGRAM_VERSION)
    logger.debug(
        "options: intents=%s faq=%s log=%s no_log=%s strict=%s",
        args.intents,
        args.faq,
        args.log,
        args.no_log,
        args.strict,
    )

    if args.validate:
        return _run_validation(args)

    if args.strict:
        if args.faq is not None:
            args.faq = validate_path(args.faq, must_be_file=True, field="--faq")
        else:
            args.intents = validate_path(args.intents, must_be_file=True, field="--intents")

    print(BANNER)
    try:
        if args.faq is not None:
            engine = IntentEngine.from_faq_json(args.faq)
            print(f"Loaded FAQ dataset: {args.faq}")
        else:
            engine = IntentEngine.from_json(args.intents)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("could not load intents: %s", exc)
        print(f"! Could not load intents: {exc}")
        return (
            "chat",
            {
                "dataset": str(args.faq if args.faq is not None else args.intents),
                "turns": [],
                "turns_count": 0,
                "log_path": None,
                "error": str(exc),
            },
            int(ExitCode.DATA_ERROR),
        )

    # The conversation transcript and the diagnostic logger are two different
    # streams: keep them in separate names so --no-log disables only the JSONL
    # transcript and never blanks out the logger the turn-level debug records
    # are written to.
    conversation = None if args.no_log else ConversationLogger(args.log)
    print("Type a message, or 'bye'/'exit'/'quit' to finish.\n")
    turns: List[Dict[str, Any]] = []

    while True:
        try:
            user_input = safe_input("you > ")
        except InputClosed:
            print("\n(interrupted)")
            break

        try:
            user_input = validate_message(user_input, strict=args.strict, field="input")
        except ValidationError as exc:
            print(f"  ! {exc}")
            continue

        if conversation:
            conversation = _log_turn(conversation, "user", user_input)
        turns.append({"role": "user", "text": user_input})

        response = engine.respond(user_input)
        logger.debug(
            "turn: intent=%s confidence=%.3f exit=%s",
            response.intent,
            response.confidence,
            response.is_exit,
        )
        print(f"bot > {response.text}  [{response.intent} · {response.confidence:.0%}]")

        if conversation:
            conversation = _log_turn(
                conversation,
                "assistant",
                response.text,
                intent=response.intent,
                confidence=response.confidence,
            )
        turns.append(
            {
                "role": "assistant",
                "text": response.text,
                "intent": response.intent,
                "confidence": round(response.confidence, 4),
            }
        )

        if response.is_exit:
            break

    if conversation:
        print(f"\nConversation saved to {conversation.path}")

    payload = {
        "dataset": str(args.faq if args.faq is not None else args.intents),
        "turns": turns,
        "turns_count": len(turns),
        "log_path": str(conversation.path) if conversation else None,
    }
    return "chat", payload, int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
