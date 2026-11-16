# Submission polish: the README as an artefact (Day 27)

The README is the only file in this repository with no test behind it. Every
other claim in the project — the exit codes, the coverage floor, the CI matrix,
the release version — is re-measured on each run by something in `tools/`. The
README was measured by nothing, which is how a submission ends up with a link to
a diagram nobody committed and a quickstart that no longer clones.

Day 27 closes that gap in three parts: a diagram, a demo, and an audit that
keeps both honest.

---

## 1. The architecture diagram

`docs/assets/architecture.svg` is a hand-authored SVG — one page, five layers:

| Layer | Modules |
| --- | --- |
| Entry points | `datheon/cli/` shims over `projects/<task>/main.py` |
| Contract | `cli_contract.py`, `validators.py`, `errors.py`, `edge_cases.py` |
| Core | `logging_config.py`, `structured_logging.py`, `persistence.py`, `caching.py`, `dataset_loader.py`, `profiling.py`, `observability.py`, `fuzzing.py`, `error_catalogue.py` |
| Artefacts | bundled datasets, `~/.datheon/logs`, `portfolio_summary.csv`, activity and conversation logs |
| Gates | pytest, the static audits, the CI matrix, the MkDocs build, the release pipeline |

**Why SVG and not a PNG.** A PNG is a screenshot of a diagram: it cannot be
diffed, its text cannot be searched, and it has to be re-exported from whatever
produced it. An SVG is text, so a reviewer reads the diff of a diagram change
the same way they read the diff of a module, and GitHub renders it natively at
any zoom. The cost is that a hand-authored SVG can be malformed — which is
exactly what the audit checks.

The diagram is embedded twice on purpose: in the repository README, where a
reviewer lands, and on the documentation home page, where the site's readers
land. The audit fails if either stops showing it.

---

## 2. The demo asset

`docs/assets/demo/quickstart.gif` is a fifteen-second animation of the five
tools running. It is generated, not recorded:

```text
docs/assets/demo/transcript.json      captured output, one session per command
        │
        ▼  python tools/demo_assets.py
docs/assets/demo/quickstart.gif       fixed 860×402 window, 32-colour palette
```

`--capture` runs the five commands from the source tree, scrubs the two things
that are true only of the capturing machine — the scratch directory's absolute
path and the organizer's measured duration — and rewrites the transcript. The
transcript is the source of truth: editing it by hand is pointless, because the
next capture overwrites it, and that is the point. A demo that is retyped drifts
away from the tool it claims to show.

The renderer sizes the window once, from the longest session, because a GIF
cannot grow a frame; the transcript scrolls inside it like a real terminal. The
first attempt grew the window frame by frame, and Pillow cropped every frame
after the first to the initial height — a bug that produced a valid GIF showing
one command. The frames are now all the same size by construction.

### Budget

The asset is a trailer, not a transcript. Three limits keep it embeddable:

- **Frames** — commands are typed four characters at a time and each session
  rests for eight frames, so 129 frames are built for five commands (~14 s);
  Pillow merges the identical hold frames, leaving 89 in the file.
- **Lines** — a session shows at most twelve output lines, then `…`.
- **Bytes** — `readme_audit` fails an asset over 2 MB. The committed GIF is
  ~0.8 MB at 32 colours; the 64-colour first cut was 4.3 MB, which is a page
  that takes seconds to paint and a repository that grows by 4 MB per re-shoot.

---

## 3. The audit

```bash
python tools/readme_audit.py            # table, exit 1 on any finding
python tools/readme_audit.py --json     # machine-readable
python tools/readme_audit.py --strict   # warnings count as failures
```

| Code | Fails when |
| --- | --- |
| `readme.section` | a required section (overview, quickstart, architecture, documentation, projects, licence) is missing |
| `readme.quickstart` | the quickstart lost its clone, install or test command |
| `readme.link` | a relative link or image does not resolve, or a `#fragment` no heading produces |
| `readme.asset` | an embedded asset is missing, empty, not valid SVG, a still GIF, or over budget |
| `readme.diagram` | the architecture diagram is not embedded in both the README and `docs/index.md` |
| `readme.demo` | the demo GIF is not referenced from the README |
| `readme.orphan` | an asset under `docs/assets` that no document references *(warning)* |
| `readme.placeholder` | `TODO`, `TBD`, `FIXME` or `lorem ipsum` left in the prose |
| `readme.secret` | a `ghp_`/`github_pat_`/`xoxb-`/`AKIA` shaped string |

### Two details that matter

**Code is not prose.** Link scanning strips fenced blocks and inline code spans
first. Otherwise every command in the quickstart that happens to contain
brackets — and every `[label]: url` example — is reported as a broken link, and
a gate with false positives gets switched off.

**Anchors are computed the way GitHub computes them.** Emoji and punctuation are
dropped, spaces become hyphens, so `## 🎯 Overview` is `#-overview` — the leading
hyphen is not a typo, it is what the emoji left behind. The audit rebuilds each
document's anchor set and checks every fragment against it, which is what makes
the table of contents safe to keep.

### Exit codes

`0` clean, `1` findings, `2` unmeasurable — the README is absent, so nothing can
be checked. The `2` is deliberate: an audit that returns `0` when it could not
look is worse than no audit, because it converts "unknown" into "pass". It is
the same rule `docs_site.py`, `distribution.py`, `ci_matrix.py` and
`release.py` follow.

---

## What this day did not do

- **No screenshots of the documentation site.** A PNG of a site that is one
  click away, and that changes on every release, is maintenance with no payoff;
  the demo GIF earns its bytes because the terminal is the product.
- **No video walkthrough.** The backlog lists a two-minute recording for the
  LinkedIn post. That needs a voice, a script and a screen recorder — not a
  reproducible build step, so it does not belong in this repository.
- **No badge or link liveness check.** Every badge points at GitHub or Shields,
  and the audit runs in a sandbox with no network. A `--online` mode that HEADs
  each external URL is the honest way to add it; a network call inside a
  hermetic gate is not.
