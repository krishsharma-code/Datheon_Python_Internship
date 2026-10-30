# Async I/O contract — the organizer's bounded-concurrency engine

Day 11 ported the file organizer's moves from a `ThreadPoolExecutor` to an
`asyncio` event loop. This document is the contract: what the engine
guarantees, how the concurrency bound is enforced, what the blocking API still
promises, and the measured numbers behind the default.

Implementation: [`async_organizer.py`](../projects/03_file_organizer_automation/async_organizer.py).
The public surface is `AsyncFileOrganizer`, `run_coroutine_sync`,
`DEFAULT_CONCURRENCY` and `MAX_CONCURRENCY`; the synchronous
`organizer.FileOrganizer` is unchanged and now delegates to it.

---

## 1. Why asyncio, and why the bound matters

A move is almost entirely kernel time — `mkdir`, then `rename(2)` — so the
Python interpreter is idle while the disk works. Overlapping those waits is the
only way to spend less wall-clock time on a directory of thousands of files.

Both a thread pool and an event loop can overlap them, because a blocking
filesystem call releases the GIL. The difference is *control over the ceiling*:

| | `ThreadPoolExecutor` (Day 3–10) | asyncio engine (Day 11) |
| --- | --- | --- |
| Ceiling | `max_workers` threads, one task each | `concurrency` worker tasks pulling from a queue |
| Failure to bound | A larger pool silently means more open syscalls in flight | The queue is only drained by `concurrency` tasks; the bound holds by construction |
| Per-file timeout / cancellation | Not expressible | `asyncio.wait_for` around any await point |
| Ordering of results | Completion order, re-sorted afterwards | Source order, because records are keyed and re-ordered once |

The bound is not decoration. Every in-flight move holds a directory handle and
a kernel I/O slot; a fan-out of a few hundred turns a latency win into disk
queueing and, on spinning media, seek thrash. `MAX_CONCURRENCY` is 64 for that
reason, and `concurrency` is validated at construction — an invalid value raises
`ValidationError` (catalogue `DT-1001`) rather than being silently clamped.

## 2. How the bound is enforced

```python
queue = asyncio.Queue()
for source in files:
    queue.put_nowait(source)

workers = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(files)))]
await asyncio.gather(*workers)
```

Each worker loops: take one path, `await` its processing, take the next. Because
a worker is a single coroutine, it can only ever have one file in flight, so the
number of simultaneous moves is exactly the number of workers. There is no
moment where a task is created but not yet gated — the pattern that lets a
semaphore leak under load, and the reason a plain `asyncio.gather` over all
files is not used.

`AsyncFileOrganizer.peak_in_flight` records the high-water mark of the last run,
so the bound is observable rather than assumed:

```python
engine = AsyncFileOrganizer(concurrency=3)
asyncio.run(engine.organize("~/Downloads"))
assert engine.peak_in_flight <= 3
```

## 3. Collision safety under concurrency

`organizer.unique_destination` answers "what free name can this file take?" for
one caller at a time. Two workers classifying into the same category can both
see `Documents/report.pdf` as free before either has moved anything, and the
second move then overwrites the first — the exact data loss the tool exists to
prevent.

`_DestinationReserver` closes that window. The "look, then claim" step runs under
an `asyncio.Lock`, and claimed paths are remembered in a set, so a path that is
reserved but not yet created is never handed out twice:

```
reserve(a.pdf, Documents) -> Documents/a.pdf     (claimed)
reserve(a.pdf, Documents) -> Documents/a (1).pdf (claimed, disk still empty)
```

The search stays bounded by `max_attempts` (default 1000), so a pathological
directory raises `FileExistsError` — reported as an error record — instead of
spinning.

## 4. The blocking API is unchanged

`FileOrganizer.organize(...)` keeps its signature, its exceptions and its
`OrganizeReport` return type. Internally it builds the async engine and drives
it to completion:

```python
FileOrganizer(max_workers=4).organize(path)                 # blocking, as before
asyncio.run(AsyncFileOrganizer(concurrency=4).organize(path))  # native async
```

`max_workers` keeps its name for compatibility and now means the concurrency
bound. `run_coroutine_sync` handles the awkward case where the caller is already
inside an event loop (a notebook, an async test, a service embedding the
organizer): `asyncio.run` would raise there, so the coroutine is driven on a
private loop in a short-lived worker thread. Both paths return the same report.

Exceptions propagate unchanged through the bridge — `NotADirectoryError` for a
missing path, `PermissionError` for an unreadable directory — so existing
callers and their `except` clauses behave identically.

## 5. Failure isolation

Per-file failures never abort a run. A `PermissionError`, `FileExistsError` or
`OSError` becomes an `error` record with the message in `detail`; anything else
unexpected is caught by the worker and recorded as `unexpected: …`. One locked
file in a directory of a thousand leaves 999 moved and one actionable line.

## 6. Measured numbers

`benchmark.py` measures the engine across concurrency levels. Real moves on a
warm page cache are too fast to show scheduling, so the default mode injects a
per-file latency to make the shape of the curve visible; `--latency-ms 0`
measures the real thing.

```
$ python projects/03_file_organizer_automation/benchmark.py --files 400 --latency-ms 3

 concurrency   files  elapsed_ms  files/sec  speedup
----------------------------------------------------
           1     400      2311.5      173.0    1.00x
           2     400      1311.9      304.9    1.76x
           4     400      1128.6      354.4    2.05x
           8     400       859.0      465.7    2.69x
          16     400       584.5      684.3    3.95x
```

```
$ python projects/03_file_organizer_automation/benchmark.py --files 500 --latency-ms 0

 concurrency   files  elapsed_ms  files/sec  speedup
----------------------------------------------------
           1     500      1317.5      379.5    1.00x
           4     500      1345.9      371.5    0.98x
           8     500      1164.8      429.3    1.13x
          16     500      1130.5      442.3    1.17x
```

Reading the two tables together is the point:

- With 3 ms of simulated latency, throughput scales sub-linearly — 8 workers give
  **2.69×**, 16 give **3.95×**, not 8× and 16×. The ceiling is the executor's
  thread pool serving `asyncio.to_thread` plus the disk, not the event loop.
- With real local moves, the same work is already ~380 files/s at
  `concurrency=1`; going to 16 buys **1.17×**. The bottleneck is the filesystem,
  and no amount of scheduling removes it.

Hence the defaults: `DEFAULT_CONCURRENCY = 8` captures most of the available win
on a latency-bound disk (2.69× here) without piling up handles for the
throughput-bound case, where the curve is flat anyway. `MAX_CONCURRENCY = 64`
leaves room for a network or slow-spinning volume while refusing values that
only add queueing.

## 7. Observability

The engine logs one DEBUG record per file through `structured_extra`, carrying
`source`, `category` and `duration_ms`, and one per run carrying `concurrency`,
`peak_in_flight` and `duration_ms`. Under `--json-logs` those become structured
fields in the same JSON stream Day 10 introduced, correlated by `run_id`, so the
per-file timings a profile would want are already in the log.

`OrganizeReport` gained `duration_ms` and `engine`, and both appear in the
activity-log `summary`, so a JSON log records how long the run took and which
engine produced it.

## 8. What the tests pin

`tests/test_async_organizer.py` covers: constructor validation (out-of-range,
non-integer, `bool`); parity between the async and sync engines on an identical
tree; source-order determinism; dry-run touching nothing; duplicate detection;
collision resolution; the concurrency bound (`peak_in_flight <= limit`, and
`== 1` for a serial run); the reserver never issuing a path twice and giving up
after `max_attempts`; per-file `PermissionError`/`OSError`/`RuntimeError`
isolation with the rest of the run completing; the blocking facade inside a
running loop; exception propagation through `run_coroutine_sync`; the CLI's
`--concurrency` flag, its `--strict` bound and the deprecated `--workers` alias;
and the benchmark harness itself.
