"""Benchmark harness for the organizer's asyncio engine (Day 11).

Two questions this answers:

1. Does the bounded-concurrency engine actually overlap filesystem latency, or
   does it just add overhead to a serial loop?
2. Where does the curve flatten, so the default ``concurrency`` is a measured
   choice rather than a guess?

Run it with::

    python projects/03_file_organizer_automation/benchmark.py
    python projects/03_file_organizer_automation/benchmark.py --files 400 --latency-ms 5

The default mode injects a small artificial per-file latency so the shape of
the curve is visible on a fast local disk; ``--latency-ms 0`` measures real
``shutil.move`` calls instead.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from async_organizer import AsyncFileOrganizer  # noqa: E402

#: Concurrency levels measured when none are given on the command line.
DEFAULT_LEVELS: Sequence[int] = (1, 2, 4, 8, 16)


def build_tree(root: Path, count: int) -> List[Path]:
    """Create ``count`` small files across a handful of extensions.

    Args:
        root: Directory to populate (created if needed).
        count: Number of files to create.

    Returns:
        The paths created, in creation order.
    """
    root.mkdir(parents=True, exist_ok=True)
    extensions = (".txt", ".pdf", ".jpg", ".py", ".zip", ".mp3")
    created: List[Path] = []
    for index in range(count):
        path = root / f"file_{index:05d}{extensions[index % len(extensions)]}"
        path.write_text(f"payload {index}", encoding="utf-8")
        created.append(path)
    return created


def _sleepy_mover(latency_s: float):
    """Return a mover that sleeps for ``latency_s`` instead of moving.

    Simulating the latency keeps the benchmark honest on a warm page cache,
    where a real move is too fast to show any scheduling effect.

    Args:
        latency_s: Seconds to sleep per file.

    Returns:
        A callable matching the ``mover(source, destination)`` contract.
    """

    def mover(source: Path, destination: Path) -> None:
        time.sleep(latency_s)

    return mover


def measure(
    root: Path,
    count: int,
    concurrency: int,
    latency_ms: float = 0.0,
) -> Dict[str, float]:
    """Time one organization run at a given concurrency.

    Args:
        root: Parent directory to build the fixture in.
        count: Number of files to organise.
        concurrency: Concurrency bound to measure.
        latency_ms: Artificial per-file latency in milliseconds; ``0`` uses the
            real ``shutil.move``.

    Returns:
        A dict with ``concurrency``, ``files``, ``elapsed_ms`` and ``files_per_sec``.
    """
    with tempfile.TemporaryDirectory(dir=root) as workdir:
        work = Path(workdir)
        build_tree(work, count)
        mover = _sleepy_mover(latency_ms / 1000.0) if latency_ms > 0 else None
        organizer = AsyncFileOrganizer(concurrency=concurrency, mover=mover)
        started = time.perf_counter()
        report = organizer.organize_sync(work)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        moved = report.moved
    return {
        "concurrency": float(concurrency),
        "files": float(moved),
        "elapsed_ms": round(elapsed_ms, 1),
        "files_per_sec": round(moved / (elapsed_ms / 1000.0), 1) if elapsed_ms else 0.0,
    }


def run_benchmark(
    count: int = 200,
    latency_ms: float = 2.0,
    levels: Sequence[int] = DEFAULT_LEVELS,
) -> List[Dict[str, float]]:
    """Measure every concurrency level and attach a speed-up against serial.

    Args:
        count: Files per run.
        latency_ms: Artificial per-file latency, or ``0`` for real moves.
        levels: Concurrency bounds to measure; must include ``1`` for the
            baseline to be meaningful.

    Returns:
        One result dict per level, in the order given, each carrying a
        ``speedup`` field relative to the ``concurrency == 1`` run.
    """
    with tempfile.TemporaryDirectory() as root_name:
        root = Path(root_name)
        results = [measure(root, count, level, latency_ms) for level in levels]

    baseline = next((r["elapsed_ms"] for r in results if r["concurrency"] == 1), None)
    for result in results:
        if baseline:
            result["speedup"] = round(baseline / result["elapsed_ms"], 2)
        else:  # pragma: no cover - only when no serial baseline was measured
            result["speedup"] = 0.0
    return results


def format_table(results: Sequence[Dict[str, float]]) -> str:
    """Render benchmark results as an aligned plain-text table.

    Args:
        results: Output of :func:`run_benchmark`.

    Returns:
        A multi-line string ready to print.
    """
    header = f"{'concurrency':>12} {'files':>7} {'elapsed_ms':>11} {'files/sec':>10} {'speedup':>8}"
    lines = [header, "-" * len(header)]
    for row in results:
        lines.append(
            f"{int(row['concurrency']):>12} "
            f"{int(row['files']):>7} "
            f"{row['elapsed_ms']:>11.1f} "
            f"{row['files_per_sec']:>10.1f} "
            f"{row.get('speedup', 0.0):>7.2f}x"
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the benchmark from the command line.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        ``0`` on success.
    """
    parser = argparse.ArgumentParser(
        prog="organizer-benchmark",
        description="Measure the organizer's asyncio engine across concurrency levels.",
    )
    parser.add_argument("--files", type=int, default=200, help="Files per run (default: 200).")
    parser.add_argument(
        "--latency-ms",
        type=float,
        default=2.0,
        help="Simulated per-file latency in ms; 0 measures real moves (default: 2.0).",
    )
    parser.add_argument(
        "--levels",
        type=int,
        nargs="+",
        default=list(DEFAULT_LEVELS),
        help="Concurrency levels to measure (default: 1 2 4 8 16).",
    )
    args = parser.parse_args(argv)

    print(f"Organizer benchmark — {args.files} files, {args.latency_ms} ms/file latency\n")
    results = run_benchmark(count=args.files, latency_ms=args.latency_ms, levels=args.levels)
    print(format_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
