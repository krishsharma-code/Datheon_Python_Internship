# Task 2 — Stock Portfolio Performance Tracker

A portfolio valuation utility that computes real-time value and persists a
summary to CSV, using only the standard library (`csv`, `pathlib`).

## Features

- **Dictionary-backed portfolio** of `Holding` objects with validation.
- **Position merging** — adding an existing ticker blends the buy price on a
  cost-weighted basis.
- **Per-holding metrics** — cost basis, market value, absolute and percentage
  gain/loss.
- **Aggregate metrics** — total invested capital vs. market value, net P/L,
  and overall return.
- **Formatted console report** plus a generated `portfolio_summary.csv` with a
  `TOTAL` row.
- **Flexible price loading** — accepts ticker columns named
  `symbol`/`ticker`/`code` and price columns named
  `price`/`current_price`/`close`/`last`/`market_price` (case-insensitive).
- **Optional live prices** — install `yfinance` and swap the price table for a
  live lookup; the bundled sample CSV keeps it offline by default.

## Run

```bash
# Interactive: type positions, blank line to finish
python projects/02_portfolio_tracker/main.py

# Non-interactive sample portfolio
python projects/02_portfolio_tracker/main.py --demo

# Custom files
python projects/02_portfolio_tracker/main.py --prices my_prices.csv --output out.csv
```

Interactive input format:

```text
AAPL 10 150.00 189.45
MSFT 5 300.00
```

## Architecture

| File | Responsibility |
| --- | --- |
| `portfolio.py` | `Holding`, `Portfolio`, `load_prices_from_csv`, `write_summary_csv`. |
| `main.py` | Argument parsing, prompts, formatted report. |
| `data/sample_prices.csv` | Offline price table (US + NSE tickers). |

## Sample output

```text
SYMBOL           SHARES       BUY       NOW        VALUE    GAIN%
---------------------------------------------------------------
AAPL              10.00    150.00    189.45     1,894.50   26.30%
MSFT               5.00    300.00    412.30     2,061.50   37.43%
...
```
