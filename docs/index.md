# Datheon Python Internship

**Five production-grade Python tools, built in four weeks.**

[![CI](https://github.com/krishsharma-code/Datheon_Python_Internship/actions/workflows/ci.yml/badge.svg)](https://github.com/krishsharma-code/Datheon_Python_Internship/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-3da639)](https://github.com/krishsharma-code/Datheon_Python_Internship/blob/main/LICENSE)

This is the engineering portfolio submitted as proof of work for the **Datheon
Python Programming Internship** (Batch 01 · 2026 Cohort, Credential ID
`DT-2026-O6Q0X`). It contains five independent tools — a Hangman game, a
portfolio tracker, a file organizer, a terminal assistant and an inventory
system — plus the shared engineering core they all run on: one CLI contract,
one validation layer, one exception taxonomy, one logging setup, one persistence
format, one test strategy.

Every tool is standard-library only at runtime. Nothing here needs a network
connection, an API key or a database.

---

## Quickstart

```bash
git clone https://github.com/krishsharma-code/Datheon_Python_Internship.git
cd Datheon_Python_Internship

python -m pip install -e .          # installs five console scripts
hangman --version                   # 1.0.0
portfolio --help
```

| Command | What it does |
| --- | --- |
| `hangman` | Word-guessing game with a curated word list and difficulty levels |
| `portfolio` | Stock portfolio tracker: holdings, cost basis, P/L, CSV summary |
| `organizer` | Rule-based file organizer with dry-run, collisions and undo |
| `assistant` | Regex/intent-driven terminal assistant with a JSONL transcript |
| `inventory` | Strict OOP inventory with ABCs, dataclasses and domain errors |

Each command also runs straight from the source tree, with the same arguments
and the same exit codes:

```bash
python projects/01_hangman_game/main.py --version
```

---

## Engineering standard

The five tools are the visible part. The interesting part is what every one of
them shares:

- **One command-line contract** — the same `argparse` conventions, `--version`,
  and the exit codes `0` success / `1` user or data error / `2` usage error in
  all five tools. See [CLI contract & exit codes](cli_contract.md).
- **Strict input validation** — bad input is rejected early with one actionable
  message, never a traceback.
- **A single exception taxonomy** — everything raised derives from
  `DatheonError`, and every error code has a user-facing explanation in the
  [error catalogue](error_catalogue.md).
- **Structured observability** — `--log-level`, rotating file handlers and JSON
  records carrying a run ID, a duration and an outcome. See
  [logging](logging.md).
- **Atomic, versioned persistence** — a temp file plus `os.replace`, with a
  schema version on every document, so a killed write never corrupts a target.
  See [persistence](persistence.md).
- **Non-blocking file operations** — the organizer's moves run on `asyncio`
  behind a bounded semaphore, with the synchronous API preserved. See
  [async I/O](async_io.md).

---

## Quality gates

The repository does not claim quality; it measures it, and the measurement is
what CI enforces.

| Gate | Threshold | Where it lives |
| --- | --- | --- |
| Test suite | 1,385 passing tests, 0 failures | `pytest` |
| Assertions | 2,098 (floor: 200) | `tools/assertion_census.py` |
| Line coverage | 95.8% (floor: 90%) | `tools/coverage_gate.py` |
| Mutation score | 100% of 182 mutants killed | `tools/mutation.py` |
| Test isolation | no real filesystem, clock or network I/O | `tools/io_audit.py` |
| CI matrix | 12 cells: 3 operating systems × Python 3.10–3.13 | `tools/ci_matrix.py` |
| Distribution | wheel + sdist, `twine check`, clean-venv install | `tools/distribution.py` |
| Docs site | every page in the nav, every module documented | `tools/docs_site.py` |

Read the reasoning behind each one in the [test strategy](test_strategy.md) and
the [Week 3 QA report](week3_qa_report.md).

---

## Repository map

```text
Datheon_Python_Internship/
├── datheon/                  # importable shim layer behind the console scripts
├── projects/                 # the five tools, in task-pack order
│   ├── 01_hangman_game/
│   ├── 02_portfolio_tracker/
│   ├── 03_file_organizer_automation/
│   ├── 04_terminal_assistant/
│   └── 05_inventory_oop/
├── docs/                     # engineering guides (the pages of this site)
├── tests/                    # 1,385 tests
├── tools/                    # quality gates, each with a documented exit code
├── mkdocs.yml                # this site
└── pyproject.toml            # PEP 621 metadata + five console scripts
```

---

## Where to go next

- New here? Start with the [CLI contract](cli_contract.md), then
  [packaging](packaging.md).
- Reading the code? The [API reference](api/index.md) is generated from the
  docstrings in `errors.py`, `validators.py`, `persistence.py` and the other
  core modules — it cannot drift from the source.
- Interested in the process? The [daily learning log](daily_learning_log.md)
  records every decision, every defect found and every measurement taken, day by
  day, and the [milestones plan](internship_milestones_plan.md) is the ledger
  those entries close.

---

*Verify this credential at <https://datheon.in/verify?id=DT-2026-O6Q0X>.*
