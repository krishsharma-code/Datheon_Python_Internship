# Inventory (object-oriented)

A library inventory built to show the object model rather than a menu: an
abstract item hierarchy, loans with fees and due dates, revenue accounting, an
audit table, and the dunder methods that make the objects behave.

```bash
inventory
```

## The session

The full demo, captured from a real run: the initial table, two checkouts, three
rejected operations, a return, revenue, the audit and the dunder showcase.

The session behind the walkthrough above is committed, not typed: [`assets/demos/inventory.json`](../assets/demos/inventory.json) is the exact file the player fetches, and `python tools/live_demos.py --check` fails if this page and that file ever disagree.

<!-- live-demo:begin -->
<div class="demo-terminal" data-session="assets/demos/inventory.json" data-command="inventory">
<pre class="demo-terminal__fallback" aria-label="Captured session: The OOP walkthrough, end to end">$ inventory
== Initial inventory ==
ID      TYPE          TITLE                     STATUS       LENGTH      FEE
----------------------------------------------------------------------------
B001    Book          Clean Code                available       464    12.00
B002    Book          The Pragmatic Programmer  available       352    12.00
D001    DigitalMedia  Python Deep Dive          available       420     0.00
M001    Magazine      Wired                     available        18     3.40
----------------------------------------------------------------------------
Items: 4 | On loan: 0 | Revenue: ₹0.00

== Checkouts ==
  Krish borrowed B001 for 14 days (fee ₹12.00)
  Aisha borrowed M001 for 7 days (fee ₹3.40)

== Demonstrating error handling ==
  ! Item 'B001' is already on loan.
  ! No such item: 'ZZZ'
  ! Item 'B002' is not on loan.

== Return ==
  Krish returned B001

== Final audit ==
ID      TYPE          TITLE                     STATUS       LENGTH      FEE
----------------------------------------------------------------------------
B001    Book          Clean Code                available       464    12.00
B002    Book          The Pragmatic Programmer  available       352    12.00
D001    DigitalMedia  Python Deep Dive          available       420     0.00
M001    Magazine      Wired                     on loan          18     3.40
----------------------------------------------------------------------------
Items: 4 | On loan: 1 | Revenue: ₹15.40

== Dunder method showcase ==
  str(book)  -&gt; The Pragmatic Programmer [B002] — ₹1,099.00 (available)
  repr(book) -&gt; Book(item_id='B002', title='The Pragmatic Programmer', price=1099.0)
  len(book)  -&gt; 352 pages
  book == copy -&gt; True</pre>
</div>
<!-- live-demo:end -->

## What to look at

- **The hierarchy earns its keep.** `LibraryItem` is abstract; `Book`,
  `DigitalMedia` and `Magazine` each override the loan period and the fee. The
  table's `TYPE` column is the concrete class, so the polymorphism is visible.
- **Rejections are part of the demo.** Borrowing an item already on loan, an
  unknown id, and returning something that was never out — three domain errors,
  each with its catalogue code and a message that says what to do next. None of
  them crashes the run.
- **Money is in rupees.** Fees are ₹12.00 for a book and ₹3.40 for a magazine,
  formatted through one helper so the currency symbol and the decimals never
  drift between the table, the totals and the JSON payload.
- **The dunder showcase is a contract test.** `str()`, `repr()`, `len()`, `==`
  and the rest are shown next to their output, because an object model that only
  exists in a class diagram is not worth much.
- **`--borrower` and `--days` are the scriptable path.** The demo is a default,
  not the only behaviour: a single checkout with a loan period is one command.

## Run it yourself

```bash
inventory                                   # the full walkthrough
inventory --borrower Aisha --days 7         # one checkout, then the audit
inventory --borrow-date 2026-11-24          # pin the clock for a reproducible run
inventory --json                            # one machine-readable document
inventory --strict                          # reject out-of-range input
```

From a checkout: `python projects/05_inventory_oop/main.py`.

## How it is built

| Module | What lives there |
| --- | --- |
| `models.py` | `LibraryItem` and its three subclasses, the fee and period rules |
| `inventory.py` | `InventoryManager`, `LoanRecord`, revenue, the audit table |
| `main.py` | The CLI and the demo script |

Dates go through `validators.validate_date` and the demo can be given a fixed
`--borrow-date`, so a transcript is reproducible instead of depending on the day
it was captured.

## Under the gates

- `tests/test_inventory.py` — 33 tests: checkout rules, double checkout, unknown
  ids, return dates and overdue fees, revenue totals, the dunder methods.
- Validation errors are raised as `InventoryError` subclasses and mapped to exit
  codes by the shared `cli_contract` layer, so a bad `--days` is a usage error
  (`2`) and a business-rule violation is a data error (`1`).
- Day 31's type gate found a latent one here: the demo formatted
  `manager.get("B002")` as if it could not be `None`. The guard is now explicit.

See the [error catalogue](../error_catalogue.md), [CLI contract & exit codes](../cli_contract.md)
and the [API reference](../api/projects.md).
