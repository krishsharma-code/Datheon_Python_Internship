"""Tests for the Day 12 profiling harness and the optimisations it justified.

Two groups live here:

* the harness itself — :func:`profiling.profile_callable`,
  :func:`profiling.time_callable`, :func:`profiling.compare_timings` and the
  ``profiling`` CLI, including their error paths; and
* the behaviour the optimisations must preserve — the suffix rule behind
  :func:`organizer.category_for`, the collision-naming rule behind
  :class:`async_organizer._DestinationReserver`, and the guarantee that
  :func:`organizer.find_duplicates` still groups identical files while only
  reading files whose size is shared.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import profiling
from async_organizer import _DestinationReserver, _split_name
from errors import ValidationError
from edge_cases import iter_files
from organizer import (
    DEFAULT_CATEGORY,
    _sha256_digest,
    _suffix_of,
    category_for,
    find_duplicates,
    unique_destination,
)

# --- profile_callable --------------------------------------------------


def _spin(iterations: int = 1000) -> int:
    """Do a little arithmetic so the profiler has something to attribute."""
    total = 0
    for value in range(iterations):
        total += value * value
    return total


def test_profile_callable_reports_statistics_in_milliseconds():
    result = profiling.profile_callable(_spin, 500, label="spin", iterations=2)

    assert result.label == "spin"
    assert result.iterations == 2
    assert result.wall_ms > 0
    assert result.stats
    assert all(stat.tottime_ms >= 0 and stat.cumtime_ms >= 0 for stat in result.stats)


def test_profile_callable_defaults_the_label_to_the_qualified_name():
    result = profiling.profile_callable(_spin, 10)

    assert result.label == "_spin"


def test_profile_callable_ranks_by_the_requested_key():
    by_tottime = profiling.profile_callable(_spin, 200, sort_by="tottime")
    by_calls = profiling.profile_callable(_spin, 200, sort_by="calls")

    assert by_tottime.sort_by == "tottime"
    assert by_calls.sort_by == "calls"
    assert [stat.total_calls for stat in by_calls.stats] == sorted(
        (stat.total_calls for stat in by_calls.stats), reverse=True
    )
    assert [stat.tottime_ms for stat in by_tottime.stats] == sorted(
        (stat.tottime_ms for stat in by_tottime.stats), reverse=True
    )


def test_profile_callable_honours_the_top_cap():
    result = profiling.profile_callable(_spin, 200, top=2)

    assert len(result.stats) == 2


@pytest.mark.parametrize(
    "kwargs",
    [{"iterations": 0}, {"iterations": -3}, {"sort_by": "cumulative"}],
)
def test_profile_callable_rejects_bad_arguments(kwargs):
    with pytest.raises(ValidationError):
        profiling.profile_callable(_spin, 10, **kwargs)


def test_profile_result_top_rejects_a_non_positive_count():
    result = profiling.profile_callable(_spin, 10)

    with pytest.raises(ValidationError):
        result.top(0)


def test_profile_result_renders_a_table_with_every_section():
    result = profiling.profile_callable(_spin, 100, label="spin")

    rendered = result.render(3)

    assert "spin: 1 iteration(s)" in rendered
    assert "ranked by tottime" in rendered
    assert "tottime" in rendered
    assert "function" in rendered


def test_profile_result_to_dict_is_json_serialisable():
    result = profiling.profile_callable(_spin, 100, label="spin")

    payload = result.to_dict(2)

    assert payload["label"] == "spin"
    assert payload["sort_by"] == "tottime"
    assert len(payload["stats"]) == 2
    assert json.loads(json.dumps(payload))["label"] == "spin"


def test_profile_result_to_dict_without_a_cap_includes_every_function():
    result = profiling.profile_callable(_spin, 100)

    assert len(result.to_dict()["stats"]) == len(result.stats)


# --- time_callable -----------------------------------------------------


def test_time_callable_collects_the_requested_samples():
    result = profiling.time_callable(_spin, 50, label="spin", repeat=3, warmup=1)

    assert result.label == "spin"
    assert result.number == 1
    assert len(result.samples_ms) == 3
    assert result.best_ms == min(result.samples_ms)
    assert result.best_ms <= result.median_ms <= max(result.samples_ms)
    assert result.stdev_ms >= 0


def test_time_callable_with_a_single_sample_has_no_spread():
    result = profiling.time_callable(_spin, 10, repeat=1)

    assert result.stdev_ms == 0.0
    assert result.best_ms == result.median_ms == result.mean_ms


def test_time_callable_records_the_number_of_calls_per_sample():
    result = profiling.time_callable(_spin, 10, repeat=2, number=5)

    assert result.number == 5


@pytest.mark.parametrize(
    "kwargs",
    [{"repeat": 0}, {"number": 0}, {"warmup": -1}],
)
def test_time_callable_rejects_bad_arguments(kwargs):
    with pytest.raises(ValidationError):
        profiling.time_callable(_spin, 10, **kwargs)


def test_timing_result_renders_a_one_line_summary():
    result = profiling.time_callable(_spin, 10, label="spin", repeat=2)

    rendered = result.render()

    assert rendered.startswith("spin: best ")
    assert "median" in rendered
    assert "2 sample(s)" in rendered


def test_timing_result_to_dict_round_trips_through_json():
    result = profiling.time_callable(_spin, 10, label="spin", repeat=2)

    payload = json.loads(json.dumps(result.to_dict()))

    assert payload["label"] == "spin"
    assert len(payload["samples_ms"]) == 2


# --- compare_timings ---------------------------------------------------


def _timing(best_ms: float, label: str = "call") -> profiling.TimingResult:
    """Build a hand-made timing result for comparison tests."""
    return profiling.TimingResult(
        label=label,
        number=1,
        samples_ms=(best_ms,),
        best_ms=best_ms,
        median_ms=best_ms,
        mean_ms=best_ms,
        stdev_ms=0.0,
    )


def test_compare_timings_reports_a_speedup_when_faster():
    comparison = profiling.compare_timings(_timing(100.0), _timing(25.0))

    assert comparison["speedup"] == 4.0
    assert comparison["change_pct"] == -75.0
    assert comparison["verdict"] == "faster"


def test_compare_timings_reports_a_regression_when_slower():
    comparison = profiling.compare_timings(_timing(25.0), _timing(50.0))

    assert comparison["speedup"] == 0.5
    assert comparison["change_pct"] == 100.0
    assert comparison["verdict"] == "slower"


def test_compare_timings_calls_equal_timings_unchanged():
    comparison = profiling.compare_timings(_timing(10.0), _timing(10.0))

    assert comparison["speedup"] == 1.0
    assert comparison["verdict"] == "unchanged"


def test_compare_timings_rejects_non_positive_measurements():
    with pytest.raises(ValidationError):
        profiling.compare_timings(_timing(0.0), _timing(1.0))


# --- fixtures and the CLI ---------------------------------------------


def test_build_file_fixture_spreads_extensions_and_copies(tmp_path):
    created = profiling._build_file_fixture(tmp_path / "files", 8, duplicates=2)

    suffixes = {path.suffix for path in created}
    assert len(created) == 10
    assert len(suffixes) > 1
    assert any(path.name.startswith("copy_") for path in created)


def test_build_file_fixture_gives_each_payload_a_distinct_size(tmp_path):
    created = profiling._build_file_fixture(tmp_path / "files", 12)

    sizes = [path.stat().st_size for path in created]
    assert len(set(sizes)) == len(sizes)


def test_build_file_fixture_rejects_a_non_positive_count(tmp_path):
    with pytest.raises(ValidationError):
        profiling._build_file_fixture(tmp_path, 0)


def test_build_price_fixture_writes_a_header_and_the_requested_rows(tmp_path):
    path = profiling._build_price_fixture(tmp_path / "prices.csv", rows=3)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Symbol,Price"
    assert len(lines) == 4


def test_run_target_rejects_an_unknown_name(tmp_path):
    with pytest.raises(ValidationError):
        profiling._run_target("nonsense", tmp_path, files=4, iterations=1)


def test_run_target_profiles_the_word_list():
    result = profiling._run_target("wordlist", Path("."), files=4, iterations=1)

    assert "load_words_from_file" in result.label
    assert result.stats


def test_cli_emits_json(capsys):
    code = profiling.main(["organizer", "--files", "12", "--top", "3", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["sort_by"] == "tottime"
    assert len(payload["stats"]) <= 3
    assert payload["stats"][0]["tottime_ms"] >= 0


def test_cli_emits_a_table_by_default(capsys):
    code = profiling.main(["duplicates", "--files", "12", "--top", "3"])

    output = capsys.readouterr().out
    assert code == 0
    assert "find_duplicates" in output
    assert "function" in output


# --- optimised suffix rule --------------------------------------------


#: Names whose extension rule is easy to get wrong, checked against pathlib.
TRICKY_NAMES = [
    "report.pdf",
    "photo.JPEG",
    "archive.tar.gz",
    "trailing.",
    ".bashrc",
    "..x",
    "...",
    "noext",
    "",
    "dir.d/file",
    "dir.with.dots/file.csv",
    "a." * 3,
    " spaced .TXT ",
]


@pytest.mark.parametrize("name", TRICKY_NAMES)
def test_suffix_of_matches_pathlib(name):
    assert _suffix_of(name) == Path(name).suffix.lower()


@pytest.mark.parametrize("name", TRICKY_NAMES)
def test_suffix_of_matches_pathlib_for_path_objects(name):
    assert _suffix_of(Path(name)) == Path(name).suffix.lower()


def test_category_for_still_maps_known_and_unknown_extensions():
    assert category_for("holiday.JPG") == "Images"
    assert category_for("notes.md") == "Documents"
    assert category_for("mystery.qqq") == DEFAULT_CATEGORY
    assert category_for("README") == DEFAULT_CATEGORY


def test_category_for_still_accepts_a_custom_mapping():
    mapping = {".qqq": "Custom"}

    assert category_for("mystery.qqq", mapping) == "Custom"
    assert category_for("notes.md", mapping) == DEFAULT_CATEGORY


# --- optimised duplicate detection ------------------------------------


def test_sha256_digest_hashes_without_the_existence_check(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hello", encoding="utf-8")

    assert _sha256_digest(path) == _sha256_digest(str(path))


def test_find_duplicates_only_reads_files_whose_size_repeats(tmp_path, monkeypatch):
    for index in range(5):
        (tmp_path / f"unique_{index}.txt").write_bytes(b"u" * (index + 1))
    (tmp_path / "twin_a.txt").write_bytes(b"same content")
    (tmp_path / "twin_b.txt").write_bytes(b"same content")

    hashed = []
    original = _sha256_digest

    def counting_digest(path, chunk_size=65536):
        hashed.append(Path(path).name)
        return original(path, chunk_size)

    monkeypatch.setattr("organizer._sha256_digest", counting_digest)

    duplicates = find_duplicates(tmp_path)

    assert sorted(hashed) == ["twin_a.txt", "twin_b.txt"]
    assert list(duplicates.values()) == [[tmp_path / "twin_a.txt", tmp_path / "twin_b.txt"]]


def test_find_duplicates_still_groups_identical_files(tmp_path):
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_bytes(b"duplicate payload")
    (tmp_path / "different.txt").write_bytes(b"something else entirely")

    duplicates = find_duplicates(tmp_path)

    assert len(duplicates) == 1
    assert sorted(path.name for path in next(iter(duplicates.values()))) == [
        "a.txt",
        "b.txt",
        "c.txt",
    ]


def test_find_duplicates_groups_empty_files(tmp_path):
    (tmp_path / "empty_a.txt").write_bytes(b"")
    (tmp_path / "empty_b.txt").write_bytes(b"")

    duplicates = find_duplicates(tmp_path)

    assert len(duplicates) == 1
    assert len(next(iter(duplicates.values()))) == 2


def test_find_duplicates_returns_nothing_for_distinct_sizes(tmp_path):
    for index in range(4):
        (tmp_path / f"f{index}.txt").write_bytes(b"x" * (index + 1))

    assert find_duplicates(tmp_path) == {}


# --- optimised directory listing --------------------------------------


def test_iter_files_still_returns_sorted_files_only(tmp_path):
    (tmp_path / "B.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "nested").mkdir()

    assert [path.name for path in iter_files(tmp_path)] == ["a.txt", "B.txt"]


def test_iter_files_still_follows_file_symlinks(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("payload", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pytest.skip("symlinks are unavailable on this platform")

    names = [path.name for path in iter_files(tmp_path)]

    assert names == ["link.txt", "real.txt"]


def test_iter_files_skips_broken_symlinks(tmp_path):
    broken = tmp_path / "broken.txt"
    try:
        broken.symlink_to(tmp_path / "missing.txt")
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pytest.skip("symlinks are unavailable on this platform")
    (tmp_path / "real.txt").write_text("payload", encoding="utf-8")

    assert [path.name for path in iter_files(tmp_path)] == ["real.txt"]


def test_iter_files_still_rejects_a_file_path(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("payload", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        iter_files(target)


# --- optimised collision naming ---------------------------------------


@pytest.mark.parametrize(
    "name",
    ["report.pdf", "archive.tar.gz", "trailing.", ".bashrc", "..x", "noext"],
)
def test_split_name_matches_pathlib(name):
    stem, suffix = _split_name(name)

    assert stem + suffix == name
    assert stem == Path(name).stem
    assert suffix == Path(name).suffix


@pytest.mark.parametrize("name", ["report.pdf", "trailing.", "noext"])
def test_reserver_names_collisions_like_unique_destination(tmp_path, name):
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()
    source = tmp_path / name
    source.write_text("payload", encoding="utf-8")
    (dest_dir / name).write_text("taken", encoding="utf-8")

    expected = unique_destination(source, dest_dir)
    reserved = os.fspath(asyncio_run(_DestinationReserver().reserve(source, dest_dir)))

    assert reserved == os.fspath(expected)


def asyncio_run(coroutine):
    """Run a coroutine to completion and return its result."""
    import asyncio

    return asyncio.run(coroutine)
