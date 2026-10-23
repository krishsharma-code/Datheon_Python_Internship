# Datheon Python Internship — 4-Week Engineering Progression Plan

**Repository:** `krishsharma-code/Datheon_Python_Internship`
**Credential ID:** DT-2026-O6Q0X · **Intern:** Krish Sharma
**Program:** Python Programming Internship — Batch 01 · 2026 Cohort
**Plan window:** Day 3 → Day 28

> This file is the **single source of truth** for the daily scheduled run.
> The 09:00 IST cron job reads the Progress Ledger below, executes the earliest
> `pending` milestone, and marks it `done` in the same pull request. Do not
> renumber or reorder rows — the ledger order *is* the execution order.

---

## 1. Progress Ledger (authoritative)

Status values: `pending` · `in-progress` · `done` · `blocked`.

| Day | Target date | Theme | Branch | Deliverable | Status | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-10-21 | Scaffold & 5 core projects | `feat/datheon-day-1-scaffold` | Repo, README, LICENSE, 5 projects, 131 tests, CI | done | #1 |
| 2 | 2026-10-21 | Official datasets integration | `feat/datheon-day-2-datasets` | Word list, S&P 500 CSV, FAQ dataset, 153 tests | done | #2 |
| 3 | 2026-10-22 | CLI ergonomics & exit-code contract | `feat/datheon-day-3-cli-contract` | Consistent argparse, `--version`, documented exit codes across all 5 CLIs | done | #4 |
| 4 | 2026-10-23 | Input validation layer | `feat/datheon-day-4-validation` | Strict validators + `--strict` mode with actionable error messages | done | #5 |
| 5 | 2026-10-24 | Edge-case handler sweep | `feat/datheon-day-5-edge-cases` | Every project's failure modes handled + regression tests | pending | — |
| 6 | 2026-10-25 | Dataset loader hardening | `feat/datheon-day-6-loader-hardening` | Schema validation, BOM/CRLF/encoding tolerance, malformed-row reporting | pending | — |
| 7 | 2026-10-26 | Week 1 consolidation | `feat/datheon-day-7-error-taxonomy` | Custom exception taxonomy + user-facing error catalogue + docs | pending | — |
| 8 | 2026-10-27 | Logging architecture | `feat/datheon-day-8-logging` | `--log-level`, console + rotating file handlers wired into all projects | pending | — |
| 9 | 2026-10-28 | Atomic & versioned persistence | `feat/datheon-day-9-persistence` | Temp-file + `os.replace` writes, schema version field, round-trip tests | pending | — |
| 10 | 2026-10-29 | Structured logging | `feat/datheon-day-10-structured-logs` | JSON log records, run IDs, durations | pending | — |
| 11 | 2026-10-30 | Async I/O | `feat/datheon-day-11-async-io` | File organizer on `asyncio` with bounded concurrency; sync API preserved | pending | — |
| 12 | 2026-10-31 | Performance profiling | `feat/datheon-day-12-profiling` | `cProfile` hot-path analysis + optimisations with before/after numbers | pending | — |
| 13 | 2026-11-01 | Caching & memoisation | `feat/datheon-day-13-caching` | Cached dataset loads (word list, constituents) with invalidation | pending | — |
| 14 | 2026-11-02 | Week 2 consolidation | `feat/datheon-day-14-perf-report` | Performance & observability report + docs | pending | — |
| 15 | 2026-11-03 | Test expansion | `feat/datheon-day-15-test-expansion` | Suite crosses **200+ assertions** | pending | — |
| 16 | 2026-11-04 | Mocking external IO | `feat/datheon-day-16-mocking` | Filesystem/clock/network mocked; no real IO in unit tests | pending | — |
| 17 | 2026-11-05 | Property-based testing | `feat/datheon-day-17-property-tests` | Hypothesis strategies for validators, parsers, loaders | pending | — |
| 18 | 2026-11-06 | Fuzz testing | `feat/datheon-day-18-fuzzing` | Random/malformed input to CLIs, loaders, chatbot — crash-free | pending | — |
| 19 | 2026-11-07 | Mutation testing | `feat/datheon-day-19-mutation` | `mutmut` run; surviving mutants killed | pending | — |
| 20 | 2026-11-08 | Coverage gate | `feat/datheon-day-20-coverage-gate` | CI fails below 90% line coverage | pending | — |
| 21 | 2026-11-09 | Week 3 consolidation | `feat/datheon-day-21-test-strategy` | Test strategy document + QA report | pending | — |
| 22 | 2026-11-10 | Packaging & entrypoints | `feat/datheon-day-22-packaging` | `pyproject.toml` console scripts for all 5 tools; `pip install -e .` works | pending | — |
| 23 | 2026-11-11 | Build & distribution | `feat/datheon-day-23-distribution` | Wheel + sdist, clean-venv install test, `twine check` | pending | — |
| 24 | 2026-11-12 | CI multi-OS matrix | `feat/datheon-day-24-ci-matrix` | ubuntu/macos/windows × Python 3.10–3.13 | pending | — |
| 25 | 2026-11-13 | Documentation generation | `feat/datheon-day-25-docs-site` | MkDocs + API reference, deployed to GitHub Pages | pending | — |
| 26 | 2026-11-14 | Release automation | `feat/datheon-day-26-release` | Versioning, CHANGELOG, git tag, GitHub Release | pending | — |
| 27 | 2026-11-15 | Submission polish | `feat/datheon-day-27-polish` | README final pass, architecture diagram, demo assets | pending | — |
| 28 | 2026-11-16 | Final verification & submission | `feat/datheon-day-28-final` | Full green run, repo freeze, certificate checklist, final post | pending | — |

---

## 2. Week-by-week milestone detail

### Week 1 — Robustness, CLI parsing & data validation (Days 3–7)

| Day | Objective | Key work | Definition of done |
| --- | --- | --- | --- |
| 3 | Make every tool behave predictably from the shell | One argparse convention across all five entry points; add `--version`; standardise exit codes (`0` success, `1` user/data error, `2` usage error); consistent help text | `--help` and `--version` work in all 5 projects; a test asserts each exit code |
| 4 | Reject bad input early and clearly | Strict validators for symbols, quantities, prices, paths, letters, dates; `--strict` mode; actionable error strings | Invalid input produces a single clear message and exit code 1; tests per validator |
| 5 | Close every known failure mode | Empty directories, permission errors, name collisions, missing/oversized files, interrupted input (EOF/Ctrl-C), broken pipes | Each case has a regression test and no unhandled traceback |
| 6 | Harden the three dataset loaders | Schema validation, BOM/CRLF tolerance, encoding fallbacks, malformed-row reporting with line numbers, `--validate` mode | Corrupt fixtures are detected and reported, never silently accepted |
| 7 | Unify error handling | Exception taxonomy (`DatheonError` base → per-domain subclasses); user-facing error catalogue in docs | All raised errors derive from the base; catalogue documented |

### Week 2 — Logging, persistence, async & performance (Days 8–14)

| Day | Objective | Key work | Definition of done |
| --- | --- | --- | --- |
| 8 | Observability foundation | Shared logging setup: `--log-level`, console + rotating file handler, consistent format | `--log-level DEBUG` writes a log file in every project |
| 9 | Safe, versioned persistence | Atomic writes (temp file + `os.replace`), schema version field, read-back validation | Round-trip tests; a killed write never corrupts the target file |
| 10 | Structured logs | JSON log records with run ID, project, duration, outcome | Log lines parse as JSON; run IDs correlate start/end |
| 11 | Non-blocking file operations | Port the organizer's moves to `asyncio` with a bounded semaphore; keep the sync API | Same results as the threaded version; benchmark recorded |
| 12 | Find and fix the hot spots | `cProfile` on the organizer, loaders, and matcher; optimise the top 3 costs | Before/after timings in `docs/`; no behaviour change |
| 13 | Stop reloading static data | Cache the word list, constituents table, and compiled intent regexes with explicit invalidation | Cache hit/miss tests; measurable load-time drop |
| 14 | Consolidate Week 2 | Performance and observability report; document the logging contract | Report committed; daily log updated |

### Week 3 — Testing depth & quality gates (Days 15–21)

| Day | Objective | Key work | Definition of done |
| --- | --- | --- | --- |
| 15 | Broaden the suite | Add cases until the suite crosses **200+ assertions** | `pytest` reports 200+ assertions; all green |
| 16 | Isolate the unit under test | `monkeypatch`/`unittest.mock` for filesystem, clock, and network | No unit test touches the real network or user filesystem |
| 17 | Prove properties, not examples | Hypothesis strategies for validators, CSV/JSON loaders, keyword extraction | Property tests run in CI with a fixed seed |
| 18 | Break it on purpose | Fuzz the CLIs, loaders, and chatbot with random and malformed input | Zero unhandled exceptions across the corpus |
| 19 | Verify the tests actually test | `mutmut` mutation run; kill surviving mutants in core modules | Mutation score reported and improved |
| 20 | Lock in a quality floor | `--cov-fail-under=90` wired into CI | CI fails on a deliberate coverage drop |
| 21 | Consolidate Week 3 | Test strategy document + QA report | Committed; daily log updated |

### Week 4 — Packaging, CI/CD, docs & submission (Days 22–28)

| Day | Objective | Key work | Definition of done |
| --- | --- | --- | --- |
| 22 | Make it installable | `pyproject.toml` (PEP 621) with a `console_scripts` entry point per tool; package layout finalised | `pip install -e .` exposes `hangman`, `portfolio`, `organizer`, `assistant`, `inventory` |
| 23 | Prove it ships | Build wheel + sdist; install into a clean venv and run each entry point; `twine check` | Built artefacts install and run from a clean environment |
| 24 | Test where users actually are | CI matrix: ubuntu-latest / macos-latest / windows-latest × Python 3.10, 3.11, 3.12, 3.13 | All matrix cells green; pip cache enabled |
| 25 | Publish the docs | MkDocs (Material) site with API reference from docstrings, deployed to GitHub Pages | Live docs URL linked from the README |
| 26 | Cut a release | SemVer bump, `CHANGELOG.md`, annotated git tag, GitHub Release with artefacts | Release visible on the repo with attached wheel/sdist |
| 27 | Make the submission shine | README final pass, architecture diagram, demo GIF/screenshots, licence review | README reads well cold; assets embedded |
| 28 | Verify and submit | Full local + CI green run, repository freeze, certificate checklist, final summary | Checklist complete; submission package ready |

---

## 3. Daily scheduled-run execution contract

The cron job **"Datheon internship daily progress"** fires at **09:00 IST** and executes exactly one milestone per run, in this order:

1. **Read this file.** Parse the Progress Ledger in §1.
2. **Select the milestone:** the lowest-numbered row whose `Status` is `pending`.
   That row — and only that row — is the day's work. Its `Branch` and
   `Deliverable` columns are the mandate.
3. **Idempotency:** a day counts as complete only when its row reads `done` and carries a PR number. If a run fails partway, the row stays `pending` and the next run retries it. **No day can be executed twice**, and no day can be skipped.
4. **Catch-up rule:** the `Target date` column is advisory. If a run was missed or the calendar has drifted, the run still picks the earliest `pending` row — it never jumps ahead. The ledger is authoritative; the calendar is a target.
5. **Never idle:** if every row is `done`, switch to maintenance mode and take the highest-value item from the Backlog (§5), or ship a reliability/polish improvement. A run must always end with a merged PR.
6. **Per-run procedure:**
   1. Inspect `projects/`, `tests/`, `docs/` and open issues to avoid duplicate work.
   2. Create the branch named in the ledger row.
   3. Implement the row's `Deliverable`, with type hints and docstrings.
   4. Add or extend tests for the change.
   5. Verify locally: `python -m compileall`, `flake8 .`, `black --check .`, `pytest`.
   6. Update **this ledger** (mark the row `done`, record the PR number) and append a dated entry to `docs/daily_learning_log.md` — in the *same* PR.
   7. Open a PR targeting `main`.
   8. Submit a formal review (`COMMENT` event — GitHub blocks self-approval).
   9. Squash-merge with `feat(internship): complete Day <N> tasks (#<PR>)`.
   10. Delete the branch and report the PR link, review ID, and merge commit URL.
7. **Hard rules:** never push directly to `main`; never commit secrets or tokens; never run destructive commands; keep every run non-destructive.

---

## 4. Final submission deliverables checklist

Required to qualify for the **Datheon Verified Certificate of Completion**
(Credential ID `DT-2026-O6Q0X`).

### Repository
- [ ] Public repository named exactly **`Datheon_Python_Internship`** on GitHub.
- [ ] **Minimum 2–3 completed task-pack tasks** — this repo implements **all 5**.
- [ ] `README.md` with installation, architecture, and quickstart commands.
- [ ] `LICENSE` (MIT, 2026 Krish Sharma), `CONTRIBUTING.md`, strict `.gitignore`.
- [ ] Full docstrings and type hints on all public callables.

### Quality
- [ ] Test suite green — target **200+ assertions**, ≥ **90%** coverage.
- [ ] CI pipeline green across the multi-OS / multi-Python matrix.
- [ ] Linting clean (`flake8`) and formatting clean (`black --check`).
- [ ] No secrets, tokens, or credentials anywhere in history.

### Delivery
- [ ] Installable package with console entry points (Day 22).
- [ ] Tagged release with `CHANGELOG.md` and attached build artefacts (Day 26).
- [ ] Generated documentation site (Day 25), linked from the README.

### Proof of work (Datheon requirements)
- [ ] **LinkedIn walkthrough post tagging @Datheon** — done on 2026-10-21 ✅
      (offer announcement; see `Datheon_LinkedIn_Post.md`).
- [ ] Optional: short **video walkthrough** of one project for the LinkedIn post.
- [ ] **Final LinkedIn post** (Option C) published once the internship completes.
- [ ] Credential ID **DT-2026-O6Q0X** verified on the Datheon portal
      (`https://datheon.in/verify?id=DT-2026-O6Q0X`).
- [ ] Repository link submitted through the **Datheon Student Workspace**.

### Certification
- [ ] Code review completed on the submitted work.
- [ ] Certificate issued — Datheon's FAQ states certificates are reviewed and issued within **3–5 business days after the code review**.
- [ ] Verified certificate received: QR-coded credential + unique verification hash.
- [ ] Letter of Recommendation (LOR) — considered for top-performing candidates.

---

## 5. Backlog / stretch (maintenance mode)

Used only when every ledger row is `done`, or to fill spare capacity:

- Add a `--json` output mode to every CLI for machine-readable results.
- Ship a GitHub Actions workflow that publishes coverage to Codecov.
- Add a benchmark harness with `pytest-benchmark` and a regression budget.
- Add Docker packaging for the organizer as a cron-friendly container.
- Add a `pre-commit` configuration (black, flake8, end-of-file-fixer).
- Add type checking (`mypy`/`pyright`) to CI once annotations are complete.
- Add a `SECURITY.md` and a `CODE_OF_CONDUCT.md`.
- Record a 2-minute demo video for the LinkedIn walkthrough.
