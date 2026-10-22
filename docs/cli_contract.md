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

## How it is wired

Each entry point imports `build_parser` to obtain an `ArgumentParser` that
already exposes `--version` and appends the exit-code legend to `--help`, then
returns `int(ExitCode.…)` from `main`. The `run_cli` helper wraps the
`if __name__ == "__main__"` call and translates an uncaught `ValueError` or
`OSError` (the standard shapes of a data error) into exit code `1` instead of a
raw traceback.

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
