# Final verification and the repository freeze (Day 28)

Twenty-seven days of work produced seven audits, a 1,300-function test suite, a
twelve-cell CI matrix, a release and a documentation site. None of that is what
a reviewer looks at first. A reviewer looks at the repository and asks one
question — *is this finished?* — and until today the repository had no single
answer to it.

Day 28 adds the answer: `tools/final_verification.py`, one command that runs the
whole green run, reads the submission's claims back out of the files that make
them, and prints the certificate checklist with a verdict per line. It also cuts
the **freeze**: a manifest of SHA-256 digests over every shipped file, so "this
is the state that was submitted" stops being a sentence in a log and becomes a
record that can be checked.

```bash
python tools/final_verification.py            # the full run, ~2.5 minutes
python tools/final_verification.py --static   # the claims only, ~0.4 s
python tools/final_verification.py --manifest # re-cut the freeze
```

Exit codes are the ones the rest of the repository uses: `0` green, `1` findings,
`2` a check that could not measure. `--strict` also fails on warnings and on
skipped checks, which is what a submission run should use.

---

## 1. One verdict instead of seven

The tool answers two different kinds of question and keeps them apart.

**Live checks** — the green run itself, executed as subprocesses:

| Check | Command |
| --- | --- |
| `compile` | `python -m compileall -q projects tests datheon tools conftest.py` |
| `lint` | `python -m flake8 .` |
| `format` | `python -m black --check .` |
| `tests` | `python -m pytest -q --cov --cov-report=json:coverage.json` |
| `coverage` | `python tools/coverage_gate.py --report-only` |
| `readme` | `python tools/readme_audit.py --strict` |
| `docs-site` | `python tools/docs_site.py --strict` |
| `release` | `python tools/release.py --strict` |
| `matrix` | `python tools/ci_matrix.py --strict` |
| `assets` | `python tools/demo_assets.py --check` |
| `io-audit` | `python tools/io_audit.py --strict` |
| `census` | `python tools/assertion_census.py --minimum 200` |

The suite runs **once**, under coverage, and the coverage gate then re-reads that
same report instead of running it again. Two verdicts, one measurement: the
naive version ran `pytest` and then `coverage_gate` back to back and spent four
and a half minutes to learn one thing twice.

**Static claims** — the facts the submission asserts in prose, read back out of
the files that assert them: the licence holder, the console scripts and the
modules they ship, the changelog entry for the declared version, the ledger's PR
numbers, one learning-log entry per completed day, the `.gitignore` rules that
keep build output out of the history, the paths the plan's own checklist names,
and the freeze manifest.

Twelve finding codes, all stable and all listed in the module docstring:

| Code | Fails when |
| --- | --- |
| `submission.file` | a file the submission needs is missing |
| `submission.licence` | `LICENSE` is not the MIT text for the intern |
| `submission.metadata` | `pyproject.toml` disagrees with what ships |
| `submission.entry-point` | a console script has no `main` behind it |
| `submission.changelog` | no changelog entry for the declared version |
| `submission.ledger` | the ledger is malformed, or a day reads `done` with no PR |
| `submission.log` | a completed day has no learning-log entry |
| `submission.document` | a path the checklist names does not exist |
| `submission.artefact` | `.gitignore` does not exclude build output |
| `submission.secret` | a token-shaped string or a secret file is present |
| `submission.freeze` | the manifest is missing, stale, or does not match the tree |
| `submission.command` | a live check failed or could not be measured |

### What a local run cannot decide

Six lines of the checklist need something this repository does not have: the
GitHub UI, LinkedIn, or the Datheon portal. They are printed as `manual` and are
never counted as evidence — a checklist that ticks itself is a checklist nobody
reads. The rest are mapped onto the checks that prove them, so a line turns red
the moment its evidence disappears.

---

## 2. The final green run

Measured on 2026-11-17 from the submission branch, in this order:

| Check | Result |
| --- | --- |
| `compile` | clean |
| `lint` | 0 findings |
| `format` | 92 files unchanged |
| `tests` | 1,653 passed, 1 skipped, 0 failed |
| `coverage` | 95.80% over 3,368 statements, 770 branches (floor 90%) |
| `readme` | PASS — README is submission-ready |
| `docs-site` | PASS — 42 pages, 15 documented modules, 0 warnings |
| `release` | PASS — 2 released versions, 0 warnings |
| `matrix` | PASS — 12 cells, 0 warnings |
| `assets` | PASS — 5 sessions, 89 frames, 824 KB |
| `io-audit` | PASS — no real I/O in the test suite |
| `census` | 1,366 test functions, 2,464 assertions (floor 200) |
| static claims | 11 checks, 0 findings |
| **verdict** | **23 checks, 23 green** |

The suite is one test module larger than yesterday's: this day adds 62 tests and
`tools/final_verification.py` is itself covered by the suite it runs, which is
the only way a gate can be trusted — a gate that is not tested is a gate that
may have been broken for a week.

---

## 3. The certificate checklist

Every line of the plan's §4, with the check that proves it. `manual` means no
local run can decide it.

| Section | Item | Status | Evidence |
| --- | --- | --- | --- |
| Repository | Public repo named `Datheon_Python_Internship` | manual | GitHub UI |
| Repository | All five task-pack projects implemented | pass | `files` |
| Repository | README: installation, architecture, quickstart | pass | `readme` |
| Repository | LICENSE — MIT, 2026 Krish Sharma | pass | `licence` |
| Repository | CONTRIBUTING.md and a strict .gitignore | pass | `files`, `artefacts` |
| Repository | Docstrings and type hints on public callables | pass | `docs-site` |
| Quality | Suite green, 200+ assertions | pass | `tests`, `census` |
| Quality | Coverage at or above the 90% floor | pass | `coverage` |
| Quality | CI green across the 12-cell matrix | manual | GitHub Actions |
| Quality | flake8 and black --check clean | pass | `lint`, `format` |
| Quality | No secrets, tokens or credentials | pass | `secrets` |
| Delivery | Installable package with console entry points | pass | `entry-points` |
| Delivery | Tagged release, CHANGELOG, attached artefacts | pass | `release` |
| Delivery | Generated docs site linked from the README | pass | `docs-site` |
| Proof | LinkedIn walkthrough post tagging @Datheon | manual | published 2026-10-21 |
| Proof | Final LinkedIn post published | manual | draft in §5 below |
| Proof | Credential verified on the Datheon portal | manual | Datheon portal |
| Certification | Code review completed on the submitted work | manual | Datheon review |

The five ticked groups in the plan's §4 are exactly the lines above that read
`pass`; the boxes that stay empty are the ones no run in this repository can
close, and the plan now says so on each line instead of leaving a reader to
guess.

---

## 4. The freeze

`docs/submission/freeze.json` is the record:

```json
{
  "schema": 1,
  "version": "1.1.0",
  "generated": "2026-11-17",
  "file_count": 146,
  "sha256": { "README.md": "…", "datheon/cli/hangman.py": "…", "…": "…" }
}
```

`tools/final_verification.py` fails with `submission.freeze` when a pinned file
changes, when a pinned file disappears, when a file appears that the manifest
does not cover, or when the manifest's version no longer matches
`pyproject.toml`. Re-cutting it is a deliberate act — `--manifest` — and a
deliberate act shows up in a diff, which is the whole point.

### What it pins, and what it deliberately does not

Pinned: the thirteen core modules, `datheon/`, `projects/`, `tools/`, `tests/`,
the documentation pages and assets, the workflows, and the root files that
declare how the project ships.

Not pinned: `docs/internship_milestones_plan.md`,
`docs/daily_learning_log.md`, this page, and the manifest itself. All four change
as part of delivering a day — the ledger gains a PR number, the log gains an
entry. Pinning them would make the freeze expire in the same commit that cuts
it, and a gate that is always red is a gate that gets deleted.

### Why digests and not a commit SHA

A commit SHA records a position in a history that a squash-merge rewrites; the
manifest is cut on the branch and would name a commit that never lands on `main`.
A digest of the content is true of the file wherever it sits, survives the
squash, and can be checked by anyone who clones the repository without trusting
either the history or this log.

---

## 5. The final post

Draft for LinkedIn (Option C). It is written, not published: publishing is a
manual step performed by the intern, because nothing in this repository has — or
should have — the credentials to post as a person.

> **Five Python tools, four weeks, one repository.**
>
> Today I closed the last day of the Python Programming Internship with
> @Datheon (Batch 01, 2026 cohort), and I want to write down what the four weeks
> actually produced, because "five projects" undersells it.
>
> The brief was five standard-library tools: a Hangman game, a stock portfolio
> tracker, a file organizer, a rule-based terminal assistant and an OOP
> inventory system. All five shipped, and all five are installable —
> `pip install .` gives you `hangman`, `portfolio`, `organizer`, `assistant` and
> `inventory` as real commands with a documented exit-code contract.
>
> What I did not expect was how much of the learning would be in everything
> *around* the features:
>
> - **1,366 test functions and 2,464 assertions**, at 95.8% branch coverage,
>   with a CI floor of 90% that fails the build on a deliberate drop.
> - **A 12-cell CI matrix** — Ubuntu, macOS and Windows on Python 3.10 through
>   3.13 — which found two real portability bugs: a POSIX-only permission
>   assertion and a bash-only smoke loop.
> - **Property-based and fuzz testing** over the validators, loaders and
>   chatbot. The fuzzer found two defects in the intent loader that no
>   hand-written test had.
> - **Mutation testing** over the error catalogue: 182 mutants, 100% killed
>   after twelve new tests were written to kill the survivors.
> - **Twelve audit tools** in `tools/`, each with its own finding codes and an
>   exit code of `2` when it cannot measure — because a gate that passes when it
>   cannot look is worse than no gate.
> - **A tagged release** (v1.1.0), a generated documentation site, and a demo
>   GIF rendered from the tools' real output rather than recorded by hand.
>
> The last day was the one I learned the most from: I built a single verifier
> for the whole submission, and it immediately failed — on a checklist line that
> pointed at a document nobody had ever committed. Four weeks of tests had not
> caught it, because nothing had ever read the checklist as data.
>
> Repository: https://github.com/krishsharma-code/Datheon_Python_Internship
> Credential ID: DT-2026-O6Q0X
>
> #Python #SoftwareEngineering #Testing #CICD #Internship

---

## 6. What this day got wrong first

Five defects, all found before the branch was finished — which is the only kind
worth writing down.

1. **The checklist named a file that was never committed.** The plan's §4 pointed
   at `Datheon_LinkedIn_Post.md` as the evidence for the walkthrough post. The
   file does not exist and never did: the post was published on LinkedIn, where
   it belongs. The first run of the verifier reported it, and the line now says
   where the evidence actually is. Four weeks of gates never caught it because
   the checklist had never been read as data.

2. **`LICENSE` had no suffix, so it was never checked.** The path extractor
   treated a backticked token as a file claim only if it ended in `.md`, `.py`
   and friends — which silently skipped the one file in the checklist with no
   suffix at all. The claim set now names bare filenames explicitly.

3. **`--strict` reported PASS over a skipped suite.** The verdict function looked
   only for `fail` and `unmeasurable`, so a run with `--skip-slow` collected the
   skip, added it to the blocking list, and then returned `pass` anyway. The
   test that asserted strictness failed on the first try.

4. **`--manifest PATH` wrote a record the audit never read.** Cutting the
   manifest somewhere else produced a file, and the very next check looked for
   the default path and reported it missing. The path is now threaded through to
   the audit.

5. **`--static` graded checks it had not run as `unmeasurable`.** A fast
   claims-only run is not a run that could not measure; the checklist now reads
   `not run`, which is the honest word for it.

---

## What this day did not do

- **No verification of the CI run itself.** The twelve cells are decided by
  GitHub Actions after the merge, and this run has no network. The line stays
  `manual` rather than being ticked on faith.
- **No LinkedIn publication.** The post above is a draft. Posting as a person is
  a person's action; a scheduled job that holds those credentials is a worse
  idea than a paragraph of copy-paste.
- **No re-run of the slow campaigns.** Mutation testing and the fuzz corpus have
  their own workflows and their own caches; the verifier gates their *outputs*
  (the census, the coverage floor, the IO policy) rather than repeating hours of
  work on the last day.
