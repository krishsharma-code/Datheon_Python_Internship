"""Core, UI-independent Hangman game engine.

This module deliberately contains no ``input()`` or ``print()`` calls so the
game logic can be exercised directly by the unit tests. The interactive shell
lives in :mod:`main`.

Typical use::

    from hangman import HangmanGame

    game = HangmanGame("python")
    result = game.guess("p")
    print(game.masked_word())
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set

#: Number of incorrect guesses a player may make before losing.
MAX_WRONG: int = 6

#: Character used to mask letters that have not yet been guessed.
MASK_CHAR: str = "_"

#: ASCII gallows art, indexed by the number of incorrect guesses (0..6).
HANGMAN_STAGES: List[str] = [
    # 0 wrong
    ("  +---+\n" "  |   |\n" "      |\n" "      |\n" "      |\n" "      |\n" "========="),
    # 1 wrong
    ("  +---+\n" "  |   |\n" "  O   |\n" "      |\n" "      |\n" "      |\n" "========="),
    # 2 wrong
    ("  +---+\n" "  |   |\n" "  O   |\n" "  |   |\n" "      |\n" "      |\n" "========="),
    # 3 wrong
    ("  +---+\n" "  |   |\n" "  O   |\n" " /|   |\n" "      |\n" "      |\n" "========="),
    # 4 wrong
    ("  +---+\n" "  |   |\n" "  O   |\n" " /|\\  |\n" "      |\n" "      |\n" "========="),
    # 5 wrong
    ("  +---+\n" "  |   |\n" "  O   |\n" " /|\\  |\n" " /    |\n" "      |\n" "========="),
    # 6 wrong — game over
    ("  +---+\n" "  |   |\n" "  O   |\n" " /|\\  |\n" " / \\  |\n" "      |\n" "========="),
]


@dataclass(frozen=True)
class GuessResult:
    """Outcome of a single :meth:`HangmanGame.guess` call.

    Attributes:
        letter: The normalised letter that was played.
        correct: ``True`` if the letter occurs in the secret word.
        already_guessed: ``True`` if the letter had been played before and was
            therefore not penalised.
        message: A human-readable description of the outcome.
    """

    letter: str
    correct: bool
    already_guessed: bool
    message: str


def normalize_letter(raw: str) -> str:
    """Validate and normalise a single-letter guess.

    Args:
        raw: The raw string entered by the player.

    Returns:
        The lower-cased, whitespace-stripped letter.

    Raises:
        ValueError: If ``raw`` is not exactly one alphabetic character.
    """
    if raw is None:
        raise ValueError("No letter was provided.")
    letter = raw.strip().lower()
    if len(letter) != 1 or not letter.isalpha():
        raise ValueError("Enter exactly one letter (A-Z).")
    return letter


class HangmanGame:
    """Stateful engine for a single round of Hangman.

    The class tracks the secret word, the set of letters guessed so far, and
    the number of incorrect guesses. It never mutates global state, so multiple
    instances can run side by side (useful in tests).

    Args:
        word: The secret word. Must contain letters only.
        max_wrong: Number of incorrect guesses allowed. Defaults to
            :data:`MAX_WRONG`.

    Raises:
        ValueError: If ``word`` is empty or contains non-alphabetic characters.
    """

    def __init__(self, word: str, max_wrong: int = MAX_WRONG) -> None:
        cleaned = (word or "").strip().lower()
        if not cleaned.isalpha():
            raise ValueError("The secret word must contain letters only.")
        if max_wrong < 1:
            raise ValueError("max_wrong must be at least 1.")
        self._word: str = cleaned
        self._max_wrong: int = max_wrong
        self._guessed: Set[str] = set()

    # -- read-only state -------------------------------------------------
    @property
    def word(self) -> str:
        """The secret word (lower-case)."""
        return self._word

    @property
    def max_wrong(self) -> int:
        """Maximum number of incorrect guesses permitted."""
        return self._max_wrong

    @property
    def guessed(self) -> Set[str]:
        """A copy of the letters guessed so far."""
        return set(self._guessed)

    @property
    def wrong_guesses(self) -> int:
        """Count of distinct incorrect letters played."""
        return len(self._guessed - set(self._word))

    @property
    def correct_guesses(self) -> int:
        """Count of distinct correct letters played."""
        return len(self._guessed & set(self._word))

    @property
    def remaining_attempts(self) -> int:
        """Incorrect guesses still available before the round is lost."""
        return self._max_wrong - self.wrong_guesses

    @property
    def is_won(self) -> bool:
        """``True`` once every letter of the secret word has been guessed."""
        return set(self._word) <= self._guessed

    @property
    def is_lost(self) -> bool:
        """``True`` once the wrong-guess budget is exhausted."""
        return self.wrong_guesses >= self._max_wrong

    @property
    def is_over(self) -> bool:
        """``True`` when the round has been won or lost."""
        return self.is_won or self.is_lost

    # -- rendering -------------------------------------------------------
    def masked_word(self) -> str:
        """Return the secret word with unguessed letters masked.

        Returns:
            The word as space-separated characters, e.g. ``"_ y _ h o n"``.
        """
        return " ".join(char if char in self._guessed else MASK_CHAR for char in self._word)

    def ascii_stage(self) -> str:
        """Return the ASCII gallows art for the current mistake count."""
        index = min(self.wrong_guesses, len(HANGMAN_STAGES) - 1)
        return HANGMAN_STAGES[index]

    # -- gameplay --------------------------------------------------------
    def guess(self, raw: str) -> GuessResult:
        """Play a single letter.

        A repeated letter is reported but **never** costs an attempt. Playing
        after the round is over is a no-op.

        Args:
            raw: The raw letter entered by the player.

        Returns:
            A :class:`GuessResult` describing the outcome.

        Raises:
            ValueError: If ``raw`` is not a single alphabetic character.
        """
        letter = normalize_letter(raw)

        if self.is_over:
            return GuessResult(letter, False, False, "This round is already over.")

        if letter in self._guessed:
            return GuessResult(
                letter,
                letter in self._word,
                True,
                f"You already tried '{letter}' — no penalty applied.",
            )

        self._guessed.add(letter)
        if letter in self._word:
            return GuessResult(letter, True, False, f"Nice! '{letter}' is in the word.")
        return GuessResult(letter, False, False, f"'{letter}' is not in the word.")


@dataclass
class ScoreBoard:
    """Cumulative win/loss record across multiple rounds.

    Attributes:
        wins: Number of rounds won.
        losses: Number of rounds lost.
    """

    wins: int = 0
    losses: int = 0

    @property
    def played(self) -> int:
        """Total rounds completed."""
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        """Fraction of completed rounds that were won (0.0–1.0)."""
        return self.wins / self.played if self.played else 0.0

    def record(self, won: bool) -> None:
        """Record the outcome of a finished round.

        Args:
            won: ``True`` if the player won, ``False`` otherwise.
        """
        if won:
            self.wins += 1
        else:
            self.losses += 1
