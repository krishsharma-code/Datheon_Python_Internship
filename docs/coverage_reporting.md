# Coverage reporting: publishing the number

Day 20 built a gate that fails the build when coverage drops below 90%. It is a
good gate and it has one blind spot: the report it produces dies with the build
log. Nothing outside that log can see the number — not a reviewer comparing two
pull requests, not a maintainer watching a trend, not a badge on the README.

Day 36 publishes it. `.github/workflows/coverage.yml` measures the suite once,
applies the same floor, keeps the report as an artefact and uploads
`coverage.xml` to Codecov; `codecov.yml` declares what "good" means in public.

```
pytest --cov --cov-report=xml:coverage.xml --cov-report=json:coverage.json
        │                    │                        │
        │                    │                        └── the local gate reads this
        │                    └── uploaded to Codecov, kept 14 days as an artefact
        └── the same suite the CI matrix runs
```

## Why a gate, and why it is not enough

A coverage report is a *measurement*. A gate is a *verdict*. A published badge is
a *claim* — and a claim made by a third party about your code needs the same
scrutiny as any other declaration in this repository. Three specific failures
motivate `tools/coverage_report.py`:

1. **An upload that finds nothing still succeeds.** `codecov/codecov-action`
   searches the workspace when `files:` is omitted. A renamed report or a typo
   turns the step green and the badge simply stops updating, which reads exactly
   like "coverage is fine".
2. **Two floors is no floor.** If the Codecov project target says 85% and
   `fail_under` says 90%, then the badge can be green on a build the local gate
   failed. The audit requires the two numbers to be equal.
3. **Two scopes is no scope.** `[tool.coverage.run] omit` and Codecov's `ignore`
   list decide which files count. If they drift apart, "90%" measures different
   things in the two places, and neither is wrong on its own terms.

## What the workflow does

| Step | Why |
| --- | --- |
| `pytest --cov --cov-report=xml --cov-report=json --cov-fail-under=0` | One run, two reports. `--cov-fail-under=0` hands the verdict to the gate, so a breach is reported as a breach rather than as "pytest failed". |
| `tools/coverage_gate.py --report-only --from-json coverage.json --minimum 90` | The Day 20 gate, reading the report instead of re-running the suite. The floor is the same 90 the CI matrix uses. |
| `actions/upload-artifact` (`if: always()`) | A failed gate is when the report matters most; a failed upload is when the artefact is the only copy left. |
| `codecov/codecov-action` (`if: always()`) | `fail_ci_if_error: false`, `use_oidc: true`, `files: ./coverage.xml`. |

The job runs on one runner with one interpreter: coverage is not a platform
signal, so twelve copies of it would cost minutes and prove nothing extra. That
is the same reasoning the type gate (Day 31) and the distribution job (Day 23)
already follow.

## Tokenless upload

The upload authenticates with GitHub's OIDC token — `id-token: write` in the
workflow's `permissions` — so a public repository needs **no stored secret** and
this repository commits none. If the project ever moves to a private repository,
set `CODECOV_TOKEN` in the repository settings and add `token: ${{
secrets.CODECOV_TOKEN }}` to the upload step; the audit accepts either form and
fails on a literal token in the file.

## `codecov.yml`

| Setting | Value | Mirrors |
| --- | --- | --- |
| `coverage.status.project.default.target` | `90` | `[tool.coverage.report] fail_under` |
| `coverage.status.project.default.threshold` | `1%` | absorbs rounding, not a real drop |
| `coverage.status.patch.default.target` | `85` | new code is held higher than the average |
| `coverage.ignore` | `tests/*`, `tools/*`, `benchmarks/*`, `conftest.py` | `[tool.coverage.run] omit` |
| `flags.unittests` | uploaded by the workflow | `paths: projects/, datheon/` |

## The audit

`tools/coverage_report.py` reads the workflow, `codecov.yml`, `pyproject.toml`,
`.gitignore` and this page as data — standard library plus PyYAML, no network, no
Codecov account, so it runs on a checkout that has never uploaded anything.

```bash
python tools/coverage_report.py              # human-readable report
python tools/coverage_report.py --json       # machine-readable verdict
python tools/coverage_report.py --strict     # warnings count as failures
python tools/coverage_report.py --root .     # audit another checkout
```

Exit codes: `0` the pipeline passes, `1` at least one finding, `2` the audit
could not reach a verdict (not a directory, PyYAML absent, the workflow or
`codecov.yml` unparsable, or no `fail_under` to compare against). A gate that
cannot measure must never report success.

### Finding codes

| Code | What it means |
| --- | --- |
| `coverage.workflow` | The coverage workflow is missing, or declares no job steps. |
| `coverage.trigger` | The workflow does not run on `push` and `pull_request` against `main`, so the badge stops tracking the default branch. An event with no branch filter is a **warning**: every branch then publishes a report, which is noisy rather than wrong. |
| `coverage.action` | No step uses `codecov/codecov-action`, or the reference floats (`@master`) instead of carrying a version (`@v5`). |
| `coverage.files` | The upload names no report the job produces — the step then searches, finds nothing, and reports success. |
| `coverage.token` | A literal token where a `${{ secrets.… }}` reference belongs. Only lines mentioning a token are scanned, so a SHA pin is not mistaken for a credential. |
| `coverage.fail-ci` | `fail_ci_if_error` is not `false`: a Codecov outage could redden a build every local gate passed. |
| `coverage.artifact` | The job keeps no copy of `coverage.xml`, so a failed upload leaves nothing to inspect. |
| `coverage.permissions` | The job grants `contents: write`, omits `id-token: write`, or declares no permissions block at all. |
| `coverage.config` | `codecov.yml` is missing, so Codecov applies its own defaults to this repository. |
| `coverage.target` | The Codecov project target is absent, `auto`, or disagrees with `fail_under` in `pyproject.toml`. |
| `coverage.ignore` | The ignore list disagrees with `[tool.coverage.run] omit`, or hides shipped code (`projects/`, `datheon/`, a shipped module). |
| `coverage.flags` | A flag is uploaded but undeclared in `codecov.yml`, or declared and never uploaded. |
| `coverage.gitignore` | `coverage.xml` or `coverage.json` is not ignored, so the next `git add .` commits a report. |
| `coverage.docs` | This page is missing, or no longer names a code the tool can emit — the table above cannot drift behind the checks. |

## Running it locally

```bash
# produce both reports the workflow produces
python -m pytest -q --cov --cov-report=xml:coverage.xml --cov-report=json:coverage.json

# apply the floor to the report that was just written
python tools/coverage_gate.py --report-only --from-json coverage.json --minimum 90

# check that the declaration still matches pyproject.toml
python tools/coverage_report.py
```

Both report files are git-ignored (`.gitignore`), so a local run never turns into
a committed artefact.
