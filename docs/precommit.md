# Pre-commit: the gates CI already runs, moved before the commit

Every rule in `.pre-commit-config.yaml` already existed in this repository.
`black --check .` and `flake8 .` run in `.github/workflows/ci.yml`; the line
lengths live in `pyproject.toml` and `setup.cfg`; `.gitattributes` normalises the
checkout to LF. What was missing was the *moment* they run. A contributor learned
about a formatting error after the push, from a red CI cell, in a review thread —
the most expensive place to learn it, and the one that wastes the most of a
reviewer's attention.

Pre-commit moves the same checks to `git commit`, where the fix is a keystroke.

```bash
pip install pre-commit          # or: pip install -e ".[dev]"
pre-commit install              # writes .git/hooks/pre-commit
pre-commit run --all-files      # what the first commit will do, over the whole tree
```

## What is installed

| Hook | Repo | Why it is here |
| --- | --- | --- |
| `black` | `psf/black` @ `24.10.0` | The formatter CI checks. No `--line-length`: the hook reads `[tool.black]`. |
| `flake8` | `PyCQA/flake8` @ `7.1.1` | The linter CI runs. No `--max-line-length`: the hook reads `setup.cfg`. |
| `end-of-file-fixer` | `pre-commit/pre-commit-hooks` @ `v5.0.0` | A file that does not end in one newline is a diff at the end of every future patch. |
| `trailing-whitespace` | same | Invisible in a review, rejected by `git diff --check`, and noise in every blame. |
| `mixed-line-ending` (`--fix=lf`) | same | Keeps a CRLF file from being committed into an LF-only tree. |
| `check-yaml`, `check-toml` | same | The workflows and `pyproject.toml` are parsed by CI; a syntax error should fail here first. |
| `check-added-large-files` (`--maxkb=1024`) | same | The demo GIF is 824 KB and deliberate; a second one should be a decision. |
| `check-merge-conflict` | same | A conflict marker that survives a commit is a syntax error three commits later. |

## Why it is audited

A configuration is a declaration, and declarations rot quietly: YAML does not
validate them, and a hook that is mis-pinned, mis-scoped or absent simply does
less work while still exiting `0`. `tools/precommit_config.py` reads the file as
data and checks it against the repository's own declarations — no network, no
hook installation, no daemon:

```bash
python tools/precommit_config.py            # the whole audit
python tools/precommit_config.py --json     # one machine-readable document
```

Exit codes follow the rest of the repository: `0` clean, `1` at least one
finding, `2` there is no configuration to read.

### The finding codes

| Code | What it means |
| --- | --- |
| `precommit.file` | The configuration is missing, empty, or not a YAML mapping. |
| `precommit.repo` | A `repos:` entry has no URL, no hooks, or a hook with no `id`. |
| `precommit.rev` | A `rev` is missing, empty, or a branch (`main`, `HEAD`, `latest`) rather than a release. |
| `precommit.hook` | A required hook — `black`, `flake8`, `end-of-file-fixer` — is absent. |
| `precommit.duplicate` | The same hook id is declared more than once. |
| `precommit.version` | A pin is below the floor declared in `pyproject.toml`'s `dev` extra. |
| `precommit.args` | A hook argument contradicts `pyproject.toml`/`setup.cfg`, or a required one is missing. |
| `precommit.scope` | A `files:`/`exclude:` pattern hides shipped code from the gate. |
| `precommit.stage` | A stage name pre-commit does not know — a hook that never runs. |
| `precommit.secret` | A token-shaped string is in the configuration. |
| `precommit.whitespace` | A tracked file would fail one of the hygiene hooks today. |
| `precommit.docs` | This page is missing, not in the site navigation, or does not document a code. |

The last one is the reason this table has to be complete: the audit fails when a
code it can emit is not named here, so the documentation cannot fall behind the
gate.

### One declaration, not two

The audit compares three pairs of numbers that are easy to state twice and then
let drift:

* `black`'s pin against `black>=24.1` in `pyproject.toml` — a hook older than the
  floor formats files the CI job then rejects.
* `[tool.black] line-length = 100` against any `--line-length` passed to the
  hook — an argument silently overrides the project's setting.
* `[flake8] max-line-length = 100` in `setup.cfg` against any
  `--max-line-length` passed to the hook.

The configuration therefore passes **no** line-length argument at all: both hooks
read the same files CI reads.

### Markdown's two trailing spaces

`trailing-whitespace` would strip the two trailing spaces that are a hard line
break in Markdown, which would silently reflow every paragraph in `docs/`. The
hook is given `--markdown-linebreak-ext=md`, and the audit fails if that argument
goes missing or stops covering `md`. The local hygiene check applies the same
rule: a markdown line with exactly two trailing spaces is a line break, not a
defect.

## What the first run found

The hygiene half of the audit reads the files git tracks — the same set the hooks
see — and the first run found two shipped datasets that did not end in a newline:

| File | Defect |
| --- | --- |
| `projects/04_terminal_assistant/intents.json` | No final newline. |
| `projects/04_terminal_assistant/sample_faq.json` | No final newline. |

Neither broke a test, because `json.load` does not care. Both would have shown up
in the first commit that touched either file, as an unrelated one-line diff at the
end of the patch — which is exactly the kind of noise a hygiene hook exists to
keep out of a review. Both now end in a single newline.

## What the audit cannot prove

It reads the configuration; it does not install the hooks. Whether the pinned
revisions still resolve on PyPI, and whether `pre-commit` itself runs on a
contributor's machine, needs the network and a person. The audit is deliberately
network-free so it can run in the normal `pytest` suite on every push — the
guarantee it gives is "the declaration agrees with the repository", not "the
hooks ran".

## Verification

| Check | Result |
| --- | --- |
| `python -m compileall` | clean |
| `flake8 .` | 0 findings |
| `black --check .` | unchanged |
| `pytest` | passing, 0 failed |
| `python tools/precommit_config.py` | PASS — 3 repos, 9 hooks, 201 files |
| `python tools/precommit_config.py --json` | one document, `ok: true` |
