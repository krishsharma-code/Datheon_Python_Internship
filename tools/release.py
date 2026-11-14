"""Release audit: prove the version that ships is the version that is tagged (Day 26).

The repository has declared a version since Day 22 — ``1.0.0`` in
``pyproject.toml``, in ``datheon.__version__`` and in
``cli_contract.PROGRAM_VERSION`` — but until today nothing ever *shipped* it.
There is no ``CHANGELOG.md``, no tag, and no GitHub Release: a reader who wants
"what changed and when" has to read twenty-six daily log entries.

A release is a promise in four places at once, and the four are allowed to
disagree in complete silence:

* ``pyproject.toml`` says the distribution's version — it is what the wheel
  filename, the ``METADATA`` file and PyPI's immutability rule are built on;
* ``datheon/__init__.py`` says the package's version — it is what
  ``importlib.metadata`` disagrees with when the two drift;
* ``cli_contract.PROGRAM_VERSION`` says what every ``--version`` banner prints —
  it is the only one a *user* sees;
* ``CHANGELOG.md`` says what the version *contains*, and a git tag says which
  commit that is.

Bump one and forget the others and every check still passes: ``pytest`` is
green, ``twine check`` is happy, the wheel builds. The defect surfaces later, as
a wheel called ``1.1.0`` that prints ``1.0.0``, or a Release page whose notes
describe work that is not in the tag.

So this module treats the release as a declaration to be audited, exactly the
way ``tools/ci_matrix.py`` audits the CI matrix and ``tools/docs_site.py``
audits the documentation site:

* **the version** — SemVer-shaped, and *identical* in the three places that
  declare it, and the version the tag names when one is given;
* **the changelog** — Keep a Changelog's structure: a title, an ``Unreleased``
  section, one dated entry per released version, only the sections the
  convention defines, newest first, a link definition per version, and no
  duplicate or future-dated entry;
* **the pipeline** — ``.github/workflows/release.yml`` triggers on a ``v*``
  tag, holds ``contents: write``, verifies the tag against the version *before*
  building, builds a wheel and an sdist, runs ``twine check``, extracts the
  notes from the changelog rather than from a second copy of them, and attaches
  the artefacts to the Release — with no token written into the file;
* **the documentation** — the release policy page exists, is reachable from the
  site's navigation, and the README links the releases page.

Each defect is a *finding* with a stable code, so the test suite can assert the
specific thing that broke instead of "the audit failed".

Run it directly::

    python tools/release.py                     # audit this repository
    python tools/release.py --list-releases     # versions in the changelog
    python tools/release.py --notes 1.1.0       # release notes, ready to paste
    python tools/release.py --tag v1.1.0        # audit *and* check the tag name
    python tools/release.py --notes v1.1.0 --notes-file RELEASE_NOTES.md
    python tools/release.py --strict --json     # warnings fail; machine-readable

Exit codes: ``0`` every check passed, ``1`` at least one finding, ``2`` the
audit could not reach a verdict (``pyproject.toml`` missing or unparsable, no
version declared, no changelog to measure). A gate that cannot measure must
never report success.

Requires ``PyYAML`` and ``tomllib`` (``requirements.txt``); it is development
tooling only — the five projects remain standard-library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - exercised only by the missing-dependency test
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency is in requirements.txt
    yaml = None  # type: ignore[assignment]

try:  # pragma: no cover - exercised only by the missing-dependency test
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

#: Exit code for a release that passes every check.
EXIT_OK = 0

#: Exit code for a release with at least one finding.
EXIT_FINDINGS = 1

#: Exit code for an audit that could not measure anything.
EXIT_UNMEASURABLE = 2

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The declaration that names the distribution and its version.
DEFAULT_PYPROJECT: Path = ROOT / "pyproject.toml"

#: The package attribute that must agree with it.
DEFAULT_PACKAGE: Path = ROOT / "datheon" / "__init__.py"

#: The banner constant that must agree with both.
DEFAULT_BANNER: Path = ROOT / "cli_contract.py"

#: The file that says what each version contains.
DEFAULT_CHANGELOG: Path = ROOT / "CHANGELOG.md"

#: The workflow that turns a tag into a Release.
DEFAULT_WORKFLOW: Path = ROOT / ".github" / "workflows" / "release.yml"

#: The policy document that explains how a release is cut.
DEFAULT_RELEASE_DOC: Path = ROOT / "docs" / "release.md"

#: The site configuration whose nav must reach that document.
DEFAULT_MKDOCS: Path = ROOT / "mkdocs.yml"

#: The page that links the Releases tab.
DEFAULT_README: Path = ROOT / "README.md"

#: Relative paths, for findings, so a report reads the same on any machine.
DEFAULT_WORKFLOW_REL = ".github/workflows/release.yml"
DEFAULT_CHANGELOG_REL = "CHANGELOG.md"

#: The prefix of every release tag. ``v1.1.0``, not ``1.1.0``: the prefix is
#: what keeps the tag from colliding with a branch name and what the workflow's
#: ``tags:`` filter matches.
TAG_PREFIX = "v"

#: SemVer 2.0.0, in full: three numeric components, an optional pre-release and
#: an optional build metadata suffix. No leading zeros, because ``01.2.3`` is
#: not a version any resolver accepts.
SEMVER_PATTERN = (
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)" r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
)
SEMVER_RE = re.compile(rf"^{SEMVER_PATTERN}$")

#: The tag name a version must produce: ``1.1.0`` -> ``v1.1.0``.
TAG_RE = re.compile(rf"^v{SEMVER_PATTERN}$")

#: ``__version__ = "1.1.0"`` in the package initialiser.
PACKAGE_VERSION_RE = re.compile(r'^__version__\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)

#: ``PROGRAM_VERSION = "1.1.0"`` in the shared CLI contract.
BANNER_VERSION_RE = re.compile(r'^PROGRAM_VERSION\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)

#: ``## [1.1.0] - 2026-11-14`` / ``## [Unreleased]`` / ``## 1.1.0``.
ENTRY_HEADING_RE = re.compile(
    r"^##\s+(?:\[(?P<bracketed>[^\]]+)\]|(?P<plain>[^\[\s][^-\s]*))"
    r"(?:\s*-\s*(?P<date>\S+))?\s*$"
)

#: ``### Added`` and friends.
SECTION_HEADING_RE = re.compile(r"^###\s+(?P<section>.+?)\s*$")

#: ``[1.1.0]: https://github.com/...`` at the foot of the changelog.
LINK_DEFINITION_RE = re.compile(r"^\[(?P<label>[^\]]+)\]:\s*(?P<url>\S+)\s*$")

#: ``2026-11-14``, the only date format Keep a Changelog defines.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The section names Keep a Changelog defines, in the order it lists them. A
#: heading outside this set is a changelog that has invented a category nobody
#: reading it expects to find.
ALLOWED_SECTIONS: Tuple[str, ...] = (
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
)

#: Sections permitted only under ``[Unreleased]``, where forward-looking work
#: has not happened yet and so is not "Added" in the released sense.
ALLOWED_UNRELEASED_SECTIONS: Tuple[str, ...] = ALLOWED_SECTIONS + ("Planned",)

#: The two specifications the changelog must cite, so a reader knows the rules
#: the file is written to.
REQUIRED_REFERENCES: Tuple[str, ...] = ("keepachangelog.com", "semver.org")

#: The marker a leaked credential leaves behind. A release workflow needs a
#: token; it must take the one Actions injects, never a literal.
TOKEN_PATTERNS: Tuple[str, ...] = (
    "ghp_",
    "github_pat_",
    "gho_",
    "ghs_",
    "xoxb-",
)

#: The two artefacts a release attaches. ``dist/*`` covers both and is what the
#: publish step is written against.
DIST_GLOB = "dist/*"


# ---------------------------------------------------------------------------
# Findings and the verdict
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    """One thing the audit does not like.

    Attributes:
        code: A stable dotted identifier, e.g. ``changelog.date-invalid``. The
            test suite asserts on these, so they are part of the contract.
        message: A one-line explanation written for whoever has to fix it.
        where: The file the finding is about.
        severity: ``"error"`` fails the audit; ``"warning"`` fails it only
            under ``--strict``.
    """

    code: str
    message: str
    where: str = DEFAULT_CHANGELOG_REL
    severity: str = "error"

    @property
    def is_error(self) -> bool:
        """Return whether this finding fails a non-strict audit."""
        return self.severity == "error"

    def to_dict(self) -> Dict[str, str]:
        """Return a JSON-serialisable view of the finding."""
        return {
            "code": self.code,
            "message": self.message,
            "where": self.where,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ChangelogEntry:
    """One ``##`` section of ``CHANGELOG.md``.

    Attributes:
        label: The heading's text, e.g. ``"1.1.0"`` or ``"Unreleased"``.
        version: The version, or ``None`` for the unreleased section.
        date: The ISO date the entry carries, or ``None``.
        sections: The ``###`` headings inside the entry, in order.
        body: The entry's text, heading included, for the release notes.
        line: The 1-based line the heading is on, so a finding can point at it.
        bracketed: Whether the heading used ``[1.1.0]`` — the form the
            convention specifies, because it is also the link reference.
    """

    label: str
    version: Optional[str]
    date: Optional[str]
    sections: Tuple[str, ...]
    body: str
    line: int = 0
    bracketed: bool = True

    @property
    def is_unreleased(self) -> bool:
        """Return whether this is the ``[Unreleased]`` section."""
        return self.version is None


@dataclass(frozen=True)
class Changelog:
    """A parsed ``CHANGELOG.md``.

    Attributes:
        title: The file's ``#`` heading, or an empty string when it has none.
        entries: Every ``##`` section, in the order they appear.
        links: The link definitions at the foot of the file, label to URL.
        text: The raw file, for reference checks that are not structural.
    """

    title: str = ""
    entries: Tuple[ChangelogEntry, ...] = ()
    links: Mapping[str, str] = None  # type: ignore[assignment]
    text: str = ""

    def __post_init__(self) -> None:
        """Default ``links`` to an empty mapping without a mutable default."""
        if self.links is None:
            object.__setattr__(self, "links", {})

    def find(self, version: str) -> Optional[ChangelogEntry]:
        """Return the entry for a version.

        Args:
            version: The version to look up, with or without the tag prefix.

        Returns:
            The matching entry, or ``None`` when the version has none.
        """
        wanted = version[len(TAG_PREFIX) :] if version.startswith(TAG_PREFIX) else version
        for entry in self.entries:
            if entry.version == wanted:
                return entry
        return None

    def released(self) -> Tuple[ChangelogEntry, ...]:
        """Return the entries for released versions, newest first as written."""
        return tuple(entry for entry in self.entries if not entry.is_unreleased)

    def versions(self) -> Tuple[str, ...]:
        """Return the released versions, in the order the file lists them."""
        return tuple(entry.version for entry in self.released() if entry.version)


@dataclass(frozen=True)
class Verdict:
    """The result of auditing one release.

    Attributes:
        version: The version the repository declares, or ``None``.
        tag: The tag the audit was asked to check against, if any.
        changelog_path: The changelog that was audited.
        releases: Every released version the changelog lists, in file order.
        findings: Everything wrong with the release, in discovery order.
        strict: Whether warnings counted as failures.
        measurable: Whether the audit reached a verdict at all.
    """

    version: Optional[str] = None
    tag: Optional[str] = None
    changelog_path: str = DEFAULT_CHANGELOG_REL
    releases: Tuple[str, ...] = ()
    findings: Tuple[Finding, ...] = ()
    strict: bool = False
    measurable: bool = True

    @property
    def errors(self) -> Tuple[Finding, ...]:
        """Return the findings that fail even a non-strict audit."""
        return tuple(finding for finding in self.findings if finding.is_error)

    @property
    def warnings(self) -> Tuple[Finding, ...]:
        """Return the findings that only ``--strict`` promotes."""
        return tuple(finding for finding in self.findings if not finding.is_error)

    @property
    def ok(self) -> bool:
        """Return whether the release passed.

        A verdict that measured no releases is never ``ok``: a changelog with no
        version in it is not a changelog, and silence is not evidence of
        correctness.
        """
        if not self.measurable or not self.releases:
            return False
        if self.errors:
            return False
        return not (self.strict and self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable view of the verdict."""
        return {
            "version": self.version,
            "tag": self.tag,
            "ok": self.ok,
            "strict": self.strict,
            "measurable": self.measurable,
            "changelog": self.changelog_path,
            "release_count": len(self.releases),
            "releases": list(self.releases),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [finding.to_dict() for finding in self.findings],
        }


# ---------------------------------------------------------------------------
# Reading the declaration
# ---------------------------------------------------------------------------
def read_text(path: Path) -> Optional[str]:
    """Read a UTF-8 text file, returning ``None`` when it cannot be read.

    Args:
        path: The file to read.

    Returns:
        The file's text, or ``None`` when it is missing or undecodable.
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def load_pyproject(path: Path) -> Optional[Mapping[str, Any]]:
    """Parse ``pyproject.toml``, returning ``None`` when it is not there.

    Args:
        path: The declaration to read.

    Returns:
        The parsed document, or ``None`` when the file does not exist.

    Raises:
        tomllib.TOMLDecodeError: If the file exists but is not valid TOML.
        OSError: If the file exists but cannot be read.
    """
    path = Path(path)
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return tomllib.load(handle)


def declared_version(pyproject: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return the distribution version from a parsed ``pyproject.toml``.

    Args:
        pyproject: The parsed declaration, or ``None``.

    Returns:
        The ``[project] version`` string, or ``None`` when it is not declared.
    """
    project = pyproject.get("project") if pyproject else None
    if not isinstance(project, Mapping):
        return None
    version = project.get("version")
    return version if isinstance(version, str) and version else None


def package_version(text: Optional[str]) -> Optional[str]:
    """Return ``__version__`` from a package initialiser's source.

    Args:
        text: The source of ``datheon/__init__.py``, or ``None``.

    Returns:
        The declared version, or ``None`` when the assignment is absent.
    """
    if not text:
        return None
    match = PACKAGE_VERSION_RE.search(text)
    return match.group("version") if match else None


def banner_version(text: Optional[str]) -> Optional[str]:
    """Return ``PROGRAM_VERSION`` from the shared CLI contract's source.

    Args:
        text: The source of ``cli_contract.py``, or ``None``.

    Returns:
        The declared version, or ``None`` when the assignment is absent.
    """
    if not text:
        return None
    match = BANNER_VERSION_RE.search(text)
    return match.group("version") if match else None


def is_semver(text: str) -> bool:
    """Return whether ``text`` is a SemVer 2.0.0 version string.

    Args:
        text: The candidate version.

    Returns:
        ``True`` when it matches the specification exactly.
    """
    return bool(SEMVER_RE.match(text))


def semver_key(text: str) -> Tuple[int, int, int]:
    """Return a sortable key for a SemVer string.

    Args:
        text: A version matching :data:`SEMVER_RE`.

    Returns:
        The ``(major, minor, patch)`` triple. Pre-release ordering is not
        modelled: the changelog is ordered by release, and two versions that
        differ only in their suffix sort equal here, which is enough to detect
        an out-of-order entry.
    """
    core = text.split("-")[0].split("+")[0]
    major, minor, patch = (int(part) for part in core.split("."))
    return (major, minor, patch)


def tag_for(version: str) -> str:
    """Return the release tag a version is published under.

    Args:
        version: The version, e.g. ``"1.1.0"``.

    Returns:
        The tag name, e.g. ``"v1.1.0"``.
    """
    return f"{TAG_PREFIX}{version}"


# ---------------------------------------------------------------------------
# The changelog
# ---------------------------------------------------------------------------
def parse_entry_heading(line: str) -> Optional[Tuple[str, Optional[str], bool]]:
    """Parse a ``##`` heading into its label, date and bracket style.

    Args:
        line: A single line of the changelog.

    Returns:
        ``(label, date, bracketed)``, or ``None`` when the line is not an entry
        heading.
    """
    match = ENTRY_HEADING_RE.match(line)
    if not match:
        return None
    bracketed = match.group("bracketed")
    label = bracketed if bracketed is not None else match.group("plain")
    return label.strip(), match.group("date"), bracketed is not None


def parse_changelog(text: str) -> Changelog:
    """Parse a ``CHANGELOG.md`` into its title, entries and link definitions.

    The parser is deliberately forgiving about *shape* — it accepts an entry
    without a date, an unbracketed heading or an unknown section — because the
    audit, not the parser, decides which of those is a defect. What it must not
    do is silently drop an entry, since an unparsed entry is an unmeasured one.

    Args:
        text: The changelog's source.

    Returns:
        The parsed changelog. A file with no ``##`` heading parses to no
        entries, which the audit reports as a missing release rather than as a
        crash.
    """
    lines = text.splitlines()
    title = ""
    entries: List[ChangelogEntry] = []
    links: Dict[str, str] = {}

    current_label: Optional[str] = None
    current_date: Optional[str] = None
    current_bracketed = True
    current_line = 0
    current_sections: List[str] = []
    current_body: List[str] = []

    def flush() -> None:
        """Append the entry being accumulated, if there is one."""
        if current_label is None:
            return
        version = None if current_label.lower() == "unreleased" else current_label
        entries.append(
            ChangelogEntry(
                label=current_label,
                version=version,
                date=current_date,
                sections=tuple(current_sections),
                body="\n".join(current_body).strip(),
                line=current_line,
                bracketed=current_bracketed,
            )
        )

    for index, line in enumerate(lines, start=1):
        if line.startswith("# ") and not title:
            title = line[2:].strip()
            continue
        heading = parse_entry_heading(line)
        if heading is not None:
            flush()
            current_label, current_date, current_bracketed = heading
            current_line = index
            current_sections = []
            current_body = [line]
            continue
        if current_label is not None:
            current_body.append(line)
            section = SECTION_HEADING_RE.match(line)
            if section:
                current_sections.append(section.group("section").strip())
            continue
        definition = LINK_DEFINITION_RE.match(line)
        if definition:
            links[definition.group("label")] = definition.group("url")

    flush()

    # Link definitions live below the last entry, so the loop above swallows
    # them into that entry's body. Read them from the whole file instead: a
    # definition is a line at column zero starting with `[label]:`.
    for line in lines:
        definition = LINK_DEFINITION_RE.match(line)
        if definition:
            links.setdefault(definition.group("label"), definition.group("url"))

    return Changelog(title=title, entries=tuple(entries), links=links, text=text)


def release_notes(changelog: Changelog, version: str) -> Optional[str]:
    """Render one version's entry as GitHub Release notes.

    The workflow publishes *these* notes, so the Release page and the changelog
    cannot say different things — there is only one copy of the text.

    Args:
        changelog: The parsed changelog.
        version: The version to render, with or without the tag prefix.

    Returns:
        The entry's markdown body, or ``None`` when the version has no entry.
    """
    entry = changelog.find(version)
    if entry is None:
        return None
    # The last entry's body swallows the link definitions at the foot of the
    # file. They belong to the changelog, not to a Release page.
    lines = entry.body.splitlines()
    while lines and (
        not lines[-1].strip() or LINK_DEFINITION_RE.match(lines[-1]) or lines[-1].strip() == "---"
    ):
        lines.pop()
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# Small structural helpers
# ---------------------------------------------------------------------------
def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` as a mapping, or an empty one if it is not a mapping."""
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    """Return ``value`` as a list, or an empty one if it is not a list."""
    return list(value) if isinstance(value, (list, tuple)) else []


def workflow_triggers(workflow: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return the ``on:`` block of a workflow.

    PyYAML implements YAML 1.1, where a bare ``on`` key is the boolean ``True``.
    Both spellings therefore have to be accepted, or the audit reports "no
    trigger" for a workflow that has one.

    Args:
        workflow: The parsed workflow, or ``None``.

    Returns:
        The trigger mapping, or an empty mapping when there is none.
    """
    if not workflow:
        return {}
    for key in ("on", True):
        if key in workflow:
            return _as_mapping(workflow[key])
    return {}


def workflow_jobs(workflow: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return the ``jobs:`` block of a workflow.

    Args:
        workflow: The parsed workflow, or ``None``.

    Returns:
        The jobs mapping, or an empty mapping when there is none.
    """
    return _as_mapping(_as_mapping(workflow).get("jobs"))


def job_steps(job: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """Return a job's steps as mappings.

    Args:
        job: One entry of the ``jobs`` mapping.

    Returns:
        The steps, with anything that is not a mapping dropped.
    """
    return [step for step in _as_list(job.get("steps")) if isinstance(step, Mapping)]


def step_commands(workflow: Optional[Mapping[str, Any]]) -> Tuple[str, ...]:
    """Return every shell line the workflow runs, across all jobs and steps.

    Args:
        workflow: The parsed workflow, or ``None``.

    Returns:
        The ``run:`` blocks, in file order.
    """
    commands: List[str] = []
    for job in workflow_jobs(workflow).values():
        for step in job_steps(_as_mapping(job)):
            run = step.get("run")
            if isinstance(run, str) and run.strip():
                commands.append(run)
    return tuple(commands)


def step_uses(workflow: Optional[Mapping[str, Any]]) -> Tuple[str, ...]:
    """Return every action the workflow uses.

    Args:
        workflow: The parsed workflow, or ``None``.

    Returns:
        The ``uses:`` values, in file order.
    """
    used: List[str] = []
    for job in workflow_jobs(workflow).values():
        for step in job_steps(_as_mapping(job)):
            uses = step.get("uses")
            if isinstance(uses, str) and uses.strip():
                used.append(uses)
    return tuple(used)


def commands_mentioning(workflow: Optional[Mapping[str, Any]], needle: str) -> Tuple[str, ...]:
    """Return the shell lines that mention ``needle``.

    Args:
        workflow: The parsed workflow, or ``None``.
        needle: The text to look for, case-insensitively.

    Returns:
        Every matching ``run:`` block.
    """
    lowered = needle.lower()
    return tuple(command for command in step_commands(workflow) if lowered in command.lower())


def nav_pages(nav: Any) -> Tuple[str, ...]:
    """Flatten an ``mkdocs.yml`` nav into its page paths, in reading order.

    Args:
        nav: The ``nav`` value: a list of strings, of ``title: path`` mappings,
            or of ``title: [children]`` sections, in any combination.

    Returns:
        The page paths, flattened depth-first.
    """
    pages: List[str] = []

    def walk(node: Any) -> None:
        """Collect the page paths under one nav node."""
        if isinstance(node, str):
            pages.append(node)
        elif isinstance(node, Mapping):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(nav)
    return tuple(pages)


def load_yaml_file(path: Path) -> Optional[Mapping[str, Any]]:
    """Parse an optional YAML file, returning ``None`` when it is unusable.

    Args:
        path: The file to read.

    Returns:
        The parsed mapping, or ``None`` when the file is absent, malformed, or
        not a mapping. A malformed workflow is a finding, not a crash: the rest
        of the release is still worth checking.
    """
    if yaml is None:  # pragma: no cover - the dependency is installed in CI
        return None
    path = Path(path)
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return document if isinstance(document, Mapping) else None


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def check_version(
    version: Optional[str],
    *,
    package: Optional[str],
    banner: Optional[str],
    tag: Optional[str] = None,
    where: str = "pyproject.toml",
) -> Iterator[Finding]:
    """Check that one version is declared once and declared the same way.

    Args:
        version: The distribution version from ``pyproject.toml``.
        package: ``datheon.__version__``, or ``None``.
        banner: ``cli_contract.PROGRAM_VERSION``, or ``None``.
        tag: A tag name to check the version against, if the caller gave one.
        where: The declaration's path, for the findings.

    Yields:
        One finding per disagreement or malformed declaration.
    """
    if version is None:
        yield Finding("version.missing", "[project] declares no version", where)
        return
    if not is_semver(version):
        yield Finding(
            "version.invalid",
            f"{version!r} is not a SemVer 2.0.0 version (expected MAJOR.MINOR.PATCH)",
            where,
        )

    if package is None:
        yield Finding(
            "version.package-missing",
            "datheon/__init__.py declares no __version__",
            "datheon/__init__.py",
        )
    elif package != version:
        yield Finding(
            "version.package-mismatch",
            f"datheon.__version__ is {package!r} but the distribution is {version!r}",
            "datheon/__init__.py",
        )

    if banner is None:
        yield Finding(
            "version.banner-missing",
            "cli_contract.py declares no PROGRAM_VERSION",
            "cli_contract.py",
        )
    elif banner != version:
        yield Finding(
            "version.banner-mismatch",
            f"PROGRAM_VERSION is {banner!r} but the distribution is {version!r}",
            "cli_contract.py",
        )

    if tag is not None:
        expected = tag_for(version)
        if not TAG_RE.match(tag):
            yield Finding(
                "tag.invalid",
                f"{tag!r} is not a release tag (expected {TAG_PREFIX}MAJOR.MINOR.PATCH)",
                where,
            )
        elif tag != expected:
            yield Finding(
                "tag.mismatch",
                f"tag {tag!r} does not match the declared version (expected {expected!r})",
                where,
            )


def check_changelog(
    changelog: Optional[Changelog],
    version: Optional[str],
    *,
    today: Optional[dt.date] = None,
    where: str = DEFAULT_CHANGELOG_REL,
) -> Iterator[Finding]:
    """Check a changelog against the Keep a Changelog convention.

    Args:
        changelog: The parsed changelog, or ``None`` when the file is absent.
        version: The version the repository declares.
        today: The date to compare entries against; defaults to the real today.
        where: The changelog's path, for the findings.

    Yields:
        One finding per structural defect, in the order they are checked.
    """
    if changelog is None:
        yield Finding("changelog.missing", "there is no CHANGELOG.md", where)
        return

    if not changelog.title:
        yield Finding("changelog.title-missing", "the file has no '# Changelog' title", where)
    elif changelog.title.lower() != "changelog":
        yield Finding(
            "changelog.title",
            f"the title is {changelog.title!r}; the convention expects 'Changelog'",
            where,
            "warning",
        )

    for reference in REQUIRED_REFERENCES:
        if reference not in changelog.text:
            yield Finding(
                "changelog.reference-missing",
                f"the file does not cite {reference}, so its rules are unstated",
                where,
            )

    if not changelog.entries:
        yield Finding("changelog.empty", "the file has no '##' entry at all", where)
        return

    if not any(entry.is_unreleased for entry in changelog.entries):
        yield Finding(
            "changelog.unreleased-missing",
            "there is no '## [Unreleased]' section for work that has not shipped",
            where,
        )

    today = today or dt.date.today()
    seen: Dict[str, int] = {}
    order: List[Tuple[int, int, int]] = []

    for entry in changelog.entries:
        prefix = f"line {entry.line}: "
        if not entry.bracketed:
            yield Finding(
                "changelog.heading-unbracketed",
                prefix + f"'## {entry.label}' should be '## [{entry.label}]'",
                where,
                "warning",
            )

        if entry.is_unreleased:
            if entry.date is not None:
                yield Finding(
                    "changelog.unreleased-dated",
                    prefix + "the Unreleased section carries a date; it has not shipped yet",
                    where,
                    "warning",
                )
            allowed = ALLOWED_UNRELEASED_SECTIONS
        else:
            allowed = ALLOWED_SECTIONS
            # `is_unreleased` is exactly `version is None`, so this is a
            # narrowing, not a fallback.
            label = entry.version or entry.label
            if not is_semver(label):
                yield Finding(
                    "changelog.version-invalid",
                    prefix + f"{label!r} is not a SemVer 2.0.0 version",
                    where,
                )
            else:
                key = semver_key(label)
                if order and key > order[-1]:
                    yield Finding(
                        "changelog.order",
                        prefix + f"{label} is newer than the entry above it",
                        where,
                    )
                order.append(key)

            if label in seen:
                yield Finding(
                    "changelog.duplicate",
                    prefix + f"{label} already has an entry on line {seen[label]}",
                    where,
                )
            else:
                seen[label] = entry.line

            if entry.date is None:
                yield Finding(
                    "changelog.date-missing",
                    prefix + f"{label} has no release date ('## [{label}] - YYYY-MM-DD')",
                    where,
                )
            elif not DATE_RE.match(entry.date):
                yield Finding(
                    "changelog.date-invalid",
                    prefix + f"{entry.date!r} is not an ISO date (YYYY-MM-DD)",
                    where,
                )
            else:
                try:
                    released = dt.date.fromisoformat(entry.date)
                except ValueError:
                    yield Finding(
                        "changelog.date-invalid",
                        prefix + f"{entry.date!r} is not a real calendar date",
                        where,
                    )
                else:
                    if released > today:
                        yield Finding(
                            "changelog.date-future",
                            prefix + f"{label} is dated {entry.date}, which is in the future",
                            where,
                        )

        for section in entry.sections:
            if section not in allowed:
                yield Finding(
                    "changelog.section-unknown",
                    prefix + f"'{section}' is not a Keep a Changelog section",
                    where,
                )

        if not entry.sections:
            yield Finding(
                "changelog.entry-empty",
                prefix + f"{entry.label} has no '###' section, so it says nothing",
                where,
            )

        label = "Unreleased" if entry.is_unreleased else (entry.version or entry.label)
        if label not in changelog.links:
            yield Finding(
                "changelog.link-missing",
                prefix + f"there is no '[{label}]: <url>' link definition",
                where,
            )

    if version is not None and changelog.find(version) is None:
        yield Finding(
            "changelog.version-missing",
            f"the declared version {version!r} has no entry, so nobody knows what it contains",
            where,
        )

    for label, url in changelog.links.items():
        if label in ("Unreleased", "Keep a Changelog", "Semantic Versioning"):
            continue
        if not is_semver(label):
            continue
        if tag_for(label) not in url:
            yield Finding(
                "changelog.link-mismatch",
                f"[{label}] links to {url}, which does not name the tag {tag_for(label)!r}",
                where,
            )


def check_workflow(
    workflow: Optional[Mapping[str, Any]],
    *,
    where: str = DEFAULT_WORKFLOW_REL,
) -> Iterator[Finding]:
    """Check the tag-triggered release workflow.

    Args:
        workflow: The parsed workflow, or ``None`` when the file is absent.
        where: The workflow's path, for the findings.

    Yields:
        One finding per missing guarantee.
    """
    if workflow is None:
        yield Finding("workflow.missing", "there is no release workflow", where)
        return

    triggers = workflow_triggers(workflow)
    push = _as_mapping(triggers.get("push"))
    tags = [tag for tag in _as_list(push.get("tags")) if isinstance(tag, str)]
    if not any(tag.startswith(TAG_PREFIX) for tag in tags):
        yield Finding(
            "workflow.trigger",
            "the workflow does not trigger on a 'v*' tag, so nothing publishes a release",
            where,
        )

    permissions = _as_mapping(workflow.get("permissions"))
    if permissions.get("contents") != "write":
        yield Finding(
            "workflow.permissions",
            "the workflow needs 'permissions: contents: write' to create a Release",
            where,
        )

    used = step_uses(workflow)
    if not any(action.startswith("actions/checkout") for action in used):
        yield Finding("workflow.checkout", "no 'actions/checkout' step", where)
    if not any(action.startswith("actions/setup-python") for action in used):
        yield Finding(
            "workflow.python",
            "no 'actions/setup-python' step, so the build uses whatever Python the runner has",
            where,
            "warning",
        )

    commands = step_commands(workflow)
    joined = "\n".join(commands)

    if "python -m build" not in joined:
        yield Finding("workflow.build", "the workflow never runs 'python -m build'", where)
    if "twine check" not in joined:
        yield Finding(
            "workflow.twine",
            "the workflow never runs 'twine check', so the metadata PyPI validates is unchecked",
            where,
        )
    if not any("release.py" in command and "--tag" in command for command in commands):
        yield Finding(
            "workflow.tag-check",
            "the workflow does not verify the tag against the declared version",
            where,
        )
    if "gh release create" not in joined:
        yield Finding("workflow.release", "the workflow never creates a GitHub Release", where)
    if "--notes-file" not in joined:
        yield Finding(
            "workflow.notes",
            "the Release notes are not taken from the changelog via '--notes-file'",
            where,
        )

    publishing = commands_mentioning(workflow, "gh release create")
    if not any(DIST_GLOB in command for command in publishing):
        yield Finding(
            "workflow.assets",
            f"the Release does not attach the built artefacts ('{DIST_GLOB}')",
            where,
        )

    for pattern in TOKEN_PATTERNS:
        if pattern in joined:
            yield Finding(
                "workflow.secret",
                f"the workflow contains a literal credential ({pattern}…)",
                where,
            )
            break

    for job in workflow_jobs(workflow).values():
        for step in job_steps(_as_mapping(job)):
            for value in _as_mapping(step.get("env")).values():
                if isinstance(value, str) and value.startswith("gh"):
                    yield Finding(
                        "workflow.secret",
                        "an environment variable holds a literal token",
                        where,
                    )

    defaults = _as_mapping(_as_mapping(workflow.get("defaults")).get("run"))
    if defaults.get("shell") != "bash":
        yield Finding(
            "workflow.shell",
            "no 'defaults.run.shell: bash'; the publishing commands are POSIX shell",
            where,
            "warning",
        )


def check_documentation(
    *,
    release_doc: Optional[str],
    nav: Sequence[str],
    readme: Optional[str],
    where: str = "docs/release.md",
) -> Iterator[Finding]:
    """Check that the release process is documented and reachable.

    Args:
        release_doc: The policy page's source, or ``None`` when it is absent.
        nav: The documentation site's page paths.
        readme: The README's text, or ``None``.
        where: The policy page's path, for the findings.

    Yields:
        One finding per missing or unreachable document.
    """
    if release_doc is None:
        yield Finding("docs.page-missing", "there is no release policy page", where)
    else:
        if len(release_doc.strip()) < 500:
            yield Finding(
                "docs.page-thin",
                "the release policy page is too short to describe a release",
                where,
            )
        if "release.md" not in nav:
            yield Finding(
                "docs.nav-missing",
                "the release policy page is not in the site's navigation",
                "mkdocs.yml",
            )

    if readme is None:
        yield Finding(
            "docs.readme-missing",
            "there is no README to link the Release from",
            "README.md",
        )
        return
    if "/releases" not in readme:
        yield Finding(
            "docs.readme-releases",
            "the README does not link the Releases page",
            "README.md",
        )
    if "CHANGELOG.md" not in readme:
        yield Finding(
            "docs.readme-changelog",
            "the README does not link CHANGELOG.md",
            "README.md",
        )


def audit_release(
    *,
    version: Optional[str],
    changelog: Optional[Changelog],
    package: Optional[str] = None,
    banner: Optional[str] = None,
    workflow: Optional[Mapping[str, Any]] = None,
    workflow_path: str = DEFAULT_WORKFLOW_REL,
    nav: Sequence[str] = (),
    readme: Optional[str] = None,
    release_doc: Optional[str] = None,
    tag: Optional[str] = None,
    today: Optional[dt.date] = None,
    strict: bool = False,
    changelog_path: str = DEFAULT_CHANGELOG_REL,
) -> Verdict:
    """Audit one release against the repository it describes.

    Args:
        version: The version ``pyproject.toml`` declares.
        changelog: The parsed ``CHANGELOG.md``, or ``None``.
        package: ``datheon.__version__``, or ``None``.
        banner: ``cli_contract.PROGRAM_VERSION``, or ``None``.
        workflow: The parsed release workflow, or ``None``.
        workflow_path: The workflow's path, for the findings' ``where``.
        nav: The documentation site's page paths.
        readme: The README's text, or ``None``.
        release_doc: The release policy page's source, or ``None``.
        tag: A tag name to check the version against, if any.
        today: The date to compare changelog entries against.
        strict: Whether warnings count as failures.
        changelog_path: The changelog's path, for the findings.

    Returns:
        The verdict: the version, the released versions, and every finding.
    """
    findings: List[Finding] = []
    findings.extend(check_version(version, package=package, banner=banner, tag=tag))
    findings.extend(check_changelog(changelog, version, today=today, where=changelog_path))
    findings.extend(check_workflow(workflow, where=workflow_path))
    findings.extend(check_documentation(release_doc=release_doc, nav=nav, readme=readme))

    releases = changelog.versions() if changelog is not None else ()
    return Verdict(
        version=version,
        tag=tag,
        changelog_path=changelog_path,
        releases=releases,
        findings=tuple(findings),
        strict=strict,
        measurable=version is not None and changelog is not None,
    )


def render_report(verdict: Verdict) -> str:
    """Render a verdict as the text a build log should show.

    Args:
        verdict: The verdict to render.

    Returns:
        A multi-line report: the release's shape, then each finding, then the
        verdict.
    """
    lines: List[str] = [
        "Release audit",
        f"  version:  {verdict.version or '(none declared)'}",
        f"  tag:      {verdict.tag or '(not checked)'}",
        f"  changelog: {verdict.changelog_path}",
        f"  releases: {len(verdict.releases)}",
    ]
    if verdict.releases:
        lines.append("    " + ", ".join(verdict.releases))
    if verdict.findings:
        lines.append("")
        for finding in verdict.findings:
            marker = "FAIL" if finding.is_error else "WARN"
            lines.append(f"  [{marker}] {finding.code}: {finding.message}")
            lines.append(f"         at {finding.where}")
    lines.append("")
    errors = len(verdict.errors)
    warnings = len(verdict.warnings)
    if verdict.ok:
        lines.append(
            f"PASS  {len(verdict.releases)} released version(s), "
            f"{warnings} warning(s), tag {verdict.tag or 'unchecked'}"
        )
    elif not verdict.measurable or not verdict.releases:
        lines.append("NO VERDICT  the repository declares no release to measure")
    else:
        lines.append(f"FAIL  {errors} error(s), {warnings} warning(s)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser accepting the audit's options.
    """
    parser = argparse.ArgumentParser(
        prog="release-audit",
        description=(
            "Audit the release: one SemVer version in every declaration, a "
            "Keep a Changelog history, a tag-triggered publish workflow and a "
            "documented release policy. Also extracts release notes."
        ),
    )
    parser.add_argument(
        "--pyproject",
        default=str(DEFAULT_PYPROJECT),
        help="packaging declaration to read (default: pyproject.toml)",
    )
    parser.add_argument(
        "--changelog",
        default=str(DEFAULT_CHANGELOG),
        help="changelog to audit (default: CHANGELOG.md)",
    )
    parser.add_argument(
        "--workflow",
        default=str(DEFAULT_WORKFLOW),
        help="release workflow to audit (default: .github/workflows/release.yml)",
    )
    parser.add_argument(
        "--mkdocs",
        default=str(DEFAULT_MKDOCS),
        help="site configuration whose nav must list the policy (default: mkdocs.yml)",
    )
    parser.add_argument(
        "--readme",
        default=str(DEFAULT_README),
        help="README that must link the Release (default: README.md)",
    )
    parser.add_argument(
        "--release-doc",
        default=str(DEFAULT_RELEASE_DOC),
        help="release policy page (default: docs/release.md)",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="tag name to check the declared version against, e.g. v1.1.0",
    )
    parser.add_argument(
        "--list-releases",
        action="store_true",
        help="print the released versions, newest first, and exit",
    )
    parser.add_argument(
        "--notes",
        default=None,
        metavar="VERSION",
        help="print the changelog entry for VERSION (or its tag) as release notes",
    )
    parser.add_argument(
        "--notes-file",
        default=None,
        metavar="PATH",
        help="write the notes from --notes to PATH instead of stdout",
    )
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit, or print the release notes it was asked for.

    Args:
        argv: Arguments to parse; ``None`` means ``sys.argv[1:]``.

    Returns:
        :data:`EXIT_OK` when the release passes, :data:`EXIT_FINDINGS` when it
        does not, :data:`EXIT_UNMEASURABLE` when nothing could be measured.
    """
    args = build_parser().parse_args(argv)

    try:
        pyproject = load_pyproject(Path(args.pyproject))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"release-audit: cannot read {args.pyproject}: {exc}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    version = declared_version(pyproject)
    changelog_text = read_text(Path(args.changelog))
    changelog = parse_changelog(changelog_text) if changelog_text is not None else None

    if args.notes is not None:
        if changelog is None:
            print(f"release-audit: cannot read {args.changelog}", file=sys.stderr)
            return EXIT_UNMEASURABLE
        notes = release_notes(changelog, args.notes)
        if notes is None:
            print(
                f"release-audit: {args.changelog} has no entry for {args.notes}",
                file=sys.stderr,
            )
            return EXIT_UNMEASURABLE
        if args.notes_file:
            Path(args.notes_file).write_text(notes, encoding="utf-8")
            print(f"release-audit: wrote release notes for {args.notes} to {args.notes_file}")
        else:
            print(notes, end="")
        return EXIT_OK

    mkdocs = load_yaml_file(Path(args.mkdocs))
    verdict = audit_release(
        version=version,
        changelog=changelog,
        package=package_version(read_text(DEFAULT_PACKAGE)),
        banner=banner_version(read_text(DEFAULT_BANNER)),
        workflow=load_yaml_file(Path(args.workflow)),
        workflow_path=Path(args.workflow).as_posix(),
        nav=nav_pages(_as_mapping(mkdocs).get("nav")),
        readme=read_text(Path(args.readme)),
        release_doc=read_text(Path(args.release_doc)),
        tag=args.tag,
        strict=args.strict,
        changelog_path=Path(args.changelog).as_posix(),
    )

    if args.list_releases:
        for release in verdict.releases:
            print(release)
        return EXIT_OK if verdict.releases else EXIT_UNMEASURABLE

    print(json.dumps(verdict.to_dict(), indent=2) if args.json else render_report(verdict))

    if not verdict.measurable or not verdict.releases:
        return EXIT_UNMEASURABLE
    return EXIT_OK if verdict.ok else EXIT_FINDINGS


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
