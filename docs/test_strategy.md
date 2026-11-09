# Test strategy — Datheon Python Internship

**Milestone:** Day 21 — *Consolidate Week 3* · **Date:** 2026-11-09
**Companion:** [Week 3 QA report](week3_qa_report.md) · **Dashboard:**
[`tools/qa_report.py`](../tools/qa_report.py)

Week 3 built six quality gates, one per day, each on its own page: the
assertion census (Day 15), the isolation audit (Day 16), property tests
(Day 17), the fuzz corpus (Day 18), the mutation score (Day 19) and the
coverage floor (Day 20). This document is the strategy those six days add up
to — what the suite is *for*, which risk each layer buys down, what it costs,
and what it deliberately does not cover. The numbers it quotes are today's;
they live in the [QA report](week3_qa_report.md), not here.

---

## 1. What is under test

The repository ships five small command-line tools over a shared core. The
test strategy follows the same split the code does:

| Unit | What it is | How it is tested |
| --- | --- | --- |
| **Pure cores** — `hangman`, `portfolio`, `organizer`, `chatbot`, `models`/`inventory` | UI-independent logic: rules, arithmetic, parsing, state machines | Unit tests, property tests, mutation |
| **Shared core** — `validators`, `dataset_loader`, `errors`, `error_catalogue`, `cli_contract`, `persistence`, `caching`, `logging_config`, `structured_logging`, `observability`, `profiling`, `edge_cases` | Contracts every tool depends on: validation, loading, error taxonomy, exit codes, atomic writes | Unit tests, property tests, mutation, loader fuzzing |
| **Interactive shells** — each project's `main.py` | `argparse` wiring, stdin prompts, printing | A handful of subprocess smoke tests; mostly left to coverage |
| **Tooling** — `tools/*.py` | The gates themselves | Unit tests over synthetic reports and parsed output |

The shells are thin on purpose. Day 1's architectural decision — *pure core,
thin shell* — is what makes this strategy affordable: the interesting logic is
importable, so it can be tested directly instead of by driving a terminal.

## 2. The risk model

Tests are bought against risks, not against files. For these five tools the
realistic failure classes are:

| # | Failure class | A concrete example | Caught by |
| --- | --- | --- | --- |
| R1 | **Wrong answer** — bad arithmetic or game rules | A portfolio average cost blended wrongly after a second buy | Unit tests, property tests |
| R2 | **Unvalidated input** — garbage reaching the core | `--price -5`, a symbol with a newline, a 10,000-character message | Unit tests (Day 4), fuzz (`argv`, `text`) |
| R3 | **Hostile data files** — a corrupt download | A constituent CSV with a BOM, CRLF, a truncated last row, an `id` column of floats | Loader tests (Day 6), fuzz (`dataset`, `loader`), property tests |
| R4 | **Unhandled exception** — a traceback instead of a message | An invalid regex in `intents.json` escaping as `re.error` | Fuzz (found two of these), error taxonomy tests (Day 7) |
| R5 | **Data loss on write** — a half-written file | A killed process leaving a truncated `portfolio.json` | Persistence tests (Day 9), round-trip properties |
| R6 | **Silent regression** — the code changes, the tests do not notice | A refactor that drops a `strip()` and nothing goes red | Mutation testing (Day 19) |
| R7 | **Suite rot** — the tests stop being meaningful | Assertions quietly deleted; coverage sliding from 94% to 61% | Assertion census, coverage floor |
| R8 | **Flaky tests** — a suite people learn to re-run | A test that reads the wall clock or `$HOME` | Isolation audit (Day 16), seeded generation |

R6 and R7 are the ones most suites miss. A green build proves the tests ran, not
that they were worth running — which is why Week 3 spent two days (19 and 20) on
the *quality of the tests themselves*.

## 3. The layers

Seven layers, ordered by cost. Each has a floor, and each floor lives in the
module that enforces it, not in this document.

| Layer | Tool | What it proves | Cost | When it runs |
| --- | --- | --- | --- | --- |
| **Static** | `flake8`, `black --check` | The code parses, is consistently formatted, and has no unused names | ~2 s | Every push and PR |
| **Assertions** | `tools/assertion_census.py` | The suite is large enough to be worth trusting (≥ 200 assertions) | < 1 s | Every push and PR |
| **Isolation** | `tools/io_audit.py` | No test imports a network stack, reads the wall clock, or resolves `$HOME` | < 1 s | Every push and PR |
| **Unit + integration** | `pytest` | Behaviour: rules, arithmetic, parsing, error messages, exit codes | ~55 s | Every push and PR |
| **Properties** | Hypothesis (`tests/test_properties.py`) | Invariants hold for *all* inputs, not just the examples someone thought of | ~10 s | Every PR, fixed seed in CI |
| **Fuzz** | `tools/fuzz.py` | Random and malformed input never escapes as an unhandled exception | 3 cases/PR (~2 min for the full corpus) | Smoke on every PR, full corpus weekly |
| **Mutation** | `tools/mutation.py` (mutmut) | The assertions actually *constrain* the code — a changed operator turns the build red | ~2 min | Weekly, and on any PR touching `*.py`, `tests/`, `tools/` |
| **Coverage** | `tools/coverage_gate.py` | Every shipped line is executed by something | ~55 s | Every push and PR |

The shape is not a classic pyramid — it is closer to a *stack of proofs*. Each
layer answers a question the layer below cannot:

- Unit tests say **"this input produces this output"**. Property tests say
  **"every input in this shape produces a valid output"**.
- Fuzzing says **"no input makes it fall over"**. Mutation says **"no input is
  being checked by accident"**.
- Coverage says **"the code ran"**. Mutation says **"the code was *asserted
  on*"**. Coverage is the cheap proxy; mutation is the expensive truth.

### 3.1 Static and formatting

`flake8` at 100 columns with `E203`/`W503` ignored (the two rules that fight
`black`), and `black --check` as the single source of formatting truth. Neither
is a test, but both fail the build, because a repository where `black --check`
is advisory is a repository where every diff carries formatting noise.

### 3.2 Unit and integration tests

The bulk of the suite: one module per unit under test, plus dedicated modules
for cross-cutting concerns (`test_cli_contract.py`, `test_edge_cases.py`,
`test_errors.py`, `test_validation.py`, `test_dataset_loader.py`,
`test_persistence.py`, `test_caching.py`, `test_logging.py`,
`test_structured_logging.py`, `test_observability.py`, `test_profiling.py`).
Integration coverage is deliberately subprocess-shaped: the five entry points
are invoked as real processes so exit codes and stderr text are asserted the
way a user experiences them.

### 3.3 Property-based tests

Hypothesis strategies in `tests/strategies.py` generate symbols, quantities,
prices, paths, letters, dates, CSV rows and message text. The properties that
matter most are the round-trip ones — *write then read returns what you wrote*,
*parse then render is stable* — because they cover the whole input space rather
than a table of examples.

Reproducibility: a fixed seed in CI (`HYPOTHESIS_PROFILE=ci`), and any failure
prints its shrinking example, which becomes a regression test.

### 3.4 Fuzzing

`fuzzing.py` is a deterministic case generator (`seed`, `kind`, `target`,
`count` → cases), so case 7 is case 7 whether the run asked for eight cases or
eight hundred. Four campaigns: `argv` (the CLIs as subprocesses with hostile
argument vectors and closed stdin), `dataset` (byte-mutated copies of the real
datasets through `--validate`), `loader` (the six loader entry points in-process,
so exceptions the CLI would mask stay visible), and `text` (hostile unicode
through the intent engine and the message validator).

Non-destructive by construction: every subprocess runs in a temporary sandbox,
the organizer is always given `--dry-run`, and absolute paths are absent from
the argument pool.

### 3.5 Mutation testing

mutmut mutates `error_catalogue.py` and the suite must kill every mutant. The
score is computed strictly — an interrupted or untested mutant scores `0`, not
`100` — so a half-finished run can never look like a perfect one. The target is
one module on purpose: mutation is the most expensive signal here, and a
score over the module every tool's user-facing error text flows through is worth
more than a shallow score over all of them. Extending the scope is a Day 19
backlog item, not a Day 21 one.

### 3.6 Coverage

Branch coverage over the shipped code only — `tests/`, `tools/` and
`conftest.py` are omitted. Measuring the test files would flatter the number,
because every line of a test that runs is a covered line. `fail_under = 90` in
`pyproject.toml`, `DEFAULT_MINIMUM = 90.0` in the gate, and `--minimum 90` in
CI are the same number, and a test asserts all three agree.

## 4. The floors, and why these numbers

| Floor | Value | Why this value |
| --- | --- | --- |
| Assertions | **200** | The plan's Day 15 budget. A suite under 200 assertions cannot cover five tools' failure modes; above it, the number stops being the constraint and the *shape* of the tests matters more. |
| Coverage | **90%** | High enough that an unexercised branch is unusual, low enough that the remaining 10% is honestly hard (interactive prompts, defensive `except` arms). At 95.8% today the floor leaves ~5 points of headroom, so one badly-covered helper trips the build without every commit tripping it. |
| Mutation | **95%** | Below 100 so a single equivalent mutant does not block a merge, above 90 so the score cannot hide a whole untested branch. Today's measured score is 100%. |
| Isolation violations | **0** | A single `import requests` turns a unit test into an integration test that fails on a plane. There is no acceptable non-zero value. |
| Fuzz crashes | **0** | The contract is "never an unhandled traceback". One crash is a defect, not a budget. |
| Lint / format violations | **0** | Same argument: the tools are deterministic, so any non-zero count is a fixable defect. |

Floors are asserted against their owning modules in
`tests/test_qa_report.py::test_floors_agree_with_the_modules_that_own_them`, so
changing a floor in one place and not the others fails the suite.

## 5. One command, one verdict

Running seven gates in seven commands means the honest answer to "is this
healthy?" is a memory exercise. `tools/qa_report.py` collects one measurement
per layer, compares each against its floor, and prints one table:

```bash
python tools/qa_report.py                 # collect everything, then judge
python tools/qa_report.py --skip-slow     # reuse coverage.json and the mutants cache
python tools/qa_report.py --json          # machine-readable
python tools/qa_report.py --write-json qa_evidence.json   # save the evidence
python tools/qa_report.py --from-json qa_evidence.json    # re-judge offline
```

Exit codes: `0` every layer measured and passing, `1` a measured layer
breached, `2` the report is **incomplete** — a layer could not be measured.

That last code is the strategy's most important rule:

> **An unmeasured layer is not a passing layer.**

A missing `flake8`, an absent mutmut cache, a coverage report that will not
parse — each leaves the layer `unmeasured` and the report exits `2`. Silence is
never reported as health. `--allow-unmeasured` is the explicit opt-out, for
partial runs like `--skip-slow`.

## 6. Determinism and isolation

Three autouse fixtures in `conftest.py` make every test hermetic:

| Fixture | Guarantee |
| --- | --- |
| `isolated_home` | `HOME`, `USERPROFILE`, `TMPDIR`/`TEMP`/`TMP`, `XDG_*` and `DATHEON_LOG_DIR` point into a per-test sandbox |
| `isolated_cwd` | The working directory is a per-test temp directory, so a stray relative write cannot touch the repo |
| `forbid_network` | Every socket entry point raises `RealIOAttempted`, and the attempt is recorded |

Clock isolation is opt-in (`frozen_today`, `install_fake_clock`) rather than
autouse, because a frozen clock only means something relative to the module
under test. `tools/io_audit.py` statically rejects any test that reads the wall
clock directly, which is what keeps that opt-in honest.

Determinism rules for generated data: fixed seeds in CI, and every generator is
a pure function of its seed — no `random.random()` at module import, no time
dependence.

## 7. What we deliberately do not test

| Not tested | Why | What protects it instead |
| --- | --- | --- |
| The interactive `input()` loops in each `main.py` | They are three-line loops over the core; a test would assert on `stdin` plumbing, not behaviour | The core is fully tested; the shells get subprocess smoke tests and coverage |
| Live price lookups (`yfinance`) | Network in a unit test is the exact thing Day 16 forbids | Optional dependency, absent in CI; the offline CSV path is what is tested |
| Real permission errors (root-owned files, `EACCES`) | Not reproducible as a non-root CI user on all three OSes | `edge_cases` injects the exception at the boundary; the handler is tested, not the kernel |
| True concurrency races in the organizer | Racy tests are flaky tests, and flakiness destroys trust in the whole suite | The threaded and async paths are tested for *equivalent results* and ordered reports, not for interleaving |
| Wall-clock durations | A timing assertion fails on a loaded CI runner | `profiling` measures counts and structure; numbers are recorded in docs, not asserted |
| `--per-file-minimum` coverage | Implemented and tested, but at today's numbers it would fail on five `main.py` shells | Left off deliberately; see the QA report's open items |

The rule behind the table: **if a test can fail for a reason unrelated to the
code, it is not a test, it is a coin flip.** Flaky tests are worse than missing
ones, because a missing test is a known gap and a flaky test teaches people to
ignore red.

## 8. Adding a test — checklist

1. **Put it in the module that owns the unit.** Cross-cutting concerns go in
   the matching `test_*.py` (`validation`, `errors`, `persistence`, …), not in a
   project module.
2. **No real I/O.** No sockets, no `Path.home()`, no `datetime.now()` — the
   isolation audit will fail the build, and `conftest.py` will raise at runtime.
   Use `tmp_path`, `frozen_today`, `tests/io_fakes.py`.
3. **Deterministic.** Fixed seeds; no dependence on ordering, locale, or the
   current date.
4. **One behaviour per test, named as a sentence.** `test_negative_quantity_is_rejected`
   beats `test_validation_2`. The name is what a failing CI log shows.
5. **Assert the message, not just the exception.** The Day 19 survivors were
   almost all mutants of error text — the suite checked *that* an error was
   raised, never *what it said*.
6. **Assert the boundary, not the middle.** At-floor values, empty input,
   one-element collections, and the exact exit code.
7. **No assertions on the tool's own numbers.** A test asserting "coverage is
   95%" has to be edited every time an honest test is added; assert the
   *mechanism* instead (see `test_qa_report.py`).
8. **Run the gate, not just pytest.** `flake8 . && black --check . && pytest`
   before pushing.

## 9. Triage order when the build goes red

1. **Static / formatting** — free to fix, and a formatting failure hides
   nothing interesting. Fix first so the log is readable.
2. **Unit tests** — a red unit test is a behaviour regression or a stale
   expectation; decide which before touching either.
3. **Coverage** — new code without tests, or deleted tests. Add tests; never
   lower the floor in the same commit that breaches it.
4. **Isolation audit** — a new test imported something it should not. Fix the
   test, not the audit.
5. **Property tests** — a real counterexample: shrink it, then keep it as a
   regression test.
6. **Fuzz** — a crash is a genuine defect; minimise the case and add it to the
   corpus.
7. **Mutation** — a survivor is a gap in the assertions, not in the code. Write
   the assertion the mutant says is missing.

## 10. Known gaps

Honest debt, carried forward:

- **Mutation scope is one module.** `error_catalogue.py` is fully mutated; the
  loaders and validators are not. Extending it needs a runtime budget, not a
  better argument.
- **`--per-file-minimum` is off.** Five `main.py` shells sit below 90%
  individually while the total is 95.8%. The fix is tests for the shells, not a
  lower threshold.
- **No static type checking in CI.** Annotations are complete on public
  callables; `mypy`/`pyright` would turn that into a check. Backlog item.
- **Windows behaviour is asserted, not executed.** Path handling is tested for
  separators and normalisation; Day 24's multi-OS matrix is what will actually
  run the suite there.
- **Fuzz depth in CI is a smoke test** (3 cases per campaign). The 400-case
  corpus runs weekly. A deeper per-PR corpus is a wall-clock trade nobody has
  needed yet.

These are tracked, not forgotten — every one of them has a day or a backlog
entry, and none of them is papered over by a green dashboard.
