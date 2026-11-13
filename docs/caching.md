# Dataset caching & memoisation — Day 13

**Date:** 2026-11-01 · **Milestone:** Day 13 — *Stop reloading static data*
**Module:** [`caching.py`](https://github.com/krishsharma-code/Datheon_Python_Internship/blob/main/caching.py) (standard library only)
**Tests:** [`tests/test_caching.py`](https://github.com/krishsharma-code/Datheon_Python_Internship/blob/main/tests/test_caching.py) (54 tests)

Day 12 profiled every loader and found nothing worth rewriting inside them: their
cost is one file read, one decode, one pass of row validation. The only way to
make the *second* load cheaper than the first is to not do it. This document
describes the cache that makes that safe, the invalidation rule that keeps it
honest, and the measured drop in load time.

The rule for the day was **no behaviour change**: every loader returns exactly
what it returned yesterday, raises exactly the same errors, and records exactly
the same report. What changed is how often the file is read.

---

## 1. What Day 12 left on the table

Day 12 measured the three static datasets on the same container:

| Dataset | Size | Day 12 baseline |
| --- | --- | --- |
| `projects/01_hangman_game/data/words.txt` | 75 KB, ~9.9k lines | 8.7 ms per load |
| `projects/02_portfolio_tracker/data/constituents.csv` | 53 KB, 500 rows | 3.3 ms per load |
| `projects/04_terminal_assistant/intents.json` | 3.4 KB, 11 intents | 8.3 ms per load |

A single Hangman round picks one word and never re-reads the list, but the CLI
reads it on every invocation, `--validate` reads it again, and the test suite
reads it hundreds of times. The constituents table is read by every portfolio
command that resolves a ticker to a company name. Both are, by definition,
*static data that does not change while a process runs* — the textbook case for a
cache, provided the cache can tell when the file *does* change.

---

## 2. Design

Four small pieces, no dependencies:

| Piece | Responsibility |
| --- | --- |
| `FileSignature` | The freshness token: `(path, mtime_ns, size, inode)` from one `os.stat`. Two signatures that differ mean the file behind an entry changed. |
| `DatasetCache` | A bounded, thread-safe, optionally time-limited `OrderedDict` of key → `(value, signature, stored_at)`. LRU on overflow. |
| `CacheStats` | Hits, misses, stores, invalidations, evictions, expirations, stale drops, size — and a `hit_rate` property, so "the cache works" is a number. |
| Registry helpers | `cache_stats()`, `clear_all_caches()`, `invalidate_path()`, `registered_caches()` across every live cache. |

Three decisions carry the design:

**Freshness is a signature, not a timer.** A TTL-only cache either expires data
that did not change (wasting the work) or serves data that did (returning stale
answers). Capturing `(mtime_ns, size, inode)` and re-stat-ing on lookup answers
the real question — *is this the same file I read?* — for one syscall. The inode
is included because a file replaced by `os.replace` can land with the same size
and, on a filesystem with coarse timestamps, the same mtime.

**Keys are caller-chosen, and include every argument that changes the result.**
The word list is keyed by `(path, min_length, max_length, strict)`; the
constituents table by `(path, strict)`. A `min_length=7` load and a `min_length=4`
load are different datasets and get different entries.

**Values are cloned in and out.** A caller that does `words.append("mine")` or
`symbols["FAKE"] = "Fake Corp."` mutates its own copy, never the cached one.
The clone is shallow — the leaves are `str`/`float`, which are immutable — so it
costs a list or dict copy, not a deep walk.

Everything is guarded by a single `threading.RLock` per cache. Loaders run
*outside* the lock, so a slow parse does not block readers of other keys; two
threads racing on the same cold key may both parse, and the last write wins.

---

## 3. Invalidation

Explicit invalidation is part of the API, not an afterthought. Four levels:

| Call | Scope | Typical use |
| --- | --- | --- |
| `cache.invalidate(key)` | one entry | "recompute this one thing" |
| `cache.invalidate_path(path)` | every entry read from one file | a test that rewrites `words.txt` in place |
| `cache.clear()` | every entry in one cache | start a fresh measurement |
| `clear_all_caches()` / `invalidate_path(path)` | every live cache | process-wide reset |

The *implicit* invalidation is the interesting one: an entry whose stored
signature no longer matches the file is dropped on the next lookup and counted
under `stats.stale`. Nothing has to remember to invalidate after an edit —
editing the dataset invalidates itself.

`clear()` and `invalidate*` keep the counters, so a post-mortem can still see how
the cache performed before it was emptied. `reset_stats()` zeroes the counters
without touching the values.

---

## 4. Where the cache is used

| Cache | Wired into | Key | Bypassed when |
| --- | --- | --- | --- |
| `WORD_LIST_CACHE` | `words.load_words_from_file` | `(path, min_length, max_length, strict)` | `report=` is passed, or `use_cache=False` |
| `CONSTITUENTS_CACHE` | `portfolio.load_symbols_from_csv` | `(path, strict)` | `report=` is passed, or `use_cache=False` |
| `PATTERN_CACHE` | `chatbot.compile_pattern` (every `Intent` regex) | `(pattern, flags)` | `clear_all_caches()` |

Both loaders gained one keyword-only parameter, `use_cache: bool = True`, and one
documented rule: **passing a `LoadReport` always re-reads the file**. A report
describes one physical read — its encoding, its line endings, its row counts —
so serving it from cache would hand the caller metadata about a read that did not
happen. The bypass still refreshes the cache entry, so the next plain call is a
hit.

The chatbot's three `re.compile` sites (`Intent.from_dict`, the FAQ keyword
builder, the greeting builder) now route through `compile_pattern`, which
compiles each `(pattern, flags)` pair once. :mod:`re` already memoises internally,
but that cache is invisible, uncountable and unclearable; this one is all three.

---

## 5. Measurements

Same container, same fixtures, Python 3.12.13, `profiling.time_callable` with the
**best** sample quoted (cold: 7 timed samples, no warm-up; warm: 200 timed
samples after 2 warm-ups):

| Dataset | Cold (parse) | Warm (cache hit) | Speedup | Saved per load |
| --- | --- | --- | --- | --- |
| word list (75 KB) | 7.91 ms | 0.28 ms | **28.4×** | 7.63 ms |
| constituents (500 rows) | 4.24 ms | 0.25 ms | **17.0×** | 3.99 ms |
| intent regexes (11 patterns) | 0.77 ms | 0.68 ms | 1.1× | 0.08 ms |

Repeated 200 times each, as a long-running process (or a test session) would:

| Scenario | Total |
| --- | --- |
| 200 × word list + 200 × constituents, uncached | 2429.1 ms |
| same, cached | 105.7 ms |

The residual ~0.25 ms of a warm hit is almost entirely the single `os.stat` that
proves the file is unchanged — the correctness cost of the signature scheme, and
the number to watch if a dataset ever needs to be re-validated more cheaply.

**The intent-regex row is honestly a dud on wall-clock time.** The dataset holds
11 patterns over 3.4 KB; compiling them is already sub-millisecond, and
:mod:`re`'s own 512-entry cache absorbs most repeats. The win there is not speed
but *observability and control*: `cache_stats()` shows 2288 pattern-cache hits
against 11 misses for a full `intents.json` parse, and `clear_all_caches()` can
drop the compiled patterns on demand. Reporting it as a 1.1× speedup rather than
quietly omitting it is the point.

### Reproducing the measurements

```python
import caching
from profiling import time_callable
from words import load_words_from_file
from portfolio import load_symbols_from_csv

WORDS = "projects/01_hangman_game/data/words.txt"
CONSTITUENTS = "projects/02_portfolio_tracker/data/constituents.csv"

cold = time_callable(load_words_from_file, WORDS, repeat=7, warmup=0, use_cache=False)
warm = time_callable(load_words_from_file, WORDS, repeat=200, warmup=2)
print(f"words: {cold.best_ms:.2f} ms -> {warm.best_ms:.2f} ms")

caching.clear_all_caches()
cold = time_callable(load_symbols_from_csv, CONSTITUENTS, repeat=7, warmup=0, use_cache=False)
warm = time_callable(load_symbols_from_csv, CONSTITUENTS, repeat=200, warmup=2)
print(f"constituents: {cold.best_ms:.2f} ms -> {warm.best_ms:.2f} ms")
print(caching.cache_stats())
```

Absolute numbers move on another machine; the ratios and the `hit_rate` figures
do not.

---

## 6. What the tests pin

`tests/test_caching.py` (54 tests) is split the same way the module is. The
integration half asserts **loader call counts and cache counters**, never
wall-clock time, so it cannot flake on a busy machine.

* **Cache mechanics** — miss then hit; `lookup` distinguishing "cached `None`"
  from "not cached"; `load` running its loader exactly once; values copied in and
  out (a mutated list or dict never leaks into the next caller); LRU eviction
  order (`a` survives because it was re-read, `b` is evicted); TTL expiry; stale
  signatures dropped and counted; `invalidate` reporting whether it removed
  anything; `invalidate_path` touching only its own file; `clear` keeping
  counters; `reset_stats` keeping values; `len`/`keys`/`repr`; and every invalid
  constructor argument (blank name, `maxsize=0`, `ttl=0`, negative `ttl`) raising
  `ValidationError`.
* **Thread safety** — eight threads doing 50 get/put rounds each leave
  `requests == hits + misses` and `size <= maxsize`, with no exception escaping.
* **Regex cache** — the same pattern returns the same object; flags are part of
  the key; an invalid pattern still raises `re.error`; clearing forces a
  recompile; two `Intent`s sharing a pattern share the compiled object.
* **Word list** — loaded once for two calls (1 miss, 1 hit, 1 store); the returned
  list is a copy; an edit is picked up with `stale == 1`; explicit invalidation
  forces a re-read; a `report=` bypass is a miss with a populated report;
  `use_cache=False` never hits but refreshes the entry; `min_length` is part of
  the key; a `strict` failure is *not* cached and the fixed file then loads; a
  missing file still raises `FileNotFoundError`.
* **Constituents** — same set, plus a check against the real 500-row dataset
  (more than 400 symbols, `AAPL` present, second call a hit).

---

## 7. Known limits

* **In-process only.** Nothing is written to disk, so two CLI invocations share
  nothing. That is deliberate: a persisted cache needs a cross-process
  invalidation protocol, which is a much larger change than Day 13 asks for.
* **Signature granularity is the filesystem's.** A file whose mtime, size and
  inode all survive a content change is not detected. `invalidate_path()` is the
  escape hatch, and no filesystem in normal use hits this.
* **Shallow cloning.** Values are copied one level deep. The loaders return
  `List[str]` and `Dict[str, str]`, so this is complete; a future loader returning
  nested structures would need `copy.deepcopy`.
* **Cold-key races.** Two threads arriving on the same cold key both run the
  loader. The result is correct, the work is duplicated. A per-key lock would fix
  it and add contention for a case that does not occur in these tools.
* **`:mod:`re`'s internal cache still exists.** `compile_pattern` can return an
  object that `re` had already compiled. What it adds is the counters and the
  ability to drop the cache, not object uniqueness.

---

## 8. Next up

Day 14 consolidates Week 2 into a performance and observability report, reusing
`profiling.py` for the numbers and `cache_stats()` for the cache evidence, so the
figures in the report are reproducible rather than quoted from memory.
