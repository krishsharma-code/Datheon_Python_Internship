"""Object-oriented library item hierarchy.

This module demonstrates strict OOP: an abstract base class, inheritance and
polymorphism, encapsulation via name-mangled attributes behind validated
properties, and dunder methods for clean object manipulation.

Hierarchy::

    LibraryItem (ABC)
    ├── Book
    ├── Magazine
    └── DigitalMedia
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Dict, Optional, Union

from errors import ValidationError

#: Default standard loan period, in days.
DEFAULT_LOAN_DAYS: int = 14


class LibraryItem(ABC):
    """Abstract base class for every item held in the library.

    Subclasses must implement :meth:`calculate_fee` and
    :meth:`display_metadata`. Price and borrow date are encapsulated in
    name-mangled private attributes and exposed through validated properties.

    Args:
        item_id: Unique identifier for the item.
        title: Human-readable title.
        price: Replacement/market price in rupees.
        borrow_date: Optional date the item was borrowed.

    Raises:
        ValidationError: If ``item_id``/``title`` is empty, ``price`` is
            negative, or ``borrow_date`` is invalid. Also a :class:`ValueError`.
    """

    def __init__(
        self,
        item_id: str,
        title: str,
        price: float,
        borrow_date: Optional[date] = None,
    ) -> None:
        self._item_id: str = self._clean_id(item_id)
        self._title: str = self._clean_title(title)
        self.__price: float = 0.0
        self.__borrow_date: Optional[date] = None
        self.price = price
        self.borrow_date = borrow_date
        self._available: bool = True

    # -- validation helpers ---------------------------------------------
    @staticmethod
    def _clean_id(item_id: str) -> str:
        cleaned = (item_id or "").strip()
        if not cleaned:
            raise ValidationError(
                "item_id must not be empty.",
                hint="give the item a short unique id, e.g. BK-001",
                context={"field": "item_id"},
            )
        return cleaned

    @staticmethod
    def _clean_title(title: str) -> str:
        cleaned = (title or "").strip()
        if not cleaned:
            raise ValidationError(
                "title must not be empty.",
                hint="give the item a human-readable title",
                context={"field": "title"},
            )
        return cleaned

    # -- encapsulated attributes ----------------------------------------
    @property
    def item_id(self) -> str:
        """Unique identifier for the item."""
        return self._item_id

    @property
    def title(self) -> str:
        """The item's title."""
        return self._title

    @property
    def price(self) -> float:
        """Replacement price in rupees."""
        return self.__price

    @price.setter
    def price(self, value: float) -> None:
        """Set the price, rejecting negative values.

        Raises:
            ValueError: If ``value`` is negative or not numeric.
        """
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"price must be a number, got {value!r}.",
                hint="use a decimal number, e.g. 499.00",
                context={"field": "price"},
            ) from exc
        if numeric < 0:
            raise ValidationError(
                f"price must not be negative, got {numeric:g}.",
                hint="use 0 or a positive price",
                context={"field": "price", "value": numeric},
            )
        self.__price = round(numeric, 2)

    @property
    def borrow_date(self) -> Optional[date]:
        """The date the item was borrowed, if any."""
        return self.__borrow_date

    @borrow_date.setter
    def borrow_date(self, value: Optional[Union[date, str]]) -> None:
        """Set the borrow date, rejecting future dates and bad formats.

        Accepts a :class:`datetime.date` or an ISO ``YYYY-MM-DD`` string.

        Raises:
            ValueError: If the value is not a date or is in the future.
        """
        if value is None:
            self.__borrow_date = None
            return
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError as exc:
                raise ValidationError(
                    f"borrow_date string must be in YYYY-MM-DD format, got {value!r}.",
                    hint="e.g. 2026-10-26",
                    context={"field": "borrow_date"},
                ) from exc
        if not isinstance(value, date):
            raise ValidationError(
                "borrow_date must be a date or ISO date string.",
                hint="pass datetime.date(2026, 10, 26) or '2026-10-26'",
                context={"field": "borrow_date"},
            )
        if value > date.today():
            raise ValidationError(
                f"borrow_date cannot be in the future, got {value.isoformat()}.",
                hint="use today's date or earlier",
                context={"field": "borrow_date", "value": value.isoformat()},
            )
        self.__borrow_date = value

    @property
    def available(self) -> bool:
        """Whether the item is currently on the shelf."""
        return self._available

    # -- abstract contract ----------------------------------------------
    @property
    @abstractmethod
    def loan_period_days(self) -> int:
        """Standard loan period for this item type, in days."""

    @abstractmethod
    def calculate_fee(self, days: Optional[int] = None) -> float:
        """Compute the rental fee for ``days`` of borrowing.

        Args:
            days: Number of days borrowed. Defaults to
                :attr:`loan_period_days`.

        Returns:
            The fee in rupees.
        """

    @abstractmethod
    def display_metadata(self) -> Dict[str, object]:
        """Return the item's type-specific metadata as a dictionary."""

    # -- dunder methods --------------------------------------------------
    def __str__(self) -> str:
        """Human-readable one-line summary."""
        state = "available" if self._available else "on loan"
        return f"{self._title} [{self._item_id}] — ₹{self.price:,.2f} ({state})"

    def __repr__(self) -> str:
        """Unambiguous representation, suitable for debugging."""
        return (
            f"{type(self).__name__}(item_id={self._item_id!r}, "
            f"title={self._title!r}, price={self.price!r})"
        )

    def __len__(self) -> int:
        """Type-specific 'length' (pages, articles, or minutes)."""
        return 0

    def __eq__(self, other: object) -> bool:
        """Two items are equal when they share a type and an id."""
        if not isinstance(other, LibraryItem):
            return NotImplemented
        return type(self) is type(other) and self._item_id == other._item_id

    def __hash__(self) -> int:
        """Hash by type and id, consistent with :meth:`__eq__`."""
        return hash((type(self).__name__, self._item_id))


class Book(LibraryItem):
    """A printed book.

    Args:
        item_id: Unique identifier.
        title: Book title.
        price: Replacement price.
        author: Author name.
        pages: Page count (must be positive).
        genre: Optional genre label.
        borrow_date: Optional borrow date.
    """

    #: Flat handling charge added to every book rental.
    BASE_FEE: float = 5.0
    #: Per-day rental rate for books.
    DAILY_RATE: float = 0.50

    def __init__(
        self,
        item_id: str,
        title: str,
        price: float,
        author: str,
        pages: int,
        genre: str = "General",
        borrow_date: Optional[date] = None,
    ) -> None:
        super().__init__(item_id, title, price, borrow_date)
        self.author = (author or "").strip() or "Unknown"
        if pages <= 0:
            raise ValidationError(
                f"pages must be greater than zero, got {pages}.",
                hint="use a positive page count",
                context={"field": "pages", "value": pages},
            )
        self.pages = int(pages)
        self.genre = (genre or "General").strip()

    @property
    def loan_period_days(self) -> int:
        """Books circulate for 14 days."""
        return DEFAULT_LOAN_DAYS

    def calculate_fee(self, days: Optional[int] = None) -> float:
        """Rental fee = base fee + daily rate × days."""
        span = self.loan_period_days if days is None else max(int(days), 0)
        return round(self.BASE_FEE + self.DAILY_RATE * span, 2)

    def display_metadata(self) -> Dict[str, object]:
        """Return book-specific metadata."""
        return {
            "type": "Book",
            "item_id": self.item_id,
            "title": self.title,
            "author": self.author,
            "pages": self.pages,
            "genre": self.genre,
            "price": self.price,
        }

    def __len__(self) -> int:
        """A book's length is its page count."""
        return self.pages


class Magazine(LibraryItem):
    """A periodical issue.

    Args:
        item_id: Unique identifier.
        title: Magazine title.
        price: Replacement price.
        issue: Issue number or label.
        articles: Number of articles (must be positive).
        borrow_date: Optional borrow date.
    """

    #: Flat handling charge added to every magazine rental.
    BASE_FEE: float = 2.0
    #: Per-day rental rate for magazines.
    DAILY_RATE: float = 0.20

    def __init__(
        self,
        item_id: str,
        title: str,
        price: float,
        issue: str,
        articles: int,
        borrow_date: Optional[date] = None,
    ) -> None:
        super().__init__(item_id, title, price, borrow_date)
        self.issue = (issue or "").strip() or "N/A"
        if articles <= 0:
            raise ValidationError(
                f"articles must be greater than zero, got {articles}.",
                hint="use a positive article count",
                context={"field": "articles", "value": articles},
            )
        self.articles = int(articles)

    @property
    def loan_period_days(self) -> int:
        """Magazines circulate for 7 days."""
        return 7

    def calculate_fee(self, days: Optional[int] = None) -> float:
        """Rental fee = base fee + daily rate × days."""
        span = self.loan_period_days if days is None else max(int(days), 0)
        return round(self.BASE_FEE + self.DAILY_RATE * span, 2)

    def display_metadata(self) -> Dict[str, object]:
        """Return magazine-specific metadata."""
        return {
            "type": "Magazine",
            "item_id": self.item_id,
            "title": self.title,
            "issue": self.issue,
            "articles": self.articles,
            "price": self.price,
        }

    def __len__(self) -> int:
        """A magazine's length is its article count."""
        return self.articles


class DigitalMedia(LibraryItem):
    """A digital asset (audio/video/ebook file).

    Digital items are free to borrow but carry a licence value used for
    replacement accounting.

    Args:
        item_id: Unique identifier.
        title: Asset title.
        price: Licence/replacement value.
        media_type: e.g. ``"Audiobook"``, ``"Video"``, ``"Ebook"``.
        duration_minutes: Runtime in minutes (must be positive).
        borrow_date: Optional borrow date.
    """

    def __init__(
        self,
        item_id: str,
        title: str,
        price: float,
        media_type: str,
        duration_minutes: int,
        borrow_date: Optional[date] = None,
    ) -> None:
        super().__init__(item_id, title, price, borrow_date)
        self.media_type = (media_type or "Digital").strip()
        if duration_minutes <= 0:
            raise ValidationError(
                f"duration_minutes must be greater than zero, got {duration_minutes}.",
                hint="use a positive duration",
                context={"field": "duration_minutes", "value": duration_minutes},
            )
        self.duration_minutes = int(duration_minutes)

    @property
    def loan_period_days(self) -> int:
        """Digital media licences last 30 days."""
        return 30

    def calculate_fee(self, days: Optional[int] = None) -> float:
        """Digital borrowing is free — no handling charge."""
        return 0.0

    def display_metadata(self) -> Dict[str, object]:
        """Return digital-media-specific metadata."""
        return {
            "type": "DigitalMedia",
            "item_id": self.item_id,
            "title": self.title,
            "media_type": self.media_type,
            "duration_minutes": self.duration_minutes,
            "price": self.price,
        }

    def __len__(self) -> int:
        """A digital asset's length is its runtime in minutes."""
        return self.duration_minutes
