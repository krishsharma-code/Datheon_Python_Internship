"""Interactive console entry point for the Hangman game.

Run with::

    python projects/01_hangman_game/main.py
    python projects/01_hangman_game/main.py --categories
    python projects/01_hangman_game/main.py --words-file path/to/words.txt

The module wires the pure game engine in :mod:`hangman` to the terminal. By
default it draws secret words from the official English word list bundled at
``data/words.txt``; pass ``--categories`` to use the smaller themed word banks
instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

# Make the repository root importable so every CLI shares one exit-code
# contract module regardless of the directory the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli_contract import (  # noqa: E402
    EXIT_CODE_EPILOG,
    PROGRAM_VERSION,
    ExitCode,
    build_parser,
    run_cli,
)
from dataset_loader import LoadReport  # noqa: E402
from edge_cases import InputClosed, safe_input  # noqa: E402
from hangman import HangmanGame, ScoreBoard  # noqa: E402
from logging_config import add_logging_flags, configure_logging_from_args  # noqa: E402
from validators import (  # noqa: E402
    ValidationError,
    add_strict_flag,
    validate_int,
    validate_letter,
    validate_path,
)
from words import (  # noqa: E402
    DEFAULT_CATEGORY,
    categories,
    get_random_word,
    get_random_word_from_list,
    load_words_from_file,
)

#: Program name, shared by the parser, the version banner and the logger.
PROG = "hangman"

HERE = Path(__file__).resolve().parent
DEFAULT_WORDS_FILE = HERE / "data" / "words.txt"

BANNER = r"""
 _   _                                          _
| | | | __ _ _ __   __ _ _ __ ___   __ _ _ __ | |
| |_| |/ _` | '_ \ / _` | '_ ` _ \ / _` | '_ \| |
|  _  | (_| | | | | (_| | | | | | | (_| | | | |_|
|_| |_|\__,_|_| |_|\__, |_| |_| |_|\__,_|_| |_(_)
                   |___/
"""


def choose_category() -> str:
    """Prompt the player to choose a word category.

    Returns:
        The selected category name, or :data:`DEFAULT_CATEGORY` if the player
        presses Enter without typing anything or enters an unknown value.
    """
    available = categories()
    print("\nAvailable categories: " + ", ".join(available))
    choice = safe_input(f"Pick a category [{DEFAULT_CATEGORY}]: ").strip().lower()
    if not choice:
        return DEFAULT_CATEGORY
    if choice not in available:
        print(f"Unknown category '{choice}'. Using '{DEFAULT_CATEGORY}'.")
        return DEFAULT_CATEGORY
    return choice


def play_round(word: str, source_label: str, strict: bool = False) -> bool:
    """Play one full round of Hangman for a given secret word.

    Args:
        word: The secret word for this round.
        source_label: A human-readable description of where the word came
            from, shown to the player.
        strict: When ``True``, only ASCII letters (``a``–``z``) are accepted as
            guesses, and the reason is spelled out on rejection.

    Returns:
        ``True`` if the player guessed the word, ``False`` otherwise.
    """
    game = HangmanGame(word)

    print(f"\nA secret word has been chosen from {source_label}.")
    print(f"You have {game.max_wrong} incorrect guesses. Good luck!\n")

    while not game.is_over:
        print(game.ascii_stage())
        print(f"\nWord:  {game.masked_word()}")
        print(f"Wrong: {' '.join(sorted(game.guessed - set(game.word))) or '-'}")
        print(f"Attempts left: {game.remaining_attempts}")

        try:
            raw = safe_input("Guess a letter: ")
        except InputClosed:
            print("\nInput stream closed — exiting.")
            raise SystemExit(int(ExitCode.OK))

        try:
            result = game.guess(validate_letter(raw, strict=strict, field="guess"))
        except ValidationError as exc:
            print(f"  ! {exc}")
            continue

        print(f"  {result.message}\n")

    print(game.ascii_stage())
    if game.is_won:
        print(f"\n🎉 You guessed it! The word was '{game.word}'.\n")
        return True

    print(f"\n💀 Out of attempts. The word was '{game.word}'.\n")
    return False


def _load_word_list(args: argparse.Namespace) -> Optional[List[str]]:
    """Resolve the word list to draw from, or ``None`` for themed categories.

    Honours ``--categories`` (force category banks), ``--words-file`` (explicit
    list), and otherwise falls back to the bundled official word list when it
    is present.

    Args:
        args: Parsed command-line arguments.

    Returns:
        The loaded word list, or ``None`` to use the themed category banks.

    Raises:
        SystemExit: If a requested word file is missing or yields no words.
    """
    if args.categories:
        return None

    words_file = args.words_file
    if words_file is None and DEFAULT_WORDS_FILE.is_file():
        words_file = DEFAULT_WORDS_FILE
    if words_file is None:
        return None

    try:
        words = load_words_from_file(words_file, args.min_length, args.max_length)
    except (OSError, ValueError) as exc:
        print(f"! {exc}")
        raise SystemExit(int(ExitCode.DATA_ERROR))
    if not words:
        print(f"! No usable words found in {words_file}.")
        raise SystemExit(int(ExitCode.DATA_ERROR))
    print(f"Loaded {len(words):,} words from {words_file}.")
    return words


def _run_validation(args: argparse.Namespace) -> int:
    """Validate the word-list dataset and report, without starting a game.

    Args:
        args: Parsed command-line arguments (``--words-file``, ``--min-length``,
            ``--max-length``).

    Returns:
        ``0`` when the dataset is valid, ``1`` when it is missing or corrupt.
    """
    words_file = args.words_file
    if words_file is None and DEFAULT_WORDS_FILE.is_file():
        words_file = DEFAULT_WORDS_FILE
    if words_file is None:
        print("! no word list to validate (pass --words-file).")
        return int(ExitCode.DATA_ERROR)

    report = LoadReport(words_file)
    try:
        load_words_from_file(
            words_file,
            args.min_length,
            args.max_length,
            strict=True,
            report=report,
        )
    except (OSError, ValueError) as exc:
        print(f"! {exc}")
        return int(ExitCode.DATA_ERROR)
    print(report.render())
    print(f"OK: {report.rows_accepted:,} words loaded from {words_file}.")
    return int(ExitCode.OK)


def main(argv: List[str] | None = None) -> int:
    """Run the Hangman shell until the player declines to replay.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``1`` if a word file fails to
        load (see :mod:`cli_contract`).
    """
    parser = build_parser(PROG, "Console Hangman game.", epilog=EXIT_CODE_EPILOG)
    add_strict_flag(parser)
    add_logging_flags(parser)
    parser.add_argument(
        "--categories",
        action="store_true",
        help="Use the themed category word banks instead of the word list.",
    )
    parser.add_argument(
        "--words-file",
        type=Path,
        default=None,
        help="Newline-delimited word list to draw secret words from.",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=4,
        help="Minimum word length when loading a word file (default: 4).",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=None,
        help="Optional maximum word length when loading a word file.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the word-list dataset (schema, encoding, rows) and exit.",
    )
    args = parser.parse_args(argv)

    session = configure_logging_from_args(PROG, args)
    logger = session.logger
    logger.debug("%s %s starting", PROG, PROGRAM_VERSION)
    logger.debug(
        "options: categories=%s words_file=%s min_length=%s max_length=%s strict=%s",
        args.categories,
        args.words_file,
        args.min_length,
        args.max_length,
        args.strict,
    )

    # Reject nonsensical length bounds before any file I/O happens.
    args.min_length = validate_int(args.min_length, minimum=1, field="--min-length")
    if args.max_length is not None:
        args.max_length = validate_int(
            args.max_length, minimum=args.min_length, field="--max-length"
        )
    if args.strict and args.words_file is not None:
        args.words_file = validate_path(args.words_file, must_be_file=True, field="--words-file")

    if args.validate:
        return _run_validation(args)

    word_list = _load_word_list(args)
    if word_list is not None:
        logger.info("drawing secret words from %d loaded words", len(word_list))
    else:
        logger.info("drawing secret words from the themed category banks")

    print(BANNER)
    print("Welcome to Hangman — Datheon Python Internship, Task 1.")

    score = ScoreBoard()
    while True:
        try:
            if word_list is not None:
                word = get_random_word_from_list(word_list)
                label = f"the official word list ({len(word_list):,} words)"
            else:
                category = choose_category()
                word = get_random_word(category)
                label = f"the '{category}' category"

            score.record(play_round(word, label, strict=args.strict))

            print(
                f"Score — played: {score.played} | wins: {score.wins} | "
                f"losses: {score.losses} | win rate: {score.win_rate:.0%}"
            )

            again = safe_input("Play again? [y/N]: ").strip().lower()
        except InputClosed:
            print("\nInput stream closed — exiting.")
            break
        if again not in {"y", "yes"}:
            break

    logger.info(
        "session finished: played=%d wins=%d losses=%d win_rate=%.2f",
        score.played,
        score.wins,
        score.losses,
        score.win_rate,
    )
    print("\nThanks for playing! Final record:")
    print(f"  Rounds played : {score.played}")
    print(f"  Wins          : {score.wins}")
    print(f"  Losses        : {score.losses}")
    print(f"  Win rate      : {score.win_rate:.0%}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
