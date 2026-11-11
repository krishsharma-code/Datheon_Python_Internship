<div align="center">

# 🐍 Datheon Python Internship

**Python Programming Internship — Batch 01 · 2026 Cohort**

[![Datheon Verified](https://img.shields.io/badge/Datheon-Verified%20Credential-1f6feb?style=flat-square)](https://datheon.in/verify?id=DT-2026-O6Q0X)
[![Offer ID](https://img.shields.io/badge/Offer%20ID-DT--2026--O6Q0X-6f42c1?style=flat-square)](https://datheon.in/verify?id=DT-2026-O6Q0X)
[![CI](https://github.com/krishsharma-code/Datheon_Python_Internship/actions/workflows/ci.yml/badge.svg)](https://github.com/krishsharma-code/Datheon_Python_Internship/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000?style=flat-square)](https://github.com/psf/black)
[![License: MIT](https://img.shields.io/badge/License-MIT-3da639?style=flat-square)](LICENSE)

*Production-ready Python engineering portfolio submitted as proof of work for the
Datheon Python Programming Internship (Credential ID `DT-2026-O6Q0X`).*

</div>

---

## 📌 Program Credential

| Field | Value |
| --- | --- |
| Candidate | **Krish Sharma** |
| Program | Python Programming Internship (Batch 01 · 2026 Cohort) |
| Application / Credential ID | **DT-2026-O6Q0X** |
| Internal Reference | DT/HR-INT/2026/O6Q0X |
| Duration | 4 Weeks (1-Month Virtual Industrial Program) |
| Commencement | 19 October 2026 · 100% Remote |
| Issuer | Datheon — AI Systems & Automation Studio |

**Verify this credential →** <https://datheon.in/verify?id=DT-2026-O6Q0X>

---

## 🎯 Overview

This repository contains five independent, self-contained Python projects that
together demonstrate the core competencies of the Datheon internship syllabus:
control flow and string manipulation, file I/O and persistence, OS-level
automation, regular-expression driven natural-language handling, and strict
object-oriented architecture.

Every project is built to the same engineering standard:

- **Modular architecture** — a pure, UI-independent core module plus a thin
  interactive `main.py` entry point.
- **Full type hints** on public callables.
- **Google-style docstrings** on every public module, class, and function.
- **Unit tests** with `pytest`, covering the happy path *and* edge cases
  (invalid input, missing files, duplicate operations, collisions).
- **CI automation** — linting and the full test suite run on every pull request.

---

## 🗂️ Repository Structure

```text
Datheon_Python_Internship/
├── .github/
│   └── workflows/
│       └── ci.yml                  # flake8 + black + pytest on every PR
├── docs/
│   ├── daily_learning_log.md       # architectural decisions & complexity notes
│   ├── cli_contract.md             # shared command-line & exit-code contract
│   ├── logging.md                  # logging contract (--log-level, --json-logs)
│   ├── persistence.md              # atomic, versioned write contract
│   ├── caching.md                  # dataset cache + invalidation contract
│   ├── mocking.md                  # external-I/O isolation policy for tests
│   ├── property_testing.md         # Hypothesis strategies & profiles (Day 17)
│   ├── fuzzing.md                  # hostile-input campaign & crash verdicts (Day 18)
│   ├── packaging.md                # install layout & console entry points (Day 22)
│   ├── distribution.md             # wheel/sdist build, verify & clean-install gate (Day 23)
│   ├── week2_performance_observability_report.md  # Week 2 consolidation
│   └── error_catalogue.md          # generated catalogue of every error code
├── projects/
│   ├── 01_hangman_game/            # Task 1 — console Hangman game
│   ├── 02_portfolio_tracker/       # Task 2 — stock portfolio tracker
│   ├── 03_file_organizer_automation/  # Task 3 — file organizer & cleanup
│   ├── 04_terminal_assistant/      # Task 4 — rule-based terminal chatbot
│   └── 05_inventory_oop/           # Task 5 — OOP inventory & asset system
├── datheon/                        # importable shim layer (Day 22)
│   └── cli/                        # one console-script shim per tool
├── tests/                          # pytest suite for all five projects
├── cli_contract.py                 # shared argparse / --version / exit codes
├── errors.py                       # exception taxonomy (DatheonError root)
├── error_catalogue.py              # generates docs/error_catalogue.md
├── logging_config.py               # shared --log-level / rotating file logging
├── structured_logging.py           # JSON records, run IDs, durations, outcomes
├── persistence.py                  # atomic writes + schema_version stamping
├── caching.py                      # bounded dataset cache with signature invalidation
├── fuzzing.py                      # deterministic fuzz corpus + crash verdicts
├── tools/                          # repository tooling (census, IO audit, fuzz driver,
│                                   #   coverage gate, mutation score, distribution gate)
├── observability.py                # reduces JSON logs to run/outcome/duration figures
├── conftest.py                     # makes each project importable by pytest
├── requirements.txt
├── pyproject.toml                  # PEP 621 metadata, console scripts, black + flake8
├── CONTRIBUTING.md
├── LICENSE
└── README.md
```

> **Note on numbering:** the projects follow the ordering of the official
> Datheon task pack (Tasks 1–5), so `03_file_organizer_automation` sits between
> the portfolio tracker and the chatbot.

---

## 🚀 Quickstart

```bash
# 1. Clone
git clone https://github.com/krishsharma-code/Datheon_Python_Internship.git
cd Datheon_Python_Internship

# 2. Create an isolated environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install development dependencies
pip install -r requirements.txt

# 4. Run the test suite
pytest -q

# 5. Lint
flake8 .
black --check .
```

### Install the commands

```bash
pip install -e .            # or: pip install -e ".[dev]" for the test tooling
```

That puts five commands on your `PATH`, each equivalent to the matching
`python projects/…/main.py` invocation — same parser, same exit codes, same
logging. The wiring lives in [`datheon/cli/`](datheon/cli/); how and why is
documented in [`docs/packaging.md`](docs/packaging.md).

```console
$ hangman --version
hangman 1.0.0
$ organizer ~/Downloads --dry-run
$ inventory
```

### Build the distributable artefacts

```bash
python tools/distribution.py     # wheel + sdist, verified, twine-checked, clean-install tested
```

The gate builds both artefacts, compares their contents with `pyproject.toml`
*and* the source tree (every console script, core module and bundled dataset),
validates the metadata with `twine check`, then installs the wheel into a
throwaway virtualenv and runs all five commands from an unrelated directory.
It exits `1` on a mismatch and `2` when it cannot reach a verdict. See
[`docs/distribution.md`](docs/distribution.md).

### Running a project

Every project also runs standalone from a checkout, with no third-party runtime
dependencies (standard library only):

```bash
python projects/01_hangman_game/main.py
python projects/02_portfolio_tracker/main.py
python projects/03_file_organizer_automation/main.py --help
python projects/04_terminal_assistant/main.py
python projects/05_inventory_oop/main.py
```

### Command-line contract

Every tool shares one consistent command-line surface, implemented once in
[`cli_contract.py`](cli_contract.py):

```bash
# Identical across all five tools
python projects/01_hangman_game/main.py --help
python projects/01_hangman_game/main.py --version      # hangman 1.0.0
```

| Exit code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | User or data error (missing file, invalid input) |
| `2` | Usage error (unknown flag, missing value, bad type) |

The full contract is documented in [`docs/cli_contract.md`](docs/cli_contract.md)
and verified by `tests/test_cli_contract.py`.

Every error a tool raises on purpose derives from `DatheonError` in
[`errors.py`](errors.py), carries a stable code (`DT-1001`, `DT-2101`, …) and a
"what to do next" hint, and is still catchable as the builtin it replaces —
`ValidationError` is a `ValueError`, `MissingFileError` is a
`FileNotFoundError`, and so on. `run_cli` prints the rendered form:

```text
! [DT-1001] symbol: must not be empty
  hint: e.g. AAPL
```

The full list — code, class, meaning and resolution for each — is the
generated [`docs/error_catalogue.md`](docs/error_catalogue.md), rebuilt from the
class metadata by `python error_catalogue.py`.

### Logging

All five tools share one opt-in logging setup from
[`logging_config.py`](logging_config.py). Nothing is written to disk unless you
ask for it:

```bash
# Console (stderr) + rotating file at ~/.datheon/logs/hangman.log
python projects/01_hangman_game/main.py --log-level DEBUG

# Pick the destination explicitly, or move every tool's log dir at once
python projects/02_portfolio_tracker/main.py --demo --log-level INFO --log-file /tmp/p.log
DATHEON_LOG_DIR=/var/log/datheon python projects/03_file_organizer_automation/main.py . --log-level INFO
```

`--log-level` accepts `DEBUG`, `INFO`, `WARNING`, `ERROR` and `CRITICAL`; the
file rotates at 1 MiB and keeps three generations.

Add `--json-logs` and every record becomes one JSON object carrying a `run_id`,
the `project`, the elapsed `duration_ms` and the run's `outcome` — so a cron
job's logs can be queried instead of read:

```bash
DATHEON_RUN_ID=cron-20261029 python projects/03_file_organizer_automation/main.py . \
  --log-level INFO --json-logs
jq -c 'select(.run_outcome == "error")' ~/.datheon/logs/organizer.log
```

The contract — formats, record schema, per-tool log points, and failure
behaviour — is documented in [`docs/logging.md`](docs/logging.md) and verified
by `tests/test_logging.py` and `tests/test_structured_logging.py`.

### Reading the logs back

[`observability.py`](observability.py) turns a directory of those JSON logs into
the figures an operator wants — runs per project, outcomes, p50/p95/worst
durations, error codes by frequency, and the live dataset-cache counters:

```bash
python observability.py --log-dir ~/.datheon/logs
python observability.py --log-dir ~/.datheon/logs --json --no-caches
```

It is read-only, counts a malformed line instead of failing on it, and treats a
run that never finished as `incomplete` rather than losing it. The consolidated
Week 2 performance and observability report is
[`docs/week2_performance_observability_report.md`](docs/week2_performance_observability_report.md),
verified by `tests/test_observability.py`.

### Safe, versioned writes

Everything the tools save — the portfolio summary CSV, the organizer's JSON
activity log, the assistant's conversation log — is published by
[`persistence.py`](persistence.py) instead of a plain `open(path, "w")`:

```python
from persistence import atomic_write_json, read_json_document

atomic_write_json("run.json", report.to_dict())   # temp file + os.replace
read_json_document("run.json", field="activity log")
# {'schema_version': 1, 'summary': {...}, 'records': [...]}
```

The content is written to a temporary file in the destination directory,
`fsync`ed, then moved into place with a single `os.replace`, so an interrupted
run leaves the previous file byte-for-byte intact instead of a truncated one.
Every JSON document carries a `schema_version`, and the reader rejects a
missing, malformed, newer or unsupported version rather than guessing. The full
contract is in [`docs/persistence.md`](docs/persistence.md), verified by
`tests/test_persistence.py`.

### Non-blocking file moves

The organizer's moves run on `asyncio` with a hard ceiling on how many files are
in flight, while `FileOrganizer.organize(...)` keeps its blocking signature:

```python
from organizer import FileOrganizer                  # unchanged, blocking
report = FileOrganizer(max_workers=8).organize("~/Downloads")

import asyncio
from async_organizer import AsyncFileOrganizer        # native coroutine API
report = asyncio.run(AsyncFileOrganizer(concurrency=8).organize("~/Downloads"))
```

Concurrency is bounded by a worker-pool queue rather than a semaphore, so no more
than `concurrency` moves are ever in flight, and a per-category destination
reserver keeps two concurrent workers from claiming the same free filename. The
engine, its benchmark numbers and the collision argument are in
[`docs/async_io.md`](docs/async_io.md), verified by
`tests/test_async_organizer.py`.

### Hermetic tests (no real I/O)

No unit test touches the network, the wall clock, or anything outside the pytest
sandbox. Three autouse fixtures redirect `HOME`, the working directory and the
log directory, block every socket entry point, and the shared doubles in
`tests/io_fakes.py` freeze time on demand:

```python
from tests.io_fakes import FakeClock, FakeFileSystem, install_fake_clock

clock = install_fake_clock(monkeypatch, structured_logging)   # frozen clock
run = structured_logging.begin_run("organizer", run_id="run-fixed")
clock.advance(0.25)                                            # 250 ms later
assert run.elapsed_ms() == 250.0
```

The policy is enforced statically as well — `python tools/io_audit.py --strict`
parses every test module and fails on a stray `import socket`, a
`datetime.now()` call or a `Path.home()` lookup. CI runs it before `pytest`. See
[`docs/mocking.md`](docs/mocking.md), verified by `tests/test_mocking.py` and
`tests/test_io_audit.py`.

### Hostile input (fuzzing)

Every CLI, loader and the assistant's intent engine are also driven with input
nobody would type on purpose — truncated UTF-8, NUL bytes, flags that do not
exist, JSON that opens and never closes — and none of it may escape as a
traceback:

```bash
python tools/fuzz.py                      # 400 cases across 16 targets, seed 18
python tools/fuzz.py --cases 100 --seed 7 # a deeper, different run
python tools/fuzz.py --kind loader --json # one campaign, machine-readable
```

The corpus is a pure function of `(kind, target, seed, count)`, so a failure
reproduces from one integer. The exit code is the verdict: `0` clean, `1` a
crash, `2` misuse. The same corpus is asserted by `tests/test_fuzzing.py`, and
the campaign is documented — including the two real defects it found in the
intent loader — in [`docs/fuzzing.md`](docs/fuzzing.md).

---

## 🧩 Projects

### Task 1 — Hangman Word Guessing Game (Console)
`projects/01_hangman_game/`

Interactive console Hangman with seven progressive ASCII gallows stages,
duplicate-guess protection (a repeated letter never costs an attempt), input
validation, per-round and cumulative score tracking, and a replay loop. Words
come from the official English word list bundled at `data/words.txt` by default
(10,000 most-common words, filtered to 4+ letters), with `--categories` to
switch to the smaller themed word banks. The engine (`hangman.py`) is decoupled
from the shell (`main.py`) so it can be unit tested.

### Task 2 — Stock Portfolio Performance Tracker
`projects/02_portfolio_tracker/`

Tracks positions in a dictionary-backed portfolio, computes per-holding and
aggregate cost basis, market value, and percentage gain/loss, prints a formatted
report, and persists a `portfolio_summary.csv`. Prices load from a bundled
sample CSV, with an optional live `yfinance` lookup when available. The official
S&P 500 constituents dataset is bundled at `data/constituents.csv` and can be
used via `--constituents` to flag tickers that are not in the reference list.

### Task 3 — Automated File Organizer & Cleanup Utility
`projects/03_file_organizer_automation/`

Organizes a cluttered directory into category subfolders (`Documents`,
`Images`, `Videos`, `Audio`, `Archives`, `Code`, `Other`), moving files on an
`asyncio` engine with a **bounded** number of moves in flight
(`--concurrency`, default 8), collision-safe renaming, SHA-256 duplicate
detection, a `--dry-run` preview mode, and a JSON activity log.

### Task 4 — Rule-Based Interactive Terminal Assistant
`projects/04_terminal_assistant/`

A conversational assistant with regex-driven multi-intent recognition, a
confidence-scored matcher, graceful fallbacks, clean exit commands
(`bye` / `exit` / `quit`), and persistent JSONL conversation logging. Two
datasets are supported: the hand-written `intents.json` and the official
Datheon FAQ dataset `sample_faq.json` (loaded with `--faq`), whose questions are
converted into keyword-driven intents automatically.

### Task 5 — OOP Inventory & Asset System
`projects/05_inventory_oop/`

A strict object-oriented library-asset system: an abstract `LibraryItem` base
class (`abc`), `Book` / `Magazine` / `DigitalMedia` subclasses demonstrating
inheritance and polymorphism, encapsulated `__price` / `__borrow_date`
attributes behind `@property` setters with validation, dunder methods
(`__str__`, `__repr__`, `__len__`, `__eq__`), and a `InventoryManager` that
handles checkout, returns, and tabular audit reports.

---

## 📊 Bundled Datasets

All data ships with the repository so every project runs fully offline.

| File | Source | Used by |
| --- | --- | --- |
| `projects/01_hangman_game/data/words.txt` | `first20hours/google-10000-english` (no-swears) | Task 1 — secret word pool |
| `projects/02_portfolio_tracker/data/sample_prices.csv` | Hand-authored sample | Task 2 — offline price table |
| `projects/02_portfolio_tracker/data/constituents.csv` | `datasets/s-and-p-500-companies` | Task 2 — ticker validation |
| `projects/04_terminal_assistant/sample_faq.json` | Datheon sample FAQ dataset | Task 4 — FAQ intents |

## 🔀 Git Workflow

Development follows a strict **Branch → Pull Request → Code Review → Squash
Merge** lifecycle. Direct pushes to `main` are never made.

```text
main ──●───────────────●──────────────●──▶
        \             / \            /
         ●─●─●─●─●─●─●   ●─●─●─●─●─●      feat/datheon-day-N-<topic>
         (PR #1)          (PR #2)
```

1. Branch: `feat/datheon-day-<N>-<topic>` or `refactor/internship-<task>`
2. Commit with Conventional Commits
3. Open a Pull Request targeting `main`
4. Automated review + CI must pass
5. Squash-merge, then delete the branch

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full checklist.

---

## 📈 Daily Progress

Day-by-day engineering notes — architectural decisions, complexity analysis, and
milestones — are recorded in
[`docs/daily_learning_log.md`](docs/daily_learning_log.md).

The full 4-week engineering progression plan (Day 3 → Day 28) — including the
Progress Ledger that the daily scheduled run executes against — lives in
[`docs/internship_milestones_plan.md`](docs/internship_milestones_plan.md).

---

## 📄 License

Released under the [MIT License](LICENSE) © 2026 Krish Sharma.

<div align="center"><sub>Built with Python 🐍 · Datheon 2026 Engineering Cohort</sub></div>
