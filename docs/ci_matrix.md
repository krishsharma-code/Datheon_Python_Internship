# CI matrix — testing where the users actually are

**Day 24 · Branch `feat/datheon-day-24-ci-matrix`**

Days 1–23 produced one verdict per commit, and every one of them came from
`ubuntu-latest`. That is a fine runner and a poor alibi: the five tools open
files, create directories, publish writes with `os.replace`, print box-drawing
characters, and register console scripts on the user's `PATH`. Those are the
places where Windows differs from Linux — and the only way to know is to run
there.

So `.github/workflows/ci.yml` now expands to **twelve cells**: `ubuntu-latest`,
`macos-latest` and `windows-latest` × Python **3.10, 3.11, 3.12, 3.13**.

---

## 1. The grid

| | 3.10 | 3.11 | 3.12 | 3.13 |
| --- | --- | --- | --- | --- |
| `ubuntu-latest` | ✅ | ✅ | ✅ | ✅ (primary) |
| `macos-latest` | ✅ | ✅ | ✅ | ✅ |
| `windows-latest` | ✅ | ✅ | ✅ | ✅ |

Every cell runs, in this order:

1. `actions/checkout@v4`
2. `actions/setup-python@v5` with **`cache: pip`**
3. `pip install -r requirements.txt`
4. `pip install -e .` then a portable smoke test of all five console scripts
5. `python -m compileall -q projects tests datheon tools conftest.py`
6. `flake8 .`
7. `black --check .`
8. `pytest -q`

The four heavier gates — the I/O audit, the matrix audit itself, the Hypothesis
profile, the fuzz corpus and the 90% coverage floor — run on **one designated
cell** (`ubuntu-latest` × 3.12). They are not platform signals: coverage,
property-based tests and the fuzz corpus produce the same verdict on every
runner, so running them twelve times would cost about twenty minutes and prove
nothing extra. The rule is: *anything that could differ by platform runs
everywhere; anything that cannot, runs once.*

## 2. Why the details are the deliverable

A matrix is a declaration, and YAML does not validate declarations. The five
ways this one can quietly measure less than it claims are the five things
`tools/ci_matrix.py` refuses to let pass:

| Defect | What it looks like | Why it is silent |
| --- | --- | --- |
| Unquoted version | `python-version: [3.10]` | YAML parses `3.10` as the float `3.1`, so the cell is a version no runner offers and the grid is one row short |
| `fail-fast: true` | the default | The first red cell cancels the other eleven; a run reports one failure where there are three |
| No `cache: pip` | `setup-python` without `with.cache` | The job still passes — it just downloads pytest, black, hypothesis and mutmut twelve times |
| PowerShell default | no `defaults.run.shell` | `for tool in …; do …; done` is a syntax error on Windows, and the console scripts there are `.exe` files |
| Metadata drift | `3.13` added to `classifiers` only | The package claims support nothing ever tested |

Two further checks are about the *shape* of the run rather than a single cell:
`permissions: contents: read` (a build matrix has no reason to hold a write
token) and an aggregate `CI status` job, because twelve check runs cannot be one
required status check on a protected branch.

## 3. `tools/ci_matrix.py`

The audit is a first-class tool, not a comment in a YAML file:

```bash
python tools/ci_matrix.py                        # audit .github/workflows/ci.yml
python tools/ci_matrix.py --list-cells           # print the expanded grid
python tools/ci_matrix.py --json                 # machine-readable verdict
python tools/ci_matrix.py --strict               # warnings become errors
python tools/ci_matrix.py --require-python 3.14  # demand an extra version
python tools/ci_matrix.py --require-os ubuntu-24.04
python tools/ci_matrix.py --no-pyproject         # skip the metadata cross-check
```

Exit codes follow the convention the rest of the repository uses: `0` every
check passed, `1` at least one finding, `2` no verdict — the workflow is
missing, is not valid YAML, PyYAML is absent, or **no cell could be measured**.
That last case matters: a gate that cannot measure must never report success.

### Finding codes

| Code | Severity | Meaning |
| --- | --- | --- |
| `matrix.missing` | error | No job declares a matrix over `os` and a Python version |
| `matrix.malformed` | error | The matrix block exists but cannot be expanded (an axis is missing or empty) |
| `matrix.unquoted-version` | error | An axis holds non-string scalars — the `3.10 → 3.1` trap |
| `matrix.missing-cell` | error | A required OS × version cell is absent |
| `matrix.unexpected-cell` | warning | A cell nobody asked for; legitimate, but untracked |
| `matrix.fail-fast` | error | `fail-fast` is not explicitly `false` |
| `matrix.pip-cache` | error | A `setup-python` step does not set `cache: pip` |
| `matrix.setup-python` | error | The job never uses `actions/setup-python` |
| `matrix.shell` | error | No `bash` shell is in effect for the job |
| `matrix.command` | error | A required command (`compileall`, `flake8 .`, `black --check .`, `pytest`) is missing |
| `matrix.aggregate` | error | No job depends on the matrix with `if: always()` |
| `permissions.missing` / `permissions.write` | error | No least-privilege block, or a write token |
| `job.runs-on` | error | A job names no runner |
| `pyproject.classifier` | error | A version is claimed but not run, or run but not claimed |
| `pyproject.floor` | error | `requires-python` disagrees with the oldest cell |

### Expansion rules implemented

`expand_matrix()` reproduces the parts of GitHub's semantics this repository
relies on: the Cartesian product of `os` and `python-version` (both `python` and
`platform` are accepted as aliases), minus every `exclude` entry, plus every
`include` entry that names **both** keys. An `include` that names one key
attaches extra variables to existing runners and creates no cell, so it is
ignored — and there is a test for exactly that.

## 4. Line endings: the defect the Windows cell would have found

The `windows-latest` image checks out with `core.autocrlf=true`. Without a
`.gitattributes`, every file arrives in the working tree with **CRLF** endings,
and then:

* `black --check .` reports the whole repository as unformatted, and
* `flake8` raises `W291` on lines that are perfectly clean on Linux.

That is a red Windows cell that says nothing about the code — the worst kind,
because the fix would look like reformatting 82 files. `.gitattributes` now
declares `* text=auto eol=lf`, so the working tree is identical on all three
runners while Git still stores blobs canonically.

The second defect was smaller and older: `tests/test_persistence.py` asserted
that `atomic_write_text` preserves `stat.S_IMODE()` after `os.chmod(target,
0o600)`. Windows has no POSIX permission bits — `os.chmod` there only toggles
the read-only flag — so the test would have failed on the one platform it says
nothing meaningful about. It now carries a `requires_posix_modes` skip marker,
matching the marker `tests/test_edge_cases.py` has used since Day 5.

## 5. What the first run found

The pull request's first CI run is the most useful thing this day produced.
Ubuntu and macOS passed in all four versions; Windows failed in all four — 50
tests each, the same 50.

| Cause | Symptom | Fix |
| --- | --- | --- |
| an event loop's self-pipe is a loopback socket pair on Windows (both Proactor and selector), and the Day 16 guard forbids every socket call | every `asyncio.run()` in the suite raised `RealIOAttempted` before the first test line — the whole organizer half of the suite | `NetworkGuard` suspends the `connect` patch for the duration of `socket.socketpair()` only; two tests hold the line from both sides |
| `FakeFileSystem` keyed virtual files by `str(path)` | `MissingFileError` for a file the fake was holding in memory (`\virtual\words.txt` vs `/virtual/words.txt`) | keys normalise through `Path(path).as_posix()` |
| a fixture wrote `"hello\nworld\n"` with `write_text`, which translates to CRLF on Windows | the byte-faithful loader returned `"hello\r\nworld\r\n"` | the fixture writes bytes |
| two assertions hard-coded POSIX formatting (`docs/error_catalogue.md`, `/tmp/x`) | the CLI and the log formatter printed the correct Windows path | compare against `str(Path(...))` |

Only the first was a defect in the code. The rest were claims the repository
made about itself that one operating system could not check — which is the
argument for the matrix, stated as five diffs.

It took two runs: 50 failures, then 45 after the three fixture fixes, then zero
once the guard stopped blocking the loop's own pipe. The first attempt at cause
one was to switch Windows to the selector event loop; that changed nothing,
because the selector loop builds the same socket pair. The policy was never the
variable — the guard was.

## 6. How the contract is enforced

`tests/test_ci_matrix.py` (69 tests) builds synthetic workflows, breaks one
thing at a time, and asserts the *named* finding that results. A code that can
be renamed without a test failing is a code nobody depends on.

Four tests point at the real repository instead:

* the real workflow expands to exactly twelve cells, all green in the audit;
* no axis holds an unquoted version;
* `tools/coverage_gate.py --minimum 90` is still in the file — Day 20's floor
  survives Day 24's rewrite of the same YAML;
* the console-script smoke test still resolves commands through `shutil.which`,
  which is what makes it true on Windows as well as POSIX.

The audit also runs **inside CI**, on the primary cell, so a pull request that
adds a Python version to the classifiers without adding it to the matrix fails
before it merges.
