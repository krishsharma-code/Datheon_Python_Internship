"""Interactive terminal entry point for the rule-based assistant.

Run with::

    python projects/04_terminal_assistant/main.py

The session reads input line by line, matches intent via :mod:`chatbot`, prints
the reply with its confidence, and appends every turn to ``conversation.jsonl``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

# Make the repository root importable so every CLI shares one exit-code
# contract module regardless of the directory the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chatbot import ConversationLogger, IntentEngine  # noqa: E402
from cli_contract import EXIT_CODE_EPILOG, ExitCode, build_parser, run_cli  # noqa: E402
from edge_cases import InputClosed, safe_input  # noqa: E402
from validators import (  # noqa: E402
    ValidationError,
    add_strict_flag,
    validate_message,
    validate_path,
)

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
    **extra: object,
) -> Optional[ConversationLogger]:
    """Append one turn to the conversation log, degrading gracefully on failure.

    A full disk or an unwritable log path must not abort the session: the
    failure is reported once and logging is switched off for the rest of the
    run.

    Args:
        logger: The active :class:`ConversationLogger`.
        role: ``"user"`` or ``"assistant"``.
        text: The turn's text.
        **extra: Extra fields forwarded to :meth:`ConversationLogger.log`
            (``intent``, ``confidence``).

    Returns:
        The logger to keep using, or ``None`` once it has been disabled.
    """
    try:
        logger.log(role, text, **extra)
    except OSError as exc:
        print(f"  ! could not write the conversation log ({exc}); logging disabled.")
        return None
    return logger


def main(argv: List[str] | None = None) -> int:
    """Run the assistant REPL.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``1`` if intents fail to load
        (see :mod:`cli_contract`).
    """
    parser = build_parser(
        "assistant",
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
    args = parser.parse_args(argv)

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
        print(f"! Could not load intents: {exc}")
        return int(ExitCode.DATA_ERROR)

    logger = None if args.no_log else ConversationLogger(args.log)
    print("Type a message, or 'bye'/'exit'/'quit' to finish.\n")

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

        if logger:
            logger = _log_turn(logger, "user", user_input)

        response = engine.respond(user_input)
        print(f"bot > {response.text}  [{response.intent} · {response.confidence:.0%}]")

        if logger:
            logger = _log_turn(
                logger,
                "assistant",
                response.text,
                intent=response.intent,
                confidence=response.confidence,
            )

        if response.is_exit:
            break

    if logger:
        print(f"\nConversation saved to {logger.path}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
