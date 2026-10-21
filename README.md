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
│   └── daily_learning_log.md       # architectural decisions & complexity notes
├── projects/
│   ├── 01_hangman_game/            # Task 1 — console Hangman game
│   ├── 02_portfolio_tracker/       # Task 2 — stock portfolio tracker
│   ├── 03_file_organizer_automation/  # Task 3 — file organizer & cleanup
│   ├── 04_terminal_assistant/      # Task 4 — rule-based terminal chatbot
│   └── 05_inventory_oop/           # Task 5 — OOP inventory & asset system
├── tests/                          # pytest suite for all five projects
├── conftest.py                     # makes each project importable by pytest
├── requirements.txt
├── pyproject.toml                  # black + flake8 configuration
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

### Running a project

Every project runs standalone with no third-party runtime dependencies
(standard library only):

```bash
python projects/01_hangman_game/main.py
python projects/02_portfolio_tracker/main.py
python projects/03_file_organizer_automation/main.py --help
python projects/04_terminal_assistant/main.py
python projects/05_inventory_oop/main.py
```

---

## 🧩 Projects

### Task 1 — Hangman Word Guessing Game (Console)
`projects/01_hangman_game/`

Interactive console Hangman with category-based word banks, seven progressive
ASCII gallows stages, duplicate-guess protection (a repeated letter never costs
an attempt), input validation, per-round and cumulative score tracking, and a
replay loop. The engine (`hangman.py`) is decoupled from the shell (`main.py`)
so it can be unit tested.

### Task 2 — Stock Portfolio Performance Tracker
`projects/02_portfolio_tracker/`

Tracks positions in a dictionary-backed portfolio, computes per-holding and
aggregate cost basis, market value, and percentage gain/loss, prints a formatted
report, and persists a `portfolio_summary.csv`. Prices load from a bundled
sample CSV, with an optional live `yfinance` lookup when available.

### Task 3 — Automated File Organizer & Cleanup Utility
`projects/03_file_organizer_automation/`

Organizes a cluttered directory into category subfolders (`Documents`,
`Images`, `Videos`, `Audio`, `Archives`, `Code`, `Other`), using a
`ThreadPoolExecutor` for concurrent moves, collision-safe renaming, SHA-256
duplicate detection, a `--dry-run` preview mode, and a JSON activity log.

### Task 4 — Rule-Based Interactive Terminal Assistant
`projects/04_terminal_assistant/`

A conversational assistant with regex-driven multi-intent recognition, a
confidence-scored matcher, graceful fallbacks, clean exit commands
(`bye` / `exit` / `quit`), and persistent JSONL conversation logging.

### Task 5 — OOP Inventory & Asset System
`projects/05_inventory_oop/`

A strict object-oriented library-asset system: an abstract `LibraryItem` base
class (`abc`), `Book` / `Magazine` / `DigitalMedia` subclasses demonstrating
inheritance and polymorphism, encapsulated `__price` / `__borrow_date`
attributes behind `@property` setters with validation, dunder methods
(`__str__`, `__repr__`, `__len__`, `__eq__`), and a `InventoryManager` that
handles checkout, returns, and tabular audit reports.

---

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

---

## 📄 License

Released under the [MIT License](LICENSE) © 2026 Krish Sharma.

<div align="center"><sub>Built with Python 🐍 · Datheon 2026 Engineering Cohort</sub></div>
