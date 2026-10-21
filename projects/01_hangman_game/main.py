"""Interactive console entry point for the Hangman game.

Run with::

    python projects/01_hangman_game/main.py

The module wires the pure game engine in :mod:`hangman` to the terminal:
category selection, the guess loop, ASCII gallows rendering, score tracking,
and a replay prompt.
"""

from __future__ import annotations

import sys

from hangman import HangmanGame, ScoreBoard
from words import DEFAULT_CATEGORY, categories, get_random_word

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


def play_round(category: str) -> bool:
    """Play one full round of Hangman.

    Args:
        category: Word bank to draw the secret word from.

    Returns:
        ``True`` if the player guessed the word, ``False`` otherwise.
    """
    game = HangmanGame(get_random_word(category))

    print(f"\nA secret word has been chosen from '{category}'.")
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
            raise SystemExit(0)

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


def main() -> int:
    """Run the Hangman shell until the player declines to replay.

    Returns:
        Process exit code (always ``0``).
    """
    print(BANNER)
    print("Welcome to Hangman — Datheon Python Internship, Task 1.")

    score = ScoreBoard()
    while True:
        category = choose_category()
        score.record(play_round(category))

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
