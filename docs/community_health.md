# Community health: the two documents a stranger reads

Every other gate in this repository checks code. `pytest` proves the tests pass,
`flake8` proves the style holds, the coverage gate proves the tests reach the
code, the Docker audit proves the container builds. Two files sit outside all of
that: [`SECURITY.md`](https://github.com/krishsharma-code/Datheon_Python_Internship/blob/main/SECURITY.md)
and [`CODE_OF_CONDUCT.md`](https://github.com/krishsharma-code/Datheon_Python_Internship/blob/main/CODE_OF_CONDUCT.md).
They are read by someone who has just found the repository — a researcher with a
bug, or a contributor deciding whether this is a safe place to spend an evening —
and they are the two documents where a stale line does real damage.

A security policy that names a release line nobody ships tells a reporter their
bug is out of scope. A policy with no private channel pushes the report into a
public issue, which is a disclosure. A code of conduct with no enforcement
contact is a promise with nobody behind it. A `TODO` survives review because a
reviewer reads intent, not text.

`tools/community_health.py` reads both documents and reports a stable finding
code for each defect, so the declaration is checked instead of trusted.

## Running it

```bash
python tools/community_health.py              # human-readable table
python tools/community_health.py --json       # machine-readable verdict
python tools/community_health.py --strict     # warnings count as failures
python tools/community_health.py --root .     # audit another checkout
```

Exit codes follow the same contract as every other audit in `tools/`:

| Code | Meaning |
| --- | --- |
| `0` | Both documents pass every check. |
| `1` | At least one finding (with `--strict`, warnings count too). |
| `2` | The audit could not reach a verdict — the root is not a directory, or neither policy exists anywhere. |

`2` rather than `0` for "I could not look" is deliberate: a gate that passes when
it cannot measure is not a gate.

## What it checks

### Location

GitHub's community profile reads these files from the repository root, from
`.github/` and from `docs/`, in that order. The audit looks in the same three
places and reports `health.missing` when a policy is in none of them — a policy
in the wrong directory is a policy GitHub shows as absent.

### `SECURITY.md`

| Code | Fires when |
| --- | --- |
| `health.section` | A section a reporter needs is missing: supported versions, reporting, expectations, scope, safe harbour. |
| `health.channel` | No private reporting channel is named — no security-advisory URL, no `mailto:`, no statement that private reporting is enabled. |
| `health.version` | The support matrix's table rows do not name the release line in `pyproject.toml`. This is the drift check: bump the version and the matrix must follow. |
| `health.response` | The policy promises no response time in days, so a reporter cannot tell silence from a decision. |
| `health.placeholder` | `TODO`, `TBD`, `FIXME`, `lorem ipsum` or an `<insert …>` placeholder is left in the document. |
| `health.secret` | A token-shaped string (`ghp_`, `github_pat_`, `xoxb-`, `AKIA`) appears in the document. |

The version check reads only **table rows**, never prose. A version mentioned in
a sentence is not a support claim, and counting prose would let the matrix rot
while the audit stayed green.

### `CODE_OF_CONDUCT.md`

| Code | Fires when |
| --- | --- |
| `health.covenant` | The document does not declare which Contributor Covenant revision it adapts, or declares one older than 2.0. |
| `health.enforcement` | No enforcement contact is named — no `@handle`, no `mailto:`, no address. |
| `health.placeholder` | A placeholder is left in the document. |
| `health.secret` | A token-shaped string appears in the document. |

The covenant version is read from the window of text around the words
"Contributor Covenant", so a `3.10` elsewhere in the file is a Python version,
not a declaration.

### Reachability

| Code | Fires when |
| --- | --- |
| `health.link` | `README.md` or `CONTRIBUTING.md` does not name the policy. A policy nobody links to is a policy nobody reads. |
| `health.docs` | This page is missing, or no longer names a code the tool can emit. |

The last check is the trick the pre-commit audit uses on its own page: the
documentation fails the build when it stops describing the tool, so the table of
codes above cannot drift behind the checks.

## What it does not prove

The audit reads two Markdown files. It does not open an advisory, send a
message, or prove that the address in the code of conduct reaches a human — it
proves the documents say what this repository needs them to say, and that the
support matrix matches the version being shipped.

Whether GitHub has private vulnerability reporting switched on for the
repository is a setting in the GitHub UI, not a fact in the working tree; the
policy links the form that only exists when it is on. The manual line in the
[submission checklist](final_verification.md) is the place that stays manual.

## Why these two documents, on this repository

The five tools are portfolio software, but they are installable software too:
`pip install -e .` puts five commands on a `PATH`, and the dataset loaders read
files a user names. That is enough of an attack surface to deserve a written
policy, and enough of a public space to deserve a written standard of behaviour.
Shipping them with the checks that keep them true is the difference between a
promise and a gate.
