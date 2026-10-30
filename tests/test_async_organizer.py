"""Tests for the asyncio organizer engine (Day 11).

The definition of done for Day 11 is that the ported engine returns exactly what
the threaded version did, that its concurrency is genuinely bounded, and that
the blocking ``FileOrganizer`` API is unchanged for existing callers. These
tests pin all three, plus the benchmark harness the day's numbers come from.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

import benchmark
from async_organizer import (
    DEFAULT_CONCURRENCY,
    MAX_CONCURRENCY,
    AsyncFileOrganizer,
    _DestinationReserver,
    run_coroutine_sync,
)
from errors import ValidationError
from organizer import FileOrganizer

ROOT = Path(__file__).resolve().parents[1]


def _make_file(directory: Path, name: str, content: str = "data") -> Path:
    """Create ``name`` inside ``directory`` with ``content``."""
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run the organizer CLI in a subprocess with a closed stdin."""
    return subprocess.run(
        [sys.executable, "projects/03_file_organizer_automation/main.py", *args],
        cwd=str(ROOT),
        input="",
        capture_output=True,
        text=True,
        timeout=60,
    )


def _slow_mover(delay: float = 0.02):
    """Return a mover that sleeps, so overlap is observable."""

    def mover(source: Path, destination: Path) -> None:
        time.sleep(delay)

    return mover


# --- constructor validation -------------------------------------------
@pytest.mark.parametrize("bad", [0, -1, MAX_CONCURRENCY + 1])
def test_rejects_out_of_range_concurrency(bad):
    with pytest.raises(ValidationError):
        AsyncFileOrganizer(concurrency=bad)


@pytest.mark.parametrize("bad", ["4", 4.0, True, None])
def test_rejects_non_integer_concurrency(bad):
    with pytest.raises(ValidationError):
        AsyncFileOrganizer(concurrency=bad)


def test_default_concurrency_is_used():
    assert AsyncFileOrganizer().concurrency == DEFAULT_CONCURRENCY


def test_engine_factory_mirrors_max_workers():
    engine = FileOrganizer(max_workers=3).engine()
    assert isinstance(engine, AsyncFileOrganizer)
    assert engine.concurrency == 3
    assert FileOrganizer(max_workers=3).engine(concurrency=5).concurrency == 5


# --- parity with the sync API -----------------------------------------
def test_async_organize_moves_files_into_categories(tmp_path):
    _make_file(tmp_path, "report.pdf")
    _make_file(tmp_path, "photo.jpg")
    _make_file(tmp_path, "script.py")

    report = asyncio.run(AsyncFileOrganizer(concurrency=2).organize(tmp_path))

    assert report.moved == 3
    assert report.engine == "asyncio"
    assert report.duration_ms >= 0.0
    assert (tmp_path / "Documents" / "report.pdf").exists()
    assert (tmp_path / "Images" / "photo.jpg").exists()
    assert (tmp_path / "Code" / "script.py").exists()


def test_sync_and_async_engines_agree(tmp_path):
    names = ["a.pdf", "b.jpg", "c.txt", "d.xyz", "e.mp3", "f.zip"]
    async_root = tmp_path / "async"
    sync_root = tmp_path / "sync"
    for root in (async_root, sync_root):
        root.mkdir()
        for name in names:
            _make_file(root, name)

    async_report = asyncio.run(AsyncFileOrganizer(concurrency=4).organize(async_root))
    sync_report = FileOrganizer(max_workers=4).organize(sync_root)

    def shape(report):
        return sorted(
            (Path(rec.source).name, rec.category, rec.status, Path(rec.destination).name)
            for rec in report.records
        )

    assert shape(async_report) == shape(sync_report)
    assert (async_report.moved, async_report.skipped) == (sync_report.moved, sync_report.skipped)


def test_records_keep_source_order(tmp_path):
    names = [f"file_{index:02d}.txt" for index in range(10)]
    for name in names:
        _make_file(tmp_path, name)

    report = asyncio.run(AsyncFileOrganizer(concurrency=4).organize(tmp_path))

    assert [Path(rec.source).name for rec in report.records] == sorted(names)


def test_async_dry_run_touches_nothing(tmp_path):
    _make_file(tmp_path, "report.pdf")
    report = asyncio.run(AsyncFileOrganizer().organize(tmp_path, dry_run=True))
    assert report.planned == 1
    assert report.moved == 0
    assert (tmp_path / "report.pdf").exists()
    assert not (tmp_path / "Documents").exists()


def test_async_empty_directory_reports_zero(tmp_path):
    report = asyncio.run(AsyncFileOrganizer().organize(tmp_path))
    assert report.total_processed == 0
    assert report.duration_ms >= 0.0


def test_async_not_a_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        asyncio.run(AsyncFileOrganizer().organize(tmp_path / "missing"))


def test_async_duplicate_detection(tmp_path):
    _make_file(tmp_path, "a.txt", "same")
    _make_file(tmp_path, "b.txt", "same")
    report = asyncio.run(
        AsyncFileOrganizer().organize(tmp_path, dry_run=True, detect_duplicates=True)
    )
    assert len(report.duplicates) == 1


def test_async_resolves_name_collisions(tmp_path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    _make_file(docs, "report.pdf", "existing")
    _make_file(tmp_path, "report.pdf", "incoming")

    report = asyncio.run(AsyncFileOrganizer().organize(tmp_path))
    assert report.moved == 1
    assert (docs / "report (1).pdf").exists()


# --- bounded concurrency ----------------------------------------------
def test_concurrency_is_bounded(tmp_path):
    for index in range(12):
        _make_file(tmp_path, f"file_{index}.txt")

    engine = AsyncFileOrganizer(concurrency=3, mover=_slow_mover())
    asyncio.run(engine.organize(tmp_path))

    assert engine.peak_in_flight <= 3
    assert engine.peak_in_flight >= 2  # the bound is used, not merely respected


def test_serial_concurrency_runs_one_at_a_time(tmp_path):
    for index in range(6):
        _make_file(tmp_path, f"file_{index}.txt")

    engine = AsyncFileOrganizer(concurrency=1, mover=_slow_mover(0.005))
    asyncio.run(engine.organize(tmp_path))

    assert engine.peak_in_flight == 1


def test_peak_in_flight_resets_between_runs(tmp_path):
    for index in range(4):
        _make_file(tmp_path, f"file_{index}.txt")

    engine = AsyncFileOrganizer(concurrency=2, mover=_slow_mover(0.005))
    asyncio.run(engine.organize(tmp_path, dry_run=True))
    first = engine.peak_in_flight
    asyncio.run(engine.organize(tmp_path, dry_run=True))
    assert engine.peak_in_flight <= first


# --- destination reserver ---------------------------------------------
def test_reserver_never_issues_the_same_path_twice(tmp_path):
    source = tmp_path / "report.pdf"
    dest_dir = tmp_path / "Documents"

    async def reserve_twice():
        reserver = _DestinationReserver()
        return (
            await reserver.reserve(source, dest_dir),
            await reserver.reserve(source, dest_dir),
        )

    first, second = asyncio.run(reserve_twice())
    assert first.name == "report.pdf"
    assert second.name == "report (1).pdf"


def test_reserver_gives_up_after_max_attempts(tmp_path):
    source = tmp_path / "a.txt"
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()
    _make_file(dest_dir, "a.txt")
    _make_file(dest_dir, "a (1).txt")

    async def reserve_once():
        return await _DestinationReserver(max_attempts=1).reserve(source, dest_dir)

    with pytest.raises(FileExistsError):
        asyncio.run(reserve_once())


# --- failure isolation ------------------------------------------------
def test_permission_error_is_recorded_not_raised(tmp_path):
    _make_file(tmp_path, "a.txt")

    def denied(source: Path, destination: Path) -> None:
        raise PermissionError("denied")

    report = asyncio.run(AsyncFileOrganizer(mover=denied).organize(tmp_path))
    assert len(report.errors) == 1
    assert "permission denied" in report.errors[0].detail


def test_unexpected_mover_error_is_captured(tmp_path):
    _make_file(tmp_path, "a.txt")

    def explode(source: Path, destination: Path) -> None:
        raise RuntimeError("boom")

    report = asyncio.run(AsyncFileOrganizer(mover=explode).organize(tmp_path))
    assert len(report.errors) == 1
    assert "boom" in report.errors[0].detail


def test_one_bad_file_does_not_stop_the_run(tmp_path):
    for index in range(4):
        _make_file(tmp_path, f"file_{index}.txt")
    bad = tmp_path / "file_2.txt"

    def picky(source: Path, destination: Path) -> None:
        if Path(source).name == bad.name:
            raise OSError("locked")
        destination.parent.mkdir(parents=True, exist_ok=True)
        Path(source).replace(destination)

    report = asyncio.run(AsyncFileOrganizer(concurrency=2, mover=picky).organize(tmp_path))
    assert report.moved == 3
    assert len(report.errors) == 1


# --- the blocking bridge ----------------------------------------------
def test_sync_facade_works_inside_a_running_loop(tmp_path):
    _make_file(tmp_path, "report.pdf")

    async def call_sync_api():
        return FileOrganizer().organize(tmp_path)

    report = asyncio.run(call_sync_api())
    assert report.moved == 1
    assert (tmp_path / "Documents" / "report.pdf").exists()


def test_run_coroutine_sync_returns_value():
    async def answer():
        return 42

    assert run_coroutine_sync(answer) == 42


def test_run_coroutine_sync_propagates_exceptions():
    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        run_coroutine_sync(boom)


# --- CLI integration ---------------------------------------------------
def test_cli_accepts_concurrency_flag(tmp_path):
    _make_file(tmp_path, "a.pdf")
    _make_file(tmp_path, "b.jpg")
    result = _run_cli(str(tmp_path), "--concurrency", "2")
    assert result.returncode == 0
    assert "asyncio engine" in result.stdout
    assert (tmp_path / "Documents" / "a.pdf").exists()


def test_cli_rejects_zero_concurrency_in_strict_mode(tmp_path):
    result = _run_cli(str(tmp_path), "--concurrency", "0", "--strict")
    assert result.returncode == 1
    assert "--concurrency" in result.stdout + result.stderr


def test_cli_workers_is_a_deprecated_alias(tmp_path):
    _make_file(tmp_path, "a.pdf")
    result = _run_cli(str(tmp_path), "--workers", "2")
    assert result.returncode == 0
    assert "deprecated" in result.stdout


# --- benchmark harness -------------------------------------------------
def test_build_tree_creates_files(tmp_path):
    created = benchmark.build_tree(tmp_path, 5)
    assert len(created) == 5
    assert all(path.exists() for path in created)


def test_measure_reports_throughput(tmp_path):
    row = benchmark.measure(tmp_path, count=6, concurrency=2, latency_ms=1.0)
    assert row["files"] == 6
    assert row["elapsed_ms"] > 0
    assert row["files_per_sec"] > 0


def test_run_benchmark_attaches_speedup():
    rows = benchmark.run_benchmark(count=4, latency_ms=1.0, levels=(1, 2))
    assert [int(row["concurrency"]) for row in rows] == [1, 2]
    assert rows[0]["speedup"] == pytest.approx(1.0)
    assert all(row["speedup"] > 0 for row in rows)


def test_format_table_lists_every_level():
    table = benchmark.format_table(benchmark.run_benchmark(count=2, latency_ms=0, levels=(1, 2)))
    assert "concurrency" in table
    assert "1.00x" in table


def test_benchmark_main_runs(capsys):
    assert benchmark.main(["--files", "3", "--latency-ms", "0", "--levels", "1", "2"]) == 0
    assert "Organizer benchmark" in capsys.readouterr().out
