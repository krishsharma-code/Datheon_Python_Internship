# Performance profiling — Day 12

**Date:** 2026-10-31 · **Milestone:** Day 12 — *Find and fix the hot spots*
**Harness:** [`profiling.py`](../profiling.py) (stdlib only: `cProfile` + `pstats` + `time.perf_counter`)

This document records what the profiler found, what was changed in response, and
the before/after numbers. Every figure below is reproducible with the commands in
[Reproducing the measurements](#reproducing-the-measurements).

The rule for the day was **no behaviour change**: the optimisations only remove
work, they never change a result, an error, a log line or an exit code. The
642-test suite (77 of them new) is the evidence, and
`tests/test_profiling.py` pins the two tricky rules that were re-implemented —
the extension rule behind `category_for` and the collision-naming rule behind the
organizer's destination reserver — against `pathlib` itself.

---

## 1. Method

Two measurements per hot path, because they answer different questions:

| Tool | Question it answers | Reported as |
| --- | --- | --- |
| `profiling.profile_callable` | *Which function is burning the time?* | per-function `tottime` / `cumtime` in milliseconds, ranked by `tottime` |
| `profiling.time_callable` | *How fast is it overall, repeatably?* | best, median, mean and σ of N samples in milliseconds |

Wall-clock figures quote the **best** sample: on a shared machine the slowest
samples are the ones that lost the CPU, so the minimum is the most stable
estimator. Profiles are taken with one untimed warm-up already done, so import
cost and cold page cache do not land in the first sample.

Environment: Debian 12 container, x86_64, Python 3.12.13, fixtures on the
container's overlay filesystem, 09:00 IST scheduled run. Re-running on another
machine will move the absolute numbers; the ratios hold, and the call counts
(`posix.stat`, `_io.open`) are hardware-independent.

Fixtures: a synthetic directory of *n* files spread across eight extensions,
each with a **distinct file size** so the duplicate detector's size pre-filter is
exercised the way a real directory exercises it; plus the repository's own
75 KB `words.txt`, a 500-row price CSV, and `intents.json`.

---

## 2. What the first profile showed

`FileOrganizer.organize` (dry run, 600 files) — 467.6 ms:

```
   tottime     cumtime     calls  function
  185.145ms    187.473ms      1385  ~:0(<built-in method posix.stat>)
   16.510ms     32.302ms      1802  pathlib.py:387(_parse_path)
   11.639ms    245.798ms       600  async_organizer.py:222(_process)
   10.809ms     17.339ms      3602  pathlib.py:358(__init__)
    9.262ms     26.601ms      3602  pathlib.py:1157(__init__)
```

`find_duplicates` (600 files) — 514.5 ms:

```
   tottime     cumtime     calls  function
  194.617ms    222.836ms      1321  ~:0(<built-in method posix.stat>)
  143.110ms    144.610ms       660  ~:0(<built-in method _io.open>)
   65.935ms     65.935ms      1320  ~:0(<method 'read' of '_io.BufferedReader' objects>)
   25.773ms     25.773ms       660  ~:0(<method '__exit__' of '_io._IOBase' objects>)
   10.489ms    413.310ms       660  organizer.py:117(sha256_of_file)
```

Three costs account for almost everything, and none of them is the tool's own
arithmetic:

1. **A `stat` per file just to answer "is this a file?"** — 1,385 `stat` calls
   for 600 files. `pathlib.Path.iterdir()` hands back paths, and
   `Path.is_file()` then has to ask the kernel about each one.
2. **`pathlib` object churn in the per-file loop** — 3,602 `Path.__init__`
   calls and 1,802 `_parse_path` calls for 600 files, from
   `Path(path).suffix`, `root / category`, and the four `Path` objects the
   destination reserver built for every single file.
3. **Reading every byte of every file to detect duplicates** — 660 `open` +
   `read` pairs and 143 ms in `_io.open` alone, even though only 120 of those
   files could possibly have a twin.

---

## 3. The three changes

### 3.1 `iter_files` lists with `os.scandir` (`edge_cases.py`)

`os.scandir` yields each entry's type next to its name, so the
"is this a regular file?" test needs no `stat` at all on filesystems that record
the type in the directory entry. The public contract is unchanged: same
`List[Path]`, same case-insensitive sort, same `DirectoryExpectedError` for a
non-directory, same `AccessDeniedError` for an unreadable one. A per-entry
`OSError` is still treated as "not a file", matching `Path.is_file()`.

This is the single widest-reaching change: `iter_files` is the entry point of
both the organizer and the duplicate detector.

`stat` calls in the dry-run profile: **1,385 → 785**.

### 3.2 `category_for` and the destination reserver stop building `Path` objects (`organizer.py`, `async_organizer.py`)

`category_for` ran `Path(path).suffix.lower()` — ~10 µs per call for what is
really a string operation. It now uses a small helper, `_suffix_of`, built on
`os.path.basename` + `str.rpartition`. The helper reproduces `pathlib`'s
*quirks*, not just its happy path: `"trailing."` has **no** suffix, `".bashrc"`
is all stem, `"..x"` has the suffix `".x"`. `tests/test_profiling.py` checks it
against `Path(...).suffix` for a table of awkward names.

In the same spirit, `_DestinationReserver` now builds and tests candidates as
strings (`os.path.join` + `os.path.exists`) and wraps only the winner in a
`Path`; and `AsyncFileOrganizer` caches the destination folder per category, so
`root / category` is joined once per category rather than once per file.
`_split_name` reproduces `pathlib`'s stem/suffix split, so a collision still
produces `"report (1).pdf"` and never `"report (1).pdf."` — a test asserts the
reserver and `unique_destination` agree on the name for awkward inputs.

### 3.3 `find_duplicates` checks size before it hashes (`organizer.py`)

Two files can only be identical if they are the same length. The detector now
groups files by `st_size` first and only reads the groups with two or more
members. On a directory with no duplicates, a full read of every file collapses
into one `stat` each. The output is byte-for-byte the same dict, including the
`st_size`-0 case (two empty files are still reported as duplicates), and the
`OSError` tolerance is unchanged — a file that vanishes mid-run is skipped, not
raised.

Because `iter_files` has already listed and stat-ed every candidate, the
detector calls the unchecked core `_sha256_digest` rather than
`sha256_of_file`, saving a second `stat` per hashed file. `sha256_of_file` keeps
its `FileNotFoundError` guard and now delegates.

`open` calls in the duplicate profile: **660 → 120**.

---

## 4. Before / after

### 4.1 Wall-clock (best of 5–7 samples, `profiling.time_callable`)

| Hot path | Before | After | Change |
| --- | ---: | ---: | ---: |
| `iter_files` (2,000 files) | 198.9 ms | 14.1 ms | **14.1× faster** |
| `category_for` × 20,000 | 205.8 ms | 45.1 ms | **4.6× faster** |
| `FileOrganizer.organize` dry run (2,000 files) | 645.4 ms | 431.3 ms | **1.50× faster** |
| `find_duplicates` (600 unique + 60 copies) | 522.2 ms | 129.7 ms | **4.0× faster** |
| `find_duplicates` (600 unique sizes, no duplicates) | 398.9 ms | 85.5 ms | **4.7× faster** |

### 4.2 Whole-run profiles (cProfile, 600-file fixture)

| Measurement | Before | After |
| --- | ---: | ---: |
| `organize` dry run, wall | 467.6 ms | **311.8 ms** |
| `posix.stat` calls / time | 1,385 / 185.1 ms | **785 / 89.0 ms** |
| `pathlib._parse_path` calls | 1,802 | 1,808 (unchanged count, same cost) |
| `find_duplicates`, wall | 514.5 ms | **206.8 ms** |
| `_io.open` calls / time | 660 / 143.1 ms | **120 / 39.7 ms** |
| `BufferedReader.read` calls | 1,320 | **240** |

### 4.3 Paths deliberately left alone

Three hot paths were profiled and **not** changed, because the profile showed
nothing worth changing:

| Hot path | Before | After | Note |
| --- | ---: | ---: | --- |
| `load_words_from_file` (75 KB) | 8.7 ms | 10.4 ms | unchanged code; the difference is run-to-run noise (±20% on this fixture) |
| `load_prices_from_csv` (500 rows) | 3.3 ms | 2.7 ms | unchanged code; dominated by the single file read |
| `IntentEngine.respond` × 200 | 8.3 ms | 7.3 ms | unchanged code; regex matching over ~10 intents |

Recording the null results matters as much as the wins: a change to any of these
three would have been churn in the diff without a number behind it. Day 13
(caching) is where the loader paths get addressed, and it will start from these
baselines.

---

## 5. Where the remaining time goes

After the changes, the organizer's dry run is ~85% filesystem metadata: one
`stat` per file for the collision check in `_DestinationReserver._is_free`
(600 files → 600 `exists()` calls). That is the algorithm's floor, not
overhead — a dry run has to report the *real* destination name, which means
asking whether the name is already taken. Removing it would make the report
wrong, so it stays.

The next two costs are Python-side and small: `Path.__str__` (8 calls per file
building `MoveRecord` fields and the debug log record) and the event-loop
scheduling of 600 coroutines. Both are candidates for later days, neither is
worth a behaviour-risking change today.

---

## 6. Reproducing the measurements

```bash
# Profile one hot path (table, or --json for machine-readable output)
python profiling.py organizer --files 600 --top 10
python profiling.py duplicates --files 600 --top 10
python profiling.py wordlist
python profiling.py portfolio --files 500
python profiling.py chatbot

# Rank by cumulative time instead of time-in-function
python profiling.py organizer --files 600 --sort-by cumtime

# Time a hot path repeatedly from Python
python -c "
from profiling import time_callable
from organizer import find_duplicates
print(time_callable(find_duplicates, 'some/dir', repeat=5).render())
"
```

`profiling.py` is a developer tool: no project imports it at run time, so none of
its cost lands on a user's path.
