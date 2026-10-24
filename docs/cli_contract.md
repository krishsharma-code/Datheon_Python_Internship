# Command-Line Contract

All five tools in this repository share one consistent command-line surface.
The contract is implemented once in [`cli_contract.py`](../cli_contract.py) and
reused by every entry point, so the tools behave identically from a shell or a
CI script.

## Shared flags

| Flag | Behaviour |
| --- | --- |
| `-h`, `--help` | Print usage and the exit-code legend, then exit with code `0`. |
| `--version` | Print `"<program> <version>"` (e.g. `hangman 1.0.0`) and exit with code `0`. |

`--version` reports the single shared version constant `PROGRAM_VERSION`
(currently `1.0.0`) so a release bump is a one-line change.

## Exit codes

Every tool maps outcomes onto the same three exit codes, also available as the
`ExitCode` `IntEnum`:

| Code | Name | Meaning |
| --- | --- | --- |
| `0` | `ExitCode.OK` | The command completed successfully. |
| `1` | `ExitCode.DATA_ERROR` | A user or data error occurred — e.g. a missing input file, an unreadable dataset, or an invalid directory. |
| `2` | `ExitCode.USAGE_ERROR` | The command line itself was invalid — an unknown flag, a missing value, or a bad argument type. This is `argparse`'s default and matches POSIX `sysexits` `EX_USAGE`. |

Codes `1` and `2` are mutually exclusive by design: a well-formed invocation
that fails on bad data exits `1`, while a malformed invocation never reaches the
tool logic and exits `2`.

## Program names

Each parser sets an explicit `prog`, so help and version output are stable no
matter how the script is launched:

| Project | `prog` | Entry point |
| --- | --- | --- |
| Task 1 — Hangman | `hangman` | `projects/01_hangman_game/main.py` |
| Task 2 — Portfolio tracker | `portfolio` | `projects/02_portfolio_tracker/main.py` |
| Task 3 — File organizer | `organizer` | `projects/03_file_organizer_automation/main.py` |
| Task 4 — Terminal assistant | `assistant` | `projects/04_terminal_assistant/main.py` |
| Task 5 — Inventory (OOP) | `inventory` | `projects/05_inventory_oop/main.py` |

## Strict input validation (`--strict`)

Every tool also accepts a shared `--strict` switch, wired through
[`validators.py`](../validators.py). In the default **lenient** mode the tools
coerce whatever can be coerced (trim whitespace, upper-case tickers, parse
`str` numbers). In **strict** mode they additionally enforce the documented
shape of each value and refuse anything ambiguous, always with one actionable
message and exit code `1`:

| Validator | Lenient | Strict additionally requires |
| --- | --- | --- |
| `validate_symbol` | non-empty, upper-cased | a well-formed ticker (`AAPL`, `RELIANCE.NS`), ≤ 15 chars |
| `validate_quantity` | finite number > 0 | a plain decimal literal (no `1,000`) |
| `validate_price` | finite number ≥ 0 | a plain decimal literal in `[0, 1e9]` |
| `validate_int` | whole number | a plain integer literal (no `1_000`), within bounds |
| `validate_path` | expanded `Path` | the parent directory exists |
| `validate_letter` | one letter | an ASCII letter (`a`–`z`) |
| `validate_date` | ISO date, not in the future | — |
| `validate_name` | non-empty | ≤ 60 chars, no control characters |
| `validate_message` | any text | non-empty, ≤ 500 chars |

Failures raise `ValidationError`, a `ValueError` subclass, so `run_cli` maps
them onto exit code `1` automatically. The layer is covered by
[`tests/test_validation.py`](../tests/test_validation.py).

## Edge-case handling

Beyond validating *values*, every tool survives the failure modes a shell user
actually hits. These are handled once in [`edge_cases.py`](../edge_cases.py) and
reused across the projects, so no case ends in a raw traceback:

| Failure mode | Behaviour |
| --- | --- |
| Empty directory (organizer) | Reports “No files found to organise” and exits `0`. |
| Permission error reading a directory | `iter_files` raises a `PermissionError` naming the directory; the organizer exits `1` with one line. |
| Permission error reading an input file | Each loader re-raises a `PermissionError` naming the file; exit `1`. |
| Missing input file | `FileNotFoundError` → exit `1` with an actionable message. |
| Oversized input file (> 64 MiB) | `check_file_size` rejects it up front → exit `1`. |
| Name collision on move | `unique_destination` appends ` (n)`; a bounded search gives up with `FileExistsError` rather than looping forever. |
| Interrupted input (`Ctrl-D`/`Ctrl-C`) | `safe_input` raises `InputClosed`; the interactive shells end the session cleanly with exit `0`. |
| Broken output pipe (`… \| head`) | `run_cli` catches `BrokenPipeError` and exits `0` quietly. |

The behaviour is pinned by
[`tests/test_edge_cases.py`](../tests/test_edge_cases.py).

## How it is wired

Each entry point imports `build_parser` to obtain an `ArgumentParser` that
already exposes `--version` and appends the exit-code legend to `--help`, then
returns `int(ExitCode.…)` from `main`. The `run_cli` helper wraps the
`if __name__ == "__main__"` call and translates an uncaught `ValueError` or
`OSError` (the standard shapes of a data error) into exit code `1` instead of a
raw traceback. It also treats a `KeyboardInterrupt` or a `BrokenPipeError` (a
closed output pipe) as a clean early exit with code `0`.

```python
from cli_contract import EXIT_CODE_EPILOG, ExitCode, build_parser, run_cli

def main(argv: list[str] | None = None) -> int:
    parser = build_parser("demo", "A demo program.", epilog=EXIT_CODE_EPILOG)
    parser.parse_args(argv)
    ...
    return int(ExitCode.OK)

if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
```

The contract is covered by [`tests/test_cli_contract.py`](../tests/test_cli_contract.py),
which runs every CLI in a subprocess and asserts each of the three exit codes.
