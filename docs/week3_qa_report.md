# Week 3 QA report — testing depth & quality gates

**Date:** 2026-11-09 · **Milestone:** Day 21 — *Consolidate Week 3*
**Scope:** Days 15–20 — assertion census, I/O isolation, property tests, fuzz
corpus, mutation score, coverage floor
**New module:** [`tools/qa_report.py`](../tools/qa_report.py) · **Tests:**
[`tests/test_qa_report.py`](../tests/test_qa_report.py) (38 functions / 55 cases, 89 assertions)
**Companion:** [Test strategy](test_strategy.md) ·
**Suite:** 1,089 tests green · `flake8 .` clean · `black --check .` clean

Week 3 was six days of separate gates, each documented on its own page. This
report is the join: what the week measured, what it found, what is still open,
and the one command that reproduces every number below.

---

## 1. Verdict

`python tools/qa_report.py` on `main` at the start of this milestone:

```
Quality report
  generated : 2026-11-09

  layer       metric        measured  floor    verdict
  ----------  ----------------------  -------------------
  static      violations           0  <= 0     pass
  assertions  assertions        1659  >= 200   pass
  isolation   violations           0  <= 0     pass
  properties  failures             0  <= 0     pass
  fuzz        crashes              0  <= 0     pass
  mutation    score          100.00%  >= 95%   pass
  coverage    percent         95.76%  >= 90%   pass


PASS — every layer was measured and every floor was met.
```

Seven layers, seven passes, exit code `0`. Every figure is reproducible with
the commands in [§7](#7-reproducing-every-figure); none is quoted from memory.

| Layer | Measurement | Floor | Margin |
| --- | --- | --- | --- |
| Static | 0 flake8 violations, 0 black reformats over 69 files | 0 | at the floor |
| Assertions | 1,659 assertions in 868 test functions across 26 modules | 200 | **8.3×** the budget |
| Isolation | 0 network imports, 0 wall-clock reads, 0 `$HOME` lookups | 0 | at the floor |
| Properties | 31 Hypothesis tests, 0 failures | 0 | at the floor |
| Fuzz | 48 cases per smoke run, 0 crashes; 400-case corpus weekly | 0 | at the floor |
| Mutation | 182 mutants, 182 killed, 0 survived, 0 untested | 95% | **+5.0 pts** |
| Coverage | 3,292 statements / 766 branches, 95.76% | 90% | **+5.8 pts** |

## 2. What the week bought

| Day | Gate | Before | After |
| --- | --- | --- | --- |
| 15 | Assertion census (`tools/assertion_census.py`) | No number; the 200+ target lived in a plan document | 644 functions / 1,221 assertions, counted by `ast` on every run |
| 16 | Isolation audit (`tools/io_audit.py`) | "No test touches the network" was a claim | 0 violations, enforced statically *and* by autouse fixtures |
| 17 | Property tests (`tests/test_properties.py`) | Examples only | 31 properties over validators, loaders, keyword extraction |
| 18 | Fuzz corpus (`tools/fuzz.py`, `fuzzing.py`) | Happy paths and known edges | 16 targets × 400 cases, deterministic, 2 real defects found |
| 19 | Mutation score (`tools/mutation.py`) | Trust in the assertions | 89.9% → **100.0%** on `error_catalogue.py` |
| 20 | Coverage floor (`tools/coverage_gate.py`) | A printed number, exit `0` regardless | 90% floor, exit `1` on breach, exit `2` when unmeasurable |

### 2.1 Suite growth

| Point in Week 3 | Test functions | Assertions |
| --- | --- | --- |
| End of Day 15 (baseline for the week) | 644 | 1,221 |
| End of Day 17 (property tests) | 719 | 1,352 |
| End of Day 18 (fuzz tests) | 755 | 1,422 |
| End of Day 19 (mutation-driven tests) | 787 | 1,497 |
| End of Day 20 (coverage-gate tests) | 830 | 1,570 |
| **End of Day 21 (this report)** | **868** | **1,659** |

Day 21 adds 38 test functions and 89 assertions — all of them for the QA
dashboard itself, which is the pattern the week settled into: *a gate ships
with the tests that prove the gate fails when it should.*

The Day 20 figure is worth reading twice. The census at the Day 20 merge
reported 830 / 1,570; the 38 functions and 89 assertions above are this
milestone's own, which is why the dashboard reports 1,659 rather than the
1,570 the Day 20 log quotes.

## 3. What the gates found

A quality programme that reports "all green" and no defects has usually not
looked hard enough. Week 3 found seven things.

### D1 — An invalid regex in a dataset escaped as a bare `re.error` (Day 18, fixed)

Fuzzing a byte-mutated `intents.json` produced the pattern `(unclosed`.
`Intent.from_dict` compiled it without a guard, and because `re.error` is not a
`ValueError`, the exception travelled past `_parse_intents`' handler, past
`run_cli`, and out to the terminal as a traceback. A bad dataset should be
reported in the taxonomy's language, not in Python's.

**Fix:** `Intent.from_dict` compiles inside a guard and raises `ValidationError`
naming the intent, the pattern and a hint; `_parse_intents` records it as a row
error with its `intents[i]` index. Pinned by regression tests.

### D2 — A non-integer `priority` raised an uncaught `TypeError` (Day 18, fixed)

Same campaign, same loader: `int()` on a JSON string raised a `TypeError` that
no handler caught. **Fix:** validated at parse time, reported as a row error.

### D3 — 39 mutants survived the Day 19 baseline (Day 19, fixed)

The first mutation run scored **89.9%**: 143 killed, 16 survived, 23 untested.
The survivors were almost entirely *error-message* mutants — the suite asserted
*that* an error was raised and never *what it said*, so blanking the message,
dropping the hint, or returning the wrong class changed nothing red. Twelve new
tests killed all 39; the score is now **100.0%** (182/182).

This is the single most useful finding of the week: it is exactly the class of
regression a green suite hides, and no amount of additional coverage would have
surfaced it.

### D4 — Five `main.py` shells sit below the per-file coverage floor (Day 20, open)

At 95.76% overall, the interactive entry points are individually thin:

| File | Statements | Coverage |
| --- | --- | --- |
| `projects/01_hangman_game/main.py` | 138 | 76.5% |
| `projects/02_portfolio_tracker/main.py` | 128 | 85.6% |
| `projects/04_terminal_assistant/main.py` | 92 | 87.3% |
| `projects/03_file_organizer_automation/main.py` | 81 | 88.8% |
| `profiling.py` | 192 | 91.8% |

`--per-file-minimum` is implemented and tested but deliberately **not** switched
on in CI. The honest fix is tests for the shells (or a decision that the shells
are covered by subprocess smoke tests and exempt), not a lower threshold. Open
item **O1**.

### D5 — Mutation scope is one module (Day 19, open)

`error_catalogue.py` is fully mutated; the loaders and validators — where the
Day 18 defects actually lived — are not. Extending the scope is a wall-clock
budget question, not an argument. Open item **O2**.

### D6 — The evidence for six gates was scattered across six commands (Day 21, fixed)

Every gate exited correctly, but "is the repository healthy?" required running
all six and remembering the numbers. Nothing aggregated them, so a gate that
was silently not running — a renamed CI step, a skipped job — would leave no
trace in any single output.

**Fix:** `tools/qa_report.py`, which collects one measurement per layer,
compares each against the floor that layer's module owns, and prints one
verdict. Covered by 55 test cases over 38 functions.

### D7 — Two generated artefacts were not ignored (Day 21, fixed)

`coverage.json` (written by the Day 20 gate) and the QA evidence file were
untracked build output with no `.gitignore` entry, so a `git add -A` could
commit a stale report as if it were source. Both are now ignored, alongside the
existing `mutants/` entry.

## 4. What the numbers mean, and what they do not

Three readings of the same green dashboard are worth keeping straight.

**1,659 assertions is a size, not a quality.** The census counts `assert`
statements; it cannot tell a real assertion from `assert True`. That is why the
mutation layer exists — it is the only one that measures whether the assertions
*constrain* anything. The two numbers belong together, and neither is a target
to optimise in isolation.

**95.76% coverage with 115 unexecuted statements is the honest figure.** The
remaining lines are interactive prompts, defensive `except` arms that require a
kernel-level failure, and `if __name__ == "__main__":` guards. Raising it by
executing the prompts would buy assertions about `input()` plumbing, not
behaviour. The floor is 90% precisely so this headroom exists and does not have
to be defended every commit.

**100% mutation on one module is not 100% mutation on the repository.** It says
the suite has teeth *where the user-facing error text lives*, which is where the
taxonomy's contract is defined. It says nothing about the loaders' row parsing.

## 5. Open items carried into Week 4

| # | Item | Why it matters | Proposed day |
| --- | --- | --- | --- |
| O1 | Enable `--per-file-minimum` (or exempt the shells explicitly) | Five modules are under the per-file floor; the total hides them | Day 22–23 |
| O2 | Extend mutation scope beyond `error_catalogue.py` | The Day 18 defects were in a loader, which is not mutated | Backlog (§5 of the plan) |
| O3 | Static type checking in CI (`mypy`/`pyright`) | Annotations are complete on public callables but unchecked | Backlog |
| O4 | Deepen the per-PR fuzz corpus above 3 cases | CI's smoke run would not have found D1 on its own; the weekly 400-case run did | Backlog |
| O5 | Run the suite on Windows and macOS for real | Path handling is asserted, not executed | Day 24 |
| O6 | Wire the QA dashboard into CI as a non-blocking report job | It is the one gate that is still manual | Day 22 |

None of these is papered over by a green dashboard. Each has a day or a backlog
entry.

## 6. What this milestone adds

- **`tools/qa_report.py`** — the seven-layer dashboard. Pure judgement
  (`build_report`) separated from collection (`collect_*`), so the verdicts are
  testable without running a six-minute campaign. Exit codes `0` / `1` / `2`,
  matching the Day 20 convention: *an unmeasured layer is not a passing layer*.
- **`tests/test_qa_report.py`** — 55 tests / 89 assertions: every layer's
  breach, every layer's absence, the inclusive-floor boundary, non-numeric
  measurements, the `--skip-slow` trade, JSON round-trips, unreadable evidence
  files, and the three exit codes.
- **`docs/test_strategy.md`** — the strategy the six gates add up to: risk
  model, layer costs, floor rationale, the isolation contract, what is
  deliberately not tested, the add-a-test checklist, and the triage order for a
  red build.
- **`docs/week3_qa_report.md`** — this report.
- **`.gitignore`** — `coverage.json` and `qa_evidence.json`.

The module deliberately re-imports its floors from `assertion_census`,
`mutation` and `coverage_gate` rather than retyping them, and a test asserts the
four copies agree. A dashboard that disagrees with the gates it reports on is
worse than no dashboard.

## 7. Reproducing every figure

```bash
# The whole dashboard (collect + judge), ~3 minutes
python tools/qa_report.py

# The same verdict, faster: reuse coverage.json and the mutmut cache
python tools/qa_report.py --skip-slow

# The individual gates, in the order they should be triaged
flake8 . && black --check .
python tools/assertion_census.py
python tools/io_audit.py --strict
pytest -q
HYPOTHESIS_PROFILE=ci pytest -q tests/test_properties.py
python tools/fuzz.py --cases 3 --seed 18
python tools/mutation.py
python tools/coverage_gate.py --minimum 90

# Re-judge a saved run offline
python tools/qa_report.py --write-json qa_evidence.json
python tools/qa_report.py --from-json qa_evidence.json
```

Two notes on reproducibility. The mutation layer reads mutmut's on-disk cache,
which is gitignored — a fresh clone reports that layer **unmeasured** and the
dashboard exits `2` until `python tools/mutation.py` has run once. That is the
intended behaviour, not a defect: an absent cache is a run that never happened,
not a perfect score. And the fuzz layer's default corpus is 3 cases per campaign
so the dashboard stays usable per-PR; the 400-case corpus is the weekly run.
