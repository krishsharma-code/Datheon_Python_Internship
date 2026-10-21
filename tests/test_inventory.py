"""Tests for the OOP library inventory system."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from inventory import InventoryError, InventoryManager, LoanRecord
from models import Book, DigitalMedia, LibraryItem, Magazine


# --- abstraction -------------------------------------------------------
def test_base_class_cannot_be_instantiated():
    with pytest.raises(TypeError):
        LibraryItem("X", "Title", 10.0)  # type: ignore[abstract]


# --- construction & validation ----------------------------------------
def test_book_construction():
    book = Book("B1", "Clean Code", 899.0, "Robert C. Martin", 464, "Software")
    assert book.author == "Robert C. Martin"
    assert book.pages == 464
    assert book.genre == "Software"
    assert book.available is True


@pytest.mark.parametrize(
    "args",
    [
        ("", "T", 10.0, "A", 10),  # empty id
        ("B1", "", 10.0, "A", 10),  # empty title
        ("B1", "T", -1.0, "A", 10),  # negative price
        ("B1", "T", 10.0, "A", 0),  # non-positive pages
    ],
)
def test_book_validation(args):
    with pytest.raises(ValueError):
        Book(*args)


def test_magazine_requires_positive_articles():
    with pytest.raises(ValueError):
        Magazine("M1", "Wired", 100.0, "Issue 1", 0)


def test_digital_media_requires_positive_duration():
    with pytest.raises(ValueError):
        DigitalMedia("D1", "Course", 0.0, "Video", 0)


# --- encapsulation -----------------------------------------------------
def test_price_setter_validation():
    book = Book("B1", "T", 100.0, "A", 10)
    book.price = 250.5
    assert book.price == 250.5
    with pytest.raises(ValueError):
        book.price = -5
    with pytest.raises(ValueError):
        book.price = "not-a-number"  # type: ignore[assignment]


def test_borrow_date_setter_accepts_iso_string():
    book = Book("B1", "T", 100.0, "A", 10)
    book.borrow_date = "2026-01-15"
    assert book.borrow_date == date(2026, 1, 15)


def test_borrow_date_setter_rejects_future():
    book = Book("B1", "T", 100.0, "A", 10)
    with pytest.raises(ValueError):
        book.borrow_date = date.today() + timedelta(days=5)


def test_borrow_date_setter_rejects_bad_string():
    book = Book("B1", "T", 100.0, "A", 10)
    with pytest.raises(ValueError):
        book.borrow_date = "15-01-2026"


# --- polymorphism ------------------------------------------------------
def test_fee_models_differ():
    book = Book("B1", "T", 100.0, "A", 100)
    magazine = Magazine("M1", "T", 50.0, "I1", 10)
    digital = DigitalMedia("D1", "T", 0.0, "Video", 60)

    assert book.calculate_fee() == pytest.approx(5.0 + 0.5 * 14)
    assert magazine.calculate_fee() == pytest.approx(2.0 + 0.2 * 7)
    assert digital.calculate_fee() == 0.0


def test_loan_periods_differ():
    assert Book("B1", "T", 1.0, "A", 1).loan_period_days == 14
    assert Magazine("M1", "T", 1.0, "I", 1).loan_period_days == 7
    assert DigitalMedia("D1", "T", 1.0, "V", 1).loan_period_days == 30


def test_calculate_fee_with_explicit_days():
    book = Book("B1", "T", 100.0, "A", 100)
    assert book.calculate_fee(10) == pytest.approx(5.0 + 0.5 * 10)


def test_len_is_polymorphic():
    assert len(Book("B1", "T", 1.0, "A", 300)) == 300
    assert len(Magazine("M1", "T", 1.0, "I", 12)) == 12
    assert len(DigitalMedia("D1", "T", 1.0, "V", 90)) == 90


def test_display_metadata_keys():
    book = Book("B1", "T", 1.0, "A", 10)
    meta = book.display_metadata()
    assert meta["type"] == "Book"
    assert meta["pages"] == 10


# --- dunder methods ----------------------------------------------------
def test_str_and_repr():
    book = Book("B1", "Clean Code", 899.0, "Robert C. Martin", 464)
    assert "Clean Code" in str(book)
    assert "available" in str(book)
    assert "Book(" in repr(book)


def test_equality_and_hash():
    a = Book("B1", "T", 1.0, "A", 10)
    b = Book("B1", "Different title", 2.0, "B", 20)
    c = Magazine("B1", "T", 1.0, "I", 5)
    assert a == b  # same type + same id
    assert a != c  # different type
    assert hash(a) == hash(b)


def test_equality_with_non_item():
    assert Book("B1", "T", 1.0, "A", 10).__eq__("B1") is NotImplemented


# --- InventoryManager --------------------------------------------------
def _manager():
    manager = InventoryManager()
    manager.add_item(Book("B1", "Clean Code", 899.0, "Robert C. Martin", 464))
    manager.add_item(Magazine("M1", "Wired", 250.0, "Issue 12", 18))
    manager.add_item(DigitalMedia("D1", "Deep Dive", 0.0, "Video", 420))
    return manager


def test_manager_len_and_contains():
    manager = _manager()
    assert len(manager) == 3
    assert "B1" in manager
    assert "ZZ" not in manager


def test_add_duplicate_item_raises():
    manager = _manager()
    with pytest.raises(InventoryError):
        manager.add_item(Book("B1", "Duplicate", 1.0, "A", 1))


def test_remove_item():
    manager = _manager()
    assert manager.remove_item("B1") is True
    assert manager.remove_item("B1") is False


def test_checkout_marks_unavailable():
    manager = _manager()
    record = manager.checkout("B1", "Krish")
    assert isinstance(record, LoanRecord)
    assert record.fee == pytest.approx(5.0 + 0.5 * 14)
    assert manager.get("B1").available is False
    assert manager.items_on_loan == 1


def test_checkout_twice_raises():
    manager = _manager()
    manager.checkout("B1", "Krish")
    with pytest.raises(InventoryError):
        manager.checkout("B1", "Ravi")


def test_checkout_unknown_item_raises():
    manager = _manager()
    with pytest.raises(InventoryError):
        manager.checkout("NOPE", "Krish")


def test_checkout_empty_borrower_raises():
    manager = _manager()
    with pytest.raises(InventoryError):
        manager.checkout("B1", "   ")


def test_return_item():
    manager = _manager()
    manager.checkout("B1", "Krish")
    record = manager.return_item("B1")
    assert record.item_id == "B1"
    assert manager.get("B1").available is True


def test_return_not_on_loan_raises():
    manager = _manager()
    with pytest.raises(InventoryError):
        manager.return_item("B1")


def test_return_unknown_item_raises():
    manager = _manager()
    with pytest.raises(InventoryError):
        manager.return_item("NOPE")


def test_total_revenue_accumulates():
    manager = _manager()
    manager.checkout("B1", "Krish")
    manager.checkout("M1", "Aisha")
    expected = (5.0 + 0.5 * 14) + (2.0 + 0.2 * 7)
    assert manager.total_revenue == pytest.approx(expected)


def test_audit_rows_and_report():
    manager = _manager()
    rows = manager.audit_rows()
    assert len(rows) == 3
    assert {row["item_id"] for row in rows} == {"B1", "M1", "D1"}
    report = manager.audit_report()
    assert "Clean Code" in report
    assert "Revenue" in report


def test_checkout_with_custom_today():
    manager = _manager()
    fixed = date(2026, 3, 1)
    record = manager.checkout("D1", "Krish", today=fixed)
    assert record.checked_out_on == fixed
