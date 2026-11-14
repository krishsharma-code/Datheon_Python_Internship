"""Tests for the Day 26 release audit (``tools/release.py``).

A release is a promise repeated in four places — ``pyproject.toml``,
``datheon.__version__``, ``cli_contract.PROGRAM_VERSION`` and ``CHANGELOG.md`` —
and a git tag that claims which commit keeps it. Nothing in Python, ``pip``,
``twine`` or GitHub makes the four agree, and when they disagree every existing
check stays green: the wheel builds, the metadata validates, the suite passes.
The defect surfaces later, as a wheel called ``1.1.0`` that prints ``1.0.0``.

So each test below breaks exactly one thing in a synthetic release and asserts
the *named* finding that results, rather than "the audit failed somehow". A
finding code that can be renamed without a test failing is a code nobody
depends on.

The last section runs the audit against this repository's real
``CHANGELOG.md``, ``pyproject.toml``, ``.github/workflows/release.yml`` and
``docs/release.md``, because the point of the day is that the declaration and
the repository cannot disagree.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from cli_contract import PROGRAM_VERSION  # noqa: E402  (path set up above)
from datheon import __version__ as package_version  # noqa: E402
from tools import release  # noqa: E402

CHANGELOG_PATH = ROOT / "CHANGELOG.md"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_DOC_PATH = ROOT / "docs" / "release.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"

#: The date the synthetic fixtures are dated relative to, so the audit never
#: depends on when the suite runs.
TODAY = dt.date(2026, 11, 14)

#: The version the synthetic release declares.
VERSION = "1.1.0"


# ---------------------------------------------------------------------------
# A release that passes, for the tests to then break
# ---------------------------------------------------------------------------
def good_changelog_text() -> str:
    """Build a ``CHANGELOG.md`` that passes the audit.

    Returns:
        The changelog's source: a title, the two specification references, an
        ``Unreleased`` section, two dated entries newest-first and a link
        definition per version.
    """
    return (
        "# Changelog\n"
        "\n"
        "All notable changes are documented here. The format is based on\n"
        "[Keep a Changelog], and this project adheres to [Semantic Versioning].\n"
        "\n"
        "## [Unreleased]\n"
        "\n"
        "### Planned\n"
        "\n"
        "- The next thing.\n"
        "\n"
        "## [1.1.0] - 2026-11-14\n"
        "\n"
        "### Added\n"
        "\n"
        "- Packaging and entry points.\n"
        "\n"
        "## [1.0.0] - 2026-10-21\n"
        "\n"
        "### Added\n"
        "\n"
        "- The first release.\n"
        "\n"
        "[Unreleased]: https://github.com/o/r/compare/v1.1.0...HEAD\n"
        "[1.1.0]: https://github.com/o/r/releases/tag/v1.1.0\n"
        "[1.0.0]: https://github.com/o/r/releases/tag/v1.0.0\n"
        "[Keep a Changelog]: https://keepachangelog.com/en/1.1.0/\n"
        "[Semantic Versioning]: https://semver.org/spec/v2.0.0.html\n"
    )


def good_workflow() -> Dict[str, Any]:
    """Build a release workflow that passes the audit.

    Returns:
        A fresh mapping; callers mutate their copy rather than the module state.
    """
    return {
        "name": "Release",
        "on": {"push": {"tags": ["v[0-9]+.[0-9]+.[0-9]+"]}, "workflow_dispatch": None},
        "permissions": {"contents": "write"},
        "defaults": {"run": {"shell": "bash"}},
        "jobs": {
            "publish": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"name": "Check out", "uses": "actions/checkout@v4"},
                    {"name": "Python", "uses": "actions/setup-python@v5"},
                    {
                        "name": "Tag check",
                        "run": 'python tools/release.py --tag "${TAG}" --strict',
                    },
                    {"name": "Build", "run": "python -m build"},
                    {"name": "Validate", "run": "twine check dist/*"},
                    {
                        "name": "Notes",
                        "run": 'python tools/release.py --notes "${TAG}" --notes-file NOTES.md',
                    },
                    {
                        "name": "Publish",
                        "env": {"GH_TOKEN": "${{ github.token }}"},
                        "run": (
                            'gh release create "${TAG}" --title "Release ${TAG}" '
                            "--notes-file NOTES.md dist/*"
                        ),
                    },
                ],
            }
        },
    }


def good_readme() -> str:
    """Build a README that links the Release and the changelog.

    Returns:
        A short README whose links satisfy the documentation checks.
    """
    return (
        "# Project\n"
        "\n"
        "[![Release](https://img.shields.io/badge/release-1.1.0-green)]"
        "(https://github.com/o/r/releases)\n"
        "\n"
        "See [CHANGELOG.md](CHANGELOG.md) for what changed.\n"
    )


def good_release_doc() -> str:
    """Build a release policy page long enough to pass the audit.

    Returns:
        Markdown comfortably over the audit's minimum length.
    """
    return (
        "# Release automation\n\n"
        + (
            "A release bumps the version in three declarations, moves the changelog "
            "entry, pushes an annotated tag and lets the workflow build, validate "
            "and publish the artefacts.\n\n"
        )
        * 5
    )


def good_nav() -> Tuple[str, ...]:
    """Return a site navigation that lists the release policy page.

    Returns:
        The page paths, as ``nav_pages`` would flatten them.
    """
    return ("index.md", "release.md")


def run_audit(**overrides: Any) -> release.Verdict:
    """Audit the passing release, with any argument replaced by the caller.

    Args:
        **overrides: Keyword arguments forwarded to
            :func:`release.audit_release`.

    Returns:
        The verdict for the (possibly broken) release.
    """
    changelog = overrides.pop("changelog", release.parse_changelog(good_changelog_text()))
    kwargs: Dict[str, Any] = {
        "version": VERSION,
        "changelog": changelog,
        "package": VERSION,
        "banner": VERSION,
        "workflow": good_workflow(),
        "workflow_path": ".github/workflows/release.yml",
        "nav": good_nav(),
        "readme": good_readme(),
        "release_doc": good_release_doc(),
        "tag": None,
        "today": TODAY,
        "strict": False,
    }
    kwargs.update(overrides)
    return release.audit_release(**kwargs)


def codes(verdict: release.Verdict) -> List[str]:
    """Return the finding codes of a verdict, in order.

    Args:
        verdict: The verdict to read.

    Returns:
        The codes, one per finding.
    """
    return [finding.code for finding in verdict.findings]


def with_changelog(text: str, **overrides: Any) -> release.Verdict:
    """Audit a release whose changelog is ``text``.

    Args:
        text: The changelog source to audit.
        **overrides: Further arguments for :func:`run_audit`.

    Returns:
        The verdict.
    """
    return run_audit(changelog=release.parse_changelog(text), **overrides)


def with_workflow(workflow: Optional[Dict[str, Any]], **overrides: Any) -> release.Verdict:
    """Audit a release whose workflow is ``workflow``.

    Args:
        workflow: The workflow mapping, or ``None``.
        **overrides: Further arguments for :func:`run_audit`.

    Returns:
        The verdict.
    """
    return run_audit(workflow=workflow, **overrides)


# ---------------------------------------------------------------------------
# The passing baseline
# ---------------------------------------------------------------------------
def test_a_good_release_passes():
    """Nothing broken: no findings at all, and the verdict is ok."""
    verdict = run_audit()
    assert codes(verdict) == []
    assert verdict.ok is True


def test_the_verdict_lists_the_released_versions():
    """Released versions are reported newest-first, as the file writes them."""
    verdict = run_audit()
    assert verdict.releases == ("1.1.0", "1.0.0")


def test_the_verdict_records_the_version_it_measured():
    """The declared version is part of the verdict, not just an input."""
    assert run_audit().version == VERSION


def test_a_release_with_no_changelog_is_not_measurable():
    """No changelog means nothing to measure, so the verdict is never ok."""
    verdict = run_audit(changelog=None)
    assert verdict.measurable is False
    assert verdict.ok is False
    assert "changelog.missing" in codes(verdict)


def test_a_release_with_no_version_is_not_measurable():
    """No declared version means the audit has no subject."""
    verdict = run_audit(version=None)
    assert verdict.measurable is False
    assert verdict.ok is False
    assert "version.missing" in codes(verdict)


def test_every_released_version_in_the_file_is_measured():
    """The audit reads the whole history, not only the version that ships."""
    text = (
        good_changelog_text()
        .replace("## [1.1.0] - 2026-11-14", "## [9.9.9] - 2026-11-14")
        .replace(
            "[1.1.0]: https://github.com/o/r/releases/tag/v1.1.0",
            "[9.9.9]: https://github.com/o/r/releases/tag/v9.9.9",
        )
    )
    verdict = with_changelog(text, version="9.9.9", package="9.9.9", banner="9.9.9")
    assert verdict.releases == ("9.9.9", "1.0.0")
    assert verdict.ok is True


def test_an_empty_changelog_parses_to_no_releases():
    """A file with no ``##`` heading yields no entries rather than a crash."""
    verdict = with_changelog("# Changelog\n\nNothing yet.\n")
    assert verdict.releases == ()
    assert verdict.ok is False


# ---------------------------------------------------------------------------
# The version, in three declarations
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "candidate",
    ["1.1.0", "0.0.1", "10.20.30", "1.1.0-rc.1", "1.1.0+build.5", "1.1.0-rc.1+build.5"],
)
def test_semver_accepts_valid_versions(candidate: str):
    """Every shape SemVer 2.0.0 allows is accepted."""
    assert release.is_semver(candidate) is True


@pytest.mark.parametrize(
    "candidate",
    ["1.1", "v1.1.0", "1.1.0.0", "01.1.0", "1.01.0", "1.1.0-", "1.1.0+", "one.two.three", ""],
)
def test_semver_rejects_invalid_versions(candidate: str):
    """Two components, a tag prefix or a leading zero are not versions."""
    assert release.is_semver(candidate) is False


def test_semver_key_orders_by_component():
    """``1.10.0`` is newer than ``1.9.0`` — string order would say otherwise."""
    assert release.semver_key("1.10.0") > release.semver_key("1.9.0")
    assert release.semver_key("2.0.0") > release.semver_key("1.99.99")
    assert release.semver_key("1.1.1") > release.semver_key("1.1.0")


def test_the_tag_carries_the_prefix():
    """A version is published under ``v`` + the version."""
    assert release.tag_for("1.1.0") == "v1.1.0"


def test_a_version_that_is_not_semver_is_a_finding():
    """``1.1`` cannot become a wheel filename anyone can resolve."""
    assert "version.invalid" in codes(run_audit(version="1.1", package="1.1", banner="1.1"))


def test_a_package_version_that_disagrees_is_a_finding():
    """The attribute and the distribution must be the same version."""
    verdict = run_audit(package="1.0.0")
    assert "version.package-mismatch" in codes(verdict)


def test_a_missing_package_version_is_a_finding():
    """An initialiser with no ``__version__`` is a finding, not a pass."""
    assert "version.package-missing" in codes(run_audit(package=None))


def test_a_banner_version_that_disagrees_is_a_finding():
    """The only version a user ever sees must match the one that ships."""
    assert "version.banner-mismatch" in codes(run_audit(banner="1.0.0"))


def test_a_missing_banner_version_is_a_finding():
    """No ``PROGRAM_VERSION`` means no banner to check."""
    assert "version.banner-missing" in codes(run_audit(banner=None))


def test_the_matching_tag_passes():
    """``v1.1.0`` for version ``1.1.0`` is the tag this release should have."""
    assert "tag.mismatch" not in codes(run_audit(tag="v1.1.0"))


def test_a_tag_that_names_another_version_is_a_finding():
    """A tag one release behind publishes the wrong wheel under the right name."""
    assert "tag.mismatch" in codes(run_audit(tag="v1.0.0"))


def test_a_tag_without_the_prefix_is_invalid():
    """``1.1.0`` is not a release tag in this repository."""
    found = codes(run_audit(tag="1.1.0"))
    assert "tag.invalid" in found
    assert "tag.mismatch" not in found


def test_an_unchecked_tag_reports_nothing():
    """No tag given, no tag finding — the audit does not invent one."""
    found = codes(run_audit(tag=None))
    assert "tag.mismatch" not in found
    assert "tag.invalid" not in found


def test_the_three_declarations_are_read_from_their_files():
    """Each declaration is parsed out of the source that holds it."""
    package_text = release.read_text(ROOT / "datheon" / "__init__.py")
    banner_text = release.read_text(ROOT / "cli_contract.py")
    assert release.package_version(package_text) == package_version
    assert release.banner_version(banner_text) == PROGRAM_VERSION


def test_a_missing_declaration_reads_as_none():
    """An absent file or an absent assignment is ``None``, never an exception."""
    assert release.package_version(None) is None
    assert release.package_version("X = 1\n") is None
    assert release.banner_version(None) is None
    assert release.banner_version('PROGRAM = "1.0.0"\n') is None


# ---------------------------------------------------------------------------
# The changelog: structure
# ---------------------------------------------------------------------------
def test_the_parser_reads_the_title_entries_and_links():
    """One parse yields the heading, every entry and every link definition."""
    changelog = release.parse_changelog(good_changelog_text())
    assert changelog.title == "Changelog"
    assert [entry.label for entry in changelog.entries] == ["Unreleased", "1.1.0", "1.0.0"]
    assert changelog.links["1.1.0"].endswith("/releases/tag/v1.1.0")


def test_the_unreleased_entry_has_no_version():
    """``Unreleased`` is a section, not a version, so it is never released."""
    changelog = release.parse_changelog(good_changelog_text())
    unreleased = changelog.entries[0]
    assert unreleased.is_unreleased is True
    assert unreleased.version is None


def test_an_entry_carries_its_date_and_line():
    """The date and the line number are what a finding points at."""
    entry = release.parse_changelog(good_changelog_text()).find("1.1.0")
    assert entry is not None
    assert entry.date == "2026-11-14"
    assert entry.line > 1


def test_find_accepts_the_tag_prefix():
    """``v1.1.0`` and ``1.1.0`` name the same entry."""
    changelog = release.parse_changelog(good_changelog_text())
    assert changelog.find("v1.1.0") is not None
    assert changelog.find("v1.1.0").label == changelog.find("1.1.0").label


def test_find_returns_none_for_an_unknown_version():
    """An absent version is ``None``, so the caller can report it."""
    assert release.parse_changelog(good_changelog_text()).find("9.9.9") is None


def test_a_missing_changelog_is_a_finding():
    """The file is the whole point; its absence is the first finding."""
    assert "changelog.missing" in codes(run_audit(changelog=None))


def test_a_changelog_without_a_title_is_a_finding():
    """A file that does not say what it is, is not a changelog."""
    text = good_changelog_text().replace("# Changelog\n", "Changelog\n", 1)
    assert "changelog.title-missing" in codes(with_changelog(text))


def test_a_different_title_is_a_warning():
    """A title of another name is a warning: strict mode promotes it."""
    text = good_changelog_text().replace("# Changelog", "# Release history", 1)
    verdict = with_changelog(text)
    assert "changelog.title" in codes(verdict)
    assert verdict.ok is True
    assert with_changelog(text, strict=True).ok is False


@pytest.mark.parametrize("reference", ["keepachangelog.com", "semver.org"])
def test_a_missing_specification_reference_is_a_finding(reference: str):
    """Without the citation, the rules the file follows are unstated."""
    text = good_changelog_text().replace(reference, "example.invalid")
    assert "changelog.reference-missing" in codes(with_changelog(text))


def test_a_changelog_with_no_entry_is_a_finding():
    """A title and nothing else measures no release."""
    assert "changelog.empty" in codes(with_changelog("# Changelog\n\nNothing yet.\n"))


def test_a_missing_unreleased_section_is_a_finding():
    """Work in flight needs somewhere to be written down."""
    text = good_changelog_text().split("## [1.1.0]")[0].replace("## [Unreleased]", "## [2.0.0]")
    text += "## [1.1.0]" + good_changelog_text().split("## [1.1.0]")[1]
    verdict = with_changelog(text)
    assert "changelog.unreleased-missing" in codes(verdict)


def test_a_dated_unreleased_section_is_a_warning():
    """``Unreleased`` has not shipped, so it has no release date."""
    text = good_changelog_text().replace("## [Unreleased]", "## [Unreleased] - 2026-11-01", 1)
    verdict = with_changelog(text)
    assert "changelog.unreleased-dated" in codes(verdict)
    assert verdict.ok is True


def test_planned_is_allowed_only_under_unreleased():
    """``Planned`` is forward-looking; in a released entry it is not defined."""
    text = good_changelog_text().replace(
        "### Added\n\n- Packaging and entry points.", "### Planned\n\n- Packaging."
    )
    assert "changelog.section-unknown" in codes(with_changelog(text))


@pytest.mark.parametrize(
    "section", ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]
)
def test_every_defined_section_is_accepted(section: str):
    """The six sections Keep a Changelog defines are all valid."""
    text = good_changelog_text().replace(
        "### Added\n\n- Packaging and entry points.", f"### {section}\n\n- A change."
    )
    assert "changelog.section-unknown" not in codes(with_changelog(text))


def test_an_entry_without_a_section_is_a_finding():
    """An entry that lists nothing tells the reader nothing."""
    text = good_changelog_text().replace("### Added\n\n- Packaging and entry points.\n", "")
    assert "changelog.entry-empty" in codes(with_changelog(text))


def test_an_entry_without_a_date_is_a_finding():
    """``## [1.1.0]`` with no date does not say when it shipped."""
    text = good_changelog_text().replace("## [1.1.0] - 2026-11-14", "## [1.1.0]", 1)
    assert "changelog.date-missing" in codes(with_changelog(text))


@pytest.mark.parametrize("bad_date", ["14-11-2026", "2026/11/14", "2026-11-14T00:00", "soon"])
def test_a_malformed_date_is_a_finding(bad_date: str):
    """Only ``YYYY-MM-DD`` is an ISO date the convention defines."""
    text = good_changelog_text().replace("2026-11-14", bad_date, 1)
    assert "changelog.date-invalid" in codes(with_changelog(text))


def test_an_impossible_date_is_a_finding():
    """``2026-02-30`` matches the shape and is not a calendar date."""
    text = good_changelog_text().replace("2026-11-14", "2026-02-30", 1)
    assert "changelog.date-invalid" in codes(with_changelog(text))


def test_a_future_date_is_a_finding():
    """A release cannot be dated after the day it is audited."""
    text = good_changelog_text().replace("2026-11-14", "2027-01-01", 1)
    assert "changelog.date-future" in codes(with_changelog(text))


def test_a_date_equal_to_today_is_not_in_the_future():
    """Releasing today is the normal case."""
    assert "changelog.date-future" not in codes(run_audit())


def test_an_out_of_order_entry_is_a_finding():
    """Newest first is what a reader of a changelog expects."""
    text = good_changelog_text().replace("## [1.1.0] - 2026-11-14", "## [0.9.0] - 2026-11-14", 1)
    text = text.replace("[1.1.0]: ", "[0.9.0]: ").replace("## [0.9.0]", "## [0.9.0]")
    assert "changelog.order" in codes(with_changelog(text, version="0.9.0"))


def test_a_duplicate_version_is_a_finding():
    """Two entries for one version is two different answers to one question."""
    text = good_changelog_text().replace("## [1.0.0] - 2026-10-21", "## [1.1.0] - 2026-10-21", 1)
    assert "changelog.duplicate" in codes(with_changelog(text))


def test_an_entry_version_that_is_not_semver_is_a_finding():
    """An entry labelled ``1.1`` cannot be tagged."""
    text = good_changelog_text().replace("## [1.0.0]", "## [1.0]", 1)
    assert "changelog.version-invalid" in codes(with_changelog(text))


def test_a_missing_link_definition_is_a_finding():
    """The link is what makes the heading clickable and the tag explicit."""
    text = good_changelog_text().replace(
        "[1.0.0]: https://github.com/o/r/releases/tag/v1.0.0\n", ""
    )
    assert "changelog.link-missing" in codes(with_changelog(text))


def test_a_missing_unreleased_link_is_a_finding():
    """``Unreleased`` compares against HEAD; without the link it compares to nothing."""
    text = good_changelog_text().replace(
        "[Unreleased]: https://github.com/o/r/compare/v1.1.0...HEAD\n", ""
    )
    assert "changelog.link-missing" in codes(with_changelog(text))


def test_a_link_pointing_at_the_wrong_tag_is_a_finding():
    """A version that links to another tag's page is a dead end."""
    text = good_changelog_text().replace("tag/v1.0.0", "tag/v0.9.0", 1)
    assert "changelog.link-mismatch" in codes(with_changelog(text))


def test_a_missing_entry_for_the_declared_version_is_a_finding():
    """The version that ships must say what it contains."""
    text = good_changelog_text().replace("## [1.1.0] - 2026-11-14", "## [1.2.0] - 2026-11-14", 1)
    text = text.replace("[1.1.0]: ", "[1.2.0]: ").replace("tag/v1.1.0", "tag/v1.2.0")
    verdict = with_changelog(text, version="1.1.0")
    assert "changelog.version-missing" in codes(verdict)


def test_an_unbracketed_heading_is_a_warning():
    """``## 1.1.0`` parses, but the convention wants the brackets."""
    text = good_changelog_text().replace("## [1.0.0]", "## 1.0.0", 1)
    verdict = with_changelog(text)
    assert "changelog.heading-unbracketed" in codes(verdict)
    assert verdict.ok is True


# ---------------------------------------------------------------------------
# Release notes
# ---------------------------------------------------------------------------
def test_the_release_notes_are_the_entry_body():
    """The Release page and the changelog are one document."""
    changelog = release.parse_changelog(good_changelog_text())
    notes = release.release_notes(changelog, "1.1.0")
    assert notes is not None
    assert "Packaging and entry points" in notes
    assert "The first release" not in notes


def test_the_release_notes_accept_the_tag_prefix():
    """The workflow passes ``${GITHUB_REF_NAME}``, which carries the prefix."""
    changelog = release.parse_changelog(good_changelog_text())
    assert release.release_notes(changelog, "v1.1.0") == release.release_notes(changelog, "1.1.0")


def test_the_release_notes_drop_the_link_definitions():
    """The last entry's body swallows the links; they are not release notes."""
    changelog = release.parse_changelog(good_changelog_text())
    notes = release.release_notes(changelog, "1.0.0")
    assert notes is not None
    assert "keepachangelog.com" not in notes
    assert "[1.0.0]:" not in notes
    assert "The first release." in notes


def test_the_release_notes_for_an_unknown_version_are_none():
    """There is nothing to publish for a version nobody wrote down."""
    changelog = release.parse_changelog(good_changelog_text())
    assert release.release_notes(changelog, "9.9.9") is None


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------
def test_a_missing_workflow_is_a_finding():
    """A release nothing publishes is not a release."""
    assert "workflow.missing" in codes(with_workflow(None))


def test_a_workflow_that_does_not_trigger_on_a_tag_is_a_finding():
    """Publishing on a branch push would release on every merge."""
    workflow = good_workflow()
    workflow["on"] = {"push": {"branches": ["main"]}}
    assert "workflow.trigger" in codes(with_workflow(workflow))


def test_a_trigger_on_a_different_prefix_is_a_finding():
    """A ``release-*`` pattern would never match ``v1.1.0``."""
    workflow = good_workflow()
    workflow["on"] = {"push": {"tags": ["release-*"]}}
    assert "workflow.trigger" in codes(with_workflow(workflow))


def test_a_missing_contents_write_permission_is_a_finding():
    """Without it the token cannot create a Release."""
    workflow = good_workflow()
    workflow["permissions"] = {"contents": "read"}
    assert "workflow.permissions" in codes(with_workflow(workflow))


def test_a_missing_permissions_block_is_a_finding():
    """The default token is read-only; publishing needs it stated."""
    workflow = good_workflow()
    del workflow["permissions"]
    assert "workflow.permissions" in codes(with_workflow(workflow))


def test_a_workflow_without_checkout_is_a_finding():
    """There is nothing to build without the repository."""
    workflow = good_workflow()
    workflow["jobs"]["publish"]["steps"] = [
        step for step in workflow["jobs"]["publish"]["steps"] if "checkout" not in str(step)
    ]
    assert "workflow.checkout" in codes(with_workflow(workflow))


def test_a_workflow_without_setup_python_is_a_warning():
    """The runner's Python would be whatever the image happens to ship."""
    workflow = good_workflow()
    workflow["jobs"]["publish"]["steps"] = [
        step for step in workflow["jobs"]["publish"]["steps"] if "setup-python" not in str(step)
    ]
    verdict = with_workflow(workflow)
    assert "workflow.python" in codes(verdict)
    assert verdict.ok is True
    assert with_workflow(workflow, strict=True).ok is False


def test_a_workflow_without_a_build_step_is_a_finding():
    """No build, no artefacts to attach."""
    workflow = good_workflow()
    workflow["jobs"]["publish"]["steps"] = [
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if "python -m build" not in str(step.get("run", ""))
    ]
    assert "workflow.build" in codes(with_workflow(workflow))


def test_a_workflow_without_twine_check_is_a_finding():
    """The metadata PyPI validates is validated here instead."""
    workflow = good_workflow()
    workflow["jobs"]["publish"]["steps"] = [
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if "twine check" not in str(step.get("run", ""))
    ]
    assert "workflow.twine" in codes(with_workflow(workflow))


def test_a_workflow_without_a_tag_check_is_a_finding():
    """The one mistake this pipeline exists to catch."""
    workflow = good_workflow()
    workflow["jobs"]["publish"]["steps"] = [
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if "--tag" not in str(step.get("run", ""))
    ]
    assert "workflow.tag-check" in codes(with_workflow(workflow))


def test_a_workflow_without_a_release_step_is_a_finding():
    """Building without publishing leaves the artefacts on the runner."""
    workflow = good_workflow()
    workflow["jobs"]["publish"]["steps"] = [
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if "gh release create" not in str(step.get("run", ""))
    ]
    found = codes(with_workflow(workflow))
    assert "workflow.release" in found
    assert "workflow.assets" in found


def test_a_workflow_without_a_notes_file_is_a_finding():
    """Notes typed into the workflow are a second copy that drifts."""
    workflow = good_workflow()
    for step in workflow["jobs"]["publish"]["steps"]:
        if "--notes-file" in str(step.get("run", "")):
            step["run"] = "true"
    assert "workflow.notes" in codes(with_workflow(workflow))


def test_a_release_that_attaches_nothing_is_a_finding():
    """A Release page with no wheel is a changelog entry, not a release."""
    workflow = good_workflow()
    for step in workflow["jobs"]["publish"]["steps"]:
        if "gh release create" in str(step.get("run", "")):
            step["run"] = 'gh release create "${TAG}" --notes-file NOTES.md'
    assert "workflow.assets" in codes(with_workflow(workflow))


@pytest.mark.parametrize("secret", ["ghp_", "github_pat_", "gho_", "ghs_", "xoxb-"])
def test_a_literal_credential_is_a_finding(secret: str):
    """A token in a workflow is a token in the history, forever."""
    workflow = good_workflow()
    workflow["jobs"]["publish"]["steps"].append(
        {"name": "Leak", "run": f'curl -H "Authorization: {secret}abc123" https://example.invalid'}
    )
    assert "workflow.secret" in codes(with_workflow(workflow))


def test_a_literal_token_in_an_environment_variable_is_a_finding():
    """``GH_TOKEN: ghp_…`` leaks just as surely as a ``run:`` line does."""
    workflow = good_workflow()
    for step in workflow["jobs"]["publish"]["steps"]:
        if "env" in step:
            step["env"]["GH_TOKEN"] = "ghp_averyrealtoken"
    assert "workflow.secret" in codes(with_workflow(workflow))


def test_the_injected_token_is_not_a_finding():
    """``${{ github.token }}`` is the token Actions injects, not a literal."""
    assert "workflow.secret" not in codes(with_workflow(good_workflow()))


def test_a_workflow_without_a_bash_shell_is_a_warning():
    """The publishing commands are POSIX shell, so the shell is stated."""
    workflow = good_workflow()
    del workflow["defaults"]
    verdict = with_workflow(workflow)
    assert "workflow.shell" in codes(verdict)
    assert verdict.ok is True


def test_a_yaml11_boolean_trigger_key_is_understood():
    """PyYAML reads a bare ``on:`` as ``True``; the trigger must still be found."""
    workflow = good_workflow()
    workflow[True] = workflow.pop("on")
    assert "workflow.trigger" not in codes(with_workflow(workflow))


def test_the_trigger_mapping_is_read_from_either_spelling():
    """The helper that normalises the key is exercised directly."""
    assert release.workflow_triggers({"on": {"push": {}}}) == {"push": {}}
    assert release.workflow_triggers({True: {"push": {}}}) == {"push": {}}
    assert release.workflow_triggers(None) == {}


def test_a_workflow_with_no_jobs_yields_no_commands():
    """An empty workflow is measured, and fails on the steps it lacks."""
    found = codes(with_workflow({"on": {"push": {"tags": ["v*"]}}}))
    assert "workflow.build" in found
    assert "workflow.release" in found


# ---------------------------------------------------------------------------
# The documentation
# ---------------------------------------------------------------------------
def test_a_missing_policy_page_is_a_finding():
    """A process nobody wrote down is a process nobody can repeat."""
    assert "docs.page-missing" in codes(run_audit(release_doc=None))


def test_a_thin_policy_page_is_a_finding():
    """A page too short to describe a release does not describe one."""
    assert "docs.page-thin" in codes(run_audit(release_doc="# Release\n\nBump it.\n"))


def test_a_policy_page_outside_the_nav_is_a_finding():
    """A page the site does not link is a page nobody reads."""
    assert "docs.nav-missing" in codes(run_audit(nav=("index.md",)))


def test_a_missing_readme_is_a_finding():
    """The Release is useless if the front page does not mention it."""
    assert "docs.readme-missing" in codes(run_audit(readme=None))


def test_a_readme_without_a_releases_link_is_a_finding():
    """The Releases tab is where a reader looks for the version."""
    readme = good_readme().replace("https://github.com/o/r/releases", "https://example.invalid")
    assert "docs.readme-releases" in codes(run_audit(readme=readme))


def test_a_readme_without_a_changelog_link_is_a_finding():
    """What changed is the first question a visitor asks."""
    readme = good_readme().replace("[CHANGELOG.md](CHANGELOG.md)", "the changelog")
    assert "docs.readme-changelog" in codes(run_audit(readme=readme))


# ---------------------------------------------------------------------------
# Navigation flattening
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "nav,expected",
    [
        (["index.md"], ("index.md",)),
        (["index.md", {"Guide": "guide.md"}], ("index.md", "guide.md")),
        (
            ["index.md", {"CI": ["ci_matrix.md", {"Release": "release.md"}]}],
            ("index.md", "ci_matrix.md", "release.md"),
        ),
        (None, ()),
        ("index.md", ("index.md",)),
    ],
)
def test_nav_pages_flattens_any_shape(nav: Any, expected: Tuple[str, ...]):
    """A nav is strings, mappings and lists in any combination."""
    assert release.nav_pages(nav) == expected


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------
def test_strict_promotes_warnings_to_failures():
    """A warning-only release passes, and fails under ``--strict``."""
    text = good_changelog_text().replace("# Changelog", "# Release history", 1)
    assert with_changelog(text).ok is True
    assert with_changelog(text, strict=True).ok is False


def test_errors_and_warnings_are_separated():
    """The verdict exposes each severity, so a caller need not re-filter."""
    text = good_changelog_text().replace("# Changelog", "# Release history", 1)
    verdict = with_changelog(text, package="9.9.9")
    assert [finding.code for finding in verdict.errors] == ["version.package-mismatch"]
    assert [finding.code for finding in verdict.warnings] == ["changelog.title"]


def test_the_verdict_serialises_to_json():
    """``--json`` is a supported output, so the shape is part of the contract."""
    payload = json.loads(json.dumps(run_audit().to_dict()))
    assert payload["version"] == VERSION
    assert payload["ok"] is True
    assert payload["releases"] == ["1.1.0", "1.0.0"]
    assert payload["errors"] == 0
    assert payload["findings"] == []


def test_a_finding_serialises_to_json():
    """Each finding carries its code, message, location and severity."""
    finding = release.Finding("x.y", "broke", "a.md", "warning")
    assert finding.to_dict() == {
        "code": "x.y",
        "message": "broke",
        "where": "a.md",
        "severity": "warning",
    }
    assert finding.is_error is False


def test_the_report_names_each_finding():
    """A build log has to say what broke and where."""
    report = release.render_report(run_audit(package="1.0.0"))
    assert "FAIL" in report
    assert "version.package-mismatch" in report
    assert "datheon/__init__.py" in report


def test_the_report_passes_a_good_release():
    """A passing verdict says PASS and counts the releases."""
    report = release.render_report(run_audit())
    assert "PASS" in report
    assert "2 released version(s)" in report


def test_the_report_refuses_a_verdict_it_could_not_reach():
    """Unmeasurable is not success, and the report says so."""
    report = release.render_report(run_audit(changelog=None))
    assert "NO VERDICT" in report


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
def write_release_tree(tmp_path: Path, *, version: str = "1.1.0", changelog: Optional[str] = None):
    """Write a minimal but complete release declaration into ``tmp_path``.

    Args:
        tmp_path: The directory to write into.
        version: The version every declaration carries.
        changelog: The changelog source; the passing one by default.

    Returns:
        The paths of the files that were written, keyed by role.
    """
    (tmp_path / "datheon").mkdir(exist_ok=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True, exist_ok=True)

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8")
    (tmp_path / "datheon" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "cli_contract.py").write_text(f'PROGRAM_VERSION = "{version}"\n', encoding="utf-8")
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(
        changelog if changelog is not None else good_changelog_text(), encoding="utf-8"
    )
    (tmp_path / ".github" / "workflows" / "release.yml").write_text(
        yaml.safe_dump(good_workflow(), sort_keys=False), encoding="utf-8"
    )
    (tmp_path / "mkdocs.yml").write_text(
        "site_name: x\nnav:\n  - index.md\n  - release.md\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "release.md").write_text(good_release_doc(), encoding="utf-8")
    (tmp_path / "README.md").write_text(good_readme(), encoding="utf-8")
    return {"pyproject": pyproject, "changelog": changelog_path, "root": tmp_path}


def run_main(tmp_path: Path, *args: str) -> int:
    """Run the tool's ``main`` against a synthetic tree.

    Args:
        tmp_path: The directory holding the release declaration.
        *args: Extra command-line arguments.

    Returns:
        The process exit code.
    """
    argv = [
        "--pyproject",
        str(tmp_path / "pyproject.toml"),
        "--changelog",
        str(tmp_path / "CHANGELOG.md"),
        "--workflow",
        str(tmp_path / ".github" / "workflows" / "release.yml"),
        "--mkdocs",
        str(tmp_path / "mkdocs.yml"),
        "--readme",
        str(tmp_path / "README.md"),
        "--release-doc",
        str(tmp_path / "docs" / "release.md"),
        *args,
    ]
    return release.main(argv)


def test_main_exits_zero_on_a_good_release(tmp_path: Path, capsys):
    """The baseline passes through the real argument parser."""
    write_release_tree(tmp_path)
    assert run_main(tmp_path) == release.EXIT_OK
    assert "PASS" in capsys.readouterr().out


def test_main_exits_one_on_a_finding(tmp_path: Path):
    """A tag one release behind is exit 1, not 0."""
    write_release_tree(tmp_path)
    assert run_main(tmp_path, "--tag", "v1.0.0") == release.EXIT_FINDINGS


def test_main_exits_two_when_the_declaration_is_unreadable(tmp_path: Path):
    """No ``pyproject.toml`` means no version, so no verdict."""
    (tmp_path / "pyproject.toml").write_text("not toml at all = = =", encoding="utf-8")
    assert run_main(tmp_path) == release.EXIT_UNMEASURABLE


def test_main_exits_two_when_there_is_no_changelog(tmp_path: Path):
    """A missing changelog is a finding *and* an unmeasurable audit."""
    write_release_tree(tmp_path)
    (tmp_path / "CHANGELOG.md").unlink()
    assert run_main(tmp_path) == release.EXIT_UNMEASURABLE


def test_main_lists_the_releases(tmp_path: Path, capsys):
    """``--list-releases`` prints one version per line, newest first."""
    write_release_tree(tmp_path)
    assert run_main(tmp_path, "--list-releases") == release.EXIT_OK
    assert capsys.readouterr().out.split() == ["1.1.0", "1.0.0"]


def test_main_prints_the_release_notes(tmp_path: Path, capsys):
    """``--notes`` is what the workflow feeds to ``gh release create``."""
    write_release_tree(tmp_path)
    assert run_main(tmp_path, "--notes", "1.1.0") == release.EXIT_OK
    assert "Packaging and entry points" in capsys.readouterr().out


def test_main_writes_the_notes_to_a_file(tmp_path: Path):
    """``--notes-file`` is the form the workflow actually uses."""
    write_release_tree(tmp_path)
    destination = tmp_path / "RELEASE_NOTES.md"
    assert run_main(tmp_path, "--notes", "v1.1.0", "--notes-file", str(destination)) == (
        release.EXIT_OK
    )
    assert "Packaging and entry points" in destination.read_text(encoding="utf-8")


def test_main_refuses_notes_for_an_unknown_version(tmp_path: Path):
    """There is nothing to publish, so the exit code says so."""
    write_release_tree(tmp_path)
    assert run_main(tmp_path, "--notes", "9.9.9") == release.EXIT_UNMEASURABLE


def test_main_refuses_notes_without_a_changelog(tmp_path: Path):
    """No file, no notes — and never an empty Release page."""
    write_release_tree(tmp_path)
    (tmp_path / "CHANGELOG.md").unlink()
    assert run_main(tmp_path, "--notes", "1.1.0") == release.EXIT_UNMEASURABLE


def test_main_prints_json(tmp_path: Path, capsys):
    """``--json`` is machine-readable and complete."""
    write_release_tree(tmp_path)
    assert run_main(tmp_path, "--json") == release.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == "1.1.0"
    assert payload["ok"] is True


def test_main_strict_turns_a_warning_into_a_failure(tmp_path: Path):
    """The same tree, one flag apart."""
    write_release_tree(
        tmp_path, changelog=good_changelog_text().replace("# Changelog", "# History", 1)
    )
    assert run_main(tmp_path) == release.EXIT_OK
    assert run_main(tmp_path, "--strict") == release.EXIT_FINDINGS


# ---------------------------------------------------------------------------
# This repository's own release
# ---------------------------------------------------------------------------
def test_this_repository_passes_the_audit():
    """The declaration the repository ships is the one the audit measures."""
    verdict = release.audit_release(
        version=release.declared_version(release.load_pyproject(PYPROJECT_PATH)),
        changelog=release.parse_changelog(CHANGELOG_PATH.read_text(encoding="utf-8")),
        package=release.package_version(release.read_text(ROOT / "datheon" / "__init__.py")),
        banner=release.banner_version(release.read_text(ROOT / "cli_contract.py")),
        workflow=release.load_yaml_file(WORKFLOW_PATH),
        workflow_path=str(WORKFLOW_PATH.relative_to(ROOT)),
        nav=release.nav_pages(
            release.load_yaml_file(ROOT / "mkdocs.yml").get("nav")  # type: ignore[union-attr]
        ),
        readme=release.read_text(ROOT / "README.md"),
        release_doc=release.read_text(RELEASE_DOC_PATH),
        strict=True,
    )
    assert codes(verdict) == []
    assert verdict.ok is True


def test_this_repository_declares_one_version_everywhere():
    """The three declarations agree, and the changelog agrees with them."""
    declared = release.declared_version(release.load_pyproject(PYPROJECT_PATH))
    assert declared == package_version == PROGRAM_VERSION
    changelog = release.parse_changelog(CHANGELOG_PATH.read_text(encoding="utf-8"))
    assert changelog.find(declared) is not None  # type: ignore[arg-type]


def test_this_repository_has_an_unreleased_section():
    """Work in flight has somewhere to go before the next release."""
    changelog = release.parse_changelog(CHANGELOG_PATH.read_text(encoding="utf-8"))
    assert any(entry.is_unreleased for entry in changelog.entries)


def test_every_released_version_has_a_link_definition():
    """A version without a link is a heading with nowhere to go."""
    changelog = release.parse_changelog(CHANGELOG_PATH.read_text(encoding="utf-8"))
    for entry in changelog.released():
        assert entry.version in changelog.links


def test_the_release_workflow_triggers_on_a_tag():
    """The pipeline starts on the tag, and only on the tag."""
    workflow = release.load_yaml_file(WORKFLOW_PATH)
    tags = release.workflow_triggers(workflow).get("push", {}).get("tags", [])
    assert any(str(tag).startswith("v") for tag in tags)


def test_the_release_policy_is_in_the_site_navigation():
    """The page is reachable, not merely present."""
    config = release.load_yaml_file(ROOT / "mkdocs.yml")
    assert config is not None
    assert "release.md" in release.nav_pages(config.get("nav"))


def test_the_repository_publishes_notes_from_the_changelog():
    """The Release page is the changelog entry, so the two cannot drift."""
    declared = release.declared_version(release.load_pyproject(PYPROJECT_PATH))
    changelog = release.parse_changelog(CHANGELOG_PATH.read_text(encoding="utf-8"))
    notes = release.release_notes(changelog, declared)  # type: ignore[arg-type]
    assert notes is not None
    assert declared in notes  # type: ignore[operator]


def test_the_tool_runs_as_a_script_from_the_repository_root():
    """``python tools/release.py`` is how CI invokes it."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "release.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == release.EXIT_OK, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_the_tool_checks_the_tag_it_is_given_on_the_command_line():
    """The workflow's guard, exercised end to end against this repository."""
    declared = release.declared_version(release.load_pyproject(PYPROJECT_PATH))
    good = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "release.py"),
            "--tag",
            release.tag_for(declared),  # type: ignore[arg-type]
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert good.returncode == release.EXIT_OK, good.stdout + good.stderr

    bad = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "release.py"), "--tag", "v0.0.1"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert bad.returncode == release.EXIT_FINDINGS
    assert "tag.mismatch" in bad.stdout
