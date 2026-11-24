# Portfolio tracker

Values a share portfolio against a CSV of prices and writes a summary you can
diff: per-holding gain, invested capital, market value, net P/L and return.

```bash
portfolio --demo
```

## The session

Four positions — AAPL, MSFT, NVDA and RELIANCE.NS — valued against the bundled
`data/sample_prices.csv`, captured from a real run.

The session behind the valuation above is committed, not typed: [`assets/demos/portfolio.json`](../assets/demos/portfolio.json) is the exact file the player fetches, and `python tools/live_demos.py --check` fails if this page and that file ever disagree.

<!-- live-demo:begin -->
<div class="demo-terminal" data-session="assets/demos/portfolio.json" data-command="portfolio --demo">
<pre class="demo-terminal__fallback" aria-label="Captured session: The sample portfolio, valued">$ portfolio --demo
SYMBOL           SHARES       BUY       NOW        VALUE    GAIN%
-----------------------------------------------------------------
AAPL              10.00    150.00    189.45     1,894.50   26.30%
MSFT               5.00    300.00    412.30     2,061.50   37.43%
NVDA               8.00    420.00    495.75     3,966.00   18.04%
RELIANCE.NS       12.00   2400.00   2465.75    29,589.00    2.74%
-----------------------------------------------------------------
Invested                                       35,160.00
Market value                                   37,511.00
Net P/L                                         2,351.00
Return                                             6.69%

Summary written to portfolio_summary.csv</pre>
</div>
<!-- live-demo:end -->

## What to look at

- **The table is the report.** Every column is derived, not stored: value is
  shares × price, gain % is measured against the buy price, and the totals are
  summed from the rows rather than tracked separately — so the footer cannot
  disagree with the body.
- **Money is formatted for the reader.** Thousands separators, two decimals,
  right-aligned columns, and a fixed width that survives a narrow terminal.
- **The summary file is written atomically.** `portfolio_summary.csv` appears
  complete or not at all: it is staged in a temporary file and published with
  `os.replace`, so an interrupted run never leaves a half-written CSV behind.
- **An unknown ticker is flagged, not fatal.** With `--constituents`, a symbol
  that is not in the S&P 500 list is reported against the row it came from; the
  run still values everything else.
- **A missing price file degrades to buy prices** with one warning line, unless
  `--strict` is passed — in which case it is a single error and exit code `1`.

## Run it yourself

```bash
portfolio --demo                                   # the sample portfolio
portfolio --prices data/sample_prices.csv          # an explicit price table
portfolio --prices prices.csv --constituents data/constituents.csv
portfolio --validate --prices data/sample_prices.csv
portfolio --json                                   # one machine-readable document
portfolio --strict                                 # a missing file is an error
```

From a checkout: `python projects/02_portfolio_tracker/main.py --demo`.

Prices come from a CSV so the tool stays standard-library only. Live quotes are
opt-in: `pip install yfinance` and the tracker will use them when present, which
is why nothing in the package depends on it.

## How it is built

| Module | What lives there |
| --- | --- |
| `portfolio.py` | The parsing, the valuation, the CSV summary — no printing |
| `main.py` | The CLI, the table renderer, the exit codes |

Two shared layers do the boring parts: `dataset_loader` handles the CSV schema,
BOM/CRLF tolerance and malformed-row reporting with line numbers, and `caching`
keys the constituents table by file signature so a second run does not re-parse
it. The summary is written through `persistence.atomic_write_json`/`_text`.

## Under the gates

- `tests/test_portfolio.py` — 25 tests: valuation arithmetic, missing columns,
  malformed rows, unknown tickers, the summary round trip.
- Property tests in `tests/test_properties.py` assert the invariants that matter
  (value ≥ 0 for a positive price, the total equals the sum of the rows) over
  generated portfolios.
- The atomic write is covered by `tests/test_persistence.py`, including a write
  killed between staging and publishing.

See [persistence](../persistence.md), [caching](../caching.md) and the
[API reference](../api/projects.md).
