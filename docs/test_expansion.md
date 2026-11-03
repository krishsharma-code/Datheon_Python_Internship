# Test expansion — the 200+ assertion budget (Day 15)

Day 15 of the internship plan asks for one thing: **the suite must cross 200+
assertions**. This document records what that target actually meant for this
repository, how it is now measured, and what the expansion uncovered.

## Where the suite stood

| Metric | Before Day 15 | After Day 15 |
| --- | --- | --- |
| Test functions | 592 | 644 |
| Assertion statements | 1,111 | 1,221 |
| Collected pytest items | 745 | 813 |
| Line coverage (whole repo) | 91% | 98% |
| `projects/*/main.py` coverage | 0% | 76–96% |

The raw budget was already met — the suite carried 1,111 assertions across 592
test functions. What was *not* met was the spirit of the target: the number was
unmeasured, and it described only the libraries. Every one of the five CLI entry
points sat at **0% coverage**, roughly 1,200 lines of argument parsing, option
precedence, demo setup and report printing that no test had ever executed in
process.

## What changed

### 1. `tools/assertion_census.py` — the budget becomes a measurement

A small, dependency-free tool that parses every `tests/test_*.py` file with
`ast` and reports assertion statements and test functions per module.

```console
$ python tools/assertion_census.py
module                            tests  asserts
--------------------------------  -----  -------
tests/test_assertion_census.py       17       44
tests/test_cli_entrypoints.py        51      109
...
TOTAL                               644     1221

644 test functions, 1221 assertions.
```

Because it reads the syntax tree rather than the text, an `assert` inside a
docstring, a comment or a string literal is not counted, and assertions nested
in `with` blocks, loops or helper functions are. `--minimum N` turns the count
into a verdict through the process exit code, so a CI step can fail a suite that
shrinks:

```console
$ python tools/assertion_census.py --minimum 200 && echo ok
ok
$ python tools/assertion_census.py --minimum 5000
! assertion budget missed: 1221 < 5000      # exit 1
```

### 2. `tests/test_cli_entrypoints.py` — the CLI layer under test

Fifty-one tests that import each project's `main.py` by file path (the numeric
project folders cannot be imported as packages, and all five ship a module
called `main`) and call `main(argv)` directly. Two rules keep them honest:

- **Hermetic.** `DATHEON_LOG_DIR` is redirected into `tmp_path`, and every path
  a tool would write — summary CSV, activity log, conversation log — is passed
  explicitly, so a test run never touches the repository or the user's home
  directory.
- **Faithful to the process boundary.** Failure paths are wrapped in
  `cli_contract.run_cli`, the same shim the `if __name__ == "__main__"` blocks
  use, so the assertions check the exit code a user actually sees.

Coverage per entry point after the expansion:

| Entry point | Coverage | Notable paths now exercised |
| --- | --- | --- |
| `01_hangman_game` | 76% | word-list mode, category mode, `--validate`, length bounds |
| `02_portfolio_tracker` | 86% | `--demo`, interactive prompt, bad line, `--constituents`, `--strict` |
| `03_file_organizer_automation` | 89% | real moves, `--dry-run`, `--duplicates`, `--log`, `--workers` alias |
| `04_terminal_assistant` | 85% | REPL turns, EOF, FAQ dataset, `--no-log`, `--log` |
| `05_inventory_oop` | 96% | full demo, `--borrower`, `--days`, `--borrow-date`, strict cap |

### 3. `tests/test_assertion_census.py` — the gate guards itself

Seventeen tests that hold the suite to the budget on every run: total assertions
≥ 200, at least 150 test functions, no test module with fewer than ten
assertions, one dedicated module per project, and the census tool's own counting
rules (docstrings and comments ignored, nested assertions counted, JSON and
`--minimum` exit codes).

## The bug the expansion found

Writing the first `--no-log` test for the terminal assistant turned up a real
defect that had survived since Day 8:

```python
logger = None if args.no_log else ConversationLogger(args.log)   # before
logger.debug("turn: intent=%s ...", ...)                          # AttributeError
```

`--no-log` rebound the *diagnostic* logger name to `None` before the REPL loop
used it, so the very first turn crashed with `AttributeError: 'NoneType' object
has no attribute 'debug'`. Every prior test had either passed `--log` or run the
assistant through a subprocess that never typed a message.

The fix keeps the two streams in separate names — `logger` stays the diagnostic
logger, `conversation` is the JSONL transcript — so `--no-log` now disables only
the transcript, which is what the flag says it does.

## Why this order of work

The suite was already large, so adding volume would have been padding. The
cheapest real coverage in the repository was the layer no test had touched: the
CLI wiring. It is also where the only user-visible bug of the week was hiding.
The census tool exists so that the next four weeks of test work — mocking,
property-based testing, fuzzing, mutation testing, the Day 20 coverage gate —
start from a number instead of an adjective.

## Running it

```console
$ python tools/assertion_census.py            # census table
$ python tools/assertion_census.py --json     # machine-readable
$ python tools/assertion_census.py --minimum 200
$ python -m pytest tests/test_cli_entrypoints.py tests/test_assertion_census.py -q
```
