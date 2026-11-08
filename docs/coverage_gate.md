# Coverage gate — the 90% floor, enforced (Day 20)

**Tool:** `tools/coverage_gate.py` · **CI step:** *Run test suite under the coverage gate*
**Floor:** 90% · **Measured scope:** the five projects + the shared core modules

Day 19 answered the expensive question — *do the tests actually test?* — with
mutation testing. Day 20 answers the cheap one that has to be asked on every
commit: *is the code still being executed at all?*

`pytest-cov` prints a number and exits `0`. That makes coverage a
**measurement**, not a **verdict**: the suite can slide from 96% to 61% across a
month of hurried commits and every build stays green. This day converts the
measurement into a verdict.

---

## 1. What the gate measures

`[tool.coverage.run]` in `pyproject.toml` decides the scope, so the gate, a
bare `pytest --cov`, and any editor integration all measure the same thing:

```toml
[tool.coverage.run]
branch = true
source = ["."]
omit = ["tests/*", "tools/*", "conftest.py", "*/__pycache__/*"]
```

Two deliberate choices:

* **Branch coverage, not just line coverage.** `if item % 2:` being executed
  proves nothing about the `else` it guards. Branch coverage counts the two
  edges separately, which is why the numbers here are lower — and more honest —
  than the line-only figures quoted in earlier days.
* **`tests/` is omitted.** Every line of a test that runs is a covered line, so
  including the suite flatters the total: measuring the whole repository
  reports 98%, of which the test files are the bulk. The gate watches the code
  that ships, not the code that checks it.

Current scope: **3,292 statements and 766 branches** across 20 modules.

## 2. How the verdict is reached

| Layer | Where the floor lives | What it does |
| --- | --- | --- |
| Library | `DEFAULT_MINIMUM = 90.0` in `tools/coverage_gate.py` | the default floor |
| Config | `[tool.coverage.report] fail_under = 90` | a bare `pytest --cov` also fails |
| CI | `--minimum 90` in `ci.yml` | the explicit, auditable invocation |

Three copies is two too many in principle, so `tests/test_coverage_gate.py`
asserts all three agree: a floor edited in one place and forgotten in another
fails the suite.

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | coverage is at or above the floor |
| `1` | the floor was breached — the build must fail |
| `2` | **no verdict**: pytest failed, or no readable report exists |

`2` is the important one. A gate that cannot measure must never report success;
a broken build that silently passes its quality gate is worse than no gate.

### Two enforcers, one number — and why the gate silences the other

`fail_under` makes `pytest-cov` itself exit `1` on a breach. When the gate runs
the suite it passes `--cov-fail-under=0` so that pytest's exit code means only
"tests passed or failed". Otherwise a coverage breach would arrive as a generic
non-zero pytest exit and be reported as `2` ("cannot measure") instead of `1`
("floor breached at 86.01%, here are the laggards"). The bare `pytest --cov`
run keeps the configured floor for anyone not going through the tool.

## 3. Usage

```bash
python tools/coverage_gate.py                       # run pytest, then gate at 90%
python tools/coverage_gate.py --minimum 92          # stricter floor
python tools/coverage_gate.py --report-only         # gate the existing coverage.json
python tools/coverage_gate.py --from-json cov.json  # gate a saved report
python tools/coverage_gate.py --json                # machine-readable verdict
python tools/coverage_gate.py --per-file-minimum 80 # no module below 80%
python tools/coverage_gate.py --no-missing          # totals only, no file table
python tools/coverage_gate.py --pytest-arg=-x       # pass an argument to pytest
```

`--report-only` and `--from-json` exist for the same reason `--report-only`
exists in `tools/mutation.py`: the verdict on a past run must be reproducible
offline, and the pytest suite must be able to test the gate without spawning a
second pytest.

## 4. Evidence that the gate bites

The definition of done for Day 20 is *"CI fails on a deliberate coverage
drop"*, so it was measured rather than assumed. A temporary module of 20
uncovered functions (260 statements, 200 branches) was dropped into
`projects/99_scratch/` and the gate re-run against the unchanged suite:

```
Coverage gate
  measured : 86.01% (3177/3552 statements)
  floor    : 90%

  file                                                        stmts  miss   cover
  projects/99_scratch/untested.py                               260   260    0.0%
  projects/01_hangman_game/main.py                              138    27   76.5%
  projects/02_portfolio_tracker/main.py                         128    17   85.6%
  projects/04_terminal_assistant/main.py                         92    10   87.3%
  projects/03_file_organizer_automation/main.py                  81    8   88.8%

FAIL — coverage floor breached:
  - total coverage 86.01% is below the 90% floor (375 of 3552 statements unexecuted)
```

Exit code `1`. The module was then deleted, leaving the tree as committed.

| Run | Statements | Missing | Coverage | Exit |
| --- | --- | --- | --- | --- |
| Baseline, before the drop | 3,292 | 115 | **95.76%** | 0 |
| + 104-statement module | 3,396 | 219 | 91.61% | 0 |
| + 260-statement module | 3,552 | 375 | **86.01%** | 1 |

The middle row is the useful one: a single badly-covered module is *absorbed*
by 5.8 points of headroom. That headroom is deliberate — a floor that trips on
one uncovered helper is a floor people disable — but it is also why the gate
reports the worst files instead of only a total. The number is the tripwire;
the file table is what makes the failure actionable.

## 5. What the gate does *not* claim

* **Coverage is not correctness.** A line can execute and assert nothing.
  Mutation testing (Day 19) covers that gap, which is why both gates exist.
* **A high total can hide a dead module.** 96% overall still leaves
  `projects/01_hangman_game/main.py` at 76.5% — the interactive shell is hard to
  drive without a terminal. `--per-file-minimum` exists for the day that
  matters; it is not switched on because a per-file floor at today's numbers
  would fail on five modules and the honest fix is tests, not a number.
* **`# pragma: no cover` is a decision, not an escape hatch.** The exclusion
  list in `pyproject.toml` covers `if TYPE_CHECKING:` and `if __name__ ==
  "__main__":` only. Any other pragma should be justified in review.

## 6. CI wiring

```yaml
      - name: Run test suite under the coverage gate
        # Day 20: the floor is enforced, not merely printed. A drop below 90%
        # line coverage exits 1 and fails the build.
        run: python tools/coverage_gate.py --minimum 90
```

The step replaces the old `pytest --cov=projects --cov-report=term-missing`,
which measured only `projects/` and could not fail. It runs in every matrix
cell (Python 3.10–3.12), so a drop fails three builds rather than one.

## 7. Next

Day 21 consolidates Week 3: a test-strategy document and QA report covering the
five layers now in place — assertion census (Day 15), I/O isolation audit
(Day 16), property tests (Day 17), fuzz corpus (Day 18), mutation score
(Day 19) and this coverage floor.
