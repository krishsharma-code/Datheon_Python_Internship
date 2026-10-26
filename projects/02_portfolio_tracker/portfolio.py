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
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from dataset_loader import LoadReport, read_text_robust, require_columns
from edge_cases import check_file_size
from errors import ValidationError

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
        ValidationError: If any field fails validation. Also a
            :class:`ValueError`.
    """

    symbol: str
    shares: float
    buy_price: float
    current_price: float = 0.0

    def __post_init__(self) -> None:
        self.symbol = (self.symbol or "").strip().upper()
        if not self.symbol:
            raise ValidationError(
                "Holding symbol must not be empty.",
                hint="use a ticker such as AAPL or RELIANCE.NS",
                context={"field": "symbol"},
            )
        if self.shares <= 0:
            raise ValidationError(
                f"Holding shares must be greater than zero, got {self.shares:g}.",
                hint="use a positive share count",
                context={"field": "shares", "value": self.shares},
            )
        if self.buy_price < 0:
            raise ValidationError(
                f"Buy price must not be negative, got {self.buy_price:g}.",
                hint="use 0 or a positive price",
                context={"field": "buy_price", "value": self.buy_price},
            )
        if self.current_price < 0:
            raise ValidationError(
                f"Current price must not be negative, got {self.current_price:g}.",
                hint="use 0 or a positive price",
                context={"field": "current_price", "value": self.current_price},
            )

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
            ValidationError: If ``new_price`` is negative. Also a
                :class:`ValueError`.
        """
        if new_price < 0:
            raise ValidationError(
                f"Current price must not be negative, got {new_price:g}.",
                hint="use 0 or a positive price",
                context={"field": "current_price", "value": new_price},
            )
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


def load_prices_from_csv(
    path: Union[str, Path],
    *,
    strict: bool = False,
    report: Optional[LoadReport] = None,
) -> Dict[str, float]:
    """Load a symbol → price mapping from a CSV file.

    The file must contain a ticker column (one of ``symbol``/``ticker``/``code``)
    and a price column (one of ``price``/``current_price``/``close``/``last``/
    ``market_price``). Column names are matched case-insensitively. The file is
    decoded through :func:`dataset_loader.read_text_robust`, so a UTF-8 BOM,
    CRLF line endings, and legacy 8-bit encodings are all tolerated.

    A row with no ticker or no price is a *malformed row*: it is skipped and
    recorded as a warning (escalated to an error under ``strict``). A row whose
    price is present but not numeric is always an error — it is never silently
    accepted — and aborts the load with a
    :class:`~dataset_loader.DatasetFormatError` that names the offending line.

    Args:
        path: Path to the CSV file.
        strict: When ``True``, treat a row with a missing ticker or price as an
            error rather than skipping it.
        report: Optional :class:`~dataset_loader.LoadReport` to populate with
            the file's encoding metadata and any malformed-row issues.

    Returns:
        A mapping of upper-cased ticker symbol to ``float`` price.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        PermissionError: If ``path`` cannot be read.
        ValueError: If the required columns are missing, a price is not
            numeric, or ``path`` exceeds
            :data:`edge_cases.MAX_INPUT_FILE_BYTES`.
    """
    csv_path = Path(path)
    check_file_size(csv_path, field="price file")
    source = read_text_robust(csv_path, field="price file")
    local = report if report is not None else LoadReport(csv_path)
    local.source = source

    reader = csv.DictReader(io.StringIO(source.text, newline=""))
    headers = reader.fieldnames or []
    symbol_key = require_columns(headers, _SYMBOL_KEYS, field="ticker column", path=csv_path)
    price_key = require_columns(headers, _PRICE_KEYS, field="price column", path=csv_path)

    raw_lines = source.text.splitlines()
    prices: Dict[str, float] = {}
    for row in reader:
        line_no = reader.line_num
        local.rows_checked += 1
        raw = raw_lines[line_no - 1] if line_no <= len(raw_lines) else ""
        symbol = (row.get(symbol_key) or "").strip().upper()
        raw_price = (row.get(price_key) or "").strip()
        if not symbol:
            local.add(
                "row has no ticker symbol",
                line=line_no,
                raw=raw,
                severity="error" if strict else "warning",
            )
            continue
        if not raw_price:
            local.add(
                f"missing price for {symbol}",
                line=line_no,
                raw=raw,
                severity="error" if strict else "warning",
            )
            continue
        try:
            prices[symbol] = float(raw_price)
        except ValueError:
            local.add(
                f"invalid price {raw_price!r} for {symbol}",
                line=line_no,
                raw=raw,
                severity="error",
            )
            continue
        local.rows_accepted += 1
    local.raise_if_errors("price file")
    return prices


def load_symbols_from_csv(
    path: Union[str, Path],
    *,
    strict: bool = False,
    report: Optional[LoadReport] = None,
) -> Dict[str, str]:
    """Load a ticker → company-name map from a constituents CSV.

    Handles the S&P 500 constituents format (``Symbol`` + ``Security``
    columns) as well as generic CSVs using ``ticker``/``code`` and
    ``name``/``company`` headers. Column matching is case-insensitive. The file
    is decoded through :func:`dataset_loader.read_text_robust`, so a UTF-8 BOM,
    CRLF line endings, and legacy 8-bit encodings are all tolerated.

    A row with no ticker is a *malformed row*: it is skipped and recorded as a
    warning (escalated to an error under ``strict``).

    Args:
        path: Path to the constituents CSV.
        strict: When ``True``, treat a row with a missing ticker as an error
            rather than skipping it.
        report: Optional :class:`~dataset_loader.LoadReport` to populate with
            the file's encoding metadata and any malformed-row issues.

    Returns:
        A mapping of upper-cased ticker symbol to company name (falling back
        to the symbol itself when no name column is present).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        PermissionError: If ``path`` cannot be read.
        ValueError: If no ticker column can be identified, or ``path`` exceeds
            :data:`edge_cases.MAX_INPUT_FILE_BYTES`.
    """
    csv_path = Path(path)
    check_file_size(csv_path, field="constituents file")
    source = read_text_robust(csv_path, field="constituents file")
    local = report if report is not None else LoadReport(csv_path)
    local.source = source

    reader = csv.DictReader(io.StringIO(source.text, newline=""))
    headers = reader.fieldnames or []
    symbol_key = require_columns(headers, _SYMBOL_KEYS, field="ticker column", path=csv_path)
    name_key = _detect_key(headers, _NAME_KEYS)

    raw_lines = source.text.splitlines()
    symbols: Dict[str, str] = {}
    for row in reader:
        line_no = reader.line_num
        local.rows_checked += 1
        raw = raw_lines[line_no - 1] if line_no <= len(raw_lines) else ""
        symbol = (row.get(symbol_key) or "").strip().upper()
        if not symbol:
            local.add(
                "row has no ticker symbol",
                line=line_no,
                raw=raw,
                severity="error" if strict else "warning",
            )
            continue
        name = (row.get(name_key) or "").strip() if name_key else ""
        symbols[symbol] = name or symbol
        local.rows_accepted += 1
    local.raise_if_errors("constituents file")
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
