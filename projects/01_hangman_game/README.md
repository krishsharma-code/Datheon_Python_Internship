# Task 1 — Hangman Word Guessing Game (Console)

An interactive, modular console Hangman game built with the Python standard
library only.

## Features

- **Category-based word banks** — programming, animals, countries, technology.
- **Seven progressive ASCII gallows stages** that update with each mistake.
- **Duplicate-guess protection** — retrying a letter never costs an attempt.
- **Input validation** — anything other than a single letter A–Z is rejected
  without penalty.
- **Score tracking** — per-round result plus a cumulative win/loss record and
  win rate across replays.
- **Clean victory/defeat screens** with a replay loop.

## Run

```bash
python projects/01_hangman_game/main.py
```

## Architecture

| File | Responsibility |
| --- | --- |
| `hangman.py` | Pure engine: `HangmanGame`, `GuessResult`, `ScoreBoard`, ASCII stages. No I/O. |
| `words.py` | Category word banks and `get_random_word()`. |
| `main.py` | Terminal shell: prompts, rendering, replay loop. |

The engine is free of `input()`/`print()`, which is what makes it directly
testable — see `tests/test_hangman.py`.

## Example

```text
  +---+
  |   |
  O   |
 /|\  |
 /    |
      |
=========

Word:  p y t h o n
Attempts left: 1
Guess a letter:
```
