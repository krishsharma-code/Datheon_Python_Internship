"""Audit the repository's community-health documents (Day 35).

Every other gate in ``tools/`` checks code: the tests, the linter, the coverage
floor, the container, the wheel. Two files in this repository are read by
strangers rather than by CI — ``SECURITY.md`` and ``CODE_OF_CONDUCT.md`` — and
until now nothing checked them at all. They are also the two files where rot is
expensive and silent:

* a **support matrix** that names a release line nobody ships tells a reporter
  their bug is out of scope when it is not;
* a **security policy with no private channel** pushes the report into a public
  issue, which is a disclosure;
* a **code of conduct with no enforcement contact** is a promise with nobody
  behind it;
* a **placeholder** (``TODO``, ``<insert contact>``) survives review because a
  reviewer reads intent, not text.

So this module treats both documents as artefacts and checks them the way the
README audit treats the README: it reads them, and reports a stable finding code
for each defect:

========================  ====================================================
Code                      What it means
========================  ====================================================
``health.missing``        A policy is not in any location GitHub looks in
                          (root, ``.github/``, ``docs/``).
``health.section``        ``SECURITY.md`` has lost a section a reporter needs
                          (supported versions, reporting, expectations, scope,
                          safe harbour).
``health.channel``        ``SECURITY.md`` names no private reporting channel, so
                          the only route left is a public issue.
``health.version``        The supported-versions table does not name the version
                          in ``pyproject.toml`` — the matrix has gone stale.
``health.response``       ``SECURITY.md`` promises no response time, so a
                          reporter cannot tell silence from a decision.
``health.covenant``       ``CODE_OF_CONDUCT.md`` does not say which Contributor
                          Covenant version it adapts.
``health.enforcement``    ``CODE_OF_CONDUCT.md`` names no enforcement contact.
``health.placeholder``    ``TODO``/``TBD``/``FIXME``/``lorem ipsum`` or an
                          ``<insert …>`` placeholder left in either document.
``health.secret``         A token-shaped string (``ghp_``, ``xoxb-``, ``AKIA``)
                          anywhere in either document.
``health.link``           ``README.md`` or ``CONTRIBUTING.md`` does not name the
                          policy, so nobody arriving at the repository finds it.
``health.docs``           ``docs/community_health.md`` is missing, or no longer
                          names a code this module can emit.
========================  ====================================================

The last one is the trick ``tools/precommit_config.py`` uses on its own page:
the documentation fails the build when it stops describing the tool, so the
table of codes cannot drift behind the checks.

Run it directly::

    python tools/community_health.py              # human-readable report
    python tools/community_health.py --json       # machine-readable
    python tools/community_health.py --strict     # warnings count as failures
    python tools/community_health.py --root .     # audit another checkout

Exit codes: ``0`` both documents pass, ``1`` at least one finding, ``2`` the
audit could not reach a verdict (the root is not a directory, or neither policy
exists anywhere, so "not written" and "wrong path" cannot be told apart). A gate
that cannot measure must never report success.

Standard library only, like every other tool here. ``tomli`` is imported only on
Python 3.10, where ``tomllib`` does not exist yet.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on a bare Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The security policy, by the name GitHub's community profile looks for.
SECURITY_POLICY = "SECURITY.md"

#: The code of conduct, by the same rule.
CODE_OF_CONDUCT = "CODE_OF_CONDUCT.md"

#: Both documents, in report order.
POLICY_FILES: Tuple[str, ...] = (SECURITY_POLICY, CODE_OF_CONDUCT)

#: The directories GitHub reads these files from, in its own priority order.
GITHUB_LOCATIONS: Tuple[Path, ...] = (Path("."), Path(".github"), Path("docs"))

#: Sections a security policy needs, as ``(name, accepted heading words)``.
SECURITY_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("supported versions", ("supported versions", "supported version", "supported")),
    ("reporting", ("reporting a vulnerability", "report a vulnerability", "reporting")),
    ("expectations", ("what to expect", "expectations", "response", "timeline")),
    ("scope", ("scope",)),
    ("safe harbour", ("safe harbour", "safe harbor", "safe-harbour", "safe-harbor")),
)

#: Ways of naming a private channel. One of these must appear in the policy:
#: a public issue is a disclosure, so "report it somewhere" is not enough.
PRIVATE_CHANNELS: Tuple[str, ...] = (
    "security/advisories",
    "private vulnerability reporting",
    "mailto:",
    "security@",
)

#: The document that declares what the code of conduct adapts.
COVENANT_NAME = "contributor covenant"

#: The oldest covenant revision this repository will accept as declared.
MINIMUM_COVENANT_VERSION = (2, 0)

#: A contact handle or address, as written in a code of conduct.
CONTACT_RE = re.compile(
    r"(?:@[A-Za-z0-9][A-Za-z0-9-]{1,}|\bmailto:[^\s)]+|\b[\w.+-]+@[\w-]+\.[\w.]+)"
)

#: ``3 business days``, ``within 10 days`` — a promise with a number in it.
RESPONSE_RE = re.compile(r"\b(\d+)\s+(?:business\s+|working\s+)?days?\b", re.IGNORECASE)

#: A version cell in the support matrix, e.g. ``1.1.x`` or ``< 1.0``.
VERSION_CELL_RE = re.compile(r"\b(\d+)\.(\d+)\b")

#: ATX headings.
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*)$", re.MULTILINE)

#: Phrases that must never reach a published policy.
PLACEHOLDERS: Tuple[str, ...] = (
    "TODO",
    "TBD",
    "FIXME",
    "lorem ipsum",
    "<insert",
    "[insert",
)

#: Token shapes that must never be committed.
SECRET_PATTERNS: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"gho_[A-Za-z0-9]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)

#: Documents that must link the policies, or nobody arriving finds them.
LINKING_DOCUMENTS: Tuple[Path, ...] = (Path("README.md"), Path("CONTRIBUTING.md"))

#: The page that documents this tool, and must name every code below.
DOCS_PAGE = Path("docs") / "community_health.md"

#: Every finding code this module can emit, in documentation order.
CODES: Tuple[str, ...] = (
    "health.missing",
    "health.section",
    "health.channel",
    "health.version",
    "health.response",
    "health.covenant",
    "health.enforcement",
    "health.placeholder",
    "health.secret",
    "health.link",
    "health.docs",
)


@dataclass(frozen=True)
class Finding:
    """One community-health defect.

    Attributes:
        code: Stable identifier (``health.channel``, ``health.version``, …).
        location: File the finding is about, relative to the repository root.
        detail: Human-readable explanation naming what is wrong.
        severity: ``error`` fails the audit, ``warning`` only under ``--strict``.
    """

    code: str
    location: str
    detail: str
    severity: str = "error"

    @property
    def is_error(self) -> bool:
        """Whether this finding fails a default (non-strict) run.

        Returns:
            ``True`` for errors, ``False`` for warnings.
        """
        return self.severity == "error"


def locate_policy(root: Path, name: str) -> Optional[Path]:
    """Find a policy document where GitHub would find it.

    Args:
        root: Repository root.
        name: File name to look for, e.g. ``SECURITY.md``.

    Returns:
        The first existing candidate in :data:`GITHUB_LOCATIONS` order, or
        ``None`` when the document is nowhere GitHub looks.
    """
    for directory in GITHUB_LOCATIONS:
        candidate = root / directory / name
        if candidate.is_file():
            return candidate
    return None


def headings(text: str) -> List[str]:
    """Collect the headings of a Markdown document, lowercased.

    Args:
        text: Document text.

    Returns:
        Heading text without the ``#`` markers, in document order.
    """
    return [match.group(2).strip().lower() for match in _HEADING_RE.finditer(text)]


def has_section(text: str, accepted: Iterable[str]) -> bool:
    """Whether any heading contains one of the accepted words.

    Args:
        text: Document text.
        accepted: Lowercase words that would satisfy the section.

    Returns:
        ``True`` when a heading matches.
    """
    found = headings(text)
    return any(word in heading for word in accepted for heading in found)


def project_version(root: Path) -> Optional[str]:
    """Read the version this repository ships from ``pyproject.toml``.

    Args:
        root: Repository root.

    Returns:
        The ``[project] version`` string, or ``None`` when the file or the key
        is missing, or the file cannot be parsed.
    """
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def version_lines(text: str) -> List[Tuple[int, int]]:
    """Read the release lines named in a supported-versions table.

    Only table rows count: a version mentioned in prose is not a support claim,
    and counting prose would let the matrix rot while the audit stayed green.

    Args:
        text: Security-policy text.

    Returns:
        ``(major, minor)`` pairs, in first-seen order, deduplicated.
    """
    lines: List[Tuple[int, int]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or "version" in cells[0].lower():
            continue
        for major, minor in VERSION_CELL_RE.findall(cells[0]):
            pair = (int(major), int(minor))
            if pair not in lines:
                lines.append(pair)
    return lines


def covenant_version(text: str) -> Optional[Tuple[int, int]]:
    """Read the Contributor Covenant revision a code of conduct declares.

    Args:
        text: Code-of-conduct text.

    Returns:
        ``(major, minor)``, or ``None`` when no version is declared next to the
        covenant's name. Only the window around the name counts: a ``3.10``
        elsewhere in the document is a Python version, not a declaration.
    """
    lowered = text.lower()
    index = lowered.find(COVENANT_NAME)
    if index < 0:
        return None
    window = text[max(0, index - 40) : index + len(COVENANT_NAME) + 120]
    for pattern in (r"version\s+(\d+)\.(\d+)", r"(\d+)\.(\d+)"):
        match = re.search(pattern, window, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _document_findings(location: str, text: str) -> List[Finding]:
    """Look for placeholders and token-shaped strings in one document.

    Args:
        location: File name used in the findings.
        text: Document text.

    Returns:
        One finding per placeholder phrase and per secret-shaped match.
    """
    findings: List[Finding] = []
    lowered = text.lower()
    for phrase in PLACEHOLDERS:
        if phrase.lower() in lowered:
            findings.append(
                Finding("health.placeholder", location, f"{phrase!r} left in the document")
            )
    for pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(
                Finding("health.secret", location, f"token-shaped string {match.group(0)[:8]}…")
            )
    return findings


def audit_security_policy(root: Path, path: Path, version: Optional[str]) -> List[Finding]:
    """Check the security policy a stranger will read first.

    Args:
        root: Repository root.
        path: The policy file that was found.
        version: The version ``pyproject.toml`` declares, when readable.

    Returns:
        Findings for missing sections, a missing private channel, a stale support
        matrix, a missing response commitment, placeholders and secrets.
    """
    try:
        location = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # a policy outside the repository, as in the tests
        location = path.name
    text = path.read_text(encoding="utf-8")
    findings: List[Finding] = []

    for name, accepted in SECURITY_SECTIONS:
        if not has_section(text, accepted):
            findings.append(
                Finding(
                    "health.section",
                    location,
                    f"no {name} section (looked for {accepted[0]!r})",
                )
            )

    if not any(marker in text for marker in PRIVATE_CHANNELS):
        findings.append(
            Finding(
                "health.channel",
                location,
                "names no private reporting channel — a public issue is a disclosure",
            )
        )

    if version is None:
        findings.append(
            Finding(
                "health.version",
                location,
                "pyproject.toml declares no [project] version, so the matrix cannot be checked",
                severity="warning",
            )
        )
    else:
        wanted = tuple(int(part) for part in version.split(".")[:2])
        if wanted not in version_lines(text):
            named = ", ".join(f"{major}.{minor}" for major, minor in version_lines(text)) or "none"
            findings.append(
                Finding(
                    "health.version",
                    location,
                    f"the support matrix names {named}, "
                    f"not {wanted[0]}.{wanted[1]} from pyproject.toml",
                )
            )

    if not RESPONSE_RE.search(text):
        findings.append(
            Finding(
                "health.response",
                location,
                "promises no response time in days, "
                "so silence is indistinguishable from a decision",
            )
        )

    findings.extend(_document_findings(location, text))
    return findings


def audit_code_of_conduct(root: Path, path: Path) -> List[Finding]:
    """Check the code of conduct for the promises a reader depends on.

    Args:
        root: Repository root.
        path: The code of conduct that was found.

    Returns:
        Findings for a missing covenant declaration, a missing enforcement
        contact, placeholders and secrets.
    """
    try:
        location = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # a policy outside the repository, as in the tests
        location = path.name
    text = path.read_text(encoding="utf-8")
    findings: List[Finding] = []

    declared = covenant_version(text)
    if declared is None:
        findings.append(
            Finding(
                "health.covenant",
                location,
                "does not name the Contributor Covenant version it adapts",
            )
        )
    elif declared < MINIMUM_COVENANT_VERSION:
        findings.append(
            Finding(
                "health.covenant",
                location,
                f"declares Contributor Covenant {declared[0]}.{declared[1]}, older than "
                f"{MINIMUM_COVENANT_VERSION[0]}.{MINIMUM_COVENANT_VERSION[1]}",
            )
        )

    if not CONTACT_RE.search(text):
        findings.append(
            Finding(
                "health.enforcement",
                location,
                "names no enforcement contact, so the code has nobody behind it",
            )
        )

    findings.extend(_document_findings(location, text))
    return findings


def _link_findings(root: Path) -> List[Finding]:
    """Check that the README and CONTRIBUTING name both policies.

    Args:
        root: Repository root.

    Returns:
        One ``health.link`` finding per document that omits a policy.
    """
    findings: List[Finding] = []
    for document in LINKING_DOCUMENTS:
        path = root / document
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for policy in POLICY_FILES:
            if policy not in text:
                findings.append(
                    Finding(
                        "health.link",
                        document.as_posix(),
                        f"does not link {policy}",
                        severity="error",
                    )
                )
    return findings


def _docs_findings(root: Path) -> List[Finding]:
    """Check that the tool's own page exists and documents every code.

    Args:
        root: Repository root.

    Returns:
        A finding when the page is missing, plus one per code it omits.
    """
    path = root / DOCS_PAGE
    if not path.is_file():
        return [
            Finding("health.docs", DOCS_PAGE.as_posix(), "the tool's documentation page is missing")
        ]
    text = path.read_text(encoding="utf-8")
    return [
        Finding("health.docs", DOCS_PAGE.as_posix(), f"does not document the {code} finding")
        for code in CODES
        if code not in text
    ]


def audit_repository(root: Path = ROOT) -> List[Finding]:
    """Run every community-health check against a checkout.

    Args:
        root: Repository root.

    Returns:
        Findings in report order. An empty list means both documents are fit to
        publish.
    """
    findings: List[Finding] = []
    version = project_version(root)

    security = locate_policy(root, SECURITY_POLICY)
    if security is None:
        findings.append(
            Finding(
                "health.missing",
                SECURITY_POLICY,
                "not in the repository root, .github/ or docs/",
            )
        )
    else:
        findings.extend(audit_security_policy(root, security, version))

    conduct = locate_policy(root, CODE_OF_CONDUCT)
    if conduct is None:
        findings.append(
            Finding(
                "health.missing",
                CODE_OF_CONDUCT,
                "not in the repository root, .github/ or docs/",
            )
        )
    else:
        findings.extend(audit_code_of_conduct(root, conduct))

    findings.extend(_link_findings(root))
    findings.extend(_docs_findings(root))
    return findings


def summary(root: Path, findings: Sequence[Finding]) -> Dict[str, object]:
    """Build the machine-readable verdict.

    Args:
        root: Repository root the audit ran against.
        findings: Findings from :func:`audit_repository`.

    Returns:
        A JSON-serialisable document describing what was found and where.
    """
    located = {name: locate_policy(root, name) for name in POLICY_FILES}
    errors = sum(1 for finding in findings if finding.is_error)
    return {
        "root": str(root),
        "version": project_version(root),
        "documents": {
            name: (path.relative_to(root).as_posix() if path is not None else None)
            for name, path in located.items()
        },
        "findings": [asdict(finding) for finding in findings],
        "errors": errors,
        "warnings": len(findings) - errors,
        "ok": not findings,
    }


def render_table(root: Path, findings: Sequence[Finding]) -> str:
    """Render the verdict as a fixed-width table.

    Args:
        root: Repository root the audit ran against.
        findings: Findings to render.

    Returns:
        The table, or a one-line pass message naming what was measured.
    """
    located = [name for name in POLICY_FILES if locate_policy(root, name) is not None]
    if not findings:
        version = project_version(root) or "unknown"
        return (
            "community-health: "
            f"{', '.join(located)} pass — support matrix names {version}, "
            f"{len(CODES)} finding codes documented."
        )
    lines = [f"{'severity':<8} {'code':<22} {'location':<26} detail"]
    lines.append("-" * 108)
    for finding in findings:
        lines.append(
            f"{finding.severity:<8} {finding.code:<22} {finding.location:<26} {finding.detail}"
        )
    errors = sum(1 for finding in findings if finding.is_error)
    lines.append("")
    lines.append(f"{errors} error(s), {len(findings) - errors} warning(s).")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser exposing ``--root``, ``--json``, ``--strict`` and ``--version``.
    """
    parser = argparse.ArgumentParser(
        prog="community-health",
        description="Check SECURITY.md and CODE_OF_CONDUCT.md as artefacts, not as prose.",
    )
    parser.add_argument("--root", default=str(ROOT), help="repository root to audit")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    parser.add_argument("--version", action="version", version="community-health 1.0")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit.

    Args:
        argv: Argument list; ``None`` means :data:`sys.argv`.

    Returns:
        ``0`` when both documents pass, ``1`` when there are findings, ``2``
        when the audit cannot reach a verdict at all.
    """
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"community-health: {root} is not a directory — nothing to measure", file=sys.stderr)
        return 2
    if all(locate_policy(root, name) is None for name in POLICY_FILES):
        print(
            f"community-health: neither {POLICY_FILES[0]} nor {POLICY_FILES[1]} exists under "
            f"{root} — nothing to measure",
            file=sys.stderr,
        )
        return 2

    findings = audit_repository(root)
    blocking = [finding for finding in findings if finding.is_error or args.strict]

    if args.json:
        print(json.dumps(summary(root, findings), indent=2))
    else:
        print(render_table(root, findings))

    return 1 if blocking else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
