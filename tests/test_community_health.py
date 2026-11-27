"""Tests for the Day 35 community-health audit (``tools/community_health.py``).

The two documents this audit reads are the only artefacts in the repository that
nothing else measures, so its tests have to prove two things separately, the way
the README audit's do: that every finding *fires* when the defect is present — a
gate that never fires is indistinguishable from a gate that is not there — and
that the committed ``SECURITY.md`` and ``CODE_OF_CONDUCT.md`` currently pass.

Each test builds a small, complete checkout and breaks exactly one thing, so a
failure names the rule it broke rather than "the audit went red".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import community_health as ch  # noqa: E402  (path set up above)

#: A security policy that satisfies every rule.
SECURITY = """# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 2.0.x | Yes |
| 1.0.x | No |

## Reporting a vulnerability

Open <https://example.invalid/security/advisories/new> and describe the issue.

## What to expect

| Stage | Commitment |
| --- | --- |
| Acknowledgement | Within 3 business days. |

## Scope

In scope: everything in this repository.

## Safe harbour

Good-faith research is welcome.
"""

#: A code of conduct that satisfies every rule.
CONDUCT = """# Code of Conduct

## Our standards

Be kind, and critique the code rather than the person.

## Reporting

Report to @maintainer privately.

## Attribution

Adapted from the Contributor Covenant, version 2.1.
"""

#: A README that links both policies.
README = (
    "# Project\n\nSee [SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).\n"
)

#: A CONTRIBUTING that links both policies.
CONTRIBUTING = "# Contributing\n\nRead `SECURITY.md` and `CODE_OF_CONDUCT.md` first.\n"


def _write(path: Path, text: str) -> Path:
    """Write text to ``path``, creating parents.

    Args:
        path: Destination file.
        text: Content.

    Returns:
        The path, for chaining.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _checkout(root: Path, version: str = "2.0.0") -> Path:
    """Build a checkout that passes every check.

    Args:
        root: Directory to build it in.
        version: The ``[project] version`` to declare.

    Returns:
        The root, populated.
    """
    _write(root / "SECURITY.md", SECURITY)
    _write(root / "CODE_OF_CONDUCT.md", CONDUCT)
    _write(root / "README.md", README)
    _write(root / "CONTRIBUTING.md", CONTRIBUTING)
    _write(
        root / "docs" / "community_health.md",
        "# Community health\n\n" + "\n".join(f"- `{code}`" for code in ch.CODES) + "\n",
    )
    _write(root / "pyproject.toml", f'[project]\nname = "demo"\nversion = "{version}"\n')
    return root


@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    """A complete checkout that passes the audit.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The populated root.
    """
    return _checkout(tmp_path)


def _codes(findings: List[ch.Finding]) -> List[str]:
    """Return the codes of some findings, in order.

    Args:
        findings: Findings to inspect.

    Returns:
        Their codes.
    """
    return [finding.code for finding in findings]


# ---------------------------------------------------------------------------
# The committed documents
# ---------------------------------------------------------------------------


def test_committed_repository_passes() -> None:
    """The policies this pull request ships must be clean."""
    findings = ch.audit_repository(ROOT)
    assert findings == [], ch.render_table(ROOT, findings)


def test_committed_policies_are_found_in_the_root() -> None:
    """GitHub reads the root first, and so does the audit."""
    for name in ch.POLICY_FILES:
        located = ch.locate_policy(ROOT, name)
        assert located is not None
        assert located == ROOT / name


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


def test_both_policies_absent_is_two_findings(tmp_path: Path) -> None:
    """An empty checkout owes both documents."""
    findings = ch.audit_repository(tmp_path)
    assert _codes(findings)[:2] == ["health.missing", "health.missing"]


def test_policy_in_the_github_directory_is_accepted(tmp_path: Path) -> None:
    """GitHub reads ``.github/`` too, so a policy there is not missing."""
    _checkout(tmp_path)
    (tmp_path / "SECURITY.md").unlink()
    _write(tmp_path / ".github" / "SECURITY.md", SECURITY)
    findings = ch.audit_repository(tmp_path)
    assert "health.missing" not in _codes(findings)


def test_locate_policy_prefers_the_root(tmp_path: Path) -> None:
    """The root wins over ``docs/``, which is GitHub's own order."""
    _write(tmp_path / "docs" / "SECURITY.md", SECURITY)
    _write(tmp_path / "SECURITY.md", SECURITY + "\n<!-- root -->\n")
    located = ch.locate_policy(tmp_path, "SECURITY.md")
    assert located == tmp_path / "SECURITY.md"


def test_locate_policy_returns_none_when_absent(tmp_path: Path) -> None:
    """No candidate anywhere is ``None``, not an exception."""
    assert ch.locate_policy(tmp_path, "SECURITY.md") is None


# ---------------------------------------------------------------------------
# The security policy
# ---------------------------------------------------------------------------


def test_missing_section_is_a_finding(checkout: Path) -> None:
    """A reporter needs the safe-harbour promise, so losing it fails."""
    _write(checkout / "SECURITY.md", SECURITY.replace("## Safe harbour", "## Notes"))
    findings = ch.audit_repository(checkout)
    assert "health.section" in _codes(findings)
    assert any("safe harbour" in finding.detail for finding in findings)


def test_private_channel_is_required(checkout: Path) -> None:
    """Without a private route the only route left is a public issue."""
    text = SECURITY.replace("https://example.invalid/security/advisories/new", "the issue tracker")
    _write(checkout / "SECURITY.md", text)
    findings = ch.audit_repository(checkout)
    assert "health.channel" in _codes(findings)
    assert "disclosure" in next(f for f in findings if f.code == "health.channel").detail


@pytest.mark.parametrize(
    "marker",
    ["mailto:security@example.invalid", "private vulnerability reporting is enabled"],
)
def test_each_private_channel_marker_is_accepted(checkout: Path, marker: str) -> None:
    """A security address or a statement about the setting both count."""
    text = SECURITY.replace("https://example.invalid/security/advisories/new", marker)
    _write(checkout / "SECURITY.md", text)
    assert "health.channel" not in _codes(ch.audit_repository(checkout))


def test_stale_support_matrix_is_a_finding(checkout: Path) -> None:
    """The matrix names 2.0.x; shipping 3.1.0 makes it wrong."""
    _write(checkout / "pyproject.toml", '[project]\nname = "demo"\nversion = "3.1.0"\n')
    findings = ch.audit_repository(checkout)
    finding = next(f for f in findings if f.code == "health.version")
    assert "3.1" in finding.detail
    assert "2.0" in finding.detail


def test_support_matrix_ignores_prose(tmp_path: Path) -> None:
    """A version in a sentence is not a support claim."""
    _checkout(tmp_path)
    text = SECURITY.replace("| 2.0.x | Yes |", "| 0.9.x | Yes |") + "\nWe ship 2.0.0 today.\n"
    _write(tmp_path / "SECURITY.md", text)
    assert "health.version" in _codes(ch.audit_repository(tmp_path))


def test_version_lines_reads_only_table_rows() -> None:
    """Table parsing is the version check's whole premise, so it is tested alone."""
    text = "We ship 9.9 now.\n\n| Version | Supported |\n| --- | --- |\n| 1.4.x | Yes |\n"
    assert ch.version_lines(text) == [(1, 4)]


def test_unreadable_version_is_a_warning(checkout: Path) -> None:
    """No ``[project] version`` cannot fail a run it could not measure."""
    (checkout / "pyproject.toml").unlink()
    findings = ch.audit_repository(checkout)
    finding = next(f for f in findings if f.code == "health.version")
    assert finding.severity == "warning"
    assert not finding.is_error


def test_malformed_pyproject_is_a_warning(checkout: Path) -> None:
    """A broken TOML file is "unknown version", not a traceback."""
    _write(checkout / "pyproject.toml", "[project\nversion =")
    assert ch.project_version(checkout) is None


def test_response_commitment_is_required(checkout: Path) -> None:
    """Silence and a decision must not look the same to a reporter."""
    text = SECURITY.replace("Within 3 business days.", "As soon as possible.")
    _write(checkout / "SECURITY.md", text)
    assert "health.response" in _codes(ch.audit_repository(checkout))


def test_placeholder_in_a_policy_is_a_finding(checkout: Path) -> None:
    """A ``TODO`` is a promise the repository has not made yet."""
    _write(checkout / "SECURITY.md", SECURITY + "\nTODO: add the address.\n")
    assert "health.placeholder" in _codes(ch.audit_repository(checkout))


def test_token_in_a_policy_is_a_finding(checkout: Path) -> None:
    """The policy is the last place a credential should appear."""
    _write(checkout / "SECURITY.md", SECURITY + "\nghp_" + "a" * 24 + "\n")
    assert "health.secret" in _codes(ch.audit_repository(checkout))


# ---------------------------------------------------------------------------
# The code of conduct
# ---------------------------------------------------------------------------


def test_undeclared_covenant_is_a_finding(checkout: Path) -> None:
    """An adaptation that names no source is a licence problem and a promise."""
    text = CONDUCT.replace("Adapted from the Contributor Covenant, version 2.1.", "Our rules.")
    _write(checkout / "CODE_OF_CONDUCT.md", text)
    assert "health.covenant" in _codes(ch.audit_repository(checkout))


def test_old_covenant_revision_is_a_finding(checkout: Path) -> None:
    """2.0 is the floor; 1.4 is refused."""
    text = CONDUCT.replace("version 2.1", "version 1.4")
    _write(checkout / "CODE_OF_CONDUCT.md", text)
    finding = next(f for f in ch.audit_repository(checkout) if f.code == "health.covenant")
    assert "older than 2.0" in finding.detail


def test_covenant_version_ignores_unrelated_versions() -> None:
    """``Python 3.10`` elsewhere in the file is not a covenant declaration."""
    text = "Runs on Python 3.10 and 3.12.\n\nAdapted from the Contributor Covenant, version 2.1.\n"
    assert ch.covenant_version(text) == (2, 1)


def test_covenant_version_without_a_name() -> None:
    """No name, no version — even when the file is full of numbers."""
    assert ch.covenant_version("Version 2.1 of something else.") is None


def test_enforcement_contact_is_required(checkout: Path) -> None:
    """A code with nobody to report to is a code with no enforcement."""
    text = CONDUCT.replace("Report to @maintainer privately.", "Report it to someone.")
    _write(checkout / "CODE_OF_CONDUCT.md", text)
    assert "health.enforcement" in _codes(ch.audit_repository(checkout))


@pytest.mark.parametrize(
    "contact",
    ["@maintainer", "mailto:conduct@example.invalid", "conduct@example.invalid"],
)
def test_each_contact_form_is_accepted(checkout: Path, contact: str) -> None:
    """A handle, a ``mailto:`` link and a bare address all name a human."""
    text = CONDUCT.replace("@maintainer", contact)
    _write(checkout / "CODE_OF_CONDUCT.md", text)
    assert "health.enforcement" not in _codes(ch.audit_repository(checkout))


# ---------------------------------------------------------------------------
# Reachability and the tool's own documentation
# ---------------------------------------------------------------------------


def test_readme_must_link_both_policies(checkout: Path) -> None:
    """A policy nobody links to is a policy nobody reads."""
    _write(checkout / "README.md", "# Project\n\nSee SECURITY.md.\n")
    findings = ch.audit_repository(checkout)
    links = [f for f in findings if f.code == "health.link"]
    assert len(links) == 1
    assert "CODE_OF_CONDUCT.md" in links[0].detail


def test_contributing_must_link_both_policies(checkout: Path) -> None:
    """The contributor guide is where a new contributor looks."""
    _write(checkout / "CONTRIBUTING.md", "# Contributing\n\nBe nice.\n")
    findings = ch.audit_repository(checkout)
    links = [f for f in findings if f.code == "health.link"]
    assert [f.location for f in links] == ["CONTRIBUTING.md", "CONTRIBUTING.md"]
    assert [f.detail for f in links] == [
        "does not link SECURITY.md",
        "does not link CODE_OF_CONDUCT.md",
    ]


def test_missing_linking_document_is_not_a_finding(checkout: Path) -> None:
    """A checkout with no README has nothing to link from."""
    (checkout / "README.md").unlink()
    (checkout / "CONTRIBUTING.md").unlink()
    assert "health.link" not in _codes(ch.audit_repository(checkout))


def test_docs_page_must_name_every_code(checkout: Path) -> None:
    """The table of codes cannot fall behind the checks."""
    _write(checkout / "docs" / "community_health.md", "# Community health\n\n- `health.missing`\n")
    findings = ch.audit_repository(checkout)
    documented = [f for f in findings if f.code == "health.docs"]
    assert len(documented) == len(ch.CODES) - 1
    assert all("does not document" in finding.detail for finding in documented)


def test_missing_docs_page_is_one_finding(checkout: Path) -> None:
    """One missing page is one finding, not one per code."""
    (checkout / "docs" / "community_health.md").unlink()
    findings = ch.audit_repository(checkout)
    assert [f.code for f in findings] == ["health.docs"]
    assert "missing" in findings[0].detail


def test_every_documented_code_is_emitted_somewhere() -> None:
    """``CODES`` is the contract; a code nothing can emit is dead documentation."""
    source = (ROOT / "tools" / "community_health.py").read_text(encoding="utf-8")
    for code in ch.CODES:
        assert f'"{code}"' in source


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_main_clean_checkout_exits_zero(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A clean checkout prints what was measured and exits 0."""
    assert ch.main(["--root", str(checkout)]) == 0
    out = capsys.readouterr().out
    assert "SECURITY.md, CODE_OF_CONDUCT.md pass" in out
    assert "2.0.0" in out


def test_main_reports_findings_and_exits_one(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Findings are printed as a table and exit 1."""
    (checkout / "CODE_OF_CONDUCT.md").unlink()
    assert ch.main(["--root", str(checkout)]) == 1
    assert "health.missing" in capsys.readouterr().out


def test_main_json_is_one_document(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` is a verdict a script can read, not a log."""
    (checkout / "README.md").unlink()
    assert ch.main(["--root", str(checkout), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["version"] == "2.0.0"
    assert document["documents"] == {
        "SECURITY.md": "SECURITY.md",
        "CODE_OF_CONDUCT.md": "CODE_OF_CONDUCT.md",
    }
    assert document["findings"] == []


def test_main_json_records_a_missing_policy(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing document is ``null`` in the map and a finding in the list."""
    (checkout / "SECURITY.md").unlink()
    assert ch.main(["--root", str(checkout), "--json"]) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["documents"]["SECURITY.md"] is None
    assert document["ok"] is False
    assert document["errors"] >= 1


def test_strict_promotes_warnings(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The only warning in this tool is an unreadable version; strict fails it."""
    (checkout / "pyproject.toml").unlink()
    assert ch.main(["--root", str(checkout)]) == 0
    assert ch.main(["--root", str(checkout), "--strict"]) == 1
    assert "warning" in capsys.readouterr().out


def test_missing_root_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A path that is not a directory cannot be measured."""
    assert ch.main(["--root", str(tmp_path / "absent")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_absent_policies_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Neither document present is "wrong path" as much as "not written"."""
    assert ch.main(["--root", str(tmp_path)]) == 2
    assert "nothing to measure" in capsys.readouterr().err


def test_parser_declares_its_version(capsys: pytest.CaptureFixture[str]) -> None:
    """Every CLI here answers ``--version``; this one is no exception."""
    with pytest.raises(SystemExit) as excinfo:
        ch.build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert "community-health" in capsys.readouterr().out
