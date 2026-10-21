"""Tests for the Hangman engine and word banks."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from hangman import (
    HANGMAN_STAGES,
    MAX_WRONG,
    HangmanGame,
    ScoreBoard,
    normalize_letter,
)
from words import (
    MIN_WORD_LENGTH,
    categories,
    get_random_word,
    get_random_word_from_list,
    load_words_from_file,
)


# --- normalize_letter --------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [("a", "a"), ("Z", "z"), ("  b  ", "b")],
)
def test_normalize_letter_accepts_single_letters(raw, expected):
    assert normalize_letter(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "ab", "1", "!", "hello", None])
def test_normalize_letter_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_letter(raw)


# --- word banks --------------------------------------------------------
def test_categories_are_non_empty():
    assert categories()
    assert "programming" in categories()


def test_get_random_word_is_deterministic_with_seed():
    word = get_random_word("animals", rng=random.Random(42))
    assert word in {
        "elephant",
        "giraffe",
        "penguin",
        "kangaroo",
        "dolphin",
        "cheetah",
        "octopus",
        "squirrel",
        "flamingo",
    }


def test_get_random_word_unknown_category_raises():
    with pytest.raises(KeyError):
        get_random_word("does-not-exist")


# --- construction ------------------------------------------------------
def test_game_rejects_non_alphabetic_word():
    with pytest.raises(ValueError):
        HangmanGame("py thon")
    with pytest.raises(ValueError):
        HangmanGame("")


def test_game_rejects_bad_max_wrong():
    with pytest.raises(ValueError):
        HangmanGame("python", max_wrong=0)


def test_word_is_lowercased():
    assert HangmanGame("PYTHON").word == "python"


# --- masking -----------------------------------------------------------
def test_masked_word_hides_unguessed_letters():
    game = HangmanGame("python")
    assert game.masked_word() == "_ _ _ _ _ _"


def test_masked_word_reveals_guessed_letters():
    game = HangmanGame("python")
    game.guess("p")
    game.guess("y")
    assert game.masked_word() == "p y _ _ _ _"


# --- guessing ----------------------------------------------------------
def test_correct_guess_is_reported():
    game = HangmanGame("python")
    result = game.guess("p")
    assert result.correct is True
    assert result.already_guessed is False
    assert game.wrong_guesses == 0


def test_incorrect_guess_costs_an_attempt():
    game = HangmanGame("python")
    result = game.guess("z")
    assert result.correct is False
    assert game.wrong_guesses == 1
    assert game.remaining_attempts == MAX_WRONG - 1


def test_duplicate_guess_is_not_penalised():
    game = HangmanGame("python")
    game.guess("z")
    assert game.wrong_guesses == 1
    again = game.guess("z")
    assert again.already_guessed is True
    assert game.wrong_guesses == 1  # unchanged


def test_duplicate_correct_guess_is_not_penalised():
    game = HangmanGame("python")
    game.guess("p")
    again = game.guess("p")
    assert again.already_guessed is True
    assert game.correct_guesses == 1


def test_guess_after_game_over_is_noop():
    game = HangmanGame("ab", max_wrong=1)
    game.guess("z")  # now lost
    assert game.is_lost
    result = game.guess("a")
    assert result.correct is False
    assert "over" in result.message.lower()


# --- win / lose --------------------------------------------------------
def test_winning_when_all_letters_guessed():
    game = HangmanGame("cat")
    for letter in "cat":
        game.guess(letter)
    assert game.is_won
    assert game.is_over


def test_losing_after_max_wrong():
    game = HangmanGame("cat", max_wrong=2)
    game.guess("x")
    game.guess("y")
    assert game.is_lost
    assert game.remaining_attempts == 0


# --- rendering ---------------------------------------------------------
def test_ascii_stage_advances_with_mistakes():
    game = HangmanGame("python")
    assert game.ascii_stage() == HANGMAN_STAGES[0]
    game.guess("z")
    assert game.ascii_stage() == HANGMAN_STAGES[1]


def test_ascii_stage_is_clamped_at_end():
    game = HangmanGame("python", max_wrong=MAX_WRONG)
    for letter in "qwerzx":  # none of these appear in "python"
        game.guess(letter)
    assert game.is_lost
    assert game.ascii_stage() == HANGMAN_STAGES[-1]


# --- scoreboard --------------------------------------------------------
def test_scoreboard_tracks_record():
    board = ScoreBoard()
    assert board.played == 0
    assert board.win_rate == 0.0
    board.record(True)
    board.record(False)
    board.record(True)
    assert board.played == 3
    assert board.wins == 2
    assert board.losses == 1
    assert board.win_rate == pytest.approx(2 / 3)


# --- external word list ------------------------------------------------
def test_load_words_from_file_filters_and_dedupes(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("apple\nbanana\nap\napple\n12345\n\nCherry\n", encoding="utf-8")
    words = load_words_from_file(path)
    # 'ap' is too short, '12345' is non-alphabetic, the duplicate is dropped,
    # and 'Cherry' is normalised to lower case.
    assert words == ["apple", "banana", "cherry"]


def test_load_words_from_file_respects_length_bounds(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("alpha\nbe\ncharlie\ndeltaecho\n", encoding="utf-8")
    words = load_words_from_file(path, min_length=3, max_length=7)
    assert "be" not in words
    assert "deltaecho" not in words
    assert "alpha" in words
    assert "charlie" in words


def test_load_words_from_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_words_from_file(tmp_path / "nope.txt")


@pytest.mark.parametrize(
    "kwargs",
    [{"min_length": 0}, {"min_length": 5, "max_length": 3}],
)
def test_load_words_from_file_bad_bounds(tmp_path, kwargs):
    path = tmp_path / "words.txt"
    path.write_text("hello\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_words_from_file(path, **kwargs)


def test_get_random_word_from_list_is_deterministic():
    words = ["alpha", "bravo", "charlie"]
    word = get_random_word_from_list(words, rng=random.Random(1))
    assert word in words


def test_get_random_word_from_list_empty_raises():
    with pytest.raises(ValueError):
        get_random_word_from_list([])


def test_bundled_official_word_list_loads():
    path = (
        Path(__file__).resolve().parents[1] / "projects" / "01_hangman_game" / "data" / "words.txt"
    )
    words = load_words_from_file(path, min_length=MIN_WORD_LENGTH)
    assert len(words) > 1000
    assert all(w.isalpha() and w.islower() and len(w) >= MIN_WORD_LENGTH for w in words)
