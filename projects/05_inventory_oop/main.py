"""Demonstration entry point for the OOP inventory system.

Run with::

    python projects/05_inventory_oop/main.py

Builds a small library, performs a few checkouts and a return, and prints the
tabular audit report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# Make the repository root importable so every CLI shares one exit-code
# contract module regardless of the directory the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli_contract import (  # noqa: E402
    EXIT_CODE_EPILOG,
    PROGRAM_VERSION,
    ExitCode,
    JsonEmitter,
    add_json_flag,
    build_parser,
    human_output,
    json_requested,
    run_cli,
)
from errors import DatheonError  # noqa: E402
from inventory import InventoryError, InventoryManager, LoanRecord  # noqa: E402
from logging_config import add_logging_flags, configure_logging_from_args  # noqa: E402
from models import Book, DigitalMedia, Magazine  # noqa: E402
from validators import add_strict_flag, validate_date, validate_int, validate_name  # noqa: E402

#: Program name, shared by the parser, the version banner and the logger.
PROG = "inventory"


def build_demo_inventory() -> InventoryManager:
    """Create an inventory pre-populated with sample items.

    Returns:
        A ready-to-use :class:`InventoryManager`.
    """
    manager = InventoryManager()
    manager.add_item(Book("B001", "Clean Code", 899.0, "Robert C. Martin", 464, "Software"))
    manager.add_item(Book("B002", "The Pragmatic Programmer", 1099.0, "Hunt & Thomas", 352))
    manager.add_item(Magazine("M001", "Wired", 250.0, "Issue 12", 18))
    manager.add_item(DigitalMedia("D001", "Python Deep Dive", 0.0, "Video", 420))
    return manager


def _loan_dict(record: LoanRecord) -> Dict[str, Any]:
    """Describe one loan as a JSON-ready mapping.

    Args:
        record: The :class:`~inventory.LoanRecord` to serialise.

    Returns:
        A mapping with the item, borrower, loan period, fee and checkout date.
    """
    return {
        "item_id": record.item_id,
        "borrower": record.borrower,
        "days": record.days,
        "fee": record.fee,
        "checked_out_on": record.checked_out_on.isoformat(),
    }


def _single_checkout(args: argparse.Namespace) -> Tuple[str, Dict[str, Any], int]:
    """Check the first demo item out to a validated borrower.

    This is the input-driven path: the borrower name, the optional loan period,
    and the optional borrow date all pass through the shared validators, so a
    bad value exits ``1`` with one actionable message.

    Args:
        args: Parsed command-line arguments (``borrower`` is required).

    Returns:
        A ``(command, payload, exit_code)`` triple for the emitter; code ``0``
        on success.
    """
    borrower = validate_name(args.borrower, strict=args.strict, field="--borrower")
    days = None
    if args.days is not None:
        days = validate_int(
            args.days, minimum=1, maximum=90 if args.strict else None, field="--days"
        )

    manager = build_demo_inventory()
    if args.borrow_date is not None:
        item = manager.get("B001")
        if item is not None:
            item.borrow_date = validate_date(args.borrow_date, field="--borrow-date")

    record = manager.checkout("B001", borrower, days)
    print(
        f"{record.borrower} borrowed {record.item_id} for {record.days} days "
        f"(fee ₹{record.fee:,.2f})"
    )
    payload = {
        "record": _loan_dict(record),
        "summary": {
            "items": len(manager),
            "on_loan": manager.items_on_loan,
            "revenue": manager.total_revenue,
        },
    }
    return "checkout", payload, int(ExitCode.OK)


def main(argv: List[str] | None = None) -> int:
    """Run the inventory demonstration.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (``0`` on success — see :mod:`cli_contract`). With
        ``--json`` the same code is returned and one document is written to
        ``stdout``.
    """
    parser = build_parser(
        PROG,
        "OOP library inventory and asset management demo.",
        epilog=EXIT_CODE_EPILOG,
    )
    add_strict_flag(parser)
    add_logging_flags(parser)
    add_json_flag(parser)
    parser.add_argument(
        "--borrower",
        default=None,
        help="Check the first demo item out to this borrower instead of the full demo.",
    )
    parser.add_argument(
        "--days", type=int, default=None, help="Loan period in days for --borrower."
    )
    parser.add_argument(
        "--borrow-date",
        default=None,
        help="ISO date (YYYY-MM-DD) the item was borrowed, for --borrower.",
    )
    args = parser.parse_args(argv)

    emitter = JsonEmitter(PROG, json_requested(args))
    try:
        with human_output(emitter.enabled):
            command, payload, code = _run(args)
    except DatheonError as exc:
        emitter.failure("checkout" if args.borrower is not None else "demo", exc)
        raise
    return emitter.success(command, payload, code=code)


def _run(args: argparse.Namespace) -> Tuple[str, Dict[str, Any], int]:
    """Run the demo (or one checkout) and return what to report.

    The JSON document is emitted by :func:`main` once this returns, so the
    printed walkthrough and the machine-readable payload never interleave on
    ``stdout``.

    Args:
        args: Parsed command-line arguments.

    Returns:
        A ``(command, payload, exit_code)`` triple for the emitter.
    """
    session = configure_logging_from_args(PROG, args)
    logger = session.logger
    logger.debug("%s %s starting", PROG, PROGRAM_VERSION)
    logger.debug(
        "options: borrower=%s days=%s borrow_date=%s strict=%s",
        args.borrower,
        args.days,
        args.borrow_date,
        args.strict,
    )

    if args.borrower is not None:
        return _single_checkout(args)

    manager = build_demo_inventory()
    logger.info("demo inventory built with %d item(s)", len(manager))

    print("== Initial inventory ==")
    print(manager.audit_report())

    print("\n== Checkouts ==")
    checkouts: List[Dict[str, Any]] = []
    for item_id, borrower in [("B001", "Krish"), ("M001", "Aisha")]:
        try:
            record = manager.checkout(item_id, borrower)
            print(
                f"  {borrower} borrowed {item_id} for {record.days} days "
                f"(fee ₹{record.fee:,.2f})"
            )
        except InventoryError as exc:
            print(f"  ! {exc}")
        else:
            checkouts.append(_loan_dict(record))

    print("\n== Demonstrating error handling ==")
    rejected: List[Dict[str, str]] = []
    actions: Tuple[Callable[[], object], ...] = (
        lambda: manager.checkout("B001", "Ravi"),  # already on loan
        lambda: manager.checkout("ZZZ", "Ravi"),  # unknown item
        lambda: manager.return_item("B002"),  # not on loan
    )
    for action in actions:
        try:
            action()
        except InventoryError as exc:
            print(f"  ! {exc}")
            rejected.append({"type": type(exc).__name__, "message": str(exc)})

    print("\n== Return ==")
    record = manager.return_item("B001")
    print(f"  {record.borrower} returned {record.item_id}")

    print("\n== Final audit ==")
    print(manager.audit_report())
    logger.info(
        "demo finished: on_loan=%d revenue=%.2f",
        manager.items_on_loan,
        manager.total_revenue,
    )

    print("\n== Dunder method showcase ==")
    book = manager.get("B002")
    # The demo inventory always holds B002; the guard keeps that assumption
    # explicit so the dunder showcase below never formats a ``None``.
    assert book is not None, "demo inventory must contain B002"
    print(f"  str(book)  -> {book}")
    print(f"  repr(book) -> {book!r}")
    print(f"  len(book)  -> {len(book)} pages")
    print(f"  book == copy -> {book == manager.get('B002')}")

    payload = {
        "items": manager.audit_rows(),
        "checkouts": checkouts,
        "returns": [_loan_dict(record)],
        "rejected": rejected,
        "summary": {
            "items": len(manager),
            "on_loan": manager.items_on_loan,
            "revenue": manager.total_revenue,
        },
    }
    return "demo", payload, int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
