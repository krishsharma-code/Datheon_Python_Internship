"""Tests for the Day 13 caching layer and its three integrations.

Two properties matter and are tested separately:

* the cache itself — hit/miss accounting, freshness, eviction, expiry, explicit
  invalidation and thread safety, on a cache built by the test; and
* the integrations — the Hangman word list, the S&P 500 constituents table and
  the assistant's compiled intent regexes actually go through those caches, and
  the cache never changes an answer.

The integration tests are deliberately deterministic: they assert *loader call
counts* and cache counters rather than wall-clock time, so they cannot flake on
a busy machine. The wall-clock numbers live in ``docs/caching.md``.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import List

import pytest

import caching
from caching import (
    CONSTITUENTS_CACHE,
    PATTERN_CACHE,
    WORD_LIST_CACHE,
    CacheStats,
    DatasetCache,
    cache_stats,
    clear_all_caches,
    compile_pattern,
    file_signature,
    invalidate_path,
    registered_caches,
)
from chatbot import Intent
from dataset_loader import DatasetFormatError
from errors import ValidationError
from portfolio import load_symbols_from_csv
from words import load_words_from_file


@pytest.fixture(autouse=True)
def clean_caches() -> None:
    """Empty and reset every live cache around each test."""
    clear_all_caches()
    for cache in registered_caches():
        cache.reset_stats()
    yield
    clear_all_caches()
    for cache in registered_caches():
        cache.reset_stats()


@pytest.fixture()
def word_file(tmp_path: Path) -> Path:
    """A small, well-formed word list."""
    path = tmp_path / "words.txt"
    path.write_text("python\nvariable\niterator\ndecorator\n", encoding="utf-8")
    return path


@pytest.fixture()
def constituents_file(tmp_path: Path) -> Path:
    """A small S&P-500-shaped constituents CSV."""
    path = tmp_path / "constituents.csv"
    path.write_text(
        "Symbol,Security\nAAPL,Apple Inc.\nMSFT,Microsoft Corp.\nNVDA,NVIDIA Corp.\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# FileSignature
# ---------------------------------------------------------------------------


def test_capture_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("hello", encoding="utf-8")
    signature = file_signature(path)
    assert signature is not None
    assert signature.path == str(path.resolve())
    assert signature.size == 5
    assert signature.mtime_ns > 0
    assert signature.is_current() is True


def test_capture_missing_file_returns_none(tmp_path: Path) -> None:
    assert file_signature(tmp_path / "absent.txt") is None


def test_signature_invalidated_by_a_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("hello", encoding="utf-8")
    before = file_signature(path)
    path.write_text("hello world", encoding="utf-8")
    after = file_signature(path)
    assert before != after
    assert before is not None and before.is_current() is False


def test_signature_accepts_str_and_path(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("x", encoding="utf-8")
    assert file_signature(str(path)) == file_signature(path)


# ---------------------------------------------------------------------------
# CacheStats
# ---------------------------------------------------------------------------


def test_stats_on_a_fresh_cache() -> None:
    cache = DatasetCache("fresh", maxsize=4)
    stats = cache.stats()
    assert isinstance(stats, CacheStats)
    assert stats.requests == 0
    assert stats.hit_rate == 0.0
    assert stats.size == 0
    assert stats.maxsize == 4
    assert stats.ttl is None


def test_hit_rate_and_as_dict() -> None:
    cache = DatasetCache("rates", maxsize=4)
    cache.put("a", [1])
    cache.get("a")
    cache.get("missing")
    stats = cache.stats()
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.requests == 2
    assert stats.hit_rate == 0.5
    payload = stats.as_dict()
    assert payload["name"] == "rates"
    assert payload["hit_rate"] == 0.5
    assert payload["stores"] == 1
    assert isinstance(payload["ttl"], type(None))


def test_describe_mentions_the_name_and_rate() -> None:
    cache = DatasetCache("described", maxsize=2)
    cache.put("k", "v")
    cache.get("k")
    line = cache.stats().describe()
    assert "described" in line
    assert "100%" in line


# ---------------------------------------------------------------------------
# DatasetCache: store, fetch, copy
# ---------------------------------------------------------------------------


def test_miss_then_hit() -> None:
    cache = DatasetCache("basic", maxsize=4)
    assert cache.get("k") is None
    assert cache.stats().misses == 1
    cache.put("k", ["a", "b"])
    assert cache.get("k") == ["a", "b"]
    assert cache.stats().hits == 1
    assert cache.stats().stores == 1


def test_values_are_copied_in_and_out() -> None:
    cache = DatasetCache("copies", maxsize=4)
    original = ["a"]
    cache.put("k", original)
    original.append("mutated-after-put")
    first = cache.get("k")
    assert first == ["a"]
    first.append("mutated-after-get")
    assert cache.get("k") == ["a"]


def test_dict_values_are_copied() -> None:
    cache = DatasetCache("dicts", maxsize=4)
    cache.put("k", {"AAPL": "Apple Inc."})
    fetched = cache.get("k")
    fetched["MSFT"] = "Microsoft Corp."
    assert cache.get("k") == {"AAPL": "Apple Inc."}


def test_lookup_distinguishes_none_from_a_miss() -> None:
    cache = DatasetCache("nones", maxsize=4)
    cache.put("k", None)
    hit, value = cache.lookup("k")
    assert hit is True
    assert value is None
    assert cache.get("k") is None


def test_load_runs_the_loader_once() -> None:
    cache = DatasetCache("loader", maxsize=4)
    calls: List[int] = []

    def loader() -> str:
        calls.append(1)
        return "value"

    assert cache.load("k", None, loader) == "value"
    assert cache.load("k", None, loader) == "value"
    assert len(calls) == 1
    assert cache.stats().hits == 1
    assert cache.stats().misses == 1


def test_load_caches_a_none_result() -> None:
    cache = DatasetCache("load-none", maxsize=4)
    calls: List[int] = []

    def loader() -> None:
        calls.append(1)
        return None

    cache.load("k", None, loader)
    cache.load("k", None, loader)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# DatasetCache: freshness, invalidation, bounds
# ---------------------------------------------------------------------------


def test_stale_signature_is_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("one", encoding="utf-8")
    first = file_signature(path)
    cache = DatasetCache("stale", maxsize=4)
    cache.put("k", "cached", first)

    path.write_text("one and two", encoding="utf-8")
    second = file_signature(path)
    assert cache.get("k", second) is None
    stats = cache.stats()
    assert stats.stale == 1
    assert stats.misses == 1
    assert stats.size == 0


def test_signature_ignored_when_caller_passes_none(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("one", encoding="utf-8")
    cache = DatasetCache("no-signature", maxsize=4)
    cache.put("k", "cached", file_signature(path))
    assert cache.get("k") == "cached"


def test_invalidate_reports_whether_it_removed_anything() -> None:
    cache = DatasetCache("invalidate", maxsize=4)
    cache.put("k", 1)
    assert cache.invalidate("k") is True
    assert cache.invalidate("k") is False
    assert cache.stats().invalidations == 1
    assert cache.get("k") is None


def test_invalidate_path_only_drops_that_file(tmp_path: Path) -> None:
    first_path = tmp_path / "a.txt"
    second_path = tmp_path / "b.txt"
    first_path.write_text("a", encoding="utf-8")
    second_path.write_text("b", encoding="utf-8")
    cache = DatasetCache("paths", maxsize=4)
    cache.put(("a", 1), "A", file_signature(first_path))
    cache.put(("a", 2), "A2", file_signature(first_path))
    cache.put(("b", 1), "B", file_signature(second_path))

    removed = cache.invalidate_path(first_path)
    assert removed == 2
    assert ("a", 1) not in cache
    assert ("b", 1) in cache


def test_invalidate_path_ignores_unknown_paths(tmp_path: Path) -> None:
    cache = DatasetCache("paths-unknown", maxsize=4)
    cache.put("k", "v")
    assert cache.invalidate_path(tmp_path / "nothing.txt") == 0
    assert cache.get("k") == "v"


def test_clear_empties_but_keeps_counters() -> None:
    cache = DatasetCache("clear", maxsize=4)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    removed = cache.clear()
    assert removed == 2
    assert len(cache) == 0
    assert cache.stats().hits == 1
    assert cache.stats().invalidations == 2


def test_lru_eviction() -> None:
    cache = DatasetCache("lru", maxsize=2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1  # "a" becomes the most recently used
    cache.put("c", 3)  # evicts "b", not "a"
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3
    assert cache.stats().evictions == 1


def test_ttl_expiry() -> None:
    cache = DatasetCache("ttl", maxsize=4, ttl=0.05)
    cache.put("k", "v")
    assert cache.get("k") == "v"
    time.sleep(0.07)
    assert cache.get("k") is None
    assert cache.stats().expirations == 1


def test_reset_stats_keeps_values() -> None:
    cache = DatasetCache("reset", maxsize=4)
    cache.put("k", "v")
    cache.get("k")
    cache.reset_stats()
    assert cache.stats().hits == 0
    assert cache.get("k") == "v"


def test_repr_and_len() -> None:
    cache = DatasetCache("shown", maxsize=3)
    cache.put("k", "v")
    assert len(cache) == 1
    assert "shown" in repr(cache)
    assert cache.keys() == ["k"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "  "},
        {"name": "bad-maxsize", "maxsize": 0},
        {"name": "bad-ttl", "ttl": 0},
        {"name": "negative-ttl", "ttl": -1.0},
    ],
)
def test_invalid_construction_is_rejected(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        DatasetCache(**kwargs)


def test_thread_safety_keeps_counters_consistent() -> None:
    cache = DatasetCache("threads", maxsize=64)
    cache.put("shared", "value")
    errors: List[BaseException] = []

    def worker(index: int) -> None:
        try:
            for step in range(50):
                cache.get("shared")
                cache.put(f"k-{index}-{step}", step)
        except BaseException as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    stats = cache.stats()
    assert stats.requests == stats.hits + stats.misses
    assert stats.size <= cache.maxsize


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def test_shared_caches_are_registered() -> None:
    names = [cache.name for cache in registered_caches()]
    assert "word_list" in names
    assert "constituents" in names
    assert "intent_patterns" in names
    assert names == sorted(names)


def test_cache_stats_covers_every_cache() -> None:
    ad_hoc = DatasetCache("ad-hoc-for-stats", maxsize=2)
    ad_hoc.put("k", "v")
    payload = cache_stats()
    assert "word_list" in payload
    assert "ad-hoc-for-stats" in payload
    assert payload["ad-hoc-for-stats"]["size"] == 1


def test_clear_all_caches_returns_the_entry_count() -> None:
    first = DatasetCache("sweep-a", maxsize=2)
    second = DatasetCache("sweep-b", maxsize=2)
    first.put("k", "v")
    second.put("k", "v")
    assert clear_all_caches() >= 2
    assert len(first) == 0
    assert len(second) == 0


def test_module_level_invalidate_path_reaches_every_cache(word_file: Path) -> None:
    load_words_from_file(word_file)
    assert len(WORD_LIST_CACHE) == 1
    assert invalidate_path(word_file) >= 1
    assert len(WORD_LIST_CACHE) == 0


# ---------------------------------------------------------------------------
# Compiled intent regexes
# ---------------------------------------------------------------------------


def test_compile_pattern_reuses_the_compiled_object() -> None:
    first = compile_pattern(r"\bhours\b")
    second = compile_pattern(r"\bhours\b")
    assert first is second
    assert PATTERN_CACHE.stats().hits == 1
    assert PATTERN_CACHE.stats().misses == 1


def test_compile_pattern_keys_on_flags() -> None:
    insensitive = compile_pattern("Hours")
    sensitive = compile_pattern("Hours", 0)
    assert insensitive is not sensitive
    assert insensitive.flags & re.IGNORECASE
    assert not sensitive.flags & re.IGNORECASE


def test_compile_pattern_matches_like_re_compile() -> None:
    pattern = compile_pattern(r"what are your hours")
    assert pattern.search("WHAT ARE YOUR HOURS?") is not None


def test_compile_pattern_propagates_invalid_regex() -> None:
    with pytest.raises(re.error):
        compile_pattern("(unclosed")


def test_clearing_the_pattern_cache_forces_recompilation() -> None:
    first = compile_pattern(r"\bprice\b")
    assert PATTERN_CACHE.stats().stores == 1
    clear_all_caches()
    assert len(PATTERN_CACHE) == 0
    second = compile_pattern(r"\bprice\b")
    assert second.pattern == first.pattern
    assert second.search("the PRICE of tea") is not None
    assert PATTERN_CACHE.stats().stores == 2


def test_intent_patterns_come_from_the_shared_cache() -> None:
    one = Intent.from_dict({"name": "hours", "patterns": [r"\bhours\b"], "responses": ["x"]})
    two = Intent.from_dict({"name": "hours-2", "patterns": [r"\bhours\b"], "responses": ["y"]})
    assert one.patterns[0] is two.patterns[0]
    assert PATTERN_CACHE.stats().hits >= 1
    assert one.score("what are your hours") == 1


# ---------------------------------------------------------------------------
# Integration: the Hangman word list
# ---------------------------------------------------------------------------


def test_word_list_is_loaded_once(word_file: Path) -> None:
    first = load_words_from_file(word_file)
    second = load_words_from_file(word_file)
    assert first == second == ["python", "variable", "iterator", "decorator"]
    stats = WORD_LIST_CACHE.stats()
    assert stats.misses == 1
    assert stats.hits == 1
    assert stats.stores == 1


def test_word_list_returns_a_copy(word_file: Path) -> None:
    first = load_words_from_file(word_file)
    first.append("polluted")
    second = load_words_from_file(word_file)
    assert "polluted" not in second
    assert second == ["python", "variable", "iterator", "decorator"]


def test_word_list_cache_is_invalidated_by_an_edit(word_file: Path) -> None:
    assert "python" in load_words_from_file(word_file)
    word_file.write_text("python\nflask\ndjango\n", encoding="utf-8")
    reloaded = load_words_from_file(word_file)
    assert reloaded == ["python", "flask", "django"]
    assert WORD_LIST_CACHE.stats().stale == 1


def test_word_list_explicit_invalidation_forces_a_reread(word_file: Path) -> None:
    assert load_words_from_file(word_file) == ["python", "variable", "iterator", "decorator"]
    word_file.write_text("python\nrust\njava\n", encoding="utf-8")
    assert WORD_LIST_CACHE.invalidate_path(word_file) == 1
    assert load_words_from_file(word_file) == ["python", "rust", "java"]


def test_word_list_bypasses_the_cache_when_a_report_is_given(word_file: Path) -> None:
    from dataset_loader import LoadReport

    first = load_words_from_file(word_file)
    report = LoadReport(word_file)
    second = load_words_from_file(word_file, report=report)
    assert first == second
    assert report.rows_checked == 4
    assert report.source is not None
    assert WORD_LIST_CACHE.stats().hits == 0


def test_word_list_use_cache_false_always_reads(word_file: Path) -> None:
    load_words_from_file(word_file)
    fresh = load_words_from_file(word_file, use_cache=False)
    assert fresh == ["python", "variable", "iterator", "decorator"]
    stats = WORD_LIST_CACHE.stats()
    assert stats.hits == 0
    assert stats.stores == 1
    # The bypass really reads: a same-length rewrite is picked up immediately.
    word_file.write_text("python\nvariable\niterator\ndecorated\n", encoding="utf-8")
    assert "decorated" in load_words_from_file(word_file, use_cache=False)


def test_word_list_arguments_are_part_of_the_key(word_file: Path) -> None:
    everything = load_words_from_file(word_file)
    short = load_words_from_file(word_file, min_length=7)
    assert len(everything) == 4
    assert short == ["variable", "iterator", "decorator"]
    assert WORD_LIST_CACHE.stats().misses == 2


def test_word_list_errors_are_not_cached(tmp_path: Path) -> None:
    path = tmp_path / "words.txt"
    path.write_text("python\nbad-word\n", encoding="utf-8")
    with pytest.raises(DatasetFormatError):
        load_words_from_file(path, strict=True)
    assert len(WORD_LIST_CACHE) == 0
    path.write_text("python\nvariable\n", encoding="utf-8")
    assert load_words_from_file(path, strict=True) == ["python", "variable"]


def test_word_list_missing_file_still_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_words_from_file(tmp_path / "absent.txt")


# ---------------------------------------------------------------------------
# Integration: the constituents table
# ---------------------------------------------------------------------------


def test_constituents_are_loaded_once(constituents_file: Path) -> None:
    first = load_symbols_from_csv(constituents_file)
    second = load_symbols_from_csv(constituents_file)
    assert first == {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corp.", "NVDA": "NVIDIA Corp."}
    assert first == second
    stats = CONSTITUENTS_CACHE.stats()
    assert stats.misses == 1
    assert stats.hits == 1
    assert stats.stores == 1


def test_constituents_return_a_copy(constituents_file: Path) -> None:
    first = load_symbols_from_csv(constituents_file)
    first["FAKE"] = "Fake Corp."
    assert "FAKE" not in load_symbols_from_csv(constituents_file)


def test_constituents_cache_is_invalidated_by_an_edit(constituents_file: Path) -> None:
    load_symbols_from_csv(constituents_file)
    constituents_file.write_text("Symbol,Security\nTSLA,Tesla Inc.\n", encoding="utf-8")
    assert load_symbols_from_csv(constituents_file) == {"TSLA": "Tesla Inc."}
    assert CONSTITUENTS_CACHE.stats().stale == 1


def test_constituents_bypass_the_cache_when_a_report_is_given(
    constituents_file: Path,
) -> None:
    from dataset_loader import LoadReport

    load_symbols_from_csv(constituents_file)
    report = LoadReport(constituents_file)
    symbols = load_symbols_from_csv(constituents_file, report=report)
    assert len(symbols) == 3
    assert report.rows_checked == 3
    assert CONSTITUENTS_CACHE.stats().hits == 0


def test_constituents_use_cache_false_always_parses(constituents_file: Path) -> None:
    load_symbols_from_csv(constituents_file)
    fresh = load_symbols_from_csv(constituents_file, use_cache=False)
    assert fresh == {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corp.", "NVDA": "NVIDIA Corp."}
    stats = CONSTITUENTS_CACHE.stats()
    assert stats.hits == 0
    assert stats.stores == 1
    constituents_file.write_text("Symbol,Security\nTSLA,Tesla Inc.\n", encoding="utf-8")
    assert load_symbols_from_csv(constituents_file, use_cache=False) == {"TSLA": "Tesla Inc."}


def test_constituents_strict_error_is_not_cached(tmp_path: Path) -> None:
    path = tmp_path / "constituents.csv"
    path.write_text("Symbol,Security\n,No Ticker Inc.\n", encoding="utf-8")
    with pytest.raises(DatasetFormatError):
        load_symbols_from_csv(path, strict=True)
    assert len(CONSTITUENTS_CACHE) == 0
    path.write_text("Symbol,Security\nAAPL,Apple Inc.\n", encoding="utf-8")
    assert load_symbols_from_csv(path, strict=True) == {"AAPL": "Apple Inc."}


def test_the_real_constituents_dataset_is_cached() -> None:
    dataset = Path(caching.__file__).parent / "projects/02_portfolio_tracker/data/constituents.csv"
    if not dataset.is_file():  # pragma: no cover - dataset always shipped
        pytest.skip("constituents dataset not present")
    symbols = load_symbols_from_csv(dataset)
    assert len(symbols) > 400
    assert symbols["AAPL"]
    assert load_symbols_from_csv(dataset) == symbols
    assert CONSTITUENTS_CACHE.stats().hits == 1
