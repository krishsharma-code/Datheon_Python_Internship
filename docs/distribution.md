# Build & distribution (Day 23)

> An editable install reads your working tree, so it cannot tell you whether a
> file was left out of the package. A wheel can. This document describes how the
> wheel and the sdist are built, what `tools/distribution.py` checks, and the two
> real defects the first run found.

---

## The problem

Day 22 ended with `pip install -e .` exposing all five commands. That proves the
*declaration* — the `console_scripts` table, the `py-modules` list, the package
layout — is coherent. It does not prove the *artefact* is, because an editable
install resolves every import straight back to the checkout:

* `py-modules` missing an entry still imports, because the module is sitting in
  the repository root either way;
* a dataset excluded from `package-data` still loads, because the path resolves
  to the source file;
* `projects/*/data/*.csv` that never made it into the archive is only discovered
  by the first user who runs `portfolio` from an installed copy.

The failure is also asymmetric: the wheel installs happily, `--version` works,
and the tool breaks the moment it reads its default dataset. A distribution
check has to look *inside* the archive, not at the exit code of `pip`.

---

## The design

```
tools/distribution.py          build → inspect → verify → twine check → install
tests/test_distribution.py     the checks, against synthetic archives
.github/workflows/ci.yml       `distribution` job, after lint-and-test
datheon/py.typed               PEP 561 marker, shipped from Day 23
```

`python tools/distribution.py` runs five steps and exits `0` only if all of them
pass:

| Step | What it does | Failure means |
| --- | --- | --- |
| Build | `python -m build --wheel --sdist` into a freshly emptied `dist/` | the package cannot be built at all |
| Inspect | opens both archives with `zipfile` / `tarfile` | the artefact is not a wheel/sdist |
| Verify | compares contents with `pyproject.toml` **and** the source tree | something that should ship did not |
| Metadata | `twine check` over both artefacts | PyPI would reject the upload |
| Install | wheel into a throwaway venv, `--no-index --no-deps`, then `--version` × 5 | the installed package does not work |

Three decisions carry it:

1. **The declaration is read, not restated.** `load_declaration()` parses
   `pyproject.toml` with `tomllib` for the name, version, `Requires-Python`,
   `py-modules`, `console_scripts` and `package-data` globs. The gate never
   keeps its own copy of "the five tools", so a rename cannot pass by being
   applied in both the declaration and the checker.

2. **The expected payload is read from disk, not from the declaration.**
   `load_declaration()` also walks `projects/*/data/` and every directory with an
   `__init__.py`. The wheel must contain *each* of those files. Comparing the
   declaration with itself would be a tautology; comparing it with the tree is
   the check that actually catches an omission.

3. **The install is the only real test.** Everything else is static analysis of
   an archive. Installing the wheel into a clean virtualenv — with
   `--no-index --no-deps`, because the tools have no runtime dependencies — and
   running each command from a *different* working directory is the closest CI
   can get to a user's machine. It exercises the generated stubs, the loader's
   `sys.path` repair, and the bundled datasets in the installed layout.

The pure half — `inspect_wheel`, `inspect_sdist`, `verify_wheel`,
`verify_sdist`, `verify_pair` — takes archives and returns `CheckResult`s, so
`tests/test_distribution.py` feeds it synthetic wheels and asserts that each
defect produces a *named* failure. The suite builds nothing and shells out to
nothing; `build_artifacts` and `twine_check` are exercised by patching
`subprocess.run`.

---

## What the checks cover

Wheel, against the declaration and the tree:

* `Name`, `Version`, `Requires-Python` match `pyproject.toml`;
* every `console_scripts` entry exists **and** points at the declared target,
  and no undeclared command is installed;
* every `py-modules` module is present as a top-level `.py` file;
* every package directory on disk has its `__init__.py` in the wheel;
* every file under `projects/*/data/` is in the wheel — not merely "a file
  matching `data/*.csv`";
* every `package-data` glob matches at least one member (a dead glob is a
  promise nobody keeps);
* `datheon/py.typed` is present, so the `Typing :: Typed` classifier is honest;
* `LICENSE` travels inside `.dist-info/licenses/`.

Sdist, against the declaration:

* name and version match, `pyproject.toml` and `LICENSE` are included;
* the core modules and every dataset are present, so a rebuild reproduces the
  wheel;
* no `__pycache__`, `.pyc`, `.whl` or `.egg` — bytecode in an sdist means the
  build ran in the wrong directory.

Both, plus the install:

* `pair:*` — the wheel and the sdist describe the same release, and that release
  is the declared version;
* `install:*` — the wheel installs, registers five `console_scripts`, the
  datasets are readable from the installed package, and each command prints its
  banner and exits `0`.

---

## Run it

```bash
pip install -e ".[dev]"          # or: pip install build twine
python tools/distribution.py     # build, verify, twine check, clean-venv install
```

Useful variants:

```bash
python tools/distribution.py --no-isolation     # no fresh build env (offline / proxy-restricted)
python tools/distribution.py --report-only      # inspect an existing dist/ without rebuilding
python tools/distribution.py --skip-install     # fast loop: skip the virtualenv
python tools/distribution.py --json             # machine-readable verdict for CI
```

Exit codes: `0` every check passed, `1` an artefact or install check failed,
`2` no verdict could be reached (the build failed, or `build`/`twine` is
missing). A gate that cannot measure never reports success.

Typical output:

```console
$ python tools/distribution.py
Distribution gate
  wheel : datheon_python_internship-1.0.0-py3-none-any.whl (200,752 bytes)
  sdist : datheon_python_internship-1.0.0.tar.gz (271,006 bytes)

  [PASS] build                            built …whl, …tar.gz
  [PASS] wheel:name                       Name is 'datheon-python-internship', declared …
  …
  [PASS] install:script:inventory         inventory --version -> 'inventory 1.0.0' exit 0

34/34 checks passed
PASS — the artefacts build, check clean and install.
```

---

## What the first run found

Two defects, both invisible to an editable install:

1. **The wheel shipped no `py.typed`.** `pyproject.toml` declares
   `Typing :: Typed`, and every public callable is annotated, but without the
   PEP 561 marker a type checker treats the installed package as untyped and
   ignores all of it. Fixed by adding `datheon/py.typed` and listing it in
   `package-data`; the gate now fails if the marker disappears again.

2. **`PIP_USER=1` in the developer's shell poisoned the install check.** The
   first version of `install_and_smoke()` inherited `os.environ`, and pip inside
   the fresh virtualenv refused with *"Can not perform a `--user` install. User
   site-packages are not visible in this virtualenv."* The check was measuring
   the shell, not the wheel. `install_environment()` now strips `PIP_USER`,
   `PIP_TARGET`, `PIP_PREFIX` and `PIP_ROOT` and sets `PYTHONNOUSERSITE=1`, so
   the same command gives the same verdict on a developer machine and on a
   runner.

Neither was a code bug; both were distribution bugs, which is exactly the class
of defect a wheel-only check exists to find.

---

## CI

`ci.yml` gains a `distribution` job that runs after `lint-and-test`:

```yaml
- name: Build, verify and install the artefacts
  run: python tools/distribution.py --json
```

It builds with isolation on the runner, checks the metadata with `twine check`,
installs the wheel into a clean virtualenv and runs all five commands, then
uploads `dist/` as a build artefact. The unit tests cannot prove any of that
without building, so CI is where the claim is settled.

---

## Deliberately not done here

* **No publishing.** `twine upload` is a release step, and a release needs a
  version bump, a `CHANGELOG.md`, a tag and a GitHub Release — Day 26 owns all
  of it. Day 23 stops at *the artefacts are correct*.
* **No artefact signing.** Sigstore/`twine --sign` is worth adding once there is
  something to publish and a key policy to go with it.
* **No multi-OS install matrix.** The `distribution` job runs on
  `ubuntu-latest` only; the ubuntu/macos/windows × Python 3.10–3.13 matrix is
  Day 24's deliverable.
* **No `src/` layout migration.** Same reasoning as Day 22: it would rewrite
  every import in the repository for no benefit to a teaching repo.
