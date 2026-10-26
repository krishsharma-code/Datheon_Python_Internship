| Day | Objective | Key work | Definition of done |
| --- | --- | --- | --- |
| 22 | Make it installable | `pyproject.toml` (PEP 621) with a `console_scripts` entry point per tool; package layout finalised | `pip install -e .` exposes `hangman`, `portfolio`, `organizer`, `assistant`, `inventory` |
| 23 | Prove it ships | Build wheel + sdist; install into a clean venv and run each entry point; `twine check` | Built artefacts install and run from a clean environment |
| 24 | Test where users actually are | CI matrix: ubuntu-latest / macos-latest / windows-latest × Python 3.10, 3.11, 3.12, 3.13 | All matrix cells green; pip cache enabled |
| 25 | Publish the docs | MkDocs (Material) site with API reference from docstrings, deployed to GitHub Pages | Live docs URL linked from the README |
| 26 | Cut a release | SemVer bump, `CHANGELOG.md`, annotated git tag, GitHub Release with artefacts | Release visible on the repo with attached wheel/sdist |
| 27 | Make the submission shine | README final pass, architecture diagram, demo GIF/screenshots, licence review | README reads well cold; assets embedded |
| 28 | Verify and submit | Full local + CI green run, repository freeze, certificate checklist, final summary | Checklist complete; submission package ready |

---

## 3. Daily scheduled-run execution contract

The cron job **"Datheon internship daily progress"** fires at **09:00 IST** and executes exactly one milestone per run, in this order:

1. **Read this file.** Parse the Progress Ledger in §1.
2. **Select the milestone:** the lowest-numbered row whose `Status` is `pending`.
   That row — and only that row — is the day's work. Its `Branch` and
   `Deliverable` columns are the mandate.
3. **Idempotency:** a day counts as complete only when its row reads `done` and carries a PR number. If a run fails partway, the row stays `pending` and the next run retries it. **No day can be executed twice**, and no day can be skipped.
4. **Catch-up rule:** the `Target date` column is advisory. If a run was missed or the calendar has drifted, the run still picks the earliest `pending` row — it never jumps ahead. The ledger is authoritative; the calendar is a target.
5. **Never idle:** if every row is `done`, switch to maintenance mode and take the highest-value item from the Backlog (§5), or ship a reliability/polish improvement. A run must always end with a merged PR.