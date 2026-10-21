# Task 5 — OOP Inventory & Asset System

A production-grade library inventory built on strict object-oriented
programming: abstraction, inheritance, polymorphism, encapsulation, and dunder
methods.

## OOP concepts demonstrated

| Concept | Where |
| --- | --- |
| **Abstraction** | `LibraryItem` is an `abc.ABC` with abstract `calculate_fee`, `display_metadata`, and `loan_period_days`. |
| **Inheritance** | `Book`, `Magazine`, `DigitalMedia` all derive from `LibraryItem`. |
| **Polymorphism** | Each subclass overrides `calculate_fee`, `display_metadata`, and `__len__` with type-specific behaviour. |
| **Encapsulation** | `__price` and `__borrow_date` are name-mangled, exposed via `@property` getters/setters with validation. |
| **Dunder methods** | `__str__`, `__repr__`, `__len__`, `__eq__`, `__hash__`. |

## Features

- Abstract `LibraryItem` base class enforcing a fee + metadata contract.
- Three concrete item types with distinct loan periods and fee models.
- Validated price and borrow-date setters (rejects negatives, future dates, and
  malformed ISO strings).
- `InventoryManager` handling add/remove, checkout, return, duplicate-id
  protection, and error signalling via `InventoryError`.
- Tabular **audit report** with per-item status, length, fee, and total revenue.

## Run

```bash
python projects/05_inventory_oop/main.py
```

## Architecture

| File | Responsibility |
| --- | --- |
| `models.py` | `LibraryItem` (ABC), `Book`, `Magazine`, `DigitalMedia`. |
| `inventory.py` | `InventoryManager`, `LoanRecord`, `InventoryError`. |
| `main.py` | Demonstration: checkouts, error handling, audit report. |

## Fee model

| Type | Loan period | Fee |
| --- | --- | --- |
| Book | 14 days | ₹5.00 + ₹0.50/day |
| Magazine | 7 days | ₹2.00 + ₹0.20/day |
| DigitalMedia | 30 days | Free |
