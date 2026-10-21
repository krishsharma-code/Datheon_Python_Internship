"""Tests for the portfolio valuation engine and CSV helpers."""

from __future__ import annotations

import csv

import pytest

from portfolio import (
    Holding,
    Portfolio,
    load_prices_from_csv,
    write_summary_csv,
)


# --- Holding -----------------------------------------------------------
def test_holding_metrics():
    holding = Holding("aapl", 10, 100.0, 150.0)
    assert holding.symbol == "AAPL"
    assert holding.cost_basis == 1000.0
    assert holding.market_value == 1500.0
    assert holding.gain == 500.0
    assert holding.gain_pct == pytest.approx(50.0)


def test_holding_negative_gain():
    holding = Holding("TSLA", 2, 200.0, 150.0)
    assert holding.gain == -100.0
    assert holding.gain_pct == pytest.approx(-25.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": "", "shares": 1, "buy_price": 10},
        {"symbol": "AAPL", "shares": 0, "buy_price": 10},
        {"symbol": "AAPL", "shares": -1, "buy_price": 10},
        {"symbol": "AAPL", "shares": 1, "buy_price": -1},
        {"symbol": "AAPL", "shares": 1, "buy_price": 10, "current_price": -5},
    ],
)
def test_holding_validation(kwargs):
    with pytest.raises(ValueError):
        Holding(**kwargs)


def test_update_price_validates():
    holding = Holding("AAPL", 1, 10.0)
    holding.update_price(20.0)
    assert holding.current_price == 20.0
    with pytest.raises(ValueError):
        holding.update_price(-1)


# --- Portfolio ---------------------------------------------------------
def test_add_and_aggregate():
    pf = Portfolio.from_prices({"AAPL": 150.0})
    pf.add("AAPL", 10, 100.0)
    assert len(pf.holdings) == 1
    assert pf.total_cost == 1000.0
    assert pf.total_value == 1500.0
    assert pf.total_gain == 500.0
    assert pf.total_gain_pct == pytest.approx(50.0)


def test_add_merges_existing_symbol_with_weighted_buy_price():
    pf = Portfolio.from_prices({"AAPL": 150.0})
    pf.add("AAPL", 10, 100.0)
    pf.add("AAPL", 10, 200.0)
    assert len(pf.holdings) == 1
    holding = pf.get("AAPL")
    assert holding.shares == 20
    assert holding.buy_price == pytest.approx(150.0)


def test_add_uses_price_table_when_current_missing():
    pf = Portfolio.from_prices({"MSFT": 400.0})
    holding = pf.add("MSFT", 1, 300.0)
    assert holding.current_price == 400.0


def test_remove_and_get():
    pf = Portfolio.from_prices({})
    pf.add("AAPL", 1, 100.0, 100.0)
    assert pf.remove("aapl") is True
    assert pf.get("AAPL") is None
    assert pf.remove("AAPL") is False


def test_empty_portfolio_totals():
    pf = Portfolio()
    assert pf.total_cost == 0
    assert pf.total_gain_pct == 0.0


# --- CSV loading -------------------------------------------------------
def test_load_prices_valid(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("Symbol,Price\nAAPL,150.5\nmsft,400\n", encoding="utf-8")
    prices = load_prices_from_csv(path)
    assert prices == {"AAPL": 150.5, "MSFT": 400.0}


def test_load_prices_alternate_headers(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("ticker,close\nAAPL,150\n", encoding="utf-8")
    assert load_prices_from_csv(path) == {"AAPL": 150.0}


def test_load_prices_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prices_from_csv(tmp_path / "nope.csv")


def test_load_prices_missing_columns(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("name,value\nfoo,bar\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_prices_from_csv(path)


def test_load_prices_bad_number(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Symbol,Price\nAAPL,not-a-number\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_prices_from_csv(path)


# --- CSV writing -------------------------------------------------------
def test_write_summary_csv_contains_total_row(tmp_path):
    pf = Portfolio.from_prices({"AAPL": 150.0})
    pf.add("AAPL", 10, 100.0)
    out = write_summary_csv(pf, tmp_path / "summary.csv")
    assert out.exists()

    with out.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["symbol"] == "AAPL"
    assert rows[-1]["symbol"] == "TOTAL"
    assert float(rows[-1]["market_value"]) == pytest.approx(1500.0)


def test_write_summary_creates_parent_dirs(tmp_path):
    pf = Portfolio()
    out = write_summary_csv(pf, tmp_path / "nested" / "summary.csv")
    assert out.exists()
