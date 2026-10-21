"""Command-line entry point for the file organizer.

Run with::

    python projects/03_file_organizer_automation/main.py ~/Downloads --dry-run
    python projects/03_file_organizer_automation/main.py ~/Downloads --log run.json

Nothing is deleted: files are only relocated into category subfolders, and
``--dry-run`` previews every move without touching the filesystem.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from organizer import CATEGORIES, FileOrganizer, write_json_log


def _print_report(report) -> None:
    """Print a human-readable execution summary."""
    mode = "DRY RUN (no changes made)" if report.dry_run else "LIVE"
    print(f"\nOrganising: {report.directory}   [{mode}]")
    print("-" * 60)

    if not report.records:
        print("No files found to organise.")
    for record in report.records:
        if record.status == "moved":
            marker = "->"
        elif record.status == "planned":
            marker = "~>"
        elif record.status == "error":
            marker = "!!"
        else:
            marker = "  "
        name = Path(record.source).name
        if record.status == "error":
            print(f" {marker} {name}  ({record.detail})")
        else:
            print(f" {marker} {name}  =>  {record.category}/")

    print("-" * 60)
    print(f"Processed : {report.total_processed}")
    print(f"Moved     : {report.moved}")
    if report.dry_run:
        print(f"Planned   : {report.planned}")
    print(f"Errors    : {len(report.errors)}")

    if report.duplicates:
        print(f"\nDuplicate groups found: {len(report.duplicates)}")
        for digest, paths in report.duplicates.items():
            print(f"  {digest[:12]}…  x{len(paths)}")
            for p in paths:
                print(f"     - {p}")


def main(argv: List[str] | None = None) -> int:
    """Run the organizer CLI.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (``0`` on success, ``1`` on a bad directory).
    """
    parser = argparse.ArgumentParser(
        description="Organize a directory into category subfolders by file type."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory to organize (default: current directory).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview actions without moving anything."
    )
    parser.add_argument(
        "--duplicates", action="store_true", help="Also detect duplicate files via SHA-256."
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="Number of concurrent move threads (default: 4)."
    )
    parser.add_argument(
        "--log", type=Path, default=None, help="Write a JSON activity log to this path."
    )
    args = parser.parse_args(argv)

    print("Categories: " + ", ".join(CATEGORIES))

    try:
        organizer = FileOrganizer(max_workers=args.workers)
        report = organizer.organize(
            args.directory,
            dry_run=args.dry_run,
            detect_duplicates=args.duplicates,
        )
    except NotADirectoryError as exc:
        print(f"! {exc}")
        return 1

    _print_report(report)

    if args.log:
        out = write_json_log(report, args.log)
        print(f"\nActivity log written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
