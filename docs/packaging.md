# Packaging & entry points (Day 22)

> How `pip install -e .` turns five directories under `projects/` into five
> commands on the user's `PATH` — without moving a single file.

---

## The problem

Until Day 21 the only supported way to run a tool was to point the interpreter
at a file:

```bash
python projects/01_hangman_game/main.py
```

That works because `main.py` starts with

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hangman import HangmanGame
```

— it repairs `sys.path` for itself, then imports its siblings by plain module
name. The test suite does the same thing in `conftest.py`.

A console script cannot do any of that. It is a two-line stub written into the
interpreter's `bin` directory:

```python
from datheon.cli.hangman import main
sys.exit(main())
```

It is started from whatever directory the user happens to be in, so
`Path(__file__).parents[2]` is meaningless, and `import hangman` would only work
if `projects/01_hangman_game` were on the path.

Two more obstacles come from the layout itself: the project directories are
numbered (`01_hangman_game` …), which keeps them in task-pack order but makes
them invalid Python identifiers, and all five entry points are called `main`,
so they cannot coexist under one flat import namespace.

---

## The design

```
pyproject.toml            [project.scripts] — the five commands
datheon/                  the importable shim layer
  __init__.py             __version__, mirroring the CLI banner
  cli/
    _registry.py          ToolSpec table + path repair + loader + run()
    hangman.py            main() -> run("hangman", argv)
    portfolio.py
    organizer.py
    assistant.py
    inventory.py
projects/                 unchanged, still numbered, still the real code
  01_hangman_game/…        main.py imports its siblings by name
```

Three decisions carry the design:

1. **The tools did not move.** Renaming `01_hangman_game` to `hangman` would
   touch every import in the repository, every path in the README, the CI
   workflow and the test suite, and would break the task-pack ordering the
   repository is graded on. The distribution adapts to the layout instead of
   the layout adapting to the distribution.

2. **One loader, five one-line shims.** `_registry.load_entrypoint()` adds the
   distribution root and the tool's own project directory to `sys.path`, then
   imports `projects.<package>.main` through `importlib` — which, unlike the
   `import` statement, is happy with a segment that starts with a digit. The
   shims hold no logic: each is `return run("<name>", argv)`, so there is
   exactly one place where the path repair happens.

3. **The shared CLI contract is reused, not re-implemented.** `run()` wraps the
   tool in `cli_contract.run_cli`, the same shim the `if __name__ ==
   "__main__"` block uses. Exit codes (`0` success, `1` data error, `2` usage
   error), the `DatheonError` rendering, the `Ctrl-C` and broken-pipe handling
   and the structured-log shutdown all keep working because none of it was
   copied.

Because the shims delegate rather than duplicate, the existing
`tests/test_cli_entrypoints.py` — 1,200 lines of argument-parsing coverage —
still tests the code a console-script user actually runs.

---

## What ships

| Command | Module | Equivalent invocation |
| --- | --- | --- |
| `hangman` | `projects/01_hangman_game/main.py` | `python projects/01_hangman_game/main.py` |
| `portfolio` | `projects/02_portfolio_tracker/main.py` | `python projects/02_portfolio_tracker/main.py` |
| `organizer` | `projects/03_file_organizer_automation/main.py` | `python projects/03_file_organizer_automation/main.py` |
| `assistant` | `projects/04_terminal_assistant/main.py` | `python projects/04_terminal_assistant/main.py` |
| `inventory` | `projects/05_inventory_oop/main.py` | `python projects/05_inventory_oop/main.py` |

`pyproject.toml` declares PEP 621 metadata plus:

* `[project.scripts]` — the five commands, each pointing at a shim's `main`;
* `[tool.setuptools] py-modules` — the thirteen shared core modules at the
  repository root (`cli_contract`, `errors`, `validators`, …), shipped as
  top-level modules because that is how the tools and the tests import them;
* `[tool.setuptools.packages.find]` — `datheon*` and `projects*`;
* `[tool.setuptools.package-data]` — `data/*.txt` and `data/*.csv`, so the word
  list and the sample price/constituent CSVs travel with the code that reads
  them (both tools resolve their default dataset from `Path(__file__).parent`).

The tools themselves remain standard-library only; `dependencies = []`. The
test and lint tooling lives in the `dev` extra, mirroring `requirements.txt`,
which CI keeps using so that a broken extra cannot break the build.

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e .                 # editable: your edits take effect immediately
pip install -e ".[dev]"          # plus pytest, flake8, black, hypothesis
```

Then, from any directory:

```console
$ hangman --version
hangman 1.1.0
$ organizer ~/Downloads --dry-run
$ inventory
```

A non-editable install (`pip install .`, or a wheel) behaves the same way: the
loader resolves `projects/` from the imported package rather than from the
working directory, so `_registry.projects_root()` is correct in both cases.

---

## Verifying it

`tests/test_packaging.py` covers the claim from both ends:

* **the declaration** — `pyproject.toml` is parsed with `tomllib`; the
  `[project.scripts]` table must equal `_registry.TOOL_NAMES` in both
  directions, each target must be `datheon.cli.<name>:main`, `py-modules` must
  match the `*.py` files actually at the root (minus `conftest.py`), and
  `package-data` must cover the datasets;
* **the wiring** — every target is imported and its `main` called; each shim is
  asserted to be a pure delegation; and `<tool> --version` / `<tool> --bad-flag`
  are driven end to end (shim → loader → project `main` → parser), asserting
  `hangman 1.1.0` with exit `0` and `2` respectively.

Nothing in the suite shells out or installs anything: the installed stub calls
the same `main` the tests call, so calling `main` is the faithful check. One
assertion reads `importlib.metadata` for the distribution's `console_scripts`
and skips itself when the package is not installed — the normal case for a
`pytest` run from a checkout.

CI adds the only thing the unit tests cannot prove:

```yaml
- name: Install the package and smoke-test the console scripts
  run: |
    pip install -e .
    for tool in hangman portfolio organizer assistant inventory; do
      "$tool" --version
    done
```

---

## Deliberately not done here

* ~~**No wheel or sdist.**~~ **Done on Day 23** — `tools/distribution.py`
  builds the wheel and the sdist, verifies their contents against this
  declaration, runs `twine check` and installs the wheel into a clean
  virtualenv; see [`distribution.md`](distribution.md). `package-data` was
  declared on Day 22 precisely so that wheel contains the datasets, and the
  Day 23 gate is what finally tests that for real.
* **No version bump or tag.** Day 26 owns SemVer, `CHANGELOG.md` and the
  GitHub Release.
* **No `src/` layout.** It is the tidier convention, but it would move the
  shared core modules out of the root and rewrite every import in the
  repository — a large diff with no benefit for a teaching repository whose
  README points at files by path.

## Known limitation

`projects/*/main.py` still repairs `sys.path` at import time. Harmless in both
install modes — the entry it inserts is already present — but it is the one
line that would have to change if the project directories were ever renamed to
valid identifiers. It is left in place so that `python
projects/01_hangman_game/main.py` keeps working for a reader who has not
installed anything.
