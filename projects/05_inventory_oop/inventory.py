"""Inventory manager for the OOP library asset system.

Provides :class:`InventoryManager`, which owns a collection of
:class:`~models.LibraryItem` objects and coordinates checkout, returns, and
tabular audit reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from errors import InventoryError
from models import LibraryItem


@dataclass
class LoanRecord:
    """A record of one checkout.

    Attributes:
        item_id: The item that was checked out.
        borrower: Name of the borrower.
        days: Agreed loan period in days.
        fee: Fee charged at checkout.
        checked_out_on: Date of checkout.
    """

    item_id: str
    borrower: str
    days: int
    fee: float
    checked_out_on: date


class InventoryManager:
    """Manages a collection of library items.

    Items are keyed by ``item_id``. The manager enforces that only available
    items can be checked out and only checked-out items can be returned.
    """

    def __init__(self) -> None:
        self._items: Dict[str, LibraryItem] = {}
        self._loans: List[LoanRecord] = []

    # -- collection management ------------------------------------------
    def add_item(self, item: LibraryItem) -> None:
        """Add an item to the inventory.

        Args:
            item: The item to add.

        Raises:
            InventoryError: If an item with the same id already exists.
        """
        if item.item_id in self._items:
            raise InventoryError(f"Item id {item.item_id!r} already exists.")
        self._items[item.item_id] = item

    def remove_item(self, item_id: str) -> bool:
        """Remove an item from the inventory.

        Args:
            item_id: Identifier of the item to remove.

        Returns:
            ``True`` if an item was removed, ``False`` otherwise.
        """
        return self._items.pop(item_id, None) is not None

    def get(self, item_id: str) -> Optional[LibraryItem]:
        """Return the item with ``item_id``, or ``None`` if absent."""
        return self._items.get(item_id)

    def __len__(self) -> int:
        """Number of items held in the inventory."""
        return len(self._items)

    def __contains__(self, item_id: object) -> bool:
        """Support ``item_id in manager``."""
        return item_id in self._items

    # -- circulation -----------------------------------------------------
    def checkout(
        self,
        item_id: str,
        borrower: str,
        days: Optional[int] = None,
        today: Optional[date] = None,
    ) -> LoanRecord:
        """Check an item out to a borrower.

        Args:
            item_id: Identifier of the item.
            borrower: Name of the borrower (must not be empty).
            days: Loan period; defaults to the item's standard period.
            today: Override for "today" (used in tests).

        Returns:
            The created :class:`LoanRecord`.

        Raises:
            InventoryError: If the item is unknown, already on loan, or the
                borrower name is empty.
        """
        item = self._items.get(item_id)
        if item is None:
            raise InventoryError(f"No such item: {item_id!r}")
        if not item.available:
            raise InventoryError(f"Item {item_id!r} is already on loan.")
        borrower = (borrower or "").strip()
        if not borrower:
            raise InventoryError("Borrower name must not be empty.")

        span = item.loan_period_days if days is None else max(int(days), 0)
        fee = item.calculate_fee(span)
        record = LoanRecord(
            item_id=item_id,
            borrower=borrower,
            days=span,
            fee=fee,
            checked_out_on=today or date.today(),
        )
        item._available = False
        self._loans.append(record)
        return record

    def return_item(self, item_id: str) -> LoanRecord:
        """Return a checked-out item to the shelf.

        Args:
            item_id: Identifier of the item.

        Returns:
            The matching :class:`LoanRecord` that is being closed.

        Raises:
            InventoryError: If the item is unknown or is not on loan.
        """
        item = self._items.get(item_id)
        if item is None:
            raise InventoryError(f"No such item: {item_id!r}")
        if item.available:
            raise InventoryError(f"Item {item_id!r} is not on loan.")

        record = next((r for r in reversed(self._loans) if r.item_id == item_id), None)
        if record is None:
            raise InventoryError(f"No loan record found for {item_id!r}.")
        item._available = True
        return record

    # -- reporting -------------------------------------------------------
    @property
    def loans(self) -> List[LoanRecord]:
        """A copy of all loan records."""
        return list(self._loans)

    @property
    def total_revenue(self) -> float:
        """Total fees collected across all checkouts."""
        return round(sum(record.fee for record in self._loans), 2)

    @property
    def items_on_loan(self) -> int:
        """Number of items currently checked out."""
        return sum(1 for item in self._items.values() if not item.available)

    def audit_rows(self) -> List[Dict[str, object]]:
        """Return a tabular snapshot of every item.

        Returns:
            A list of dictionaries, one per item, with identity, type, status,
            length, and standard fee columns.
        """
        rows: List[Dict[str, object]] = []
        for item in sorted(self._items.values(), key=lambda i: i.item_id):
            rows.append(
                {
                    "item_id": item.item_id,
                    "type": type(item).__name__,
                    "title": item.title,
                    "status": "available" if item.available else "on loan",
                    "length": len(item),
                    "price": item.price,
                    "fee": item.calculate_fee(),
                }
            )
        return rows

    def audit_report(self) -> str:
        """Render the audit table as a formatted string.

        Returns:
            A multi-line, fixed-width report string.
        """
        rows = self.audit_rows()
        header = f"{'ID':<8}{'TYPE':<14}{'TITLE':<26}" f"{'STATUS':<11}{'LENGTH':>8}{'FEE':>9}"
        lines = [header, "-" * len(header)]
        for row in rows:
            lines.append(
                f"{row['item_id']:<8}{row['type']:<14}{str(row['title'])[:24]:<26}"
                f"{row['status']:<11}{row['length']:>8}{row['fee']:>9.2f}"
            )
        lines.append("-" * len(header))
        lines.append(
            f"Items: {len(self._items)} | On loan: {self.items_on_loan} | "
            f"Revenue: ₹{self.total_revenue:,.2f}"
        )
        return "\n".join(lines)
