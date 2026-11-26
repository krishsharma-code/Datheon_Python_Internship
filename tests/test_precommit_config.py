"""Tests for the Day 34 pre-commit audit (``tools/precommit_config.py``).

Three halves, and they prove different things.

* **Every finding code is reachable.** A gate that cannot fail is a comment, so
  each rule is fed a tree that breaks exactly one of them — a floating `rev`, a
  pin below the declared floor, a `--line-length` that contradicts
  `pyproject.toml`, an `exclude` that hides `datheon/` — and the test asserts
  that code and no other.
* **The parsers are honest about malformed input.** Requirement strings carry
  markers and extras, revisions come with and without a `v`, hook arguments come
  in both spellings (`--flag value` and `--flag=value`), and YAML arrives empty,
  as a list, or broken. Everything downstream trusts these readers, so they get
  their own tests.
* **The real tree is clean.** The committed configuration is audited as it
  stands, because "the hooks agree with the repository" is worth exactly as much
  as its weakest run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import precommit_config as pc  # noqa: E402  (path set up above)

# --- fixtures -------------------------------------------------------------
VALID_CONFIG = """\
default_language_version:
  python: python3

fail_fast: false

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
        args: [--markdown-linebreak-ext=md]
      - id: check-yaml

  - repo: https://github.com/psf/black
    rev: 24.10.0
    hooks:
      - id: black
        language_version: python3

  - repo: https://github.com/PyCQA/flake8
    rev: 7.1.1
    hooks:
      - id: flake8
"""

VALID_PYPROJECT = """\
[project]
name = "fixture"
version = "0.0.0"

[project.optional-dependencies]
dev = [
  "tomli>=2.0; python_version < '3.11'",
  "black>=24.1",
  "flake8>=6.1",
  "pre-commit>=3.7",
]

[tool.black]
line-length = 100
target-version = ["py310"]
"""

VALID_SETUP_CFG = """\
[flake8]
max-line-length = 100
extend-ignore = E203, W503
"""

VALID_MKDOCS = """\
site_name: fixture
nav:
  - Home: index.md
  - Continuous integration:
      - Pre-commit hooks: precommit.md
"""


def _doc_text() -> str:
    """Build a page that documents every finding code.

    Returns:
        A markdown body naming each code once.
    """
    return "# Pre-commit\n\n" + "\n".join(f"- `{code}`" for code in pc.FINDING_CODES) + "\n"


def _write(root: Path, relative: str, text: str) -> Path:
    """Write a text file inside a fixture tree, creating its parents.

    Args:
        root: The fixture root.
        relative: The repository-relative path.
        text: The contents.

    Returns:
        The path that was written.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Build a clean fixture tree that passes every check.

    Args:
        tmp_path: Pytest's per-test directory.

    Returns:
        The fixture root.
    """
    _write(tmp_path, pc.CONFIG, VALID_CONFIG)
    _write(tmp_path, pc.PYPROJECT, VALID_PYPROJECT)
    _write(tmp_path, pc.SETUP_CFG, VALID_SETUP_CFG)
    _write(tmp_path, pc.MKDOCS, VALID_MKDOCS)
    _write(tmp_path, pc.DOC, _doc_text())
    _write(tmp_path, "datheon/__init__.py", '"""Fixture package."""\n')
    _write(tmp_path, "index.md", "# Home\n")
    return tmp_path


class FakeGit:
    """A stand-in for :func:`subprocess.run` that answers ``git ls-files``.

    Attributes:
        stdout: What the command prints.
        returncode: What it exits with.
        error: An exception to raise instead of returning, when set.
        calls: Every command it was asked to run.
    """

    def __init__(
        self,
        stdout: str = "",
        returncode: int = 0,
        error: Optional[Exception] = None,
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.calls: List[Sequence[str]] = []

    def __call__(self, command: Sequence[str], **kwargs: Any) -> SimpleNamespace:
        """Record the call and answer it.

        Args:
            command: The argv the caller would have run.
            **kwargs: Ignored; present so the signature matches ``subprocess.run``.

        Returns:
            A completed-process stand-in.

        Raises:
            Exception: The configured ``error``, when one was set.
        """
        self.calls.append(list(command))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr="")


def _codes(findings: Sequence[pc.Finding]) -> List[str]:
    """Return the codes of a finding list, sorted.

    Args:
        findings: The findings.

    Returns:
        Their codes, sorted.
    """
    return sorted(finding.code for finding in findings)


def _config_with(replacement: str) -> str:
    """Rewrite the fixture configuration's first ``repos:`` block body.

    Args:
        replacement: The new text to place after ``repos:``.

    Returns:
        A configuration document built from the replacement.
    """
    head = VALID_CONFIG.split("repos:", 1)[0]
    return head + "repos:\n" + replacement


# --- versions -------------------------------------------------------------
def test_parse_version_reads_a_plain_number():
    assert pc.parse_version("7.1.1") == (7, 1, 1)


def test_parse_version_strips_a_leading_v():
    assert pc.parse_version("v5.0.0") == (5, 0, 0)


def test_parse_version_reads_a_two_part_pin():
    assert pc.parse_version("24.10") == (24, 10)


def test_parse_version_rejects_a_branch_name():
    assert pc.parse_version("main") is None


def test_parse_version_rejects_an_empty_string():
    assert pc.parse_version("  ") is None


def test_version_at_least_accepts_an_equal_pin():
    assert pc.version_at_least((24, 1, 0), (24, 1)) is True


def test_version_at_least_accepts_a_newer_pin():
    assert pc.version_at_least((24, 10, 0), (24, 1)) is True


def test_version_at_least_rejects_an_older_pin():
    assert pc.version_at_least((23, 12, 1), (24, 1)) is False


def test_version_at_least_pads_a_short_pin():
    """``24`` is ``24.0``, which is below ``24.1``."""
    assert pc.version_at_least((24,), (24, 1)) is False


def test_version_at_least_ignores_trailing_components():
    assert pc.version_at_least((24, 1, 9), (24, 1)) is True


# --- requirement strings --------------------------------------------------
def test_requirement_name_reads_a_simple_floor():
    assert pc.requirement_name("black>=24.1") == "black"


def test_requirement_name_drops_an_environment_marker():
    assert pc.requirement_name("tomli>=2.0; python_version < '3.11'") == "tomli"


def test_requirement_name_drops_extras():
    assert pc.requirement_name("mkdocstrings[python]>=0.26") == "mkdocstrings"


def test_requirement_name_normalises_underscores():
    assert pc.requirement_name("pytest_cov>=4.1") == "pytest-cov"


def test_requirement_floor_reads_a_minimum():
    assert pc.requirement_floor("black>=24.1") == (24, 1)


def test_requirement_floor_reads_an_exact_pin():
    assert pc.requirement_floor("flake8==7.1.1") == (7, 1, 1)


def test_requirement_floor_reads_a_compatible_release():
    assert pc.requirement_floor("mkdocs~=1.6") == (1, 6)


def test_requirement_floor_ignores_a_ceiling():
    assert pc.requirement_floor("mkdocs<2") is None


def test_requirement_floor_ignores_an_unconstrained_requirement():
    assert pc.requirement_floor("pytest") is None


# --- reading --------------------------------------------------------------
def test_load_document_parses_a_mapping():
    document = pc.load_document(VALID_CONFIG)

    assert document is not None
    assert len(document["repos"]) == 3


def test_load_document_rejects_an_empty_file():
    assert pc.load_document("") is None


def test_load_document_rejects_a_top_level_list():
    assert pc.load_document("- a\n- b\n") is None


def test_load_document_rejects_broken_yaml():
    assert pc.load_document("repos: [unclosed\n") is None


def test_read_text_returns_none_for_a_missing_file(tmp_path: Path):
    assert pc.read_text(tmp_path / "nope.yaml") is None


def test_load_yaml_file_reads_the_nav(tmp_path: Path):
    _write(tmp_path, "mkdocs.yml", VALID_MKDOCS)

    document = pc.load_yaml_file(tmp_path / "mkdocs.yml")

    assert document is not None
    assert document["nav"][0] == {"Home": "index.md"}


def test_load_yaml_file_returns_none_for_a_missing_file(tmp_path: Path):
    assert pc.load_yaml_file(tmp_path / "mkdocs.yml") is None


# --- revisions ------------------------------------------------------------
def test_is_floating_rev_flags_main():
    assert pc.is_floating_rev("main") is True


def test_is_floating_rev_flags_head():
    assert pc.is_floating_rev("HEAD") is True


def test_is_floating_rev_flags_latest():
    assert pc.is_floating_rev("latest") is True


def test_is_floating_rev_flags_a_ref():
    assert pc.is_floating_rev("refs/heads/main") is True


def test_is_floating_rev_flags_an_empty_rev():
    assert pc.is_floating_rev("") is True


def test_is_floating_rev_accepts_a_release_tag():
    assert pc.is_floating_rev("v5.0.0") is False


def test_is_floating_rev_accepts_a_bare_version():
    assert pc.is_floating_rev("24.10.0") is False


# --- option parsing -------------------------------------------------------
def test_option_value_reads_a_separate_value():
    assert pc.option_value(["--line-length", "100"], "--line-length") == "100"


def test_option_value_reads_an_equals_form():
    assert pc.option_value(["--line-length=120"], "--line-length") == "120"


def test_option_value_reads_an_alias():
    assert pc.option_value(["-l", "88"], "--line-length", "-l") == "88"


def test_option_value_returns_an_empty_string_for_a_bare_flag():
    assert pc.option_value(["--line-length"], "--line-length") == ""


def test_option_value_returns_none_when_absent():
    assert pc.option_value(["--quiet"], "--line-length") is None


# --- parsing the configuration -------------------------------------------
def test_parse_repos_flattens_every_hook():
    document = pc.load_document(VALID_CONFIG)
    assert document is not None

    repos, findings = pc.parse_repos(document)

    assert findings == []
    assert [repo.rev for repo in repos] == ["v5.0.0", "24.10.0", "7.1.1"]
    assert len(pc.all_hooks(repos)) == 5


def test_parse_repos_keeps_hook_arguments():
    document = pc.load_document(VALID_CONFIG)
    assert document is not None

    repos, _ = pc.parse_repos(document)
    trailing = [hook for hook in pc.all_hooks(repos) if hook.id == "trailing-whitespace"]

    assert trailing[0].args == ("--markdown-linebreak-ext=md",)


def test_parse_repos_reports_a_missing_repos_key():
    repos, findings = pc.parse_repos({})

    assert repos == []
    assert _codes(findings) == ["precommit.file"]


def test_parse_repos_reports_an_entry_that_is_not_a_mapping():
    repos, findings = pc.parse_repos({"repos": ["https://example.invalid/repo"]})

    assert repos == []
    assert _codes(findings) == ["precommit.repo"]


def test_parse_repos_reports_a_missing_url():
    document = {"repos": [{"rev": "v1.0.0", "hooks": [{"id": "black"}]}]}

    repos, findings = pc.parse_repos(document)

    assert _codes(findings) == ["precommit.repo"]
    assert repos[0].url == ""


def test_parse_repos_reports_a_repo_without_hooks():
    document = {"repos": [{"repo": "https://example.invalid/r", "rev": "v1.0.0"}]}

    _, findings = pc.parse_repos(document)

    assert _codes(findings) == ["precommit.repo"]


def test_parse_repos_reports_a_hook_without_an_id():
    document = {
        "repos": [
            {"repo": "https://example.invalid/r", "rev": "v1.0.0", "hooks": [{"args": ["x"]}]}
        ]
    }

    repos, findings = pc.parse_repos(document)

    assert _codes(findings) == ["precommit.repo"]
    assert repos[0].hooks == ()


def test_parse_repos_coerces_non_string_arguments():
    document = {
        "repos": [
            {
                "repo": "https://example.invalid/r",
                "rev": "v1.0.0",
                "hooks": [{"id": "black", "args": [100]}],
            }
        ]
    }

    repos, _ = pc.parse_repos(document)

    assert repos[0].hooks[0].args == ("100",)


# --- the audit: files -----------------------------------------------------
def test_audit_config_reports_a_missing_configuration(tmp_path: Path):
    report = pc.audit_config(tmp_path)

    assert report.unmeasurable is True
    assert _codes(report.findings) == ["precommit.file"]
    assert pc.CONFIG in report.reason


def test_audit_config_reports_a_configuration_that_is_not_a_mapping(tree: Path):
    _write(tree, pc.CONFIG, "- just\n- a list\n")

    report = pc.audit_config(tree)

    assert report.unmeasurable is True
    assert _codes(report.findings) == ["precommit.file"]


def test_audit_config_counts_repos_and_hooks(tree: Path):
    report = pc.audit_config(tree)

    assert report.passed is True
    assert report.repos == 3
    assert report.hooks == 5


# --- the audit: pins ------------------------------------------------------
def test_audit_config_reports_a_missing_rev(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        _config_with(
            "  - repo: https://github.com/psf/black\n"
            "    hooks:\n      - id: black\n      - id: flake8\n"
            "      - id: end-of-file-fixer\n        args: [--markdown-linebreak-ext=md]\n"
        ),
    )

    codes = _codes(pc.audit_config(tree).findings)

    assert codes == ["precommit.rev"]


def test_audit_config_reports_a_floating_rev(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG.replace("rev: 24.10.0", "rev: main"))

    codes = _codes(pc.audit_config(tree).findings)

    assert codes == ["precommit.rev"]


# --- the audit: required hooks -------------------------------------------
def test_audit_config_reports_a_missing_black_hook(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace("      - id: black\n        language_version: python3\n", ""),
    )

    codes = _codes(pc.audit_config(tree).findings)

    # The repo entry is left with no hooks, so it is also malformed.
    assert codes == ["precommit.hook", "precommit.repo"]


def test_audit_config_reports_a_missing_flake8_hook(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG.replace("      - id: flake8\n", ""))

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.hook", "precommit.repo"]
    assert "flake8" in report.findings[0].message


def test_audit_config_reports_a_missing_end_of_file_hook(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG.replace("      - id: end-of-file-fixer\n", ""))

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.hook"]
    assert "end-of-file-fixer" in report.findings[0].message


def test_audit_config_reports_a_duplicate_hook(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace("      - id: flake8\n", "      - id: flake8\n      - id: flake8\n"),
    )

    codes = _codes(pc.audit_config(tree).findings)

    assert codes == ["precommit.duplicate"]


# --- the audit: version floors -------------------------------------------
def test_audit_config_reports_a_pin_below_the_floor(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG.replace("rev: 24.10.0", "rev: 23.12.1"))

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.version"]
    assert "black>=24.1" in report.findings[0].message


def test_audit_config_accepts_a_pin_above_the_floor(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG.replace("rev: 24.10.0", "rev: 25.1.0"))

    assert pc.audit_config(tree).passed is True


def test_audit_config_reports_a_distribution_with_no_floor(tree: Path):
    _write(tree, pc.PYPROJECT, VALID_PYPROJECT.replace('"flake8>=6.1",\n', ""))

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.version"]
    assert "flake8" in report.findings[0].message


def test_audit_config_reads_the_floor_out_of_pyproject(tree: Path):
    floors = pc.project_floors(tree)

    assert floors["black"] == (24, 1)
    assert floors["flake8"] == (6, 1)
    assert "tomli" in floors


# --- the audit: arguments ------------------------------------------------
def test_audit_config_reports_a_contradicting_black_line_length(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "      - id: black\n        language_version: python3\n",
            "      - id: black\n        args: [--line-length, '88']\n",
        ),
    )

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.args"]
    assert "88" in report.findings[0].message


def test_audit_config_accepts_a_matching_black_line_length(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "      - id: black\n        language_version: python3\n",
            "      - id: black\n        args: ['--line-length=100']\n",
        ),
    )

    assert pc.audit_config(tree).passed is True


def test_audit_config_reports_a_contradicting_flake8_line_length(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "      - id: flake8\n",
            "      - id: flake8\n        args: ['--max-line-length=120']\n",
        ),
    )

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.args"]
    assert "120" in report.findings[0].message


def test_audit_config_reports_a_non_numeric_line_length(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "      - id: flake8\n",
            "      - id: flake8\n        args: ['--max-line-length=wide']\n",
        ),
    )

    assert _codes(pc.audit_config(tree).findings) == ["precommit.args"]


def test_audit_config_reports_a_missing_markdown_linebreak_argument(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace("        args: [--markdown-linebreak-ext=md]\n", ""),
    )

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.args"]
    assert "markdown" in report.findings[0].message


def test_audit_config_reports_a_markdown_extension_that_misses_md(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "args: [--markdown-linebreak-ext=md]",
            "args: ['--markdown-linebreak-ext=txt']",
        ),
    )

    assert _codes(pc.audit_config(tree).findings) == ["precommit.args"]


def test_audit_config_skips_the_markdown_rule_without_markdown(tmp_path: Path):
    _write(
        tmp_path,
        pc.CONFIG,
        VALID_CONFIG.replace("        args: [--markdown-linebreak-ext=md]\n", ""),
    )
    _write(tmp_path, pc.PYPROJECT, VALID_PYPROJECT)
    _write(tmp_path, pc.SETUP_CFG, VALID_SETUP_CFG)

    report = pc.audit_config(tmp_path)

    assert report.passed is True


def test_ships_markdown_sees_a_docs_directory(tmp_path: Path):
    _write(tmp_path, "docs/precommit.md", "# x\n")

    assert pc.ships_markdown(tmp_path) is True


def test_ships_markdown_is_false_without_markdown(tmp_path: Path):
    assert pc.ships_markdown(tmp_path) is False


# --- the audit: scope ----------------------------------------------------
def test_audit_config_reports_a_top_level_exclude_that_hides_code(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\nexclude: '^datheon/'\n")

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.scope"]
    assert "datheon/__init__.py" in report.findings[0].message


def test_audit_config_reports_a_top_level_files_pattern_that_matches_nothing(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\nfiles: '\\.rs$'\n")

    assert _codes(pc.audit_config(tree).findings) == ["precommit.scope"]


def test_audit_config_reports_an_invalid_top_level_files_pattern(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\nfiles: '([unclosed'\n")

    assert _codes(pc.audit_config(tree).findings) == ["precommit.scope"]


def test_audit_config_accepts_a_top_level_files_pattern_that_covers_code(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\nfiles: '\\.(py|md|ya?ml)$'\n")

    assert pc.audit_config(tree).passed is True


def test_audit_config_reports_a_hook_exclude_that_hides_code(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "      - id: flake8\n",
            "      - id: flake8\n        exclude: '^tools/'\n",
        ),
    )

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.scope"]
    assert "tools/precommit_config.py" in report.findings[0].message


def test_audit_config_reports_a_hook_files_pattern_that_misses_the_code(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "      - id: black\n        language_version: python3\n",
            "      - id: black\n        files: '\\.rs$'\n",
        ),
    )

    assert _codes(pc.audit_config(tree).findings) == ["precommit.scope"]


def test_audit_config_reports_an_invalid_hook_files_pattern(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "      - id: black\n        language_version: python3\n",
            "      - id: black\n        files: '([unclosed'\n",
        ),
    )

    assert _codes(pc.audit_config(tree).findings) == ["precommit.scope"]


def test_pattern_hits_ignores_an_empty_pattern():
    assert pc._pattern_hits("", pc.SHIPPED_CODE) is None


def test_pattern_hits_ignores_an_invalid_pattern():
    assert pc._pattern_hits("([unclosed", pc.SHIPPED_CODE) is None


# --- the audit: stages ---------------------------------------------------
def test_audit_config_reports_an_unknown_default_stage(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\ndefault_stages: [precommit]\n")

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.stage"]
    assert "precommit" in report.findings[0].message


def test_audit_config_accepts_a_known_default_stage(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\ndefault_stages: [commit, manual]\n")

    assert pc.audit_config(tree).passed is True


def test_audit_config_reports_an_unknown_hook_stage(tree: Path):
    _write(
        tree,
        pc.CONFIG,
        VALID_CONFIG.replace(
            "      - id: flake8\n",
            "      - id: flake8\n        stages: [pre-push]\n",
        ),
    )

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.stage"]
    assert "pre-push" in report.findings[0].message


# --- the audit: secrets --------------------------------------------------
def test_audit_config_reports_a_github_token(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\n# ghp_" + "a" * 36 + "\n")

    report = pc.audit_config(tree)

    assert _codes(report.findings) == ["precommit.secret"]
    assert "credential" in report.findings[0].message


def test_audit_config_reports_an_aws_key(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\n# AKIA" + "B" * 16 + "\n")

    assert _codes(pc.audit_config(tree).findings) == ["precommit.secret"]


def test_audit_config_accepts_a_comment_that_is_not_a_token(tree: Path):
    _write(tree, pc.CONFIG, VALID_CONFIG + "\n# pinned by the Day 34 run\n")

    assert pc.audit_config(tree).passed is True


# --- documentation -------------------------------------------------------
def test_audit_docs_accepts_a_complete_page(tree: Path):
    assert pc.audit_docs(tree) == []


def test_audit_docs_reports_a_missing_page(tree: Path):
    (tree / pc.DOC).unlink()

    findings = pc.audit_docs(tree)

    assert _codes(findings) == ["precommit.docs"]
    assert "missing" in findings[0].message


def test_audit_docs_reports_a_page_missing_from_the_nav(tree: Path):
    _write(tree, pc.MKDOCS, "site_name: fixture\nnav:\n  - Home: index.md\n")

    findings = pc.audit_docs(tree)

    assert _codes(findings) == ["precommit.docs"]
    assert "nav" in findings[0].message


def test_audit_docs_reports_a_missing_site_configuration(tree: Path):
    (tree / pc.MKDOCS).unlink()

    findings = pc.audit_docs(tree)

    assert _codes(findings) == ["precommit.docs"]


def test_audit_docs_reports_an_undocumented_code(tree: Path):
    body = _doc_text().replace("precommit.whitespace", "precommit.hygiene")
    _write(tree, pc.DOC, body)

    findings = pc.audit_docs(tree)

    assert _codes(findings) == ["precommit.docs"]
    assert "precommit.whitespace" in findings[0].message


def test_nav_pages_flattens_a_nested_tree():
    nav = [
        {"Home": "index.md"},
        {"Group": [{"Child": "child.md"}, "sibling.md"]},
        "plain.md",
    ]

    assert pc.nav_pages(nav) == ["index.md", "child.md", "sibling.md", "plain.md"]


def test_nav_pages_ignores_a_missing_nav():
    assert pc.nav_pages(None) == []


# --- file hygiene --------------------------------------------------------
def test_audit_whitespace_reports_a_missing_final_newline(tree: Path):
    _write(tree, "notes.txt", "no newline at the end")

    findings = pc.audit_whitespace(tree, ["notes.txt"])

    assert _codes(findings) == ["precommit.whitespace"]
    assert "no final newline" in findings[0].message


def test_audit_whitespace_reports_trailing_whitespace(tree: Path):
    _write(tree, "module.py", '"""Doc."""\nx = 1   \n')

    findings = pc.audit_whitespace(tree, ["module.py"])

    assert "trailing whitespace at line 2" in findings[0].message


def test_audit_whitespace_reports_crlf_endings(tree: Path):
    (tree / "windows.txt").write_bytes(b"one\r\ntwo\r\n")

    findings = pc.audit_whitespace(tree, ["windows.txt"])

    assert "CRLF line endings" in findings[0].message


def test_audit_whitespace_allows_a_markdown_hard_break(tree: Path):
    _write(tree, "page.md", "a line with a break  \nand the next one\n")

    assert pc.audit_whitespace(tree, ["page.md"]) == []


def test_audit_whitespace_reports_three_spaces_in_markdown(tree: Path):
    _write(tree, "page.md", "a line with noise   \nand the next one\n")

    assert _codes(pc.audit_whitespace(tree, ["page.md"])) == ["precommit.whitespace"]


def test_audit_whitespace_accepts_an_empty_file(tree: Path):
    _write(tree, "empty.py", "")

    assert pc.audit_whitespace(tree, ["empty.py"]) == []


def test_audit_whitespace_skips_a_binary_suffix(tree: Path):
    (tree / "logo.png").write_bytes(b"\x89PNG\r\n")

    assert pc.audit_whitespace(tree, ["logo.png"]) == []


def test_audit_whitespace_skips_a_file_with_a_null_byte(tree: Path):
    (tree / "blob.bin").write_bytes(b"text\x00more")

    assert pc.audit_whitespace(tree, ["blob.bin"]) == []


def test_audit_whitespace_reports_a_file_that_is_not_utf8(tree: Path):
    (tree / "latin.txt").write_bytes(b"caf\xe9\n")

    findings = pc.audit_whitespace(tree, ["latin.txt"])

    assert "not valid UTF-8" in findings[0].message


def test_audit_whitespace_ignores_a_file_that_vanished(tree: Path):
    assert pc.audit_whitespace(tree, ["never-existed.txt"]) == []


def test_audit_whitespace_names_at_most_three_lines(tree: Path):
    body = "".join(f"line {number}   \n" for number in range(1, 7))
    _write(tree, "noisy.py", body)

    findings = pc.audit_whitespace(tree, ["noisy.py"])

    assert "(+3 more)" in findings[0].message


def test_audit_whitespace_walks_the_tree_without_git(tree: Path):
    _write(tree, "clean.py", "x = 1\n")
    _write(tree, "dirty.py", "x = 1")

    findings = pc.audit_whitespace(tree, runner=FakeGit(returncode=128))

    assert [finding.message.split(":")[0] for finding in findings] == ["dirty.py"]


def test_walk_files_skips_generated_directories(tree: Path):
    _write(tree, "site/page.html", "<html>")
    _write(tree, "keep.py", "x = 1\n")

    names = [path.name for path in pc.walk_files(tree)]

    assert "keep.py" in names
    assert "page.html" not in names


# --- git integration -----------------------------------------------------
def test_tracked_files_reads_the_nul_separated_list(tree: Path):
    runner = FakeGit(stdout="a.py\0docs/b.md\0")

    assert pc.tracked_files(tree, runner) == ["a.py", "docs/b.md"]


def test_tracked_files_returns_none_when_git_fails(tree: Path):
    assert pc.tracked_files(tree, FakeGit(returncode=128)) is None


def test_tracked_files_returns_none_without_a_git_binary(tree: Path):
    assert pc.tracked_files(tree, FakeGit(error=FileNotFoundError("git"))) is None


def test_audit_whitespace_uses_the_git_index(tree: Path):
    _write(tree, "tracked.py", "x = 1\n")
    _write(tree, "untracked.py", "x = 1")
    runner = FakeGit(stdout="tracked.py\0")

    assert pc.audit_whitespace(tree, runner=runner) == []


# --- the whole audit -----------------------------------------------------
def test_audit_tree_passes_a_clean_tree(tree: Path):
    report = pc.audit_tree(tree, FakeGit(stdout="index.md\0"))

    assert report.passed is True
    assert report.checked == 1


def test_audit_tree_counts_the_files_it_read(tree: Path):
    _write(tree, "dirty.py", "x = 1")

    report = pc.audit_tree(tree, FakeGit(stdout="dirty.py\0index.md\0"))

    assert _codes(report.findings) == ["precommit.whitespace"]
    assert report.checked == 2


def test_audit_tree_reports_documentation_and_configuration_together(tree: Path):
    (tree / pc.DOC).unlink()
    _write(tree, pc.CONFIG, VALID_CONFIG.replace("rev: 24.10.0", "rev: main"))

    report = pc.audit_tree(tree, FakeGit(stdout="index.md\0"))

    assert _codes(report.findings) == ["precommit.docs", "precommit.rev"]


def test_audit_tree_skips_the_hygiene_walk_when_the_config_is_missing(tmp_path: Path):
    runner = FakeGit(stdout="x.py\0")

    report = pc.audit_tree(tmp_path, runner)

    assert report.unmeasurable is True
    assert runner.calls == []


# --- output --------------------------------------------------------------
def test_report_to_dict_is_one_document(tree: Path):
    report = pc.audit_tree(tree, FakeGit(stdout="index.md\0"))

    payload = pc.report_to_dict(report, tree)

    assert payload["tool"] == "precommit_config"
    assert payload["ok"] is True
    assert payload["repos"] == 3
    assert payload["hooks"] == 5
    assert payload["findings"] == []


def test_report_to_dict_names_every_finding(tree: Path):
    (tree / pc.DOC).unlink()
    report = pc.audit_tree(tree, FakeGit(stdout=""))

    payload = pc.report_to_dict(report, tree)

    assert payload["ok"] is False
    assert payload["findings"][0]["code"] == "precommit.docs"


def test_render_report_ends_in_a_pass_line(tree: Path):
    report = pc.audit_tree(tree, FakeGit(stdout=""))

    text = pc.render_report(report, tree)

    assert "PASS" in text
    assert text.endswith("\n")


def test_render_report_ends_in_a_fail_line(tree: Path):
    (tree / pc.DOC).unlink()

    text = pc.render_report(pc.audit_tree(tree, FakeGit(stdout="")), tree)

    assert "FAIL — 1 finding(s)" in text


def test_render_report_names_an_unmeasurable_tree(tmp_path: Path):
    text = pc.render_report(pc.audit_config(tmp_path), tmp_path)

    assert "UNMEASURABLE" in text


# --- the command line ----------------------------------------------------
def test_main_exits_zero_on_a_clean_tree(tree: Path, capsys: pytest.CaptureFixture[str]):
    code = pc.main(["--root", str(tree)])

    assert code == pc.EXIT_OK
    assert "PASS" in capsys.readouterr().out


def test_main_exits_one_on_a_finding(tree: Path, capsys: pytest.CaptureFixture[str]):
    (tree / pc.DOC).unlink()

    code = pc.main(["--root", str(tree)])

    assert code == pc.EXIT_FINDINGS
    assert "precommit.docs" in capsys.readouterr().err


def test_main_exits_two_when_unmeasurable(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    code = pc.main(["--root", str(tmp_path)])

    assert code == pc.EXIT_UNMEASURABLE
    assert "cannot measure" in capsys.readouterr().err


def test_main_prints_json(tree: Path, capsys: pytest.CaptureFixture[str]):
    code = pc.main(["--root", str(tree), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == pc.EXIT_OK
    assert payload["schema"] == 1
    assert payload["files_checked"] >= 6


def test_build_parser_defaults_to_the_current_directory():
    args = pc.build_parser().parse_args([])

    assert args.root == "."
    assert args.json is False


# ---------------------------------------------------------------------------
# This repository
# ---------------------------------------------------------------------------
def test_the_real_configuration_passes_the_audit():
    report = pc.audit_config(ROOT)

    assert report.findings == []
    assert report.repos == 3
    assert report.hooks == 9


def test_the_real_configuration_pins_every_repository():
    document = pc.load_document(pc.read_text(ROOT / pc.CONFIG) or "")
    assert document is not None

    repos, _ = pc.parse_repos(document)

    assert repos
    assert all(not pc.is_floating_rev(repo.rev) for repo in repos)


def test_the_real_required_hooks_are_installed():
    document = pc.load_document(pc.read_text(ROOT / pc.CONFIG) or "")
    assert document is not None

    repos, _ = pc.parse_repos(document)
    installed = {hook.id for hook in pc.all_hooks(repos)}

    assert set(pc.REQUIRED_HOOKS) <= installed


def test_the_real_pins_satisfy_the_declared_floors():
    document = pc.load_document(pc.read_text(ROOT / pc.CONFIG) or "")
    assert document is not None

    repos, _ = pc.parse_repos(document)
    floors = pc.project_floors(ROOT)

    for hook in pc.all_hooks(repos):
        distribution = pc.PINNED_HOOKS.get(hook.id)
        if distribution is None:
            continue
        assert pc.version_at_least(pc.parse_version(hook.rev) or (), floors[distribution])


def test_the_real_dev_extra_installs_pre_commit():
    floors = pc.project_floors(ROOT)

    assert "pre-commit" in floors


def test_the_real_tree_would_pass_the_hygiene_hooks():
    assert pc.audit_whitespace(ROOT) == []


def test_the_real_page_documents_every_code():
    assert pc.audit_docs(ROOT) == []


def test_the_real_tree_passes_the_whole_audit():
    assert pc.main(["--root", str(ROOT)]) == pc.EXIT_OK
