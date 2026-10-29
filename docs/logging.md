# Logging contract

Every Datheon tool writes its diagnostics through one shared module,
[`logging_config.py`](../logging_config.py). This page is the reference for
that contract: the switches, the levels, the two handlers, where the file
lives, and the behaviour you can rely on when something goes wrong.

Logging is **opt-in**. Without `--log-level` (or an explicit `--log-file`) a
tool installs no handlers and writes nothing to disk, so a plain
`python projects/01_hangman_game/main.py` behaves exactly as it did before Day
8.

Since Day 10 the same contract also speaks **JSON**: add `--json-logs` and every
record becomes one JSON object carrying a `run_id`, the `project`, the elapsed
`duration_ms` and the run's `outcome`. The text format stays the default, so
nothing a human reads today changes. See [Structured logs](#structured-logs).

## Switches

| Switch | Argument | Default | Effect |
| --- | --- | --- | --- |
| `--log-level` | `DEBUG` · `INFO` · `WARNING` · `ERROR` · `CRITICAL` | off | Enable logging at that level; also enables the rotating log file. Case-insensitive (`--log-level debug` works). |
| `--log-file` | path | `<log dir>/<prog>.log` | Write the rotating log file here instead. Supplying it without `--log-level` logs at `WARNING`. |
| `--json-logs` | flag | off | Emit one JSON object per record on **both** handlers instead of text lines. Needs `--log-level` or `--log-file` to have any effect. |
| `--run-id` | id | generated | Correlation ID stamped on every record of the run. `DATHEON_RUN_ID` overrides the generated default. |

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
6. **The formatter is a swappable strategy, not a code path.** `FILE_FORMAT`
   is a single string constant and the handlers take whatever
   `logging.Formatter` they are given, which is why Day 10 could add a JSON
   mode by choosing a different formatter and touching **no call site**.

---

## Structured logs

`--json-logs` swaps both formatters for `structured_logging.JsonFormatter`.
Each line becomes one JSON object; nothing else about the setup changes — same
handlers, same levels, same file, same rotation, same opt-in default.

```bash
python projects/03_file_organizer_automation/main.py ~/Downloads --log-level INFO --json-logs
jq -c 'select(.run_outcome == "error")' ~/.datheon/logs/organizer.log
```

```json
{"schema_version": 1, "ts": "2026-10-29T09:03:12.418+05:30", "level": "INFO",
 "levelno": 20, "logger": "organizer", "message": "run started",
 "source": "logging_config.py:538", "function": "configure_logging",
 "run_id": "run-20261029T033104Z-9f3c1a2b", "project": "organizer",
 "started": "2026-10-29T09:03:12.377+05:30", "duration_ms": 0.747,
 "run_outcome": "running", "seq": 1,
 "fields": {"event": "run_start", "project": "organizer", "json_logs": true,
            "log_file": "/home/krish/.datheon/logs/organizer.log"}}
```

### The record schema

| Key | Always | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Version of this schema (`1`). Bumped on a breaking key change, so a pipeline can branch on it. |
| `ts` | yes | Record time, ISO-8601 with the local UTC offset, millisecond precision. |
| `level` / `levelno` | yes | Level name and number. |
| `logger` | yes | Logger name — the program name, e.g. `organizer`. |
| `message` | yes | The fully formatted message. |
| `source` / `function` | yes | `file.py:line` and the function that emitted the record. |
| `run_id` | when a run is open | Correlation ID shared by every record of one invocation. |
| `project` | when a run is open | Program the run belongs to. |
| `started` | when a run is open | Wall-clock start of the run. |
| `duration_ms` | when a run is open | Milliseconds since the run started — so a record's offset, and on `run_finish` the total cost. |
| `run_outcome` | when a run is open | `running`, then `success`, `error`, `interrupted` or `incomplete`. |
| `seq` | when a run is open | Contiguous record counter from `1`, shared by the console and file copies of the same record. |
| `exception` | on `exc_info` | Formatted traceback text. |
| `fields` | when the caller passes fields | Caller-supplied structured fields, **always nested** so they can never shadow a reserved key. |

### Run IDs, durations and outcomes

A session opens a run when it configures logging and closes it at the process
boundary, so the first and last record of an invocation share a `run_id`:

```text
run_start   run_outcome=running    duration_ms=0.7
…
run_finish  run_outcome=success    duration_ms=41.2
```

The ID is minted as `run-<UTC stamp>-<random>` (sortable, collision-resistant).
Three ways to control it:

1. `--run-id cron-20261029` on the command line;
2. `DATHEON_RUN_ID` in the environment — set once by a cron wrapper or CI job
   and every tool in the process tree inherits it;
3. nothing at all — a fresh ID is generated per run.

`run_cli` decides the outcome: `success` when the tool returns `0`, `error`
when it raises a `DatheonError` or returns non-zero, `interrupted` on `Ctrl-C`
or a broken pipe, and `incomplete` for a session closed without ever finishing.
A failed run's finish record carries the machine-readable error — the same
payload `DatheonError.to_dict()` produces:

```json
{"message": "run finished", "run_outcome": "error", "duration_ms": 12.9,
 "fields": {"event": "run_finish", "outcome": "error",
            "error": {"code": "DT-1001", "type": "ValidationError",
                      "message": "invalid symbol", "hint": "use a ticker like AAPL",
                      "context": {"symbol": "??"}, "exit_code": 1}}}
```

That makes the cron case one query: *"did anything fail in the last day?"*

```bash
jq -c 'select(.run_outcome == "error") | {ts, project, error: .fields.error.code}'   ~/.datheon/logs/*.log
```

### Adding structured fields

```python
from logging_config import structured_extra

session.logger.info(
    "processed=%d moved=%d", moved, moved, extra=structured_extra(processed=moved, moved=moved)
)
```

Fields land under `fields` and values that are not JSON-serialisable are
rendered with `str` — a `Path`, a set or a dataclass never costs you the
record. To log an exception structurally, use `log_error(logger, exc)`: a
taxonomy error contributes its full `to_dict()` payload, any other exception
its type and message.

## Tests

`tests/test_logging.py` covers the text contract end to end: level resolution
and its error cases, the environment override, the flags' defaults and usage
errors, file creation and format, rotation, the unwritable-path fallback,
reconfiguration, session cleanup (including through `run_cli`), and — for
each of the five CLIs, in a subprocess — that `--log-level DEBUG` writes a log
file, that the default run writes nothing, that `--help` documents both
switches, and that an unknown level exits `2`.

`tests/test_structured_logging.py` covers the structured layer: the run-ID
shape and its three sources, context isolation, `duration_ms` monotonicity, the
full record schema, nested-field precedence over reserved keys, non-serialisable
values, `exc_info`, contiguous `seq` across both handlers, idempotent `finish`,
the `incomplete` outcome, every `run_cli` outcome and its error payload, and —
per CLI in a subprocess — that `--json-logs` produces only parseable JSON whose
records share one `run_id` and end with a `run_finish` record.
