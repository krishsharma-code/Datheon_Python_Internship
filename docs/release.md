# Release automation

How a version of this repository becomes a tag, a wheel, an sdist and a GitHub
Release — and why none of those four can quietly disagree with the other three.

Day 26 of the internship. The pipeline lives in
`.github/workflows/release.yml`; the audit that keeps it honest lives in
`tools/release.py`; the history it publishes lives in `CHANGELOG.md`.

---

## 1. The four declarations of one version

A release states its version in four places. Nothing in Python, `pip`, `twine`
or GitHub makes them agree:

| Where | What it drives | Why it drifts |
| --- | --- | --- |
| `pyproject.toml` → `[project] version` | The wheel filename, the `METADATA` version, the sdist root directory, PyPI's immutability rule | Edited by hand at bump time |
| `datheon/__init__.py` → `__version__` | What `importlib.metadata` and `datheon.__version__` report | A second hand edit |
| `cli_contract.py` → `PROGRAM_VERSION` | What every `--version` banner prints — the only one a *user* sees | A third hand edit |
| `CHANGELOG.md` | What the version contains, and therefore what the Release notes say | A fourth hand edit |

Bump one and forget the rest and **every existing check still passes**:
`pytest` is green, `twine check` is happy, the wheel builds. The defect appears
later, as a wheel called `1.1.0` that prints `1.0.0`, or a Release page
describing work that is not in the tag.

So the audit fails on a disagreement:

```text
[FAIL] version.banner-mismatch: PROGRAM_VERSION is '1.0.0' but the distribution is '1.1.0'
       at cli_contract.py
```

A bump is therefore a three-file edit — `pyproject.toml`,
`datheon/__init__.py`, `cli_contract.py` — plus the changelog entry. The tests
that used to hard-code `"1.0.0"` now read the declared version, so the *next*
bump does not have to touch the test suite at all.

---

## 2. Versioning policy

[Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html), with the
mapping this project actually uses:

| Change | Bump | Example |
| --- | --- | --- |
| A tool's command-line interface, file format or exit-code contract changes in a way that breaks a caller | **MAJOR** | Renaming `--log-level` |
| A new tool, a new subcommand, a new output mode, a new packaged module | **MINOR** | Day 22 added five console scripts → `1.1.0` |
| A fix that changes no interface | **PATCH** | Correcting a malformed-row message |

Pre-release versions (`1.2.0-rc.1`) are permitted by the audit and by the tag
pattern, and are published as GitHub pre-releases rather than latest.

The version is **not** derived from the date or from the commit count. A release
is a deliberate statement that the contents are ready, and the changelog entry
is where that statement is made.

---

## 3. `CHANGELOG.md` conventions

[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), which the audit
enforces structurally rather than by good intentions:

- a `# Changelog` title, and a citation of both specifications in the preamble;
- a `## [Unreleased]` section, always present, for work that has not shipped.
  Its only extra section is `### Planned`;
- one `## [X.Y.Z] - YYYY-MM-DD` entry per released version, **newest first**;
- only the six defined sections — `Added`, `Changed`, `Deprecated`, `Removed`,
  `Fixed`, `Security` — because a seventh category is one a reader does not
  know to look for;
- a link definition per version at the foot of the file, pointing at the tag:
  `[1.1.0]: https://github.com/…/releases/tag/v1.1.0`;
- no duplicate version, no date in the future, no entry without a section.

An entry is written for the person upgrading, not for the person who wrote the
code: what changed, and what they have to do about it.

---

## 4. The pipeline

```text
annotated tag v1.1.0 pushed
        │
        ▼
  actions/checkout (fetch-depth: 0)
        │
        ▼
  python tools/release.py --tag v1.1.0 --strict     ← refuses a tag ≠ version
        │
        ▼
  python -m build                → dist/*.whl, dist/*.tar.gz
        │
        ▼
  twine check dist/*             ← the metadata PyPI would validate
        │
        ▼
  python tools/release.py --notes v1.1.0 --notes-file RELEASE_NOTES.md
        │
        ▼
  gh release create v1.1.0 --notes-file RELEASE_NOTES.md dist/*
```

Two details carry most of the value:

1. **The tag check runs before the build.** A mistyped tag fails in seconds,
   before an artefact exists that would have to be thrown away — and before a
   Release exists that would have to be deleted.
2. **The notes come from the changelog.** `--notes-file` is fed by
   `tools/release.py --notes`, which prints the `CHANGELOG.md` entry verbatim.
   The Release page and the changelog are one document, so they cannot drift.

`permissions: contents: write` is the only permission the workflow holds, and
the token is the one Actions injects (`${{ github.token }}`). No credential is
written into the file; the audit fails on `ghp_`, `github_pat_`, `gho_`, `ghs_`
or `xoxb-` anywhere in it.

---

## 5. Cutting a release

```bash
# 1. Bump the version in all three declarations.
#    pyproject.toml · datheon/__init__.py · cli_contract.py
$EDITOR pyproject.toml datheon/__init__.py cli_contract.py

# 2. Move the Unreleased items into a dated entry, newest first.
$EDITOR CHANGELOG.md

# 3. Audit before committing: the tag is checked once it exists, so pass it here.
python tools/release.py --tag v1.1.0 --strict
pytest -q

# 4. Land it through a pull request — never directly on main.
git switch -c feat/datheon-day-26-release
git commit -am "feat(release): cut 1.1.0"
# … open the PR, review, squash-merge …

# 5. Tag the merge commit, annotated, and push the tag.
git switch main && git pull
git tag -a v1.1.0 -m "Datheon Python Internship 1.1.0"
git push origin v1.1.0          # ← this is what starts the workflow
```

The tag is **annotated** (`-a`), not lightweight: a release tag carries a
tagger, a date and a message, and `git describe` prefers it.

---

## 6. Verifying a published release

```bash
# The Release exists, and its assets are the two artefacts.
gh release view v1.1.0 --repo krishsharma-code/Datheon_Python_Internship

# The wheel installs and reports the version the tag claims.
python -m venv /tmp/verify && /tmp/verify/bin/pip install \
  https://github.com/krishsharma-code/Datheon_Python_Internship/releases/download/v1.1.0/datheon_python_internship-1.1.0-py3-none-any.whl
/tmp/verify/bin/hangman --version      # hangman 1.1.0
```

If any of those disagree, the audit would have caught it before the tag was
pushed — which is the point of running it in the pipeline rather than trusting
the person cutting the release.

---

## 7. When a release is wrong

A published version is immutable: PyPI refuses to accept the same version twice,
and a downloaded wheel is already on someone's disk. So the correction is always
forward:

| Situation | Action |
| --- | --- |
| A defect in the published code | Fix it, bump **PATCH**, release again. Never re-tag. |
| The Release notes are wrong | Edit the Release on GitHub. The changelog entry is still the source; fix it in the next PR so the two agree again. |
| The tag points at the wrong commit | Delete the tag and the Release (the artefacts were never installable), then re-tag the right commit. Only before anyone has downloaded it. |
| A credential was published | Rotate it first, then follow the security policy. The audit exists so this does not happen. |

---

## 8. Auditing the pipeline

```bash
python tools/release.py                     # audit this repository
python tools/release.py --list-releases     # 1.1.0, 1.0.0
python tools/release.py --notes 1.1.0       # the entry, ready to paste
python tools/release.py --tag v1.1.0 --strict
python tools/release.py --json              # machine-readable verdict
```

Exit codes follow the repository-wide contract: `0` every check passed, `1` at
least one finding, `2` the audit could not reach a verdict. A gate that cannot
measure never reports success.

The finding codes are the contract the test suite asserts on:

| Code | Meaning |
| --- | --- |
| `version.missing` / `version.invalid` | No version, or one that is not SemVer |
| `version.package-mismatch` / `version.banner-mismatch` | The declarations disagree |
| `version.package-missing` / `version.banner-missing` | A declaration is absent entirely |
| `tag.mismatch` / `tag.invalid` | The tag is not `v` + the declared version |
| `changelog.missing` / `changelog.empty` | No file, or no entry in it |
| `changelog.unreleased-missing` | No `## [Unreleased]` section |
| `changelog.version-missing` | The declared version has no entry |
| `changelog.date-missing` / `-invalid` / `-future` | The release date is wrong |
| `changelog.order` / `changelog.duplicate` | Entries are out of order, or repeated |
| `changelog.section-unknown` / `-entry-empty` | An invented section, or an empty one |
| `changelog.link-missing` / `-link-mismatch` | No link definition, or one pointing elsewhere |
| `workflow.trigger` / `workflow.permissions` | The pipeline cannot be started, or cannot publish |
| `workflow.build` / `-twine` / `-release` / `-assets` / `-notes` | A publishing step is missing |
| `workflow.tag-check` | The tag is not verified against the version |
| `workflow.secret` | A credential is written into the workflow |
| `docs.page-missing` / `docs.nav-missing` | The policy page is absent, or unreachable |
| `docs.readme-releases` / `-changelog` | The README links neither the Release nor the changelog |

---

## 9. What this day does not do

- **No PyPI publish.** The release is a GitHub Release with attached artefacts.
  Publishing to PyPI needs an account, a trusted-publisher configuration and a
  name that is actually free; attaching the wheel is the part that proves the
  build, and it is reversible.
- **No automatic version bump.** Deciding MAJOR/MINOR/PATCH is a judgement about
  the contents, and a tool that guesses it guesses wrong at the worst moment.
  The audit checks that the decision was made consistently, not that it was
  made automatically.
- **No signed tags.** GPG signing needs a key on the runner and a key in the
  repository's history; it is a reasonable next step and not a Day 26 one.
