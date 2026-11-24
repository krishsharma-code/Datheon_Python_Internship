# Static type checking

**Day 31 — maintenance, backlog §5: “add type checking (`mypy`/`pyright`) to CI
once annotations are complete.”**

Every other gate in this repository asks a question about execution: is the code
covered, do the tests survive mutation, does the fuzzer find a crash. None of
them asks the question a type checker asks, and it is the cheapest question
there is to answer on every commit:

> Can a call be wrong without a test ever running?

On this tree the answer was yes. `mypy --strict` reported **39 errors in 15
files** on its first run, and six of them were defects rather than missing
annotations:

| Defect | Where | What it would have done |
| --- | --- | --- |
| `asyncio.run(factory())` on an `Awaitable` | `async_organizer.run_coroutine_sync` | `asyncio.run` accepts a *coroutine* only; a `Future`, a task or any other awaitable raised `ValueError` before the work started. The annotation promised “an awaitable”; the body honoured only one kind. |
| A JSON *string* iterated as a list | `chatbot.IntentEngine.from_json` (`fallbacks`, `exit_commands`) and `from_faq_json` (`greetings`) | `"fallbacks": "sorry"` became `["s", "o", "r", …]` — one response per character. |
| `len()` on an `Optional` | `inventory.main` demo | `manager.get("B002")` may return `None`; the dunder showcase formatted it as if it could not. |
| A callback contract that no caller met | `persistence.atomic_write_with` | The parameter said `Callable[[TextIO], None]`; every writer in the tree returns something (`handle.write` returns an `int`). The type was wrong, not the callers. |
| An import that was never a re-export | `inventory.main` → `inventory.InventoryError` | Worked at runtime by accident; `--no-implicit-reexport` is what makes “this module owns that name” a declaration instead of a side effect. |
| A `type: ignore` that hid the wrong code | `chatbot.Intent._parse` | `int(priority)  # type: ignore[arg-type]` suppressed nothing: the real code was `call-overload`, so the suppression was dead and the error was live. |

The rest were honest annotation gaps — a bare `dict`, a bare `frozenset`, a
`set` without an element type, a `pstats.Stats.stats` read that typeshed does not
declare, a label that could be `None` where a `str` was promised.

## What the gate checks

`tools/typecheck.py` runs mypy in **six phases** and audits the declaration:

| Phase | What it checks |
| --- | --- |
| `core` | The thirteen shared modules and the `datheon` package, in place. |
| `01_hangman_game` … `05_inventory_oop` | One phase per task pack, from a shadow tree (see below). |

That is **33 targets** — every file that ships, with nothing excused.

```bash
python tools/typecheck.py                # the whole gate
python tools/typecheck.py --json         # machine-readable verdict
python tools/typecheck.py --core-only    # skip the five project phases
python tools/typecheck.py --keep-shadow  # keep the shadow trees for triage
python -m mypy                           # the core scope alone, from the config
```

Exit codes follow the rest of the repository: **0** every phase clean and the
declaration honest, **1** at least one type error or one finding, **2** the gate
could not measure — mypy is not installed, or a phase failed to run. A gate that
cannot measure never reports success.

## Why a shadow tree

`projects/01_hangman_game` is not a valid Python package name — it starts with a
digit — so mypy cannot derive a module name for the files inside it and refuses
to look at them at all:

```text
01_hangman_game contains __init__.py but is not a valid Python package name
```

The tempting answer is to exclude the five projects from the gate, which would
leave the largest part of the codebase untyped. The gate does the opposite: for
each project it builds a **shadow tree** — the shared core modules copied next to
that project's own files, flat in one directory — and checks it there.

That layout is not an invention. It is the module graph the CLIs create at
runtime: each entry point puts its own directory and the repository root on
`sys.path` and then imports its siblings by bare name (`from words import
load_words_from_file`). Copying is what lets mypy see the same edges, and it is
also why the shadow tree needs `--explicit-package-bases`: without it mypy names
the files after the shadow directory and then reports every one of them as “found
twice under different module names”.

The shadow tree lives in a temporary directory and is removed when the phase
finishes. `--keep-shadow` keeps it, which is what you want when a diagnostic
points at a line you cannot find.

## The declaration is audited, not trusted

A gate is only as good as its scope. `[tool.mypy] files` in `pyproject.toml`
declares what is checked, and the gate compares that declaration with the
packaging tables that say what *ships*:

| Code | What it means |
| --- | --- |
| `typecheck.config` | `[tool.mypy]` declares no `files`: the scope would be whatever happens to be on the command line. |
| `typecheck.strict` | `strict = true` is missing, so untyped defs and implicit `Any` would pass. |
| `typecheck.python` | `python_version` is missing, or disagrees with the floor in `requires-python`. |
| `typecheck.scope` | A module in `[tool.setuptools] py-modules` — or a shipped package — has no entry in `files`. |
| `typecheck.pytyped` | `datheon/py.typed` is gone, so consumers' checkers would ignore every annotation the gate just proved. |
| `typecheck.ci` | No step in `.github/workflows/ci.yml` runs the gate. |
| `typecheck.requirement` | `mypy` is not in `requirements.txt`, so CI would exit 2 on every run. |

The `typecheck.scope` check is the one that keeps this honest over time: adding a
fourteenth core module means adding it to `py-modules` **and** to `files`, and
the gate fails until both agree. The five projects are the documented exception —
`projects*` ships, but it is checked through the per-project phases rather than
through `files`, and the audit verifies the directory the phases enumerate
exists.

## In CI

`.github/workflows/ci.yml` gained one job:

```yaml
type-check:
  name: Type check (mypy --strict)
  runs-on: ubuntu-latest
```

One runner, one interpreter. A type error is not a platform signal — mypy's
answer does not change between macOS and Windows — so twelve copies of the job
would cost minutes and prove nothing extra. The job is listed in the `CI status`
aggregate, which is the single status a branch-protection rule can require.

## What it does not cover

* **`tests/`** — 48 files and 1,400+ fixture-heavy functions. Pytest's fixtures
  are dynamic by design, and strict mypy over a test suite mostly produces
  annotations that document nothing. The tests are checked by running them.
* **`tools/`** — the gates themselves. They are excluded from the wheel, and
  every one of them is exercised by its own test module.
* **Runtime behaviour** — a clean mypy run says nothing about whether the code
  is *correct*, only that the calls agree with the signatures. That is what the
  1,800-test suite, the mutation score and the fuzzer are for.

## Where it fits

| Question | Gate |
| --- | --- |
| Does it run at all? | `pytest` |
| Do the tests have teeth? | `tools/mutation.py` |
| Is the code executed? | `tools/coverage_gate.py` |
| **Do the calls agree with the signatures?** | **`tools/typecheck.py`** |
| Is it still fast? | `tools/benchmark_budget.py` |

See the [test strategy](test_strategy.md) for how the gates divide the work, and
[benchmark regression gate](benchmarking.md) for the same treatment of a
different question.
