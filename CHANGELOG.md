# Changelog

All notable changes to **Datheon Python Internship** are documented in this
file. The format is based on [Keep a Changelog], and this project adheres to
[Semantic Versioning].

Every released version in this file has a matching git tag (`v1.1.0`,
`v1.0.0`, …) and a GitHub Release whose notes are the entry below — the workflow
publishes `CHANGELOG.md` verbatim rather than a second copy of it, so the two
cannot disagree. `tools/release.py` audits the structure of this file, and
`pytest` runs that audit.

## [Unreleased]

### Planned

- Day 27 — submission polish: README final pass, architecture diagram, demo
  assets.
- Day 28 — final verification: full green local and CI run, repository freeze,
  certificate checklist, final summary.

## [1.1.0] - 2026-11-14

The distribution release: the five tools become an installable package with a
published documentation site, a twelve-cell CI matrix and an automated release
pipeline.

### Added

- **Packaging & entry points (Day 22).** PEP 621 metadata and one
  `console_scripts` entry per tool, so `pip install -e .` puts `hangman`,
  `portfolio`, `organizer`, `assistant` and `inventory` on the user's `PATH`.
  The `datheon/cli/` shim layer loads the numbered project directories through
  `importlib`, so no tool had to move.
- **Build & distribution (Day 23).** `tools/distribution.py` builds a wheel and
  an sdist and verifies their contents against both `pyproject.toml` and the
  source tree — five console scripts, thirteen core modules, eight packages,
  three datasets, `py.typed` and the licence — then installs the wheel into a
  clean virtual environment and runs every `--version`. `twine check` validates
  the metadata PyPI would validate.
- **CI multi-OS matrix (Day 24).** Twelve cells: `ubuntu-latest`,
  `macos-latest` and `windows-latest` × Python 3.10–3.13, with `fail-fast:
  false`, pip caching, one `bash` shell on all three runners and an aggregate
  `CI status` job. `tools/ci_matrix.py` audits the declaration.
- **Documentation site (Day 25).** MkDocs (Material) with an API reference
  generated from the docstrings by `mkdocstrings`, deployed to GitHub Pages
  from `.github/workflows/docs.yml` and built with `--strict`, so a dead link
  fails the build. `tools/docs_site.py` audits the site against the repository.
- **Release automation (Day 26).** `CHANGELOG.md` (this file),
  `.github/workflows/release.yml` and `tools/release.py`, which audits the
  version in all three declarations, the changelog's structure, the publishing
  workflow and the release policy, and extracts the release notes from this
  file for `gh release create --notes-file`.

### Changed

- The version moves from `1.0.0` to `1.1.0` in `pyproject.toml`,
  `datheon.__version__` and `cli_contract.PROGRAM_VERSION` together. The
  version-dependent tests now read the declared version instead of repeating a
  literal, so the next bump touches three files rather than seven.
- `README.md` links the Releases page and this changelog from the project
  header, and the documentation site gained a **Release automation** page under
  *Continuous integration*.

### Fixed

- The distribution ships `py.typed`, so the `Typing :: Typed` classifier is
  true for an installed package and not only for an editable checkout.
- `PIP_USER` is scrubbed from the environment of the clean-venv install probe,
  which had been silently letting a user-site install satisfy the check.
- `.gitattributes` forces LF line endings, so the Windows CI cell checks out a
  tree that `black --check` and the POSIX-mode assertions agree with.

## [1.0.0] - 2026-10-21

The first release: five task-pack projects, taken from "it runs" to a gated,
observable, tested package over four weeks.

### Added

- **Repository scaffold (Day 1).** MIT licence, contributing guide, strict
  `.gitignore`, five projects under `projects/01_…`–`05_…`, a pytest suite and
  a GitHub Actions pipeline running `flake8`, `black --check` and `pytest`.
- **Official datasets (Day 2).** The hangman word list, the S&P 500
  constituents CSV and the assistant's FAQ corpus, loaded by code that validates
  them.
- **CLI ergonomics (Day 3).** One `argparse` convention across all five tools,
  `--version` everywhere, and a documented exit-code contract — `0` success,
  `1` user or data error, `2` usage error.
- **Input validation (Day 4).** Strict validators for symbols, quantities,
  prices, paths, letters and dates, plus a `--strict` mode with actionable
  messages.
- **Edge-case sweep (Day 5).** Empty directories, permission errors, name
  collisions, missing and oversized files, interrupted input and broken pipes,
  each with a regression test.
- **Loader hardening (Day 6).** Schema validation, BOM/CRLF tolerance,
  encoding fallbacks and malformed-row reporting with line numbers.
- **Error taxonomy (Day 7).** A `DatheonError` base with per-domain subclasses
  and a user-facing error catalogue in `docs/error_catalogue.md`.
- **Logging (Days 8, 10).** `--log-level`, console and rotating file handlers
  wired into every project, then JSON log records with run IDs and durations.
- **Persistence (Day 9).** Atomic writes (temp file plus `os.replace`), a schema
  version field and read-back validation, so a killed write cannot corrupt the
  target file.
- **Async I/O (Day 11).** The organizer's moves run on `asyncio` with a bounded
  semaphore; the synchronous API is preserved.
- **Performance work (Day 12).** A `cProfile` analysis of the organizer, the
  loaders and the matcher, with the top three costs optimised and before/after
  timings recorded in `docs/performance_profiling.md`.
- **Caching (Day 13).** The word list, the constituents table and the compiled
  intent regexes are cached with explicit invalidation.
- **Test depth (Days 15–21).** Assertion census gated at 200+ (now well past
  2,000), filesystem/clock/network mocking, Hypothesis property tests, a fuzz
  corpus of sixteen targets, a `mutmut` mutation gate at 100% over
  `error_catalogue.py`, a 90% coverage floor enforced by
  `tools/coverage_gate.py`, and the Week 3 test strategy and QA report.
- **Consolidation reports.** `docs/week2_performance_observability_report.md`
  and `docs/week3_qa_report.md`, plus a daily learning log covering every day.

### Security

- No secrets, tokens or credentials anywhere in the history; the CI workflows
  hold `contents: read` and nothing else.

[Unreleased]: https://github.com/krishsharma-code/Datheon_Python_Internship/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/krishsharma-code/Datheon_Python_Internship/releases/tag/v1.1.0
[1.0.0]: https://github.com/krishsharma-code/Datheon_Python_Internship/releases/tag/v1.0.0
[Keep a Changelog]: https://keepachangelog.com/en/1.1.0/
[Semantic Versioning]: https://semver.org/spec/v2.0.0.html
