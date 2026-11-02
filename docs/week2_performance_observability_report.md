# Week 2 consolidated report — performance & observability

**Date:** 2026-11-02 · **Milestone:** Day 14 — *Consolidate Week 2*
**Scope:** Days 8–13 — logging, structured records, atomic persistence, async I/O,
profiling, caching
**New module:** [`observability.py`](../observability.py) · **Tests:**
[`tests/test_observability.py`](../tests/test_observability.py) (49 tests)
**Suite:** 745 tests green · `flake8 .` clean · `black --check .` clean

Week 2 was six days of separate changes, each documented on its own page. This
report is the join: what the week bought in wall-clock terms, what an operator
can now learn about a run from the logs alone, and which numbers a later day can
be measured against. Every figure here is reproducible with the commands in
[§6](#6-reproducing-every-figure); none of them is quoted from memory.

---

## 1. What Week 2 shipped

| Day | Change | Evidence |
| --- | --- | --- |
| 8 | One logging setup for all five tools: `--log-level`, console + rotating file handler | [`docs/logging.md`](logging.md), `tests/test_logging.py` |
| 9 | Atomic writes (temp file + `os.replace`), `schema_version` on every JSON document | [`docs/persistence.md`](persistence.md), `tests/test_persistence.py` |
| 10 | JSON records, run IDs, `duration_ms`, `run_outcome` | [`docs/logging.md#structured-logs`](logging.md#structured-logs), `tests/test_structured_logging.py` |
| 11 | Organizer moves on `asyncio` with a bounded worker queue | [`docs/async_io.md`](async_io.md), `tests/test_async_organizer.py` |
| 12 | `cProfile` on the hot paths; three optimisations, no behaviour change | [`docs/performance_profiling.md`](performance_profiling.md), `tests/test_profiling.py` |
| 13 | Dataset caches with signature-based invalidation | [`docs/caching.md`](caching.md), `tests/test_caching.py` |
| 14 | **This report** + `observability.py`, which reduces JSON logs to run figures | `tests/test_observability.py` |

The thread running through all six days is the same: **make the behaviour
measurable before changing it**. Day 8 made runs traceable, Day 10 made the
traces machine-readable, Day 12 measured the hot paths before touching them, and
Day 13 measured the cache it had just built — a step that caught a 3× regression
in its own first cut (see [`docs/caching.md`](caching.md) §5, the `path_key`
decision).

---

## 2. Performance: what the week bought

### 2.1 Organizer throughput (Day 11)

`benchmark.py`, 400 files with 3 ms of injected per-file latency, best of the
measured runs:

| concurrency | elapsed_ms | files/sec | speedup |
| ---: | ---: | ---: | ---: |
| 1 | 2311.5 | 173.0 | 1.00× |
| 2 | 1311.9 | 304.9 | 1.76× |
| 4 | 1128.6 | 354.4 | 2.05× |
| 8 | 859.0 | 465.7 | 2.69× |
| 16 | 584.5 | 684.3 | 3.95× |

With real local moves and no injected latency (500 files) the same engine is
already ~380 files/sec at `concurrency=1` and reaches only **1.17×** at 16. The
honest reading of those two tables together: the event loop removes *waiting*,
and once the wait is gone the filesystem is the ceiling. That is why
`DEFAULT_CONCURRENCY` is a modest number and `MAX_CONCURRENCY` is 64 rather than
"as many as possible".

### 2.2 Hot paths (Day 12)

Best sample of 5–7 runs, `profiling.time_callable`:

| Hot path | Before | After | Change |
| --- | ---: | ---: | ---: |
| `iter_files` (2,000 files) | 198.9 ms | 14.1 ms | **14.1× faster** |
| `category_for` × 20,000 | 205.8 ms | 45.1 ms | **4.6× faster** |
| `FileOrganizer.organize` dry run (2,000 files) | 645.4 ms | 431.3 ms | **1.50× faster** |
| `find_duplicates` (600 unique + 60 copies) | 522.2 ms | 129.7 ms | **4.0× faster** |
| `find_duplicates` (600 unique sizes) | 398.9 ms | 85.5 ms | **4.7× faster** |

In profile terms, on a 600-file fixture: `posix.stat` calls fell 1,385 → 785,
`_io.open` calls 660 → 120, `BufferedReader.read` calls 1,320 → 240, and the
duplicate scan's wall time 514.5 ms → 206.8 ms.

Three hot paths were profiled and deliberately left alone (word list, price CSV,
intent matching) because the profile showed nothing worth changing. Null results
are part of the record — see
[`docs/performance_profiling.md`](performance_profiling.md) §4.3.

### 2.3 Dataset loads (Day 13)

| Dataset | Cold (parse) | Warm (cache hit) | Speedup |
| --- | ---: | ---: | ---: |
| word list (75 KB) | 7.91 ms | 0.28 ms | **28.4×** |
| constituents (500 rows) | 4.24 ms | 0.25 ms | **17.0×** |
| intent regexes (11 patterns) | 0.77 ms | 0.68 ms | 1.1× |

200 loads of each dataset: 2429.1 ms uncached → 105.7 ms cached. The residual
~0.25 ms of a warm hit is the single `os.stat` that proves the file is
unchanged — the price of not serving stale data, and the number to watch if a
dataset ever needs cheaper revalidation.

### 2.4 Where the remaining time goes

Two costs survive all of Week 2 and neither is Python overhead:

1. **One `stat` per file in the collision check.** A dry run has to report the
   *real* destination name, so it must ask whether the name is taken. 600 files
   → 600 `exists()` calls, ~85% of the remaining organizer time. Removing it
   would make the report wrong.
2. **One `stat` per cache hit.** The freshness signature is what makes the cache
   correct; it is also the entire cost of a hit.

Both are floors imposed by the problem, not overhead imposed by the code. The
next real lever is the number of runs, not the cost of one — which is what the
observability half of this report is for.

---

## 3. The observability contract

The logging contract is specified in [`docs/logging.md`](logging.md). What
follows is the summary a reviewer needs, plus the guarantees the suite pins.

### 3.1 The switches

| Switch | Default | Effect |
| --- | --- | --- |
| `--log-level LEVEL` | off | Enable logging and the rotating file handler |
| `--log-file PATH` | `<log dir>/<prog>.log` | Redirect the file handler |
| `--json-logs` | off | One JSON object per record on both handlers |
| `--run-id ID` | generated | Pin the correlation ID (`DATHEON_RUN_ID` also works) |

Logging is **opt-in**: a plain `python projects/01_hangman_game/main.py` writes
nothing, which keeps the default silent and the test suite hermetic.

### 3.2 The record schema (schema_version 1)

| Key | Always | Meaning |
| --- | --- | --- |
| `schema_version` | yes | `1`; bumped on a breaking key change |
| `ts`, `level`, `levelno`, `logger`, `message` | yes | Who said what, when, at which level |
| `source`, `function` | yes | `file.py:line` and the emitting function |
| `run_id`, `project`, `started` | while a run is open | Correlation and provenance |
| `duration_ms` | while a run is open | Offset from run start; the total on `run_finish` |
| `run_outcome` | while a run is open | `running` → `success` / `error` / `interrupted` / `incomplete` |
| `seq` | while a run is open | Contiguous counter shared by console and file copies |
| `fields` | when the caller passes fields | Caller data, **always nested**, so it cannot shadow a reserved key |
| `exception` | on `exc_info` | Formatted traceback |

A failed run's finish record carries the same payload `DatheonError.to_dict()`
produces — `code`, `type`, `message`, `hint`, `context`, `exit_code` — so a log
consumer can point at an error-catalogue entry without parsing prose.

### 3.3 Guarantees, and what proves them

| Guarantee | Pinned by |
| --- | --- |
| Without `--log-level`/`--log-file`, nothing is written anywhere | `test_logging.py` (default run writes nothing, per CLI) |
| Console output goes to `stderr`; `stdout` stays machine-readable | `test_logging.py` |
| The file handler rotates at 1 MiB × 3 generations | `test_logging.py` |
| An unwritable log path degrades to console-only and never aborts a run | `test_logging.py` (`file_error`, `file_enabled`) |
| Reconfiguring replaces handlers instead of duplicating lines | `test_logging.py` |
| `run_cli` closes the run on every path — success, error, `Ctrl-C`, closed pipe | `test_structured_logging.py` |
| Every record of one invocation shares one `run_id`; `seq` is contiguous across handlers | `test_structured_logging.py` |
| `--json-logs` emits only parseable JSON in **all five** projects | `test_structured_logging.py` (subprocess, per CLI) |
| A persisted document carries `schema_version`; a mismatched read is refused | `test_persistence.py` |
| Every JSON log line the tools write can be reduced to a run summary | `test_observability.py` |

### 3.4 Run lifecycle

```text
run_start   run_outcome=running    duration_ms=0.7
…           …                      …            (one record per log point)
run_finish  run_outcome=success    duration_ms=41.2
```

`run_cli` decides the terminal outcome: `success` when the tool returns `0`,
`error` when it raises a `DatheonError` or returns non-zero, `interrupted` on
`Ctrl-C` or a broken pipe, and `incomplete` for a session closed without ever
finishing. Day 14's reader treats a run with no terminal record as `incomplete`
too, so a killed process shows up as one instead of silently vanishing.

---

## 4. The report module

`observability.py` reads a directory of JSON logs and reduces them to the figures
an operator wants: runs per project, outcomes, p50/p95/worst durations, error
codes by frequency, malformed-line counts, and the live cache counters. It is
read-only — it never writes, rotates or deletes a log.

```bash
# the tools' default log directory
python observability.py

# any directory of *.log files, including rotated siblings
python observability.py --log-dir ~/.datheon/logs

# machine-readable, for a dashboard or a cron report
python observability.py --log-dir ~/.datheon/logs --json --no-caches
```

Sample output from five real runs of the five tools, plus one deliberate failure
(`organizer` pointed at a missing directory, `DT-1001`):

```text
observability report — 5 file(s), 19 record(s), 6 run(s)

project         runs    ok   err  intr   inc    p50_ms    p95_ms    max_ms
--------------------------------------------------------------------------
organizer          2     1     1     0     0      1.33      4.27      4.27
assistant          1     1     0     0     0      9.43      9.43      9.43
hangman            1     1     0     0     0     20.74     20.74     20.74
inventory          1     1     0     0     0      2.88      2.88      2.88
portfolio          1     1     0     0     0     20.23     20.23     20.23

outcomes: success=5  error=1  interrupted=0  incomplete=0  (success rate 83.3%)
records without a run_id: 0

error codes
  DT-1001     1

cache stats
  cache                 requests    hits  misses  hit_rate   size
  ---------------------------------------------------------------
  constituents                 0       0       0      0.0%      0
  intent_patterns              0       0       0      0.0%      0
  word_list                    0       0       0      0.0%      0

malformed lines: 0
```

Absolute milliseconds are container-specific and move on another machine; the
run counts, outcomes and error codes do not.

### 4.1 Design decisions

1. **Nearest-rank percentiles, never interpolated.** A duration in this report is
   a measurement, so p95 must be a duration some run actually took. Reporting an
   interpolated 41.3 ms that no invocation ever cost would be inventing data.
2. **A malformed line is counted, never fatal.** The file is written by a process
   that may be killed mid-write; one truncated tail must not hide the other 900
   records. Each rejection keeps its line number and an 80-character preview.
3. **`incomplete` is a first-class outcome.** A run that opened and never finished
   is exactly the case an operator is looking for, so it is reported rather than
   dropped or counted as a success.
4. **Rotated files are included.** `*.log*` picks up `organizer.log.1`, because a
   rotation may have moved the run being investigated.
5. **Cache counters are labelled as live, because they are.** They come from
   `caching.cache_stats()` *of the reporting process*. Run the module from a
   script that also loaded the datasets and the counters are populated; run it
   from a bare CLI and they read zero. Both are honest, and §6 shows the
   populated case.
6. **Empty input is a data error, not an empty report.** A silent "0 runs" would
   be indistinguishable from a quiet day; a missing path exits `1` with a hint
   telling you to produce a log first.

### 4.2 Exit codes

| Code | When |
| ---: | --- |
| `0` | The report was produced |
| `1` | A path does not exist, or no `*.log*` file was found |
| `2` | Usage error — bad flag, non-positive `--top`, or `paths` given alongside `--log-dir` |

---

## 5. What Week 3 inherits

* **A quality floor to raise.** 745 tests, all green, ~34 s. Day 15 targets 200+
  assertions on top of the existing suite; Day 20 wires `--cov-fail-under=90`.
* **Baselines to regress against.** The Day 12 timings and the Day 13 cache
  ratios above are the numbers a benchmark harness can pin.
* **A machine-readable log format** whose schema is versioned, so a coverage or
  benchmark run can be measured the same way a production run is.
* **A known gap, stated plainly.** Nothing here is persisted across processes:
  caches are in-process by design (Day 13 §7), and the report re-reads logs each
  time. Neither is worth a cross-process protocol at this size.

---

## 6. Reproducing every figure

```bash
# Suite and lint gates
python -m compileall . && flake8 . && black --check . && pytest

# Day 11 — async throughput
python projects/03_file_organizer_automation/benchmark.py --files 400 --latency-ms 3
python projects/03_file_organizer_automation/benchmark.py --files 500 --latency-ms 0

# Day 12 — hot-path profiles and timings
python profiling.py organizer --files 600 --top 10
python profiling.py duplicates --files 600 --top 10

# Day 13 — cold vs warm loads
python -c "
from profiling import time_callable
from words import load_words_from_file
W = 'projects/01_hangman_game/data/words.txt'
print(time_callable(load_words_from_file, W, repeat=7, warmup=0, use_cache=False).render())
print(time_callable(load_words_from_file, W, repeat=200, warmup=2).render())
"

# Day 14 — the report, over real logs
DATHEON_LOG_DIR=/tmp/datheon-logs \
  python projects/05_inventory_oop/main.py --log-level INFO --json-logs
python observability.py --log-dir /tmp/datheon-logs
```

Cache evidence in the report needs a process that has actually loaded data:

```bash
python - <<'PY'
from words import load_words_from_file
from portfolio import load_symbols_from_csv
import observability

W = "projects/01_hangman_game/data/words.txt"
C = "projects/02_portfolio_tracker/data/constituents.csv"
for _ in range(20):
    load_words_from_file(W)
    load_symbols_from_csv(C)

print(observability.build_report(["/tmp/datheon-logs"]).render(top=3))
PY
```

which reports `word_list` and `constituents` at 20 requests each, 19 hits and a
95.0% hit rate — the same hit rate the Day 13 counters predicted, now visible
next to the run figures instead of in a separate REPL session.
