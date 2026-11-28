"""Tests for the Day 36 coverage-reporting audit (``tools/coverage_report.py``).

The audit reads a workflow, a Codecov declaration, ``pyproject.toml``,
``.gitignore`` and the tool's own page. Its tests have to prove three things
separately, the way the community-health audit's do:

* every finding **fires** when the defect is present — a gate that never fires
  is indistinguishable from a gate that is not there;
* every finding **stays quiet** when it should, including on the false positive
  that would teach a reader to ignore it (a commit SHA pinned in the workflow is
  a pin, not a credential);
* the committed pipeline in this repository currently passes.

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

from tools import coverage_report as cr  # noqa: E402  (path set up above)

#: A workflow that satisfies every rule.
WORKFLOW = """name: Coverage

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

permissions:
  contents: read
  id-token: write

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Measure
        run: |
          python -m pytest -q \\
            --cov \\
            --cov-report=xml:coverage.xml \\
            --cov-report=json:coverage.json
      - name: Keep the report
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: |
            coverage.xml
            coverage.json
      - name: Upload to Codecov
        uses: codecov/codecov-action@v5
        with:
          files: ./coverage.xml
          flags: unittests
          fail_ci_if_error: false
          use_oidc: true
"""

#: A Codecov declaration that agrees with the ``pyproject.toml`` below.
CODECOV = """coverage:
  precision: 1
  status:
    project:
      default:
        target: 90
        threshold: 1%
    patch:
      default:
        target: 85
  ignore:
    - "tests/*"
    - "tools/*"
    - "benchmarks/*"
    - "conftest.py"
    - "*/__pycache__/*"

flags:
  unittests:
    paths:
      - projects/
    carryforward: false
"""

#: A ``pyproject.toml`` carrying the three keys the audit compares against.
PYPROJECT = """[project]
name = "demo"
version = "1.0.0"

[tool.setuptools]
py-modules = ["caching", "validators"]

[tool.coverage.run]
branch = true
source = ["."]
omit = [
    "tests/*",
    "tools/*",
    "conftest.py",
    "benchmarks/*",
    "*/__pycache__/*",
]

[tool.coverage.report]
fail_under = 90
"""

#: The ignore rules that keep a local report out of the history.
GITIGNORE = "# output\n.coverage\ncoverage.xml\ncoverage.json\n"


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


def _checkout(root: Path) -> Path:
    """Build a checkout that passes every check.

    Args:
        root: Directory to build it in.

    Returns:
        The root, populated.
    """
    _write(root / cr.WORKFLOW, WORKFLOW)
    _write(root / cr.CODECOV_CONFIG, CODECOV)
    _write(root / cr.PYPROJECT, PYPROJECT)
    _write(root / cr.GITIGNORE, GITIGNORE)
    _write(
        root / cr.DOCS_PAGE,
        "# Coverage reporting\n\n" + "\n".join(f"- `{code}`" for code in cr.CODES) + "\n",
    )
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


def _codes(findings: List[cr.Finding]) -> List[str]:
    """Return the codes of some findings, in order.

    Args:
        findings: Findings to inspect.

    Returns:
        Their codes.
    """
    return [finding.code for finding in findings]


def _rewrite(root: Path, relative: Path, text: str) -> Path:
    """Replace one file in a built checkout.

    Args:
        root: Checkout root.
        relative: File to replace, relative to the root.
        text: New content.

    Returns:
        The written path.
    """
    return _write(root / relative, text)


def _audit(root: Path) -> List[cr.Finding]:
    """Audit a checkout, surfacing a parsing failure as a test failure.

    Args:
        root: Checkout root.

    Returns:
        The findings.
    """
    return cr.audit_repository(root)


# ---------------------------------------------------------------------------
# The committed pipeline
# ---------------------------------------------------------------------------


def test_committed_repository_passes() -> None:
    """The pipeline this pull request ships must be clean."""
    findings = cr.audit_repository(ROOT)
    assert findings == [], cr.render_table(ROOT, findings)


def test_committed_workflow_uploads_the_report_it_writes() -> None:
    """The rule the whole day exists for: the uploaded file is the written one."""
    document = cr.load_yaml(ROOT / cr.WORKFLOW)
    assert isinstance(document, dict)
    steps = cr.job_steps(document)
    produced = cr.produced_reports(cr.run_text(steps))
    named = [path for step in cr.codecov_steps(steps) for path in cr.uploaded_paths(step)]
    assert produced["xml"] == cr.REPORT_XML
    assert named == [cr.REPORT_XML]


def test_committed_workflow_pins_the_uploader() -> None:
    """A floating tag would let the uploader change without a commit here."""
    document = cr.load_yaml(ROOT / cr.WORKFLOW)
    assert isinstance(document, dict)
    for step in cr.codecov_steps(cr.job_steps(document)):
        pin = cr.uses_of(step).split("@", 1)[1]
        assert cr.ACTION_PIN_RE.match(pin)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def test_workflow_triggers_reads_the_boolean_key(tmp_path: Path) -> None:
    """YAML 1.1 turns ``on:`` into ``True``; both spellings are one declaration."""
    # Parsed from a file, not a hand-built dict: the ``True`` key is PyYAML's
    # doing, so a dict literal would test the helper against itself.
    document = cr.load_yaml(_write(tmp_path / "cr_probe.yml", WORKFLOW))
    assert isinstance(document, dict)
    assert "push" in cr.workflow_triggers(document)


def test_workflow_triggers_reads_a_string_form() -> None:
    """``on: push`` is a bare event name, not a mapping."""
    assert cr.workflow_triggers({"on": "push"}) == {"push": None}


def test_workflow_triggers_reads_a_list_form() -> None:
    """``on: [push, pull_request]`` is the same declaration as a mapping."""
    assert set(cr.workflow_triggers({"on": ["push", "pull_request"]})) == {
        "push",
        "pull_request",
    }


def test_workflow_triggers_tolerates_a_missing_block() -> None:
    """No triggers at all is an empty mapping, not an exception."""
    assert cr.workflow_triggers({}) == {}


def test_event_config_reports_absence() -> None:
    """An event that is not declared is reported as absent."""
    assert cr.event_config({}, "push") == (False, None)


def test_branch_filter_accepts_a_scalar() -> None:
    """``branches: main`` is one name, not a string to iterate."""
    assert cr.branch_filter({"branches": "main"}) == ["main"]


def test_branch_filter_is_none_without_a_filter() -> None:
    """A bare event runs everywhere, which the filter reports as ``None``."""
    assert cr.branch_filter(None) is None


def test_job_steps_flattens_every_job() -> None:
    """Two jobs contribute their steps in document order."""
    document = {
        "jobs": {
            "a": {"steps": [{"run": "one"}]},
            "b": {"steps": [{"run": "two"}, "not a step"]},
        }
    }
    assert [step.get("run") for step in cr.job_steps(document)] == ["one", "two"]


def test_job_steps_without_jobs_is_empty() -> None:
    """A workflow with no jobs has no steps."""
    assert cr.job_steps({}) == []


def test_produced_reports_fills_in_pytest_cov_defaults() -> None:
    """A bare ``--cov-report=xml`` writes ``coverage.xml``."""
    produced = cr.produced_reports("pytest --cov-report=xml --cov-report=json")
    assert produced == {"xml": "coverage.xml", "json": "coverage.json"}


def test_produced_reports_reads_explicit_paths() -> None:
    """An explicit ``:path`` is the path the uploader must name."""
    produced = cr.produced_reports("pytest --cov-report=xml:build/cov.xml")
    assert produced["xml"] == "build/cov.xml"


def test_step_input_joins_a_list() -> None:
    """A YAML list input is read as one comma-separated string."""
    assert cr.step_input({"with": {"files": ["a.xml", "b.xml"]}}, "files") == "a.xml,b.xml"


def test_step_input_is_none_when_absent() -> None:
    """A step without the input returns ``None``, not ``''``."""
    assert cr.step_input({"with": {}}, "files") is None


def test_uploaded_paths_normalises_a_leading_dot() -> None:
    """``./coverage.xml`` and ``coverage.xml`` are the same file."""
    step = {"with": {"files": "./coverage.xml, ./coverage.json"}}
    assert cr.uploaded_paths(step) == ["coverage.xml", "coverage.json"]


def test_uploaded_paths_is_empty_without_the_input() -> None:
    """No ``files`` input means the uploader searches — reported as empty."""
    assert cr.uploaded_paths({"with": {}}) == []


def test_base_of_strips_globs() -> None:
    """``tests/*`` and ``./tests/**`` both name ``tests``."""
    assert cr._base_of("tests/*") == "tests"
    assert cr._base_of("./tests/**") == "tests"


def test_pyproject_coverage_reads_floor_omit_and_modules(checkout: Path) -> None:
    """The three keys the audit compares against are read together."""
    declared = cr.pyproject_coverage(checkout)
    assert declared is not None
    floor, omit, modules = declared
    assert floor == 90.0
    assert "tests/*" in omit
    assert modules == ["caching", "validators"]


def test_pyproject_coverage_without_a_floor_is_none(tmp_path: Path) -> None:
    """No ``fail_under`` means there is nothing to compare a target against."""
    _write(tmp_path / "pyproject.toml", '[project]\nname = "demo"\n')
    assert cr.pyproject_coverage(tmp_path) is None


def test_project_target_reads_a_number(checkout: Path) -> None:
    """The project target is the number the badge enforces."""
    document = cr.load_yaml(checkout / cr.CODECOV_CONFIG)
    assert isinstance(document, dict)
    assert cr.project_target(document) == 90.0


def test_project_target_ignores_auto() -> None:
    """``auto`` is read off the previous commit, which no local gate can match."""
    document = {"coverage": {"status": {"project": {"default": {"target": "auto"}}}}}
    assert cr.project_target(document) is None


def test_declared_flags_are_read_in_order(checkout: Path) -> None:
    """The flag names come from the declaration's keys."""
    document = cr.load_yaml(checkout / cr.CODECOV_CONFIG)
    assert isinstance(document, dict)
    assert cr.declared_flags(document) == ["unittests"]


def test_workflow_flags_are_deduplicated() -> None:
    """Two steps uploading the same flag is one flag."""
    steps = [
        {"uses": "codecov/codecov-action@v5", "with": {"flags": "unittests"}},
        {"uses": "codecov/codecov-action@v5", "with": {"flags": "unittests, e2e"}},
    ]
    assert cr.workflow_flags(steps) == ["unittests", "e2e"]


def test_gitignore_rules_skip_comments(tmp_path: Path) -> None:
    """Comments and blank lines are not rules."""
    _write(tmp_path / ".gitignore", "# note\n\ncoverage.xml\n")
    assert cr.gitignore_rules(tmp_path) == ["coverage.xml"]


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------


def test_missing_workflow_is_a_finding(tmp_path: Path) -> None:
    """Nothing publishes a report when there is no workflow."""
    _checkout(tmp_path)
    (tmp_path / cr.WORKFLOW).unlink()
    assert "coverage.workflow" in _codes(_audit(tmp_path))


def test_workflow_without_steps_is_a_finding(tmp_path: Path) -> None:
    """A workflow that declares no steps measures nothing."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, "name: Coverage\non: push\n")
    assert "coverage.workflow" in _codes(_audit(tmp_path))


def test_missing_pull_request_trigger_is_a_finding(tmp_path: Path) -> None:
    """A badge that only tracks pushes cannot gate a pull request."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path, cr.WORKFLOW, WORKFLOW.replace("  pull_request:\n    branches: [ main ]\n", "")
    )
    findings = _audit(tmp_path)
    assert "coverage.trigger" in _codes(findings)
    assert any("pull_request" in finding.detail for finding in findings)


def test_unfiltered_trigger_is_a_warning(tmp_path: Path) -> None:
    """Every branch publishing a report is noisy, not wrong."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace("  push:\n    branches: [ main ]", "  push:"),
    )
    findings = [f for f in _audit(tmp_path) if f.code == "coverage.trigger"]
    assert [finding.severity for finding in findings] == ["warning"]
    assert not any(finding.is_error for finding in findings)


def test_trigger_filtered_away_from_main_is_a_finding(tmp_path: Path) -> None:
    """``branches: [ develop ]`` never publishes the default branch's number."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace(
            "branches: [ main ]\n  pull_request", "branches: [ develop ]\n  pull_request"
        ),
    )
    assert "coverage.trigger" in _codes(_audit(tmp_path))


def test_missing_codecov_step_is_a_finding(tmp_path: Path) -> None:
    """No uploader means the report never leaves the runner."""
    _checkout(tmp_path)
    text = WORKFLOW.split("      - name: Upload to Codecov")[0]
    _rewrite(tmp_path, cr.WORKFLOW, text)
    assert "coverage.action" in _codes(_audit(tmp_path))


def test_floating_action_ref_is_a_finding(tmp_path: Path) -> None:
    """``@master`` changes without a commit here."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("@v5", "@master"))
    assert "coverage.action" in _codes(_audit(tmp_path))


def test_full_version_pin_is_accepted(tmp_path: Path) -> None:
    """A full ``v5.4.0`` pin is stricter than a major tag, so it passes."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("@v5", "@v5.4.0"))
    assert "coverage.action" not in _codes(_audit(tmp_path))


def test_upload_naming_an_unwritten_report_is_a_finding(tmp_path: Path) -> None:
    """The uploader would search, find nothing, and report success."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("files: ./coverage.xml", "files: ./cov.xml"))
    findings = _audit(tmp_path)
    assert "coverage.files" in _codes(findings)
    assert any("cov.xml" in finding.detail for finding in findings)


def test_upload_naming_nothing_is_a_finding(tmp_path: Path) -> None:
    """An omitted ``files:`` input is the silent-failure case."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("          files: ./coverage.xml\n", ""))
    assert "coverage.files" in _codes(_audit(tmp_path))


def test_xml_written_somewhere_else_is_a_finding(tmp_path: Path) -> None:
    """Moving the report is fine only if the uploader moves with it."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace("--cov-report=xml:coverage.xml", "--cov-report=xml:build/coverage.xml"),
    )
    assert "coverage.files" in _codes(_audit(tmp_path))


def test_fail_ci_if_error_true_is_a_finding(tmp_path: Path) -> None:
    """A third party must not decide the build's colour."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path, cr.WORKFLOW, WORKFLOW.replace("fail_ci_if_error: false", "fail_ci_if_error: true")
    )
    assert "coverage.fail-ci" in _codes(_audit(tmp_path))


def test_fail_ci_if_error_absent_is_a_finding(tmp_path: Path) -> None:
    """Absent is not the same as false: the action's default is unknown here."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("          fail_ci_if_error: false\n", ""))
    assert "coverage.fail-ci" in _codes(_audit(tmp_path))


def test_literal_token_is_a_finding(tmp_path: Path) -> None:
    """A token written into the file is a credential in the history."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace(
            "          use_oidc: true",
            "          token: 3f786850-e387-550f-dab8-36ed7e6dc881",
        ),
    )
    findings = [f for f in _audit(tmp_path) if f.code == "coverage.token"]
    # Two rules fire: the input is not a secret reference, and the literal it
    # carries is a credential.
    assert len(findings) == 2
    literals = [f for f in findings if "literal token" in f.detail]
    assert len(literals) == 1
    # The value is masked in the report: a finding must not reprint a credential.
    assert "3f786850" in literals[0].detail
    assert "dab8-36ed7e6dc881" not in literals[0].detail


def test_secret_reference_is_accepted(tmp_path: Path) -> None:
    """``${{ secrets.CODECOV_TOKEN }}`` is the right way to pass a token."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace(
            "          use_oidc: true",
            "          token: ${{ secrets.CODECOV_TOKEN }}",
        ),
    )
    assert "coverage.token" not in _codes(_audit(tmp_path))


def test_commit_sha_is_not_mistaken_for_a_token(tmp_path: Path) -> None:
    """Only lines mentioning a token are scanned, so a SHA pin stays clean."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace(
            "      - uses: actions/checkout@v4",
            "      - uses: actions/checkout@3f786850e387550fdab836ed7e6dc881de23001b",
        ),
    )
    assert "coverage.token" not in _codes(_audit(tmp_path))


def test_tokenless_upload_without_oidc_is_a_finding(tmp_path: Path) -> None:
    """No token and no OIDC means the upload cannot authenticate at all."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("          use_oidc: true\n", ""))
    assert "coverage.permissions" in _codes(_audit(tmp_path))


def test_missing_artifact_step_is_a_finding(tmp_path: Path) -> None:
    """A failed upload with no kept report leaves nothing to inspect."""
    _checkout(tmp_path)
    text = WORKFLOW.replace(
        "      - name: Keep the report\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          name: coverage-report\n"
        "          path: |\n"
        "            coverage.xml\n"
        "            coverage.json\n",
        "",
    )
    _rewrite(tmp_path, cr.WORKFLOW, text)
    assert "coverage.artifact" in _codes(_audit(tmp_path))


def test_artifact_that_drops_the_xml_is_a_finding(tmp_path: Path) -> None:
    """Keeping only the JSON report loses the file the badge is built from."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace(
            "            coverage.xml\n            coverage.json\n", "            coverage.json\n"
        ),
    )
    assert "coverage.artifact" in _codes(_audit(tmp_path))


def test_contents_write_is_a_finding(tmp_path: Path) -> None:
    """A job that only reads the checkout has no use for a write token."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("contents: read", "contents: write"))
    assert "coverage.permissions" in _codes(_audit(tmp_path))


def test_missing_id_token_is_a_finding(tmp_path: Path) -> None:
    """The tokenless upload authenticates with OIDC, which needs the grant."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("  id-token: write\n", ""))
    assert "coverage.permissions" in _codes(_audit(tmp_path))


def test_missing_permissions_block_is_a_finding(tmp_path: Path) -> None:
    """No block means the default scope, which is wider than this job needs."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace("permissions:\n  contents: read\n  id-token: write\n\n", ""),
    )
    assert "coverage.permissions" in _codes(_audit(tmp_path))


# ---------------------------------------------------------------------------
# The Codecov declaration
# ---------------------------------------------------------------------------


def test_missing_config_is_a_finding(tmp_path: Path) -> None:
    """Without it, Codecov applies its own defaults to this repository."""
    _checkout(tmp_path)
    (tmp_path / cr.CODECOV_CONFIG).unlink()
    assert "coverage.config" in _codes(_audit(tmp_path))


def test_target_disagreeing_with_fail_under_is_a_finding(tmp_path: Path) -> None:
    """Two floors is no floor."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.CODECOV_CONFIG, CODECOV.replace("target: 90", "target: 85", 1))
    findings = _audit(tmp_path)
    assert "coverage.target" in _codes(findings)
    assert any("85" in finding.detail and "90" in finding.detail for finding in findings)


def test_auto_target_is_a_finding(tmp_path: Path) -> None:
    """``auto`` tracks the previous commit, not the declared floor."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.CODECOV_CONFIG, CODECOV.replace("target: 90", 'target: "auto"', 1))
    assert "coverage.target" in _codes(_audit(tmp_path))


def test_ignore_list_missing_an_omit_entry_is_a_finding(tmp_path: Path) -> None:
    """The badge would measure files the local gate does not."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.CODECOV_CONFIG, CODECOV.replace('    - "tools/*"\n', ""))
    findings = _audit(tmp_path)
    assert "coverage.ignore" in _codes(findings)
    assert any("tools/*" in finding.detail for finding in findings)


def test_ignore_list_hiding_shipped_code_is_a_finding(tmp_path: Path) -> None:
    """Ignoring ``projects/`` would hide the five tools from the badge."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.CODECOV_CONFIG,
        CODECOV.replace("  ignore:\n", '  ignore:\n    - "projects/*"\n'),
    )
    findings = _audit(tmp_path)
    assert "coverage.ignore" in _codes(findings)
    assert any("shipped" in finding.detail for finding in findings)


def test_ignore_list_hiding_a_shipped_module_is_a_finding(tmp_path: Path) -> None:
    """The core modules are shipped code too, so ignoring one is the same defect."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.CODECOV_CONFIG,
        CODECOV.replace("  ignore:\n", '  ignore:\n    - "caching.py"\n'),
    )
    assert "coverage.ignore" in _codes(_audit(tmp_path))


def test_undeclared_flag_is_a_finding(tmp_path: Path) -> None:
    """A report arriving under an unknown flag is read by no status check."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, WORKFLOW.replace("flags: unittests", "flags: e2e"))
    findings = _audit(tmp_path)
    assert "coverage.flags" in _codes(findings)
    assert any("e2e" in finding.detail for finding in findings)


def test_declared_flag_never_uploaded_is_a_finding(tmp_path: Path) -> None:
    """A dead flag is a declaration nobody honours."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.CODECOV_CONFIG,
        CODECOV.replace("flags:\n", "flags:\n  e2e:\n    paths:\n      - tests/\n"),
    )
    findings = _audit(tmp_path)
    assert "coverage.flags" in _codes(findings)
    assert any("no upload step sends" in finding.detail for finding in findings)


# ---------------------------------------------------------------------------
# Artefacts and documentation
# ---------------------------------------------------------------------------


def test_report_not_ignored_is_a_finding(tmp_path: Path) -> None:
    """The next ``git add .`` would commit a report."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.GITIGNORE, ".coverage\n")
    findings = _audit(tmp_path)
    assert _codes(findings).count("coverage.gitignore") == 2


def test_missing_docs_page_is_a_finding(tmp_path: Path) -> None:
    """A gate nobody documented is a gate nobody runs."""
    _checkout(tmp_path)
    (tmp_path / cr.DOCS_PAGE).unlink()
    assert "coverage.docs" in _codes(_audit(tmp_path))


def test_docs_page_omitting_a_code_is_a_finding(tmp_path: Path) -> None:
    """The code table cannot drift behind the checks."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.DOCS_PAGE, "# Coverage\n\n" + "\n".join(cr.CODES[:-1]) + "\n")
    findings = [f for f in _audit(tmp_path) if f.code == "coverage.docs"]
    assert [cr.CODES[-1] in finding.detail for finding in findings] == [True]


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_main_passes_on_a_clean_checkout(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clean checkout exits 0 and says what it measured."""
    assert cr.main(["--root", str(checkout)]) == 0
    assert "pass" in capsys.readouterr().out


def test_main_fails_on_a_finding(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A finding is exit 1, and the table names the code."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.GITIGNORE, "")
    assert cr.main(["--root", str(tmp_path)]) == 1
    assert "coverage.gitignore" in capsys.readouterr().out


def test_main_strict_promotes_a_warning(tmp_path: Path) -> None:
    """A warning is exit 0 by default and exit 1 under ``--strict``."""
    _checkout(tmp_path)
    _rewrite(
        tmp_path,
        cr.WORKFLOW,
        WORKFLOW.replace("  push:\n    branches: [ main ]", "  push:"),
    )
    assert cr.main(["--root", str(tmp_path)]) == 0
    assert cr.main(["--root", str(tmp_path), "--strict"]) == 1


def test_main_json_is_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` emits the verdict as data, not as a table."""
    _checkout(tmp_path)
    assert cr.main(["--root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["floor"] == 90.0
    assert payload["errors"] == 0


def test_main_rejects_a_missing_root(tmp_path: Path) -> None:
    """A path that is not a directory is unmeasurable, not passing."""
    assert cr.main(["--root", str(tmp_path / "absent")]) == 2


def test_main_rejects_an_unparsable_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Broken YAML is exit 2: "cannot measure" must never read as "passes"."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.WORKFLOW, "name: [unclosed\n")
    assert cr.main(["--root", str(tmp_path)]) == 2
    assert "nothing to measure" in capsys.readouterr().err


def test_main_rejects_a_pyproject_without_a_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``fail_under`` there is nothing to compare the target against."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.PYPROJECT, '[project]\nname = "demo"\n')
    assert cr.main(["--root", str(tmp_path)]) == 2
    assert "fail_under" in capsys.readouterr().err


def test_audit_repository_raises_on_unparsable_config(tmp_path: Path) -> None:
    """The config is parsed the same way, and a failure is not a verdict."""
    _checkout(tmp_path)
    _rewrite(tmp_path, cr.CODECOV_CONFIG, "coverage: [\n")
    with pytest.raises(ValueError):
        cr.audit_repository(tmp_path)


def test_render_table_names_every_finding(checkout: Path) -> None:
    """The table is a report: severity, code, location and detail per row."""
    findings = _audit(checkout)
    findings = [cr.Finding("coverage.target", "codecov.yml", "off by five points")]
    table = cr.render_table(checkout, findings)
    assert "coverage.target" in table
    assert "codecov.yml" in table
    assert "1 error(s)" in table


def test_summary_reports_counts(checkout: Path) -> None:
    """The JSON verdict carries the counts a caller gates on."""
    findings = [
        cr.Finding("coverage.target", "codecov.yml", "off"),
        cr.Finding("coverage.trigger", "workflow", "noisy", severity="warning"),
    ]
    payload = cr.summary(checkout, findings)
    assert payload["errors"] == 1
    assert payload["warnings"] == 1
    assert payload["ok"] is False
