"""Interactive entry point for the stock portfolio tracker.

Run with::

    python projects/02_portfolio_tracker/main.py            # interactive
    python projects/02_portfolio_tracker/main.py --demo     # sample portfolio

The script collects positions, prints a formatted valuation report, and writes
``portfolio_summary.csv`` using :mod:`portfolio`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

# Make the repository root importable so every CLI shares one exit-code
# contract module regardless of the directory the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cli_contract import EXIT_CODE_EPILOG, ExitCode, build_parser, run_cli  # noqa: E402
from portfolio import (  # noqa: E402
    Portfolio,
    load_prices_from_csv,
    load_symbols_from_csv,
    write_summary_csv,
)
from validators import (  # noqa: E402
    ValidationError,
    add_strict_flag,
    validate_path,
    validate_price,
    validate_quantity,
    validate_symbol,
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


def _load_prices(path: Path, *, strict: bool = False) -> dict:
    """Load the price table, degrading gracefully if the file is missing.

    Args:
        path: The price CSV to load.
        strict: When ``True``, a missing file is a hard error rather than a
            silent fallback to buy prices.

    Returns:
        A symbol → price mapping (empty when the file is missing and the run is
        not strict).

    Raises:
        ValidationError: In strict mode, if the file does not exist or cannot
            be read.
    """
    try:
        return load_prices_from_csv(path)
    except FileNotFoundError:
        if strict:
            raise ValidationError(f"--prices: file not found: {path} — e.g. data/sample_prices.csv")
        print(f"! Price file not found at {path}. Falling back to buy prices.")
        return {}
    except OSError as exc:
        if strict:
            raise ValidationError(f"--prices: {exc}")
        print(f"! Could not read the price file ({exc}). Falling back to buy prices.")
        return {}


def _prompt_positions(portfolio: Portfolio, *, strict: bool = False) -> None:
    """Read positions from standard input until the user submits a blank line.

    Each field is validated with the shared validators, so a bad line produces
    a single actionable message and the loop continues.

    Args:
        portfolio: The portfolio to add positions to.
        strict: Forwarded to the validators (ticker shape, plain numbers).
    """
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
            print("  ! line: expected SYMBOL SHARES BUY_PRICE [CURRENT_PRICE] — e.g. AAPL 10 150")
            continue
        try:
            symbol = validate_symbol(parts[0], strict=strict, field="symbol")
            shares = validate_quantity(parts[1], strict=strict, field="shares")
            buy_price = validate_price(parts[2], strict=strict, field="buy price")
            current_price = (
                validate_price(parts[3], strict=strict, field="current price")
                if len(parts) > 3
                else None
            )
            portfolio.add(symbol, shares, buy_price, current_price)
        except ValidationError as exc:
            print(f"  ! {exc}")
            continue
        print(f"  + Added {symbol}")


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
        Process exit code: ``0`` on success, ``1`` on a user or data error
        (see :mod:`cli_contract`).
    """
    parser = build_parser(
        "portfolio",
        "Stock portfolio performance tracker.",
        epilog=EXIT_CODE_EPILOG,
    )
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
    add_strict_flag(parser)
    args = parser.parse_args(argv)

    prices = _load_prices(args.prices, strict=args.strict)
    portfolio = Portfolio.from_prices(prices)

    if args.demo:
        for symbol, shares, buy in DEMO_POSITIONS:
            portfolio.add(symbol, shares, buy)
    else:
        _prompt_positions(portfolio, strict=args.strict)

    _print_report(portfolio)

    if args.constituents is not None:
        if args.strict:
            validate_path(args.constituents, must_be_file=True, field="--constituents")
        try:
            universe = load_symbols_from_csv(args.constituents)
        except (FileNotFoundError, ValueError) as exc:
            print(f"! Could not load constituents: {exc}")
        else:
            unknown = portfolio.unknown_symbols(universe)
            print(f"\nValidated against {len(universe):,} known symbols.")
            if unknown:
                if args.strict:
                    raise ValidationError(
                        "--constituents: unknown tickers "
                        f"{', '.join(unknown)} — not in the reference list"
                    )
                print(f"  ! Not found in the reference list: {', '.join(unknown)}")

    if portfolio.holdings:
        out = write_summary_csv(portfolio, args.output)
        print(f"\nSummary written to {out}")
    return int(ExitCode.OK)


if __name__ == "__main__":
    sys.exit(run_cli(lambda: main()))
