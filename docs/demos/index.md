# Live demos

Five command-line tools, each replayed below from a **real captured session** —
the same `main.py` a shell user runs, with real stdin, in a scratch directory.
Nothing on these pages is a mock-up or a screenshot.

Each page has a terminal you can play, pause, step through and re-run at 1×, 2×
or 4×. The transcript is also printed as plain text inside every page, so the
demos work without JavaScript, in a feed reader, and for a screen reader.

These pages are published with the rest of the site at
**<https://krishsharma-code.github.io/Datheon_Python_Internship/demos/>** — that
is the link to share, and every tool page below has its own URL under it. To run
the site locally instead:

```bash
pip install -e ".[docs]"
mkdocs serve          # -> http://127.0.0.1:8000/demos/
```

- **[Hangman](hangman.md)** — `hangman --categories`. A full round played from
  stdin: the ASCII gallows, the letter board, the score and the play-again
  prompt.
- **[Portfolio tracker](portfolio.md)** — `portfolio --demo`. Four positions
  valued against a CSV of prices, with per-holding gain, net P/L and the summary
  file it writes.
- **[File organizer](organizer.md)** — `organizer ~/datheon-demo --dry-run
  --duplicates`. A messy folder sorted into categories by the asyncio engine,
  with SHA-256 duplicate detection — in dry-run, so nothing moves.
- **[Terminal assistant](assistant.md)** — `assistant --no-log`. Three turns
  against the rule-based intent matcher, each reply carrying the intent it
  matched and its confidence.
- **[Inventory (OOP)](inventory.md)** — `inventory`. The object-oriented
  walkthrough: checkouts, returns, rejected operations, revenue, the audit table
  and the dunder methods.

## How these pages stay true

A demo page that quietly drifts away from the tool it documents is worse than no
demo, so the pages are generated from the tools themselves:

```bash
python tools/live_demos.py --capture   # re-run the five tools, rewrite the pages
python tools/live_demos.py --check     # pages, sessions, player and nav agree
python tools/live_demos.py --verify    # re-run each tool, confirm the session still holds
```

- **Captured, not typed.** `--capture` runs each command line with real stdin and
  writes `docs/assets/demos/<tool>.json`. The page block between
  `<!-- live-demo:begin -->` and `<!-- live-demo:end -->` is rendered from that
  JSON, and `--check` compares the two byte for byte.
- **Machine-specific lines are scrubbed.** Absolute paths and measured durations
  are replaced with stable forms (`Organising: ~/datheon-demo`,
  `Duration  : 2.4 ms`), and `--check` fails if a host path ever reaches a
  published page.
- **Offline by design.** The player is two local files —
  [`terminal-player.js`](../javascripts/terminal-player.js) and
  [`live-demos.css`](../stylesheets/live-demos.css). No CDN, no third-party
  script, no build step.
- **Staleness is caught.** `--verify` re-runs each tool and fails if a marker
  line the session promises no longer appears, which is what turns "the docs
  said so" into "the tool still does it".

## Run them for yourself

```bash
pip install datheon-python-internship   # installs all five console scripts
hangman --categories
portfolio --demo
organizer ~/Downloads --dry-run --duplicates
assistant
inventory
```

Or from a checkout, without installing anything — the tools import only the
standard library:

```bash
python projects/01_hangman_game/main.py --categories
python projects/02_portfolio_tracker/main.py --demo
python projects/03_file_organizer_automation/main.py ~/Downloads --dry-run
python projects/04_terminal_assistant/main.py
python projects/05_inventory_oop/main.py
```

Every tool also speaks JSON (`--json`) for scripting, honours a shared exit-code
contract (`0` success, `1` data error, `2` usage error), and writes an optional
rotating log. The contract is documented once in
[CLI contract & exit codes](../cli_contract.md).
