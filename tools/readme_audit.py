"""Audit the README as a submission artefact, not as prose (Day 27).

The README is the first thing a reviewer, a recruiter or the Datheon assessor
reads, and it is the one file in the repository with no tests behind it. A
relative link that rots, a diagram that is referenced but not committed, a
placeholder that ships to production — all of them pass CI silently, because
nothing has ever looked at the README as a build artefact.

This module looks at it. It checks four things and reports a stable finding
code for each:

=====================  =======================================================
Code                   What it means
=====================  =======================================================
``readme.section``     A section the submission needs is missing (quickstart,
                       architecture, documentation, projects, licence).
``readme.quickstart``  The quickstart block has lost a clone, install or test
                       command, so a reader cannot get from zero to green.
``readme.link``        A relative link or image points at a file that is not
                       there, or a ``#fragment`` no heading produces.
``readme.asset``       A referenced asset is missing, empty, not a valid SVG,
                       a still GIF, or larger than the byte budget.
``readme.diagram``     The architecture diagram is not referenced from both the
                       README and the documentation home page.
``readme.demo``        The demo GIF is not referenced from the README.
``readme.orphan``      An asset under ``docs/assets`` that nothing references
                       (a warning: dead weight, not a broken claim).
``readme.placeholder`` ``TODO``/``TBD``/``FIXME``/``lorem ipsum`` in the prose.
``readme.secret``      A token-shaped string (``ghp_``, ``github_pat_``,
                       ``xoxb-``, ``AKIA``) anywhere in the README.
=====================  =======================================================

The exit code is the verdict: ``0`` clean, ``1`` findings, ``2`` unmeasurable
(the README is absent, or ``docs/assets`` is missing, so the asset claims cannot
be checked at all). Reporting ``2`` instead of ``0`` for "I could not look" is
the same rule the other audits in ``tools/`` follow — a gate that passes when it
cannot measure is not a gate.

Run it directly::

    python tools/readme_audit.py              # human-readable report
    python tools/readme_audit.py --json       # machine-readable
    python tools/readme_audit.py --strict     # warnings count as failures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The README audited by default.
DEFAULT_README = ROOT / "README.md"

#: Where the Day 27 assets live, relative to the repository root.
ASSETS_DIR = Path("docs") / "assets"

#: The architecture diagram, relative to the repository root.
DIAGRAM = ASSETS_DIR / "architecture.svg"

#: The animated demo, relative to the repository root.
DEMO_GIF = ASSETS_DIR / "demo" / "quickstart.gif"

#: Documents that must both show the architecture diagram.
DIAGRAM_REFERRERS = (Path("README.md"), Path("docs") / "index.md")

#: Sections the submission needs, as ``(code-name, accepted heading words)``.
REQUIRED_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("overview", ("overview",)),
    ("quickstart", ("quickstart", "getting started", "installation")),
    ("architecture", ("architecture",)),
    ("documentation", ("documentation", "docs")),
    ("projects", ("projects", "tasks")),
    ("licence", ("license", "licence")),
)

#: Commands the quickstart block must still contain.
QUICKSTART_COMMANDS: Tuple[str, ...] = ("git clone", "pip install", "pytest")

#: File extensions treated as embeddable assets.
ASSET_SUFFIXES = frozenset({".svg", ".gif", ".png", ".jpg", ".jpeg", ".webp"})

#: Ceiling for a single asset, in bytes. A README that takes ten seconds to
#: paint is a README nobody reads.
ASSET_BUDGET_BYTES = 2 * 1024 * 1024

#: Phrases that must never reach a submission.
PLACEHOLDERS = ("TODO", "TBD", "FIXME", "lorem ipsum")

#: Token shapes that must never be committed.
SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"gho_[A-Za-z0-9]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)

#: ``[text](target)`` and ``![alt](target)``, with an optional title.
_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
#: Reference definitions: ``[label]: target``.
_REF_RE = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*(\S+)", re.MULTILINE)
#: Fenced code blocks, dropped before link scanning.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
#: Inline code spans, dropped for the same reason.
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
#: ATX headings.
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*)$", re.MULTILINE)
#: Characters GitHub drops when it builds an anchor.
_ANCHOR_DROP_RE = re.compile(r"[^\w\s-]", re.UNICODE)


@dataclass(frozen=True)
class Finding:
    """One README defect.

    Attributes:
        code: Stable identifier (``readme.link``, ``readme.asset``, …).
        location: File the finding is about, relative to the repository root.
        detail: Human-readable explanation naming the offending target.
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


@dataclass(frozen=True)
class AssetReport:
    """What an asset file turned out to be.

    Attributes:
        path: Path as given.
        exists: Whether the file is there.
        size: Size in bytes (``0`` when absent).
        kind: ``svg``, ``gif``, ``image`` or ``unknown``.
        frames: Frame count for a GIF, ``1`` for a still image.
        width: Pixel width when the format records one, else ``0``.
        height: Pixel height when the format records one, else ``0``.
        error: Why the asset is unusable, or ``""`` when it is fine.
    """

    path: str
    exists: bool
    size: int
    kind: str
    frames: int = 1
    width: int = 0
    height: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        """Whether the asset is present, non-empty, valid and within budget.

        Returns:
            ``True`` when nothing is wrong with the file.
        """
        return self.exists and self.size > 0 and not self.error

    def summary(self) -> str:
        """Render a one-line description for a terminal.

        Returns:
            Something like ``gif 900x1188, 132 frames, 412 KB — ok``.
        """
        if not self.exists:
            return "missing"
        size_kb = self.size / 1024
        geometry = f"{self.width}x{self.height}" if self.width else "geometry unknown"
        frames = f", {self.frames} frames" if self.kind == "gif" else ""
        verdict = "ok" if self.ok else self.error
        return f"{self.kind} {geometry}{frames}, {size_kb:.0f} KB — {verdict}"


def gif_geometry(data: bytes) -> Tuple[int, int, int]:
    """Read a GIF's dimensions and frame count straight from its bytes.

    The block walk is the only reliable way to count frames: counting ``0x2C``
    bytes would also count them inside compressed image data, and counting
    graphic-control extensions assumes the encoder emits one per frame.

    Args:
        data: The complete GIF file.

    Returns:
        ``(width, height, frames)``; ``(0, 0, 0)`` when the bytes are not a GIF
        this walker understands.
    """
    if len(data) < 13 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return 0, 0, 0
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    packed = data[10]
    offset = 13
    if packed & 0x80:  # a global colour table follows the descriptor
        offset += 3 * (2 ** ((packed & 0x07) + 1))

    frames = 0
    while offset < len(data):
        marker = data[offset]
        if marker == 0x3B:  # trailer
            break
        if marker == 0x21:  # extension: label, then sub-blocks
            offset += 2
            offset = _skip_sub_blocks(data, offset)
        elif marker == 0x2C:  # image descriptor
            frames += 1
            if offset + 10 > len(data):
                break
            local = data[offset + 9]
            offset += 10
            if local & 0x80:
                offset += 3 * (2 ** ((local & 0x07) + 1))
            offset += 1  # LZW minimum code size
            offset = _skip_sub_blocks(data, offset)
        else:  # pragma: no cover - corrupt stream
            return width, height, frames
    return width, height, frames


def _skip_sub_blocks(data: bytes, offset: int) -> int:
    """Advance past a chain of length-prefixed sub-blocks.

    Args:
        data: The GIF bytes.
        offset: Index of the first length byte.

    Returns:
        The index just past the terminating zero-length block.
    """
    while offset < len(data):
        length = data[offset]
        offset += 1
        if length == 0:
            return offset
        offset += length
    return offset


def inspect_asset(path: Path) -> AssetReport:
    """Classify one asset file and say whether it is fit to embed.

    Args:
        path: File to inspect.

    Returns:
        An :class:`AssetReport`; ``error`` is empty only when the file is
        present, non-empty, structurally valid and within :data:`ASSET_BUDGET_BYTES`.
    """
    suffix = path.suffix.lower()
    if not path.is_file():
        return AssetReport(str(path), False, 0, _kind_of(suffix), error="file not found")
    data = path.read_bytes()
    size = len(data)
    if size == 0:
        return AssetReport(str(path), True, 0, _kind_of(suffix), error="empty file")

    if suffix == ".svg":
        try:
            root = ET.fromstring(data.decode("utf-8"))
        except (ET.ParseError, UnicodeDecodeError) as exc:
            return AssetReport(str(path), True, size, "svg", error=f"not valid XML: {exc}")
        if not root.tag.endswith("svg"):
            return AssetReport(str(path), True, size, "svg", error=f"root element is {root.tag!r}")
        return AssetReport(str(path), True, size, "svg")

    if suffix == ".gif":
        width, height, frames = gif_geometry(data)
        report = AssetReport(
            str(path), True, size, "gif", frames=frames, width=width, height=height
        )
        if size > ASSET_BUDGET_BYTES:
            return AssetReport(
                str(path),
                True,
                size,
                "gif",
                frames=frames,
                width=width,
                height=height,
                error=_budget_error(size),
            )
        if frames < 2:
            return AssetReport(
                str(path),
                True,
                size,
                "gif",
                frames=frames,
                width=width,
                height=height,
                error="not animated (1 frame)",
            )
        return report

    if suffix in ASSET_SUFFIXES:
        report = AssetReport(str(path), True, size, "image")
        if size > ASSET_BUDGET_BYTES:
            return AssetReport(str(path), True, size, "image", error=_budget_error(size))
        return report

    return AssetReport(str(path), True, size, "unknown", error=f"unsupported suffix {suffix!r}")


def _kind_of(suffix: str) -> str:
    """Map a file suffix to an asset kind.

    Args:
        suffix: Lower-case suffix including the dot.

    Returns:
        ``svg``, ``gif``, ``image`` or ``unknown``.
    """
    if suffix == ".svg":
        return "svg"
    if suffix == ".gif":
        return "gif"
    return "image" if suffix in ASSET_SUFFIXES else "unknown"


def _budget_error(size: int) -> str:
    """Build the over-budget message for an asset.

    Args:
        size: Size in bytes.

    Returns:
        A message naming both the size and the budget.
    """
    return f"{size / 1024:.0f} KB exceeds the {ASSET_BUDGET_BYTES // 1024} KB budget"


def strip_code(text: str) -> str:
    """Remove fenced blocks and inline code spans from markdown.

    Links inside code are examples, not links; scanning them would report a
    ``readme.link`` finding for every command in the quickstart.

    Args:
        text: Markdown source.

    Returns:
        The same text with code removed, line structure preserved.
    """
    without_fences = _FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return _CODE_SPAN_RE.sub("", without_fences)


def iter_links(text: str) -> List[Tuple[bool, str]]:
    """Collect every link and image target in a markdown document.

    Args:
        text: Markdown source; call :func:`strip_code` first.

    Returns:
        ``(is_image, target)`` pairs in document order.
    """
    links = [(m.group(1) == "!", m.group(3)) for m in _LINK_RE.finditer(text)]
    links.extend((False, m.group(2)) for m in _REF_RE.finditer(text))
    return links


def slugify(heading: str) -> str:
    """Turn a heading into the anchor GitHub would give it.

    Emoji and punctuation are dropped, spaces become hyphens and the result is
    lower-cased. ``## 🎯 Overview`` therefore becomes ``-overview``, which is
    exactly the fragment GitHub serves.

    Args:
        heading: Heading text without the leading ``#`` markers.

    Returns:
        The anchor slug.
    """
    slug = _ANCHOR_DROP_RE.sub("", heading.strip().lower())
    return slug.replace(" ", "-")


def heading_anchors(text: str) -> set[str]:
    """Collect the anchors a markdown document defines.

    Args:
        text: Markdown source.

    Returns:
        Every slug produced by an ATX heading, plus the HTML ``name``/``id``
        attributes a page may add by hand.
    """
    anchors = {slugify(m.group(2)) for m in _HEADING_RE.finditer(strip_code(text))}
    anchors.update(re.findall(r"""<a\s+(?:name|id)=["']([^"']+)["']""", text))
    return anchors


def _is_external(target: str) -> bool:
    """Whether a link target leaves the repository.

    Args:
        target: Link target as written.

    Returns:
        ``True`` for absolute URLs, ``mailto:`` links and bare anchors.
    """
    return target.startswith(("http://", "https://", "mailto:", "#"))


def _section_headings(text: str) -> List[str]:
    """Return every heading's text, lower-cased.

    Args:
        text: Markdown source.

    Returns:
        Heading texts without their ``#`` markers.
    """
    return [m.group(2).strip().lower() for m in _HEADING_RE.finditer(strip_code(text))]


def audit_document(root: Path, document: Path) -> List[Finding]:
    """Audit one markdown document's links and embedded assets.

    Args:
        root: Repository root that relative targets resolve against.
        document: Markdown file to audit.

    Returns:
        Findings for broken links, bad anchors and unusable assets. A missing
        document yields a single ``readme.link`` finding.
    """
    findings: List[Finding] = []
    try:
        location = document.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # a document outside the repository, as in the tests
        location = document.name
    if not document.is_file():
        return [Finding("readme.link", location, "document not found")]

    text = document.read_text(encoding="utf-8")
    prose = strip_code(text)
    for is_image, target in iter_links(prose):
        if _is_external(target):
            continue
        path_part, _, fragment = target.partition("#")
        if not path_part:
            continue
        resolved = (document.parent / path_part).resolve()
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError:
            findings.append(Finding("readme.link", location, f"{target} leaves the repository"))
            continue

        if not resolved.exists():
            findings.append(Finding("readme.link", location, f"{target} does not exist"))
            continue

        if is_image or resolved.suffix.lower() in ASSET_SUFFIXES:
            report = inspect_asset(resolved)
            if not report.ok:
                code = "readme.asset"
                findings.append(
                    Finding(code, relative.as_posix(), report.error or "unusable asset")
                )
            if report.size > ASSET_BUDGET_BYTES:
                findings.append(
                    Finding("readme.asset", relative.as_posix(), _budget_error(report.size))
                )
            continue

        if fragment and resolved.suffix == ".md":
            anchors = heading_anchors(resolved.read_text(encoding="utf-8"))
            if fragment not in anchors:
                findings.append(
                    Finding("readme.link", location, f"{target}: no heading produces #{fragment}")
                )
    return findings


def audit_readme(root: Path = ROOT, readme: Optional[Path] = None) -> List[Finding]:
    """Run every README check.

    Args:
        root: Repository root.
        readme: README to audit; defaults to ``<root>/README.md``.

    Returns:
        Findings in report order. An empty list means the README is fit to ship.
    """
    readme = readme or (root / "README.md")
    try:
        location = readme.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # a README outside the repository, as in the tests
        location = readme.name
    text = readme.read_text(encoding="utf-8")
    prose = strip_code(text)
    findings: List[Finding] = []

    headings = _section_headings(text)
    for name, accepted in REQUIRED_SECTIONS:
        if not any(any(word in heading for word in accepted) for heading in headings):
            findings.append(
                Finding(
                    "readme.section", location, f"no {name} section (looked for {accepted[0]!r})"
                )
            )

    for command in QUICKSTART_COMMANDS:
        if command not in text:
            findings.append(Finding("readme.quickstart", location, f"quickstart lost {command!r}"))

    findings.extend(audit_document(root, readme))

    referenced = {target for _, target in iter_links(prose)}
    referenced.update(target for _, target in iter_links(_read_text(root / "docs" / "index.md")))
    # Every page, not only the top level of ``docs/``: the live-demo pages live
    # one directory down and are the only documents that reference the captured
    # session JSON, so a shallow glob reported five healthy assets as orphans.
    referenced.update(
        target
        for page in sorted((root / "docs").rglob("*.md"))
        for _, target in iter_links(strip_code(_read_text(page)))
    )

    for required, code in ((DIAGRAM, "readme.diagram"), (DEMO_GIF, "readme.demo")):
        name = required.name
        if not any(name in target for target in referenced):
            findings.append(
                Finding(code, required.as_posix(), f"{name} is not referenced anywhere")
            )

    for referrer in DIAGRAM_REFERRERS:
        page = root / referrer
        if DIAGRAM.name not in _read_text(page):
            findings.append(
                Finding(
                    "readme.diagram",
                    referrer.as_posix(),
                    f"does not embed {DIAGRAM.name}",
                )
            )

    findings.extend(_orphan_assets(root, referenced))
    findings.extend(_prose_findings(location, prose))
    return findings


def _read_text(path: Path) -> str:
    """Read a file, returning an empty string when it is absent.

    Args:
        path: File to read.

    Returns:
        The file's text, or ``""``.
    """
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _orphan_assets(root: Path, referenced: Iterable[str]) -> List[Finding]:
    """Find assets nothing links to.

    Args:
        root: Repository root.
        referenced: Every link target collected from the README and ``docs/``.

    Returns:
        One ``readme.orphan`` warning per unreferenced asset.
    """
    assets_dir = root / ASSETS_DIR
    if not assets_dir.is_dir():
        return []
    findings: List[Finding] = []
    for asset in sorted(assets_dir.rglob("*")):
        if not asset.is_file():
            continue
        if not any(asset.name in target for target in referenced):
            findings.append(
                Finding(
                    "readme.orphan",
                    asset.relative_to(root).as_posix(),
                    "no document references this asset",
                    severity="warning",
                )
            )
    return findings


def _prose_findings(location: str, prose: str) -> List[Finding]:
    """Look for placeholders and token-shaped strings.

    Args:
        location: File name used in the findings.
        prose: Document text with code removed.

    Returns:
        One finding per placeholder phrase and per secret-shaped match.
    """
    findings: List[Finding] = []
    lowered = prose.lower()
    for phrase in PLACEHOLDERS:
        if phrase.lower() in lowered:
            findings.append(
                Finding("readme.placeholder", location, f"{phrase!r} left in the README")
            )
    for pattern in SECRET_PATTERNS:
        match = pattern.search(prose)
        if match:
            findings.append(
                Finding("readme.secret", location, f"token-shaped string {match.group(0)[:8]}…")
            )
    return findings


def render_table(findings: Sequence[Finding]) -> str:
    """Render findings as a fixed-width table.

    Args:
        findings: Findings to render.

    Returns:
        The table, or a one-line pass message.
    """
    if not findings:
        return "readme-audit: README is submission-ready."
    lines = [f"{'severity':<8} {'code':<20} {'location':<34} detail"]
    lines.append("-" * 108)
    for finding in findings:
        lines.append(
            f"{finding.severity:<8} {finding.code:<20} {finding.location:<34} {finding.detail}"
        )
    errors = sum(1 for f in findings if f.is_error)
    lines.append("")
    lines.append(f"{errors} error(s), {len(findings) - errors} warning(s).")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser exposing ``--readme``, ``--json`` and ``--strict``.
    """
    parser = argparse.ArgumentParser(
        prog="readme-audit",
        description="Check the README's links, sections, diagram and demo assets.",
    )
    parser.add_argument("--readme", default=str(DEFAULT_README), help="README to audit")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    parser.add_argument("--version", action="version", version="readme-audit 1.0")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit.

    Args:
        argv: Argument list; ``None`` means :data:`sys.argv`.

    Returns:
        ``0`` when the README is clean, ``1`` when it has findings, ``2`` when
        it cannot be measured at all.
    """
    args = build_parser().parse_args(argv)
    readme = Path(args.readme)
    if not readme.is_file():
        print(f"readme-audit: {readme} not found — nothing to measure", file=sys.stderr)
        return 2

    findings = audit_readme(readme.resolve().parent, readme)
    blocking = [f for f in findings if f.is_error or args.strict]

    if args.json:
        print(
            json.dumps(
                {
                    "readme": str(readme),
                    "findings": [asdict(f) for f in findings],
                    "errors": sum(1 for f in findings if f.is_error),
                    "warnings": sum(1 for f in findings if not f.is_error),
                },
                indent=2,
            )
        )
    else:
        print(render_table(findings))

    return 1 if blocking else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
