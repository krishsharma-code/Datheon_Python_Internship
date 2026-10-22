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

from cli_contract import EXIT_CODE_EPILOG, ExitCode, build_parser, run_cli  # noqa: E402
from hangman import HangmanGame, ScoreBoard  # noqa: E402
from words import (  # noqa: E402
    DEFAULT_CATEGORY,
    categories,
    get_random_word,
    get_random_word_from_list,
    load_words_from_file,
)

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
    choice = input(f"Pick a category [{DEFAULT_CATEGORY}]: ").strip().lower()
    if not choice:
        return DEFAULT_CATEGORY
    if choice not in available:
        print(f"Unknown category '{choice}'. Using '{DEFAULT_CATEGORY}'.")
        return DEFAULT_CATEGORY
    return choice


def play_round(word: str, source_label: str) -> bool:
    """Play one full round of Hangman for a given secret word.

    Args:
        word: The secret word for this round.
        source_label: A human-readable description of where the word came
            from, shown to the player.

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
            raw = input("Guess a letter: ")
        except (EOFError, KeyboardInterrupt):
            print("\nInput stream closed — exiting.")
            raise SystemExit(int(ExitCode.OK))

        try:
            result = game.guess(raw)
        except ValueError as exc:
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
    except FileNotFoundError as exc:
        print(f"! {exc}")
        raise SystemExit(int(ExitCode.DATA_ERROR))
    if not words:
        print(f"! No usable words found in {words_file}.")
        raise SystemExit(int(ExitCode.DATA_ERROR))
    print(f"Loaded {len(words):,} words from {words_file}.")
    return words


def main(argv: List[str] | None = None) -> int:
    """Run the Hangman shell until the player declines to replay.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success, ``1`` if a word file fails to
        load (see :mod:`cli_contract`).
    """
    parser = build_parser("hangman", "Console Hangman game.", epilog=EXIT_CODE_EPILOG)
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
    args = parser.parse_args(argv)

    word_list = _load_word_list(args)

    print(BANNER)
    print("Welcome to Hangman — Datheon Python Internship, Task 1.")

    score = ScoreBoard()
    while True:
        if word_list is not None:
            word = get_random_word_from_list(word_list)
            label = f"the official word list ({len(word_list):,} words)"
        else:
            category = choose_category()
            word = get_random_word(category)
            label = f"the '{category}' category"

        score.record(play_round(word, label))

        print(
            f"Score — played: {score.played} | wins: {score.wins} | "
            f"losses: {score.losses} | win rate: {score.win_rate:.0%}"
        )

        again = input("Play again? [y/N]: ").strip().lower()
        if again not in {"y", "yes"}:
            break

    print("\nThanks for playing! Final record:")
    print(f"  Rounds played : {score.played}")
    print(f"  Wins          : {score.wins}")
    print(f"  Losses        : {score.losses}")
    print(f"  Win rate      : {score.win_rate:.0%}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
