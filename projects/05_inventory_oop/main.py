"""Demonstration entry point for the OOP inventory system.

Run with::

    python projects/05_inventory_oop/main.py

Builds a small library, performs a few checkouts and a return, and prints the
tabular audit report.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

# Make the repository root importable so every CLI shares one exit-code
# contract module regardless of the directory the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli_contract import EXIT_CODE_EPILOG, ExitCode, build_parser, run_cli  # noqa: E402
from inventory import InventoryError, InventoryManager  # noqa: E402
from models import Book, DigitalMedia, Magazine  # noqa: E402


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


def main(argv: List[str] | None = None) -> int:
    """Run the inventory demonstration.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (``0`` on success — see :mod:`cli_contract`).
    """
    parser = build_parser(
        "inventory",
        "OOP library inventory and asset management demo.",
        epilog=EXIT_CODE_EPILOG,
    )
    parser.parse_args(argv)

    manager = build_demo_inventory()

    print("== Initial inventory ==")
    print(manager.audit_report())

    print("\n== Checkouts ==")
    for item_id, borrower in [("B001", "Krish"), ("M001", "Aisha")]:
        try:
            record = manager.checkout(item_id, borrower)
            print(
                f"  {borrower} borrowed {item_id} for {record.days} days "
                f"(fee ₹{record.fee:,.2f})"
            )
        except InventoryError as exc:
            print(f"  ! {exc}")

    print("\n== Demonstrating error handling ==")
    for action in (
        lambda: manager.checkout("B001", "Ravi"),  # already on loan
        lambda: manager.checkout("ZZZ", "Ravi"),  # unknown item
        lambda: manager.return_item("B002"),  # not on loan
    ):
        try:
            action()
        except InventoryError as exc:
            print(f"  ! {exc}")

    print("\n== Return ==")
    record = manager.return_item("B001")
    print(f"  {record.borrower} returned {record.item_id}")

    print("\n== Final audit ==")
    print(manager.audit_report())

    print("\n== Dunder method showcase ==")
    book = manager.get("B002")
    print(f"  str(book)  -> {book}")
    print(f"  repr(book) -> {book!r}")
    print(f"  len(book)  -> {len(book)} pages")
    print(f"  book == copy -> {book == manager.get('B002')}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
