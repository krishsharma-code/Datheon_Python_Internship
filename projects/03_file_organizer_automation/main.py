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

from cli_contract import (  # noqa: E402
    EXIT_CODE_EPILOG,
    PROGRAM_VERSION,
    ExitCode,
    build_parser,
    run_cli,
)
from logging_config import add_logging_flags, configure_logging_from_args  # noqa: E402
from async_organizer import DEFAULT_CONCURRENCY, MAX_CONCURRENCY  # noqa: E402
from organizer import CATEGORIES, FileOrganizer, write_json_log  # noqa: E402
from validators import add_strict_flag, validate_int, validate_path  # noqa: E402

#: Program name, shared by the parser, the version banner and the logger.
PROG = "organizer"


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
    print(f"Duration  : {report.duration_ms:.1f} ms ({report.engine} engine)")

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
        PROG,
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
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=(
            "Maximum number of moves in flight at once on the asyncio engine "
            f"(default: {DEFAULT_CONCURRENCY})."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Deprecated alias for --concurrency; kept for script compatibility.",
    )
    parser.add_argument(
        "--log", type=Path, default=None, help="Write a JSON activity log to this path."
    )
    add_strict_flag(parser)
    add_logging_flags(parser)
    args = parser.parse_args(argv)

    session = configure_logging_from_args(PROG, args)
    logger = session.logger
    logger.debug("%s %s starting", PROG, PROGRAM_VERSION)

    # Validate the boundary inputs up front so the failure is one clear line.
    bound_field = "--concurrency"
    if args.workers is not None:
        print("! --workers is deprecated; use --concurrency instead.")
        args.concurrency = args.workers
        # Report a bad value against the flag the user actually typed.
        bound_field = "--workers"
    args.concurrency = validate_int(
        args.concurrency,
        minimum=1,
        maximum=MAX_CONCURRENCY if args.strict else None,
        field=bound_field,
    )
    args.directory = str(
        validate_path(args.directory, must_be_dir=True, strict=args.strict, field="directory")
    )
    if args.log is not None:
        args.log = validate_path(args.log, strict=args.strict, field="--log")

    logger.debug(
        "options: directory=%s dry_run=%s duplicates=%s concurrency=%d log=%s strict=%s",
        args.directory,
        args.dry_run,
        args.duplicates,
        args.concurrency,
        args.log,
        args.strict,
    )
    print("Categories: " + ", ".join(CATEGORIES))

    try:
        organizer = FileOrganizer(max_workers=args.concurrency)
        report = organizer.organize(
            args.directory,
            dry_run=args.dry_run,
            detect_duplicates=args.duplicates,
        )
    except OSError as exc:
        # Covers a non-directory argument and a directory we cannot list
        # (permission denied) with a single actionable line.
        logger.error("could not organize %s: %s", args.directory, exc)
        print(f"! {exc}")
        return int(ExitCode.DATA_ERROR)

    logger.info(
        "processed=%d moved=%d planned=%d errors=%d duplicates=%d duration_ms=%.1f",
        report.total_processed,
        report.moved,
        report.planned,
        len(report.errors),
        len(report.duplicates),
        report.duration_ms,
    )
    _print_report(report)

    if args.log:
        out = write_json_log(report, args.log)
        logger.info("activity log written to %s", out)
        print(f"\nActivity log written to {out}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
