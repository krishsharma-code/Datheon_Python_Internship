# Mutation testing — Day 19

Day 18 proved the tools survive *hostile input*. Day 19 asks a harder question
about the tests themselves: **do they actually test?** A green suite with 90%
line coverage can still be decorative — every statement executed, no assertion
that would notice if a comparison were flipped.

Mutation testing answers it directly. `mutmut` edits the source (flip `<` to
`<=`, drop a branch, replace a constant), re-runs the selected tests, and
records the verdict. A mutant no test notices has **survived**, and every
survivor is a place where the suite is asleep.

---

## 1. The claim, measured

```
python tools/mutation.py                        # run mutmut, then gate the score
python tools/mutation.py --report-only          # re-read the cache, no run
python tools/mutation.py --json                 # machine-readable
python tools/mutation.py --show-survivors       # name every survivor
```

```
module                        killed  survived  no tests    score
-----------------------------------------------------------------
error_catalogue.py               182         0         0   100.0%
-----------------------------------------------------------------
TOTAL                            182         0         0   100.0%
```

182 mutants generated, 182 killed, none surviving, none uncovered. The exit
code is the verdict — `0` above `--minimum-score` (default `95`), `1` below it —
so a suite that stops noticing a mutation turns the build red.

## 2. Before and after

| Run | Killed | Survived | No tests | Score |
| --- | --- | --- | --- | --- |
| Baseline (`tests/test_errors.py` as it stood) | 143 | 16 | 23 | **89.9%** |
| After the Day 19 tests | 182 | 0 | 0 | **100.0%** |

The thirty-nine mutants that survived the baseline fell into three groups:

**The failure path of the code lookups (16 mutants).** `describe_code` and
`error_class_for` raise `UnknownCategoryError` for an unknown code. The suite
asserted *that* the error was raised — never what it said. So a mutant could
blank the message (`f"Unknown error code {code!r}."` → `None`), drop the hint
entirely, change the hint's separator from `", "` to `"XX, XX"`, rename the
context key from `code` to `CODE`, or delete the `context` argument outright,
and the suite stayed green. Two of the sixteen were subtler: mutating the
pipe-escaping in `render_catalogue_markdown` from `cell.replace("|", "\\|")` to
`cell.replace("XX|XX", "\\|")` produces a *broken Markdown table* that no test
looked at.

**Uncovered code (23 mutants).** `write_catalogue` and `main` had no test in the
selected module at all — `write_catalogue` was exercised from
`tests/test_persistence.py` (for its atomicity), and `main` was exercised
nowhere. Mutmut reports these as `no tests`, and this driver counts them
against the score rather than dropping them, because untested code is exactly
what mutation testing exists to find.

**The default argument (2 mutants, found in a later run).** After the first
round of tests the score reached 99.0% with two survivors left: both mutations
of `write_catalogue`'s default parameter. Nothing called `write_catalogue()`
with no argument, so the default was dead code — and the documented command
`python error_catalogue.py` relies on the equivalent default inside `main`.
One test that calls the bare form killed both.

## 3. What was added

`tests/test_errors.py` gained a **mutation-testing hardening** section — eleven
tests, mostly parametrised over both lookups:

- the unknown-code error's **exact message**, its catalogue code, and its
  `context == {"code": ...}`;
- the unknown-code **hint**, asserted to equal `"Known codes: "` joined with
  `", "` over `catalogue_codes()` — and to actually contain a real code;
- the **rendered block**, first line `! [DT-1004] Unknown error code 'NOPE'.`,
  second line starting `  hint: Known codes: `;
- **pipe escaping** in a table row, asserted with a synthetic `ErrorEntry` whose
  title, summary and resolution all contain `|`, plus a count of exactly four
  unescaped `" | "` separators in the row;
- `write_catalogue` — the returned path, the content, parent-directory
  creation, a `str` path, and the **no-argument default**;
- `main` — the default destination, an explicit destination, and the fallback
  to `sys.argv` when called with no argument at all.

`tools/mutation.py` and `tests/test_mutation_tool.py` cover the driver itself:
the exit-code mapping, the scoring rule, parsing a truncated or missing cache,
and the gate's exit codes.

## 4. Why the score is computed strictly

The denominator is every mutant `mutmut` produced, minus the ones it never got
to (`not checked`), the ones that were `skipped`, and runs interrupted by the
user. Everything else counts — including `no tests`.

Two consequences worth stating plainly:

- **An interrupted run cannot look perfect.** A cache full of `not checked`
  scores `0.0`, not `100.0`, so a half-finished mutation run fails the gate.
- **Uncovered code cannot be hidden.** A module with ten survivors and forty
  uncovered mutants scores 0%, not 100%.

`timeout` and `segfault` count as kills: a mutant that makes the test process
hang or crash has certainly been noticed. That matches `mutmut`'s own
classification.

## 5. Why `error_catalogue.py` is the target

`[tool.mutmut]` in `pyproject.toml` scopes the run:

```toml
[tool.mutmut]
source_paths = ["error_catalogue.py"]
pytest_add_cli_args_test_selection = ["tests/test_errors.py"]
also_copy = ["conftest.py", "errors.py", "edge_cases.py", ..., "projects", "tools"]
```

Three reasons for this module first:

1. **It is the shared contract.** Every one of the five tools renders its
   failures through this catalogue, and the committed
   `docs/error_catalogue.md` is generated from it — a mutation here is a
   user-visible documentation bug.
2. **It is fast.** Its test module runs in about 3.5 s, so a mutant costs well
   under a second in a warm forked worker: 182 mutants in 1 m 55 s on two
   cores. Mutating a whole project against its full test module would take
   tens of minutes per run.
3. **`also_copy` is load-bearing.** `mutmut` copies only the mutated file and
   `tests/` into `mutants/`, so the first attempt died at the stats phase with
   `ModuleNotFoundError: No module named 'errors'`. The `also_copy` list ships
   the rest of the shared core, `projects/`, `tools/` and `docs/` into the
   sandbox.

### Scope, stated honestly

One module is the *start* of a mutation-testing programme, not the end of one.
Two observations from this run are worth recording:

- **The golden-file test does most of the work.** With the generated-document
  comparison (`test_committed_catalogue_matches_the_generator`) excluded, the
  same suite scores **23.6%** (43 killed of 182). That test asserts the entire
  rendered document byte for byte, so it kills nearly every formatting mutant
  at once. It is a legitimate detector — the document really is the
  deliverable — but it means the score above is a statement about *this
  module*, not a licence to assume the rest of the suite is equally sharp.
- **`edge_cases.py` was attempted and deferred.** Its 99 mutants generate fine,
  but `mutmut`'s stats phase cannot pass: `tests/test_edge_cases.py` contains
  `test_run_cli_broken_pipe_end_to_end`, which spawns a real subprocess and
  fails inside the `mutants/` copy. Adding a target whose baseline is already
  red would make every mutant look killed. The fix is to make that test
  sandbox-relative, and it is the next item on the list below.

## 6. Running it in CI

`.github/workflows/mutation.yml` runs the full mutation pass on pull requests
that touch the shared core, and weekly on a schedule:

```
pip install -r requirements.txt
python tools/mutation.py --minimum-score 95 --show-survivors
```

It is a separate workflow rather than a step in `ci.yml` because it takes about
two minutes and it is not a per-commit signal — it is a weekly audit that a
test suite still has teeth. The `mutants/` cache and `.mutmut-cache` are
gitignored: they are local artefacts, never committed.

## 7. Next up

- Make `test_run_cli_broken_pipe_end_to_end` sandbox-relative, then add
  `edge_cases.py` as a second mutation target.
- Add `validators.py` — the input-validation layer every tool depends on.
- Raise `--minimum-score` to `100` for the configured targets once the second
  target is green, so a single new survivor fails the build.
- Day 20 locks in a coverage floor (`--cov-fail-under=90`); coverage and
  mutation score together are the pair that makes a quality gate mean
  something.
