"""Command-line entry point for the file organizer.

Run with::

    python projects/03_file_organizer_automation/main.py ~/Downloads --dry-run
    python projects/03_file_organizer_automation/main.py ~/Downloads --log run.json

Nothing is deleted: files are only relocated into category subfolders, and
``--dry-run`` previews every move without touching the filesystem.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

# Make the repository root importable so every CLI shares one exit-code
# contract module regardless of the directory the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli_contract import EXIT_CODE_EPILOG, ExitCode, build_parser, run_cli  # noqa: E402
from organizer import CATEGORIES, FileOrganizer, write_json_log  # noqa: E402
from validators import add_strict_flag, validate_int, validate_path  # noqa: E402


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
        Process exit code: ``0`` on success, ``1`` on a bad directory
        (see :mod:`cli_contract`).
    """
    parser = build_parser(
        "organizer",
        "Organize a directory into category subfolders by file type.",
        epilog=EXIT_CODE_EPILOG,
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
    add_strict_flag(parser)
    args = parser.parse_args(argv)

    # Validate the boundary inputs up front so the failure is one clear line.
    args.workers = validate_int(
        args.workers, minimum=1, maximum=32 if args.strict else None, field="--workers"
    )
    args.directory = str(
        validate_path(args.directory, must_be_dir=True, strict=args.strict, field="directory")
    )
    if args.log is not None:
        args.log = validate_path(args.log, strict=args.strict, field="--log")

    print("Categories: " + ", ".join(CATEGORIES))

    try:
        organizer = FileOrganizer(max_workers=args.workers)
        report = organizer.organize(
            args.directory,
            dry_run=args.dry_run,
            detect_duplicates=args.duplicates,
        )
    except OSError as exc:
        # Covers a non-directory argument and a directory we cannot list
        # (permission denied) with a single actionable line.
        print(f"! {exc}")
        return int(ExitCode.DATA_ERROR)

    _print_report(report)

    if args.log:
        out = write_json_log(report, args.log)
        print(f"\nActivity log written to {out}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
