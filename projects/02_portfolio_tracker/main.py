"""Interactive entry point for the stock portfolio tracker.

Run with::

    python projects/02_portfolio_tracker/main.py            # interactive
    python projects/02_portfolio_tracker/main.py --demo     # sample portfolio

The script collects positions, prints a formatted valuation report, and writes
``portfolio_summary.csv`` using :mod:`portfolio`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from portfolio import (
    Portfolio,
    load_prices_from_csv,
    load_symbols_from_csv,
    write_summary_csv,
)

HERE = Path(__file__).resolve().parent
DEFAULT_PRICES = HERE / "data" / "sample_prices.csv"
DEFAULT_CONSTITUENTS = HERE / "data" / "constituents.csv"
DEFAULT_OUTPUT = HERE / "portfolio_summary.csv"

#: Positions used by ``--demo`` so the tool can be exercised without input.
DEMO_POSITIONS: List[tuple] = [
    ("AAPL", 10, 150.00),
    ("MSFT", 5, 300.00),
    ("NVDA", 8, 420.00),
    ("RELIANCE.NS", 12, 2400.00),
]


def _load_prices(path: Path) -> dict:
    """Load the price table, degrading gracefully if the file is missing."""
    try:
        return load_prices_from_csv(path)
    except FileNotFoundError:
        print(f"! Price file not found at {path}. Falling back to buy prices.")
        return {}


def _prompt_positions(portfolio: Portfolio) -> None:
    """Read positions from standard input until the user submits a blank line."""
    print("\nEnter positions as:  SYMBOL  SHARES  BUY_PRICE  [CURRENT_PRICE]")
    print("Leave the symbol blank and press Enter when you are done.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            return
        parts = line.replace(",", " ").split()
        if len(parts) < 3:
            print("  ! Expected at least SYMBOL SHARES BUY_PRICE.")
            continue
        symbol, shares_raw, buy_raw = parts[0], parts[1], parts[2]
        current_raw = parts[3] if len(parts) > 3 else None
        try:
            shares = float(shares_raw)
            buy_price = float(buy_raw)
            current_price = float(current_raw) if current_raw else None
            portfolio.add(symbol, shares, buy_price, current_price)
        except ValueError as exc:
            print(f"  ! {exc}")
            continue
        print(f"  + Added {symbol.upper()}")


def _print_report(portfolio: Portfolio) -> None:
    """Print a formatted valuation table plus portfolio totals."""
    if not portfolio.holdings:
        print("\nNo positions entered — nothing to value.")
        return

    header = f"{'SYMBOL':<14}{'SHARES':>9}{'BUY':>10}{'NOW':>10}{'VALUE':>13}{'GAIN%':>9}"
    print("\n" + header)
    print("-" * len(header))
    for holding in portfolio.holdings:
        print(
            f"{holding.symbol:<14}{holding.shares:>9.2f}"
            f"{holding.buy_price:>10.2f}{holding.current_price:>10.2f}"
            f"{holding.market_value:>13,.2f}{holding.gain_pct:>8.2f}%"
        )
    print("-" * len(header))
    totals = portfolio.summary()
    print(f"{'Invested':<14}{totals['total_cost']:>42,.2f}")
    print(f"{'Market value':<14}{totals['total_value']:>42,.2f}")
    print(f"{'Net P/L':<14}{totals['total_gain']:>42,.2f}")
    print(f"{'Return':<14}{totals['total_gain_pct']:>41.2f}%")


def main(argv: List[str] | None = None) -> int:
    """Run the portfolio tracker CLI.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description="Stock portfolio performance tracker.")
    parser.add_argument(
        "--prices", type=Path, default=DEFAULT_PRICES, help="CSV file with ticker/price columns."
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write the CSV summary."
    )
    parser.add_argument(
        "--demo", action="store_true", help="Value a sample portfolio instead of prompting."
    )
    parser.add_argument(
        "--constituents",
        type=Path,
        default=None,
        help="Optional constituents CSV used to flag unknown tickers.",
    )
    args = parser.parse_args(argv)

    prices = _load_prices(args.prices)
    portfolio = Portfolio.from_prices(prices)

    if args.demo:
        for symbol, shares, buy in DEMO_POSITIONS:
            portfolio.add(symbol, shares, buy)
    else:
        _prompt_positions(portfolio)

    _print_report(portfolio)

    if args.constituents is not None:
        try:
            universe = load_symbols_from_csv(args.constituents)
        except (FileNotFoundError, ValueError) as exc:
            print(f"! Could not load constituents: {exc}")
        else:
            unknown = portfolio.unknown_symbols(universe)
            print(f"\nValidated against {len(universe):,} known symbols.")
            if unknown:
                print(f"  ! Not found in the reference list: {', '.join(unknown)}")

    if portfolio.holdings:
        out = write_summary_csv(portfolio, args.output)
        print(f"\nSummary written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
