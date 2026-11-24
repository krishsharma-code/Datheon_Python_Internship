# Hangman

A console Hangman with themed word banks, an ASCII gallows, a running score and
a strict-input mode that rejects ambiguous guesses instead of silently ignoring
them.

```bash
hangman --categories
```

## The session

This is one real round, captured from `main.py` with scripted stdin: the
category prompt, every guess, the board after each one, the win, and the
play-again prompt.

The session behind the round above is committed, not typed: [`assets/demos/hangman.json`](../assets/demos/hangman.json) is the exact file the player fetches, and `python tools/live_demos.py --check` fails if this page and that file ever disagree.

<!-- live-demo:begin -->
<div class="demo-terminal" data-session="assets/demos/hangman.json" data-command="hangman --categories">
<pre class="demo-terminal__fallback" aria-label="Captured session: One round, played from stdin">$ hangman --categories
 _   _                                          _
| | | | __ _ _ __   __ _ _ __ ___   __ _ _ __ | |
| |_| |/ _` | '_ \ / _` | '_ ` _ \ / _` | '_ \| |
|  _  | (_| | | | | (_| | | | | | | (_| | | | |_|
|_| |_|\__,_|_| |_|\__, |_| |_| |_|\__,_|_| |_(_)
                   |___/

Welcome to Hangman — Datheon Python Internship, Task 1.

Available categories: animals, countries, programming, technology
Pick a category [programming]: programming
A secret word has been chosen from the 'programming' category.
You have 6 incorrect guesses. Good luck!

  +---+
  |   |
      |
      |
      |
      |
=========

Word:  _ _ _ _ _ _ _ _ _
Wrong: -
Attempts left: 6
Guess a letter: e
Nice! 'e' is in the word.

  +---+
  |   |
      |
      |
      |
      |
=========

Word:  _ e _ _ _ _ _ _ _
Wrong: -
Attempts left: 6
Guess a letter: t
't' is not in the word.

  +---+
  |   |
  O   |
      |
      |
      |
=========

Word:  _ e _ _ _ _ _ _ _
Wrong: t
Attempts left: 5
Guess a letter: a
'a' is not in the word.

  +---+
  |   |
  O   |
  |   |
      |
      |
=========

Word:  _ e _ _ _ _ _ _ _
Wrong: a t
Attempts left: 4
Guess a letter: o
Nice! 'o' is in the word.

  +---+
  |   |
  O   |
  |   |
      |
      |
=========

Word:  _ e _ _ _ _ _ o _
Wrong: a t
Attempts left: 4
Guess a letter: i
Nice! 'i' is in the word.

  +---+
  |   |
  O   |
  |   |
      |
      |
=========

Word:  _ e _ _ _ _ i o _
Wrong: a t
Attempts left: 4
Guess a letter: n
Nice! 'n' is in the word.

  +---+
  |   |
  O   |
  |   |
      |
      |
=========

Word:  _ e _ _ _ _ i o n
Wrong: a t
Attempts left: 4
Guess a letter: s
Nice! 's' is in the word.

  +---+
  |   |
  O   |
  |   |
      |
      |
=========

Word:  _ e _ _ _ s i o n
Wrong: a t
Attempts left: 4
Guess a letter: r
Nice! 'r' is in the word.

  +---+
  |   |
  O   |
  |   |
      |
      |
=========

Word:  r e _ _ r s i o n
Wrong: a t
Attempts left: 4
Guess a letter: h
'h' is not in the word.

  +---+
  |   |
  O   |
 /|   |
      |
      |
=========

Word:  r e _ _ r s i o n
Wrong: a h t
Attempts left: 3
Guess a letter: l
'l' is not in the word.

  +---+
  |   |
  O   |
 /|\  |
      |
      |
=========

Word:  r e _ _ r s i o n
Wrong: a h l t
Attempts left: 2
Guess a letter: d
'd' is not in the word.

  +---+
  |   |
  O   |
 /|\  |
 /    |
      |
=========

Word:  r e _ _ r s i o n
Wrong: a d h l t
Attempts left: 1
Guess a letter: c
Nice! 'c' is in the word.

  +---+
  |   |
  O   |
 /|\  |
 /    |
      |
=========

Word:  r e c _ r s i o n
Wrong: a d h l t
Attempts left: 1
Guess a letter: u
Nice! 'u' is in the word.

  +---+
  |   |
  O   |
 /|\  |
 /    |
      |
=========

🎉 You guessed it! The word was 'recursion'.

Score — played: 1 | wins: 1 | losses: 0 | win rate: 100%
Play again? [y/N]: m
Thanks for playing! Final record:
  Rounds played : 1
  Wins          : 1
  Losses        : 0
  Win rate      : 100%</pre>
</div>
<!-- live-demo:end -->

## What to look at

- **The board is redrawn, not scrolled.** Every guess prints the gallows, the
  masked word, the letters already ruled out and the attempts left, so the state
  is legible in a terminal with no cursor control.
- **A guess is validated before it is used.** `e`, `E` and ` e ` are the same
  guess; a digit, a word or a letter already tried is rejected with one
  actionable message and *does not* cost an attempt.
- **The score survives the round.** Rounds played, wins, losses and win rate are
  printed at the end, which is what the `ScoreBoard` object exists for.
- **Play again is a prompt, not a loop flag.** Answering `n` ends the run with
  exit code `0`; `Ctrl-C` and a closed stdin end it through the shared
  `InputClosed` path with exit code `1`.

## Run it yourself

```bash
hangman --categories                     # themed word banks
hangman --words-file data/words.txt      # the bundled 2,000-word list
hangman --min-length 5 --max-length 7    # narrow the draw
hangman --validate                       # check a word list and exit
hangman --json                           # one machine-readable document
hangman --strict                         # reject out-of-range input
```

From a checkout, without installing: `python projects/01_hangman_game/main.py`.

## How it is built

| Module | What lives there |
| --- | --- |
| `hangman.py` | `HangmanGame` (pure game state) and `ScoreBoard` — no I/O, no `input()` |
| `words.py` | The word list and category banks, loaded through `dataset_loader` and cached by `caching` |
| `main.py` | The CLI: `argparse`, the round loop, the score, the exit codes |

The split matters: the game rules are tested without a terminal, which is why
`tests/test_hangman.py` (36 tests) can play thousands of games in a second. The
word list is loaded once and cached by file signature, so a second run in the
same process re-reads nothing.

## Under the gates

- `tests/test_hangman.py` — 36 tests: win, loss, duplicate guess, invalid input,
  EOF, `--validate`, exit codes.
- The CLI contract (`--version`, `--help`, `--json`, exit codes `0/1/2`) is
  shared with the other four tools and pinned by `tests/test_cli_contract.py`.
- Word loading is covered by `tests/test_dataset_loader.py` and the fuzz corpus
  in `tools/fuzz.py`, which throws malformed word lists at it.

See [CLI contract & exit codes](../cli_contract.md), [caching](../caching.md) and
the [API reference](../api/projects.md).
