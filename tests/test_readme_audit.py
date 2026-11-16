"""Tests for the Day 27 README audit (``tools/readme_audit.py``).

The audit exists because the README is the one artefact with no test behind it,
so its own tests have to prove two things separately: that every finding *fires*
when the defect is present (a gate that never fires is indistinguishable from a
gate that is not there) and that the committed README is currently clean.

The GIF fixtures are base64 constants rather than generated files: the audit is
standard-library only, and a test for a standard-library tool should not need an
imaging library to build its input.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import readme_audit  # noqa: E402  (path set up above)

#: A 4x4 GIF with two frames, and one with a single frame. Both were produced
#: once with Pillow and pasted here as bytes; nothing regenerates them.
ANIMATED_GIF = base64.b64decode(
    "R0lGODlhBAAEAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAABAAEAAAICQAB"
    "CBxIsCCAgAAh+QQBCgABACwAAAAABAAEAIEA/wAAAAAAAAAAAAAICQABCBxIsCCAgAA7"
)
STILL_GIF = base64.b64decode(
    "R0lGODlhBAAEAIEAAAAA/wAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACgAAACwAAAAABAAEAAAICQAB"
    "CBxIsCCAgAA7"
)

SVG_OK = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    '<rect width="10" height="10"/></svg>'
)

SECTIONS = """## 🎯 Overview
## 🏗️ Architecture
## 🚀 Quickstart
## 📚 Documentation
## 🧩 Projects
## 📄 License
"""


def _write(path: Path, text: str) -> Path:
    """Write text to ``path``, creating parents.

    Args:
        path: Destination file.
        text: Content.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _repo(tmp_path: Path, readme_extra: str = "", docs_index: str = "") -> Path:
    """Build a minimal repository the audit can measure.

    Args:
        tmp_path: pytest's temporary directory.
        readme_extra: Markdown appended to the generated README.
        docs_index: Markdown for ``docs/index.md``; defaults to embedding the
            diagram and the demo.

    Returns:
        The repository root.
    """
    (tmp_path / "docs" / "assets" / "demo").mkdir(parents=True)
    (tmp_path / "docs" / "assets" / "architecture.svg").write_text(SVG_OK, encoding="utf-8")
    (tmp_path / "docs" / "assets" / "demo" / "quickstart.gif").write_bytes(ANIMATED_GIF)
    readme = (
        "# Portfolio\n\n"
        "![arch](docs/assets/architecture.svg)\n\n"
        "![demo](docs/assets/demo/quickstart.gif)\n\n"
        f"{SECTIONS}\n"
        "```bash\ngit clone x\npip install -e .\npytest -q\n```\n"
        f"{readme_extra}"
    )
    _write(tmp_path / "README.md", readme)
    default_index = "![arch](assets/architecture.svg)\n"
    _write(tmp_path / "docs" / "index.md", docs_index or default_index)
    return tmp_path


def _codes(findings) -> list:
    """Return the finding codes, for concise assertions.

    Args:
        findings: Findings from the audit.

    Returns:
        The codes in order.
    """
    return [finding.code for finding in findings]


# --- gif parsing -------------------------------------------------------
def test_animated_gif_frame_count_is_read_from_the_blocks():
    assert readme_audit.gif_geometry(ANIMATED_GIF) == (4, 4, 2)


def test_still_gif_has_one_frame():
    assert readme_audit.gif_geometry(STILL_GIF) == (4, 4, 1)


@pytest.mark.parametrize("data", [b"", b"not a gif at all", b"GIF89a"])
def test_non_gif_bytes_are_not_mistaken_for_a_gif(data: bytes):
    assert readme_audit.gif_geometry(data) == (0, 0, 0)


# --- asset inspection --------------------------------------------------
def test_animated_gif_is_accepted(tmp_path: Path):
    path = tmp_path / "demo.gif"
    path.write_bytes(ANIMATED_GIF)
    report = readme_audit.inspect_asset(path)
    assert report.ok
    assert (report.width, report.height, report.frames) == (4, 4, 2)
    assert "2 frames" in report.summary()


def test_still_gif_is_rejected(tmp_path: Path):
    path = tmp_path / "demo.gif"
    path.write_bytes(STILL_GIF)
    report = readme_audit.inspect_asset(path)
    assert not report.ok
    assert report.error == "not animated (1 frame)"


def test_missing_asset_is_reported_not_raised(tmp_path: Path):
    report = readme_audit.inspect_asset(tmp_path / "nope.gif")
    assert not report.exists
    assert report.error == "file not found"
    assert report.summary() == "missing"


def test_empty_asset_is_rejected(tmp_path: Path):
    path = tmp_path / "empty.svg"
    path.write_text("", encoding="utf-8")
    assert readme_audit.inspect_asset(path).error == "empty file"


def test_malformed_svg_is_rejected(tmp_path: Path):
    path = tmp_path / "broken.svg"
    path.write_text("<svg><rect></svg>", encoding="utf-8")
    assert "not valid XML" in readme_audit.inspect_asset(path).error


def test_svg_with_the_wrong_root_element_is_rejected(tmp_path: Path):
    path = tmp_path / "wrong.svg"
    path.write_text("<html></html>", encoding="utf-8")
    assert "root element" in readme_audit.inspect_asset(path).error


def test_valid_svg_is_accepted(tmp_path: Path):
    path = tmp_path / "arch.svg"
    path.write_text(SVG_OK, encoding="utf-8")
    report = readme_audit.inspect_asset(path)
    assert report.ok and report.kind == "svg"


def test_oversized_asset_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(readme_audit, "ASSET_BUDGET_BYTES", 8)
    path = tmp_path / "big.gif"
    path.write_bytes(ANIMATED_GIF)
    report = readme_audit.inspect_asset(path)
    assert not report.ok
    assert "budget" in report.error


def test_unknown_suffix_is_rejected(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    assert "unsupported suffix" in readme_audit.inspect_asset(path).error


# --- markdown parsing --------------------------------------------------
def test_fenced_and_inline_code_are_not_scanned_for_links():
    text = "```\n[not a link](missing.md)\n```\nand `[also](missing.md)` here.\n"
    assert readme_audit.iter_links(readme_audit.strip_code(text)) == []


def test_links_and_images_are_both_collected():
    text = "![alt](a.svg) and [text](b.md)\n\n[ref]: c.md\n"
    assert readme_audit.iter_links(text) == [(True, "a.svg"), (False, "b.md"), (False, "c.md")]


def test_reference_definitions_must_start_a_line():
    text = "text [ref]: c.md\n"
    assert readme_audit.iter_links(text) == []


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        ("🎯 Overview", "-overview"),
        ("Install the commands", "install-the-commands"),
        ("Day 27 — polish!", "day-27--polish"),
        ("API (v2)", "api-v2"),
    ],
)
def test_slugify_matches_github(heading: str, anchor: str):
    assert readme_audit.slugify(heading) == anchor


def test_heading_anchors_include_hand_written_ids():
    text = '## Real Heading\n<a name="manual"></a>\n'
    assert readme_audit.heading_anchors(text) == {"real-heading", "manual"}


def test_external_and_bare_anchor_links_are_exempt():
    assert readme_audit._is_external("https://example.com")
    assert readme_audit._is_external("mailto:a@b.c")
    assert readme_audit._is_external("#local")
    assert not readme_audit._is_external("docs/polish.md")


# --- document audit ----------------------------------------------------
def test_broken_relative_link_is_reported(tmp_path: Path):
    _write(tmp_path / "README.md", "see [missing](docs/nope.md)\n")
    findings = readme_audit.audit_document(tmp_path, tmp_path / "README.md")
    assert _codes(findings) == ["readme.link"]
    assert "does not exist" in findings[0].detail


def test_link_leaving_the_repository_is_reported(tmp_path: Path):
    _write(tmp_path / "README.md", "see [out](../../etc/passwd)\n")
    findings = readme_audit.audit_document(tmp_path, tmp_path / "README.md")
    assert "leaves the repository" in findings[0].detail


def test_fragment_must_be_produced_by_a_heading(tmp_path: Path):
    _write(tmp_path / "README.md", "see [arch](guide.md#architecture)\n")
    _write(tmp_path / "guide.md", "# Something else\n")
    findings = readme_audit.audit_document(tmp_path, tmp_path / "README.md")
    assert _codes(findings) == ["readme.link"]
    assert "#architecture" in findings[0].detail


def test_fragment_that_exists_is_accepted(tmp_path: Path):
    _write(tmp_path / "README.md", "see [arch](guide.md#-architecture)\n")
    _write(tmp_path / "guide.md", "## 🏗️ Architecture\n")
    assert readme_audit.audit_document(tmp_path, tmp_path / "README.md") == []


def test_missing_document_is_reported(tmp_path: Path):
    findings = readme_audit.audit_document(tmp_path, tmp_path / "README.md")
    assert _codes(findings) == ["readme.link"]


def test_unusable_asset_inside_a_document_is_reported(tmp_path: Path):
    _write(tmp_path / "README.md", "![demo](still.gif)\n")
    (tmp_path / "still.gif").write_bytes(STILL_GIF)
    findings = readme_audit.audit_document(tmp_path, tmp_path / "README.md")
    assert _codes(findings) == ["readme.asset"]


# --- whole-README audit ------------------------------------------------
def test_a_complete_readme_passes(tmp_path: Path):
    assert readme_audit.audit_readme(_repo(tmp_path)) == []


def test_missing_section_is_reported(tmp_path: Path):
    root = _repo(tmp_path)
    readme = root / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("## 🏗️ Architecture", ""))
    assert "readme.section" in _codes(readme_audit.audit_readme(root))


def test_quickstart_that_lost_pytest_is_reported(tmp_path: Path):
    root = _repo(tmp_path)
    readme = root / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("pytest -q", "echo hi"))
    findings = readme_audit.audit_readme(root)
    assert "readme.quickstart" in _codes(findings)


def test_unreferenced_demo_is_reported(tmp_path: Path):
    root = _repo(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            "![demo](docs/assets/demo/quickstart.gif)\n\n", ""
        )
    )
    assert "readme.demo" in _codes(readme_audit.audit_readme(root))


def test_diagram_missing_from_the_docs_home_is_reported(tmp_path: Path):
    root = _repo(tmp_path, docs_index="no diagram here\n")
    findings = readme_audit.audit_readme(root)
    assert "readme.diagram" in _codes(findings)


def test_orphan_asset_is_a_warning_not_an_error(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "docs" / "assets" / "unused.svg").write_text(SVG_OK, encoding="utf-8")
    findings = readme_audit.audit_readme(root)
    assert _codes(findings) == ["readme.orphan"]
    assert not findings[0].is_error


def test_placeholder_is_reported(tmp_path: Path):
    root = _repo(tmp_path, readme_extra="TODO: write the demo section\n")
    assert "readme.placeholder" in _codes(readme_audit.audit_readme(root))


def test_token_shaped_string_is_reported(tmp_path: Path):
    root = _repo(tmp_path, readme_extra="token: ghp_" + "a" * 20 + "\n")
    assert "readme.secret" in _codes(readme_audit.audit_readme(root))


# --- command line ------------------------------------------------------
def test_main_exits_two_when_the_readme_is_absent(tmp_path: Path, capsys):
    code = readme_audit.main(["--readme", str(tmp_path / "README.md")])
    assert code == 2
    assert "nothing to measure" in capsys.readouterr().err


def test_main_reports_json(tmp_path: Path, capsys):
    root = _repo(tmp_path)
    code = readme_audit.main(["--readme", str(root / "README.md")])
    assert code == 0
    assert "submission-ready" in capsys.readouterr().out


def test_main_json_payload_names_every_finding(tmp_path: Path, capsys):
    root = _repo(tmp_path, readme_extra="TODO\n")
    code = readme_audit.main(["--readme", str(root / "README.md"), "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"] == 1
    assert payload["findings"][0]["code"] == "readme.placeholder"


def test_strict_turns_a_warning_into_a_failure(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "docs" / "assets" / "unused.svg").write_text(SVG_OK, encoding="utf-8")
    assert readme_audit.main(["--readme", str(root / "README.md")]) == 0
    assert readme_audit.main(["--readme", str(root / "README.md"), "--strict"]) == 1


# --- the real repository ----------------------------------------------
def test_the_committed_readme_is_submission_ready():
    assert readme_audit.audit_readme(ROOT) == []


def test_the_committed_diagram_and_demo_are_usable():
    for relative in (readme_audit.DIAGRAM, readme_audit.DEMO_GIF):
        report = readme_audit.inspect_asset(ROOT / relative)
        assert report.ok, f"{relative}: {report.error}"
        assert report.size <= readme_audit.ASSET_BUDGET_BYTES


def test_the_committed_demo_is_animated_and_sized_for_a_readme():
    report = readme_audit.inspect_asset(ROOT / readme_audit.DEMO_GIF)
    assert report.frames >= 60, "the demo should show all five commands"
    assert report.width <= 1000, "a README image wider than the page is a thumbnail"
