"""Portfolio valuation engine and CSV persistence helpers.

The module is standard-library only and contains no interactive code, so it can
be imported and tested directly. The interactive shell lives in :mod:`main`.

Typical use::

    from portfolio import Portfolio, load_prices_from_csv, write_summary_csv

    prices = load_prices_from_csv("data/sample_prices.csv")
    portfolio = Portfolio.from_prices(prices)
    portfolio.add("AAPL", shares=10, buy_price=150.0)
    write_summary_csv(portfolio, "portfolio_summary.csv")
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

#: Header names (lower-cased) accepted as the ticker column in a price CSV.
_SYMBOL_KEYS = ("symbol", "ticker", "code")

#: Header names (lower-cased) accepted as the price column in a price CSV.
_PRICE_KEYS = ("price", "current_price", "close", "last", "market_price")

#: Header names (lower-cased) accepted as the company-name column in a
#: constituents CSV (e.g. the S&P 500 dataset).
_NAME_KEYS = ("security", "name", "company", "company_name")


@dataclass
class Holding:
    """A single position in the portfolio.

    Attributes:
        symbol: Ticker symbol, e.g. ``"AAPL"``.
        shares: Number of shares held (may be fractional).
        buy_price: Average purchase price per share.
        current_price: Latest known price per share.

    Raises:
        ValueError: If any field fails validation.
    """

    symbol: str
    shares: float
    buy_price: float
    current_price: float = 0.0

    def __post_init__(self) -> None:
        self.symbol = (self.symbol or "").strip().upper()
        if not self.symbol:
            raise ValueError("Holding symbol must not be empty.")
        if self.shares <= 0:
            raise ValueError("Holding shares must be greater than zero.")
        if self.buy_price < 0:
            raise ValueError("Buy price must not be negative.")
        if self.current_price < 0:
            raise ValueError("Current price must not be negative.")

    @property
    def cost_basis(self) -> float:
        """Total capital invested in this position."""
        return self.shares * self.buy_price

    @property
    def market_value(self) -> float:
        """Current market value of this position."""
        return self.shares * self.current_price

    @property
    def gain(self) -> float:
        """Absolute profit (positive) or loss (negative)."""
        return self.market_value - self.cost_basis

    @property
    def gain_pct(self) -> float:
        """Percentage gain/loss relative to cost basis (0.0 if cost is zero)."""
        return (self.gain / self.cost_basis * 100.0) if self.cost_basis else 0.0

    def update_price(self, new_price: float) -> None:
        """Set a new current price after validating it.

        Args:
            new_price: The new market price per share.

        Raises:
            ValueError: If ``new_price`` is negative.
        """
        if new_price < 0:
            raise ValueError("Current price must not be negative.")
        self.current_price = float(new_price)

    def as_row(self) -> Dict[str, Union[str, float]]:
        """Return the holding as a flat dict suitable for a CSV row."""
        return {
            "symbol": self.symbol,
            "shares": round(self.shares, 4),
            "buy_price": round(self.buy_price, 2),
            "current_price": round(self.current_price, 2),
            "cost_basis": round(self.cost_basis, 2),
            "market_value": round(self.market_value, 2),
            "gain": round(self.gain, 2),
            "gain_pct": round(self.gain_pct, 2),
        }


@dataclass
class Portfolio:
    """A collection of :class:`Holding` objects with aggregate metrics.

    Args:
        holdings: Optional initial list of holdings.
        prices: Optional symbol → price map used to fill in missing current
            prices when positions are added.
    """

    holdings: List[Holding] = field(default_factory=list)
    prices: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_prices(cls, prices: Dict[str, float]) -> "Portfolio":
        """Create an empty portfolio that knows a price table.

        Args:
            prices: Mapping of ticker symbol to current price.

        Returns:
            A new :class:`Portfolio`.
        """
        return cls(prices=dict(prices))

    # -- mutation --------------------------------------------------------
    def add(
        self,
        symbol: str,
        shares: float,
        buy_price: float,
        current_price: Optional[float] = None,
    ) -> Holding:
        """Add a position, merging with an existing one if the symbol repeats.

        When the symbol already exists, the quantities are combined and the buy
        price is averaged on a cost-weighted basis.

        Args:
            symbol: Ticker symbol.
            shares: Number of shares to add.
            buy_price: Purchase price per share.
            current_price: Current price per share. If omitted, the value is
                looked up in :attr:`prices` (falling back to ``buy_price``).

        Returns:
            The created or updated :class:`Holding`.
        """
        symbol = (symbol or "").strip().upper()
        if current_price is None:
            current_price = self.prices.get(symbol, buy_price)

        existing = self.get(symbol)
        if existing is None:
            holding = Holding(symbol, shares, buy_price, current_price)
            self.holdings.append(holding)
            return holding

        total_shares = existing.shares + shares
        blended_buy = (
            (existing.cost_basis + shares * buy_price) / total_shares if total_shares else buy_price
        )
        existing.shares = total_shares
        existing.buy_price = blended_buy
        existing.update_price(current_price)
        return existing

    def remove(self, symbol: str) -> bool:
        """Remove a position by symbol.

        Args:
            symbol: Ticker symbol to remove (case-insensitive).

        Returns:
            ``True`` if a position was removed, ``False`` if it was not found.
        """
        symbol = (symbol or "").strip().upper()
        before = len(self.holdings)
        self.holdings = [h for h in self.holdings if h.symbol != symbol]
        return len(self.holdings) < before

    def get(self, symbol: str) -> Optional[Holding]:
        """Return the holding for ``symbol`` or ``None`` if absent."""
        symbol = (symbol or "").strip().upper()
        for holding in self.holdings:
            if holding.symbol == symbol:
                return holding
        return None

    # -- aggregates ------------------------------------------------------
    @property
    def total_cost(self) -> float:
        """Sum of cost basis across all holdings."""
        return sum(h.cost_basis for h in self.holdings)

    @property
    def total_value(self) -> float:
        """Sum of market value across all holdings."""
        return sum(h.market_value for h in self.holdings)

    @property
    def total_gain(self) -> float:
        """Absolute portfolio profit or loss."""
        return self.total_value - self.total_cost

    @property
    def total_gain_pct(self) -> float:
        """Portfolio percentage gain/loss (0.0 if no capital invested)."""
        return (self.total_gain / self.total_cost * 100.0) if self.total_cost else 0.0

    def summary(self) -> Dict[str, float]:
        """Return the aggregate metrics as a dictionary."""
        return {
            "total_cost": round(self.total_cost, 2),
            "total_value": round(self.total_value, 2),
            "total_gain": round(self.total_gain, 2),
            "total_gain_pct": round(self.total_gain_pct, 2),
        }

    def rows(self) -> List[Dict[str, Union[str, float]]]:
        """Return every holding as a CSV-ready row."""
        return [h.as_row() for h in self.holdings]

    def unknown_symbols(self, universe: Iterable[str]) -> List[str]:
        """Return held symbols that are absent from ``universe``.

        Useful for validating entered tickers against a reference list such as
        the S&P 500 constituents.

        Args:
            universe: An iterable of known ticker symbols.

        Returns:
            A sorted list of held symbols not present in ``universe``.
        """
        known = {symbol.strip().upper() for symbol in universe}
        return sorted(h.symbol for h in self.holdings if h.symbol not in known)


def _detect_key(headers: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    """Return the first header that matches one of ``candidates`` (case-insensitive)."""
    lowered = {h.lower().strip(): h for h in headers}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def load_prices_from_csv(path: Union[str, Path]) -> Dict[str, float]:
    """Load a symbol → price mapping from a CSV file.

    The file must contain a ticker column (one of ``symbol``/``ticker``/``code``)
    and a price column (one of ``price``/``current_price``/``close``/``last``/
    ``market_price``). Column names are matched case-insensitively.

    Args:
        path: Path to the CSV file.

    Returns:
        A mapping of upper-cased ticker symbol to ``float`` price.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the required columns are missing or a price is not
            numeric.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Price file not found: {csv_path}")

    prices: Dict[str, float] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        symbol_key = _detect_key(headers, _SYMBOL_KEYS)
        price_key = _detect_key(headers, _PRICE_KEYS)
        if symbol_key is None or price_key is None:
            raise ValueError(
                "CSV must contain a ticker column "
                f"({', '.join(_SYMBOL_KEYS)}) and a price column "
                f"({', '.join(_PRICE_KEYS)}). Found: {headers}"
            )
        for line_no, row in enumerate(reader, start=2):
            symbol = (row.get(symbol_key) or "").strip().upper()
            raw_price = (row.get(price_key) or "").strip()
            if not symbol or not raw_price:
                continue
            try:
                prices[symbol] = float(raw_price)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid price {raw_price!r} for {symbol} on line {line_no}."
                ) from exc
    return prices


def load_symbols_from_csv(path: Union[str, Path]) -> Dict[str, str]:
    """Load a ticker → company-name map from a constituents CSV.

    Handles the S&P 500 constituents format (``Symbol`` + ``Security``
    columns) as well as generic CSVs using ``ticker``/``code`` and
    ``name``/``company`` headers. Column matching is case-insensitive.

    Args:
        path: Path to the constituents CSV.

    Returns:
        A mapping of upper-cased ticker symbol to company name (falling back
        to the symbol itself when no name column is present).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If no ticker column can be identified.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Constituents file not found: {csv_path}")

    symbols: Dict[str, str] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        symbol_key = _detect_key(headers, _SYMBOL_KEYS)
        name_key = _detect_key(headers, _NAME_KEYS)
        if symbol_key is None:
            raise ValueError(
                "CSV must contain a ticker column " f"({', '.join(_SYMBOL_KEYS)}). Found: {headers}"
            )
        for row in reader:
            symbol = (row.get(symbol_key) or "").strip().upper()
            if not symbol:
                continue
            name = (row.get(name_key) or "").strip() if name_key else ""
            symbols[symbol] = name or symbol
    return symbols


def write_summary_csv(portfolio: Portfolio, path: Union[str, Path]) -> Path:
    """Persist a portfolio's holdings and totals to a CSV file.

    The file contains one row per holding followed by a ``TOTAL`` row carrying
    the aggregate cost, value, gain, and percentage.

    Args:
        portfolio: The portfolio to persist.
        path: Destination CSV path.

    Returns:
        The resolved destination :class:`~pathlib.Path`.
    """
    out_path = Path(path)
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "symbol",
        "shares",
        "buy_price",
        "current_price",
        "cost_basis",
        "market_value",
        "gain",
        "gain_pct",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in portfolio.rows():
            writer.writerow(row)

        totals = portfolio.summary()
        writer.writerow(
            {
                "symbol": "TOTAL",
                "shares": "",
                "buy_price": "",
                "current_price": "",
                "cost_basis": totals["total_cost"],
                "market_value": totals["total_value"],
                "gain": totals["total_gain"],
                "gain_pct": totals["total_gain_pct"],
            }
        )
    return out_path
