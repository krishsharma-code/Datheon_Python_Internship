# Documentation site — MkDocs, mkdocstrings and GitHub Pages

**Day 25 · Branch `feat/datheon-day-25-docs-site`**

Twenty-four days of work produced twenty markdown files in `docs/`, and every
one of them was written to be read in a pull request. That is a fine place for a
reviewer and a poor place for a reader: to find the persistence contract you had
to know that `docs/persistence.md` existed, and to check a signature you had to
open the source.

This day turns that directory into a site — **MkDocs with the Material theme**,
an **API reference generated from the docstrings**, and a **GitHub Pages
deployment** that rebuilds on every push to `main`. And, because a site is a
declaration that rots silently, it adds `tools/docs_site.py`, the gate that
keeps the declaration true.

---

## 1. What was built

| Piece | File | Why |
| --- | --- | --- |
| Site configuration | `mkdocs.yml` | Nav, theme, plugins, `strict: true` |
| Landing page | `docs/index.md` | The five tools, quickstart, quality-gate table |
| API reference | `docs/api/*.md` | One page per shipped module, rendered by `mkdocstrings` |
| Deployment | `.github/workflows/docs.yml` | Build strictly, publish to Pages |
| Gate | `tools/docs_site.py` | Audits the declaration against the repository |
| Toolchain | `pyproject.toml` → `[project.optional-dependencies].docs` | `pip install -e ".[docs]"` |
| Link | `README.md` | Badge and a live URL, so the site is findable |

```bash
pip install -e ".[docs]"
mkdocs serve                 # http://127.0.0.1:8000
mkdocs build --strict        # what CI runs
python tools/docs_site.py    # the audit alone
```

Live site: <https://krishsharma-code.github.io/Datheon_Python_Internship/>

---

## 2. The API reference is generated, not written

Every page under `docs/api/` is a heading, a sentence of context and a
directive:

```markdown
# `persistence`

Atomic, versioned writes with read-back validation.

::: persistence
```

`mkdocstrings` imports `persistence`, reads its type hints and its Google-style
docstrings, and renders the module: classes, functions, signatures, `Args:`,
`Returns:` and `Raises:` sections. Nothing is copied, so nothing can drift.

That only works because of two earlier decisions:

- **Every public callable already carries a type hint and a docstring** (Day 1's
  standard, enforced ever since). A reference generated from source is only as
  good as the annotations in it.
- **The modules are importable by name.** `paths: [.]` puts the repository root
  on the handler's import path, which is exactly what the root-level layout
  (`errors.py`, `validators.py`, …) needs.

The handler is configured for `docstring_style: google` — the style the tree is
written in. With the wrong style the sections do not fail, they silently render
as one paragraph, which is why the audit checks the option rather than trusting
it.

---

## 3. Why a gate, and not just `strict: true`

`strict: true` in `mkdocs.yml` is necessary and not sufficient. It fails the
build on a broken link or an unknown nav page — good. It does **not** fail when:

- a page exists on disk and is absent from the nav (MkDocs builds it; nobody can
  reach it);
- a module that `pyproject.toml` ships has no page at all (the site is simply
  smaller than the package);
- the README stops linking the site (a published site nobody finds);
- the deployment workflow uploads an artefact no job deploys;
- `strict` itself is flipped to `false` in a refactor, which turns every one of
  the above back into a line in a log.

So `tools/docs_site.py` checks the site against the repository, with a stable
code per defect:

| Code | Meaning |
| --- | --- |
| `config.site-name` / `config.site-url` / `config.site-url-mismatch` | The site does not know its own name or address |
| `config.site-url-relative` | `site_url` is not absolute, so canonical links are broken |
| `config.theme` | Not Material — every palette and feature option is ignored |
| `config.strict` | Warnings would ship |
| `config.docs-dir` | The docs directory is missing |
| `config.site-dir-ignored` | The build output is not git-ignored (warning) |
| `nav.missing` | No nav, so the structure is the file tree |
| `nav.entry-missing` | A nav page does not exist |
| `nav.orphan` | A page exists and is unreachable from the nav |
| `nav.duplicate` | The same page is listed twice (warning) |
| `nav.index-first` | The home page is not first (warning) |
| `nav.path-invalid` | A nav path is absolute or Windows-style |
| `api.plugin-missing` | `mkdocstrings` is not enabled |
| `api.docstring-style` | Not Google style (warning) |
| `api.paths` | The handler cannot import the root-level modules |
| `api.page-missing` | A shipped module has no reference page |
| `api.unknown-target` | A `:::` target that `pyproject.toml` does not ship |
| `api.duplicate-target` | The same module rendered twice (warning) |
| `api.directive-missing` | An API page with no directive under it |
| `deploy.workflow-missing` | Nothing publishes the site |
| `deploy.trigger` | The workflow does not run on `main` |
| `deploy.permissions-pages` / `deploy.permissions-id-token` | Pages cannot publish |
| `deploy.artifact` | No Pages artefact is uploaded |
| `deploy.build` / `deploy.strict-build` | No `mkdocs build`, or not `--strict` |
| `deploy.action` / `deploy.environment` / `deploy.needs` | The deploy job is not wired |
| `deploy.audit` | The build job skips this audit (warning) |
| `readme.missing` / `readme.no-site-link` | The site is linked from nowhere |
| `readme.no-docs-badge` | No docs badge on the README (warning) |
| `packaging.docs-extra-missing` / `packaging.docs-extra-incomplete` | The toolchain has no declared home |

Exit codes follow the repository's convention: `0` pass, `1` findings, `2` the
audit could not measure (config missing or unparsable, PyYAML absent). A gate
that cannot measure never reports success.

---

## 4. Deployment

`.github/workflows/docs.yml` has two jobs:

- **build** — installs the `docs` extra, runs `tools/docs_site.py --strict`, then
  `mkdocs build --strict`, then uploads `site/` with
  `actions/upload-pages-artifact`. It runs on pull requests too, so a broken nav
  fails the PR rather than the deployment.
- **deploy** — `actions/deploy-pages` from a `github-pages` environment, gated on
  `github.ref == 'refs/heads/main'` and `needs: build`. A pull request builds; it
  never publishes.

Permissions are least-privilege at the workflow level: `contents: read`,
`pages: write`, `id-token: write` (Pages authenticates with an OIDC token).
`concurrency: pages` serialises deployments rather than cancelling one halfway.

The repository setting this depends on: **Settings → Pages → Build and
deployment → Source: GitHub Actions**.

---

## 5. Two defects found on the way

1. **Twenty relative links pointed outside the site.** `docs/cli_contract.md` and
   eight other pages linked `../validators.py`, `../tests/test_validation.py` and
   so on. Those are valid on GitHub and invalid on the site — MkDocs reported
   each as *"the target is not found among documentation files"*, and
   `--strict` refused to build. They are absolute `blob/main` URLs now: correct
   in both places, and the site build proves it.
2. **One intra-page anchor was stale.** `docs/performance_profiling.md` linked
   `#reproducing-the-measurements`, but the heading had been renumbered to
   `## 6. Reproducing the measurements`, whose slug is
   `#6-reproducing-the-measurements`. A warning locally, a broken jump on the
   published page.

Neither would have failed a test. Both failed the strict build the first time it
was run — which is the argument for building strictly in CI rather than trusting
that the pages look right.

---

## 6. Verification

| Check | Result |
| --- | --- |
| `python -m compileall` (projects, tests, datheon, tools, conftest) | clean |
| `flake8 .` | 0 findings over 84 files |
| `black --check .` | 84 files unchanged |
| `pytest` | 1,385 passed, 1 skipped, 0 failed |
| `mkdocs build --strict` | clean — 39 pages, 15 documented modules |
| `python tools/docs_site.py --strict` | PASS — 39 pages, 15 modules, 0 warnings |
| Assertion census | 1,134 test functions / 2,098 assertions (floor 200) |
| Coverage gate | 95.8% over 3,368 statements (floor 90%) |
| `tools/io_audit.py --strict` | no real I/O in the test suite |
| `tools/ci_matrix.py` | PASS — 12 cells, 0 warnings |

The audit itself is covered by 92 tests: each one breaks a single thing in a
synthetic site and asserts the *named* finding that results, and the last block
runs the audit against this repository's real `mkdocs.yml`, `pyproject.toml`,
`README.md` and `.github/workflows/docs.yml`.

---

## 7. Adding a page or a module

- **New guide** — write `docs/<topic>.md`, add it to `nav` in `mkdocs.yml`. If
  you forget the second step, `nav.orphan` fails the build.
- **New core module** — add it to `py-modules` in `pyproject.toml`, write
  `docs/api/<module>.md` with a `::: <module>` directive and add it to the API
  section of the nav. If you forget, `api.page-missing` fails the build.

That is the whole contract: the site cannot claim less than the package ships,
and no page can exist without being reachable.
