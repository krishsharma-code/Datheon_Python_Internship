# Logging contract

Every Datheon tool writes its diagnostics through one shared module,
[`logging_config.py`](../logging_config.py). This page is the reference for
that contract: the switches, the levels, the two handlers, where the file
lives, and the behaviour you can rely on when something goes wrong.

Logging is **opt-in**. Without `--log-level` (or an explicit `--log-file`) a
tool installs no handlers and writes nothing to disk, so a plain
`python projects/01_hangman_game/main.py` behaves exactly as it did before Day
8.

## Switches

| Switch | Argument | Default | Effect |
| --- | --- | --- | --- |
| `--log-level` | `DEBUG` · `INFO` · `WARNING` · `ERROR` · `CRITICAL` | off | Enable logging at that level; also enables the rotating log file. Case-insensitive (`--log-level debug` works). |
| `--log-file` | path | `<log dir>/<prog>.log` | Write the rotating log file here instead. Supplying it without `--log-level` logs at `WARNING`. |

All five tools accept the same two switches:

```bash
python projects/01_hangman_game/main.py --log-level DEBUG
python projects/02_portfolio_tracker/main.py --demo --log-level INFO --log-file /tmp/portfolio.log
python projects/03_file_organizer_automation/main.py ~/Downloads --dry-run --log-level DEBUG
python projects/04_terminal_assistant/main.py --log-level DEBUG
python projects/05_inventory_oop/main.py --log-level DEBUG
```

An unknown level is a usage error: `argparse` rejects it and the process exits
`2`, exactly like any other malformed command line.

## Where the file goes

`DATHEON_LOG_DIR` decides the directory for every tool at once:

```bash
DATHEON_LOG_DIR=/var/log/datheon python projects/03_file_organizer_automation/main.py . --log-level INFO
```

When it is unset the directory is `~/.datheon/logs`, and each program appends
`.log` to its own name — `hangman.log`, `portfolio.log`, `organizer.log`,
`assistant.log`, `inventory.log`. Missing parent directories are created on
demand.

## The two handlers

| Handler | Stream / path | Level | Format |
| --- | --- | --- | --- |
| Console | `stderr` | the requested level | `LEVEL    prog: message` |
| Rotating file | `<log dir>/<prog>.log` | the requested level | `timestamp LEVEL    prog [file:line] message` |

The console handler deliberately writes to `stderr`, so `stdout` stays
machine-readable — a report piped into another tool is never polluted by log
lines. The file handler keeps the detail an unattended run needs after the
fact: an ISO-8601 timestamp with the UTC offset and the exact source location.

```text
2026-10-27T09:03:12+0530 INFO     organizer [main.py:154] processed=12 moved=12 planned=0 errors=0 duplicates=0
```

Rotation is bounded: the file rotates at **1 MiB** and keeps **3** generations
(`organizer.log.1` … `organizer.log.3`), so a cron job can never fill the disk.

## What each tool logs

| Tool | `INFO` | `DEBUG` |
| --- | --- | --- |
| `hangman` | where the secret words came from, final score | start-up banner, resolved options |
| `portfolio` | price rows loaded, positions valued, summary path | start-up banner, resolved options |
| `organizer` | processed / moved / planned / error / duplicate counts | start-up banner, resolved options |
| `assistant` | intents or FAQ dataset loaded | start-up banner, resolved options, per-turn intent and confidence |
| `inventory` | demo inventory size, loans outstanding, revenue | start-up banner, resolved options |

Every tool logs `"<prog> <version> starting"` at `DEBUG` before it does any
work, so `--log-level DEBUG` always leaves a trace even when the run fails
early.

## Failure behaviour

* **An unwritable log path never aborts a run.** If the directory cannot be
  created or the file cannot be opened, the tool logs one
  `file logging disabled: …` warning to the console and continues with the
  console handler only. `LoggingSession.file_error` records the reason and
  `LoggingSession.file_enabled` is `False`.
* **Reconfiguring replaces, never duplicates.** `configure_logging` detaches
  the handlers it installed previously for the same logger, so a second call
  (or a second test) cannot double every line.
* **Cleanup happens once, at the process boundary.** `run_cli` calls
  `shutdown_logging()` in a `finally` block, so the file handler is flushed and
  closed on the success path and on every error path. Only handlers this
  module installed are touched — a host application's own logging is left
  alone.
* **Interrupted runs still log.** `Ctrl-C` and a closed pipe exit `0` through
  the same `finally`, so the tail of the log file is never truncated.

## Design decisions

1. **One module, five tools.** The alternative — a `logging.basicConfig` call
   in each `main.py` — would drift within a week: different formats, different
   destinations, some tools forgetting rotation. One module makes the contract
   testable in isolation and identical everywhere.
2. **Opt-in, not always-on.** Writing to `~/.datheon/logs` on every casual run
   is surprising for a console game. Requiring `--log-level` keeps the default
   behaviour silent, keeps the test suite hermetic, and still gives the cron
   use case a one-flag switch.
3. **Session object, not a global.** `configure_logging` returns a
   `LoggingSession` that owns its handlers. The caller can close it, use it as
   a context manager, or inspect `log_file` / `file_error`. Nothing is hidden
   in module-level state that a test cannot reset.
4. **`propagate = False`.** Each tool's logger is self-contained, so a host
   application or `pytest` cannot capture (or duplicate) a tool's records by
   accident, and a tool never inherits a foreign root configuration.
5. **Rotation with small constants.** 1 MiB × 3 generations is enough for
   weeks of a daily cron run and small enough to be harmless if a tool is
   misconfigured.
6. **Ready for structured logs.** The formatter is a single string constant
   (`FILE_FORMAT`); Day 10 replaces it with a JSON formatter and the
   `DatheonError.to_dict()` record shape, without touching a single call site.

## Tests

`tests/test_logging.py` covers the contract end to end: level resolution and
its error cases, the environment override, the flags' defaults and usage
errors, file creation and format, rotation, the unwritable-path fallback,
reconfiguration, session cleanup (including through `run_cli`), and — for
each of the five CLIs, in a subprocess — that `--log-level DEBUG` writes a log
file, that the default run writes nothing, that `--help` documents both
switches, and that an unknown level exits `2`.
