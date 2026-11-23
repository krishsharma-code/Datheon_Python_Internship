"""Timings for the hot paths Day 12 profiled, plus the calibration they ride on.

Day 12 answered "where does the time go" with a profile of four paths: the word
list, the S&P 500 constituents table, the dataset cache and the atomic write.
Those are the four this module times, because a gate is only worth having on
code a user actually waits for. Two additions make the set honest:

* :func:`test_bench_calibration` times a pure-arithmetic loop — no file, no
  clock, no allocation pattern any of the other benchmarks touch. It is the
  machine's speedometer: ``tools/benchmark_budget.py`` divides this run's
  calibration by the budgeted one and scales every ceiling by that factor, so a
  slower runner gets a slower budget instead of a false regression.
* every other benchmark times a *complete* call of the real public API. Nothing
  here re-implements the code it measures, because a benchmark that drifts from
  the implementation measures a program nobody runs.

Run them with::

    pytest benchmarks/ -q
    pytest benchmarks/ --benchmark-json=benchmark-results.json
    python tools/benchmark_budget.py --results benchmark-results.json

The timings are not assertions: a benchmark that also asserted correctness
would turn a slow machine into a red suite. Each one does the cheap shape check
its fixture needs (a non-empty result), and the correctness itself lives in
``tests/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from pytest_benchmark.fixture import BenchmarkFixture

import caching
import error_catalogue
import persistence
import validators
from portfolio import load_symbols_from_csv
from words import load_words_from_file

#: Tickers validated per iteration. One call to ``validate_symbol`` costs a
#: microsecond or two, which is below the resolution worth quoting; a batch of
#: 200 puts the measurement well above the noise floor.
VALIDATION_BATCH: int = 200

#: Cache lookups per iteration, for the same reason.
CACHE_LOOKUPS: int = 1000

#: Arithmetic iterations in the calibration loop.
CALIBRATION_STEPS: int = 20_000

#: The symbols the validator batch runs over — real shapes, upper- and lower-case,
#: because ``validate_symbol`` upper-cases and strips before it checks.
SYMBOLS: List[str] = [
    f"{lead}{tail}"
    for lead in "ABCDEFGHIJ"
    for tail in ("", "X", "YZ", "QRS", "TUVW")
    for _ in range(4)
]


def _calibration_work(steps: int = CALIBRATION_STEPS) -> int:
    """Run a fixed amount of integer arithmetic.

    The work is deliberately dull: a sum of squares with no I/O, no allocation
    beyond the integers Python caches, and no dependence on the clock. Its cost
    tracks the interpreter and the processor, which is exactly what the gate
    needs to normalise a budget recorded on another machine.

    Args:
        steps: How many integers to accumulate.

    Returns:
        The sum of the squares of ``0 .. steps - 1``.
    """
    total = 0
    for value in range(steps):
        total += value * value
    return total


def test_bench_calibration(benchmark: BenchmarkFixture) -> None:
    """Time the machine, so the gate can scale every other ceiling.

    Args:
        benchmark: The pytest-benchmark fixture.
    """
    result = benchmark(_calibration_work)
    assert result > 0


def test_bench_word_list_cold(benchmark: BenchmarkFixture, words_path: Path) -> None:
    """Time a cold read and sanitise of the 9,894-line word list.

    ``use_cache=False`` is what a first run of the hangman game pays: read the
    file, strip the BOM, split the lines, lower-case, filter and de-duplicate.

    Args:
        benchmark: The pytest-benchmark fixture.
        words_path: The bundled word list.
    """
    words = benchmark(load_words_from_file, words_path, use_cache=False)
    assert words


def test_bench_word_list_cached(benchmark: BenchmarkFixture, words_path: Path) -> None:
    """Time the warm path: a cache hit and the defensive copy it returns.

    The warm path is what every round after the first pays, and it is the one
    that must stay cheap — a hit that costs as much as a parse is a cache that
    is only a memory leak with extra steps.

    Args:
        benchmark: The pytest-benchmark fixture.
        words_path: The bundled word list.
    """
    load_words_from_file(words_path)  # warm the entry before timing the hit
    words = benchmark(load_words_from_file, words_path)
    assert words


def test_bench_constituents_parse(benchmark: BenchmarkFixture, constituents_path: Path) -> None:
    """Time parsing the 500-row S&P 500 constituents table.

    Args:
        benchmark: The pytest-benchmark fixture.
        constituents_path: The bundled constituents CSV.
    """
    symbols = benchmark(load_symbols_from_csv, constituents_path, use_cache=False)
    assert symbols


def test_bench_validator_batch(benchmark: BenchmarkFixture) -> None:
    """Time ``validate_symbol`` over a batch of real-shaped tickers.

    Args:
        benchmark: The pytest-benchmark fixture.
    """
    batch = SYMBOLS[:VALIDATION_BATCH]
    validated = benchmark(lambda: [validators.validate_symbol(item) for item in batch])
    assert len(validated) == len(batch)


def test_bench_cache_lookup(benchmark: BenchmarkFixture) -> None:
    """Time a thousand hits against a warm :class:`caching.DatasetCache`.

    Args:
        benchmark: The pytest-benchmark fixture.
    """
    cache: caching.DatasetCache = caching.DatasetCache("bench", maxsize=CACHE_LOOKUPS)
    keys = [f"key-{index}" for index in range(CACHE_LOOKUPS)]
    for key in keys:
        cache.put(key, [key])

    def lookups() -> int:
        found = 0
        for key in keys:
            found += 1 if cache.get(key) is not None else 0
        return found

    hits = benchmark(lookups)
    assert hits == CACHE_LOOKUPS


def test_bench_atomic_write_json(benchmark: BenchmarkFixture, tmp_path: Path) -> None:
    """Time an atomic JSON write: temp file, fsync, ``os.replace``.

    This is the cost the portfolio tracker and the organizer pay on every save,
    and the one Day 9 accepted in exchange for never leaving a half-written file
    behind. Timing it is how that trade stays a decision rather than a habit.

    Args:
        benchmark: The pytest-benchmark fixture.
        tmp_path: Pytest's temporary directory.
    """
    target = tmp_path / "state.json"
    payload: Dict[str, object] = {
        "schema": 1,
        "holdings": [{"symbol": "INFY", "quantity": 10, "price": 1500.0}] * 20,
    }

    def write() -> int:
        persistence.atomic_write_json(target, payload)
        return len(json.loads(target.read_text(encoding="utf-8"))["holdings"])

    written = benchmark(write)
    assert written == 20


def test_bench_catalogue_lookup(benchmark: BenchmarkFixture) -> None:
    """Time one lookup per catalogue code — the assistant's error path.

    Args:
        benchmark: The pytest-benchmark fixture.
    """
    codes = error_catalogue.catalogue_codes()

    def lookups() -> int:
        return sum(1 for code in codes if error_catalogue.describe_code(code).code)

    found = benchmark(lookups)
    assert found == len(codes)
