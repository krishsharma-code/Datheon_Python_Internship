"""Tests for the Day 24 CI matrix audit (``tools/ci_matrix.py``).

The audit exists because a matrix is a *declaration*, and declarations rot
silently: a version written unquoted becomes a float, ``fail-fast`` defaults to
the wrong value, ``cache: pip`` goes missing in a refactor, a bash ``for`` loop
lands on PowerShell. None of those show up as a failing test — they show up as
a matrix that quietly measures less than it claims.

So the tests here are organised the same way the audit is: each test breaks one
thing in a synthetic workflow and asserts the *named* finding that results,
rather than "the audit failed somehow". A code that can be renamed without a
test failing is a code nobody depends on.

The last section runs the audit against the real ``.github/workflows/ci.yml``
and against the packaging declaration, because the point of the day is that the
workflow and ``pyproject.toml`` cannot disagree.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import ci_matrix  # noqa: E402  (path set up above)

WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"

#: The grid the repository promises: three runners, four interpreters.
GOOD_MATRIX: Dict[str, List[str]] = {
    "os": ["ubuntu-latest", "macos-latest", "windows-latest"],
    "python-version": ["3.10", "3.11", "3.12", "3.13"],
}


def cell_steps() -> List[Dict[str, Any]]:
    """Build the steps every matrix cell is required to run.

    Returns:
        A fresh list of step mappings: setup with a pip cache, then the four
        commands :data:`ci_matrix.REQUIRED_CELL_COMMANDS` looks for.
    """
    return [
        {
            "uses": "actions/setup-python@v5",
            "with": {"python-version": "${{ matrix.python-version }}", "cache": "pip"},
        },
        {"run": "python -m compileall -q projects tests"},
        {"run": "flake8 ."},
        {"run": "black --check ."},
        {"run": "pytest -q"},
    ]


def make_workflow(
    matrix: Optional[Dict[str, Any]] = None,
    *,
    steps: Optional[List[Dict[str, Any]]] = None,
    fail_fast: Optional[bool] = False,
    permissions: Any = "default",
    shell: Optional[str] = "bash",
    status_job: bool = True,
    status_if: str = "always()",
) -> Dict[str, Any]:
    """Build a workflow that passes the audit, for tests to then break.

    Args:
        matrix: The ``strategy.matrix`` block; defaults to :data:`GOOD_MATRIX`.
        steps: The matrix job's steps; defaults to :func:`cell_steps`.
        fail_fast: Value for ``strategy.fail-fast``; ``None`` omits the key.
        permissions: ``"default"`` for ``contents: read``, ``None`` to omit the
            block entirely, or any explicit value.
        shell: The workflow-level ``defaults.run.shell``; ``None`` omits it.
        status_job: Whether to add the aggregate ``ci-status`` job.
        status_if: The ``if:`` expression of that aggregate job.

    Returns:
        A workflow mapping shaped like a parsed ``ci.yml``.
    """
    strategy: Dict[str, Any] = {"matrix": dict(matrix if matrix is not None else GOOD_MATRIX)}
    if fail_fast is not None:
        strategy["fail-fast"] = fail_fast

    workflow: Dict[str, Any] = {
        "name": "CI",
        "on": {"push": {"branches": ["main"]}},
        "jobs": {
            "lint-and-test": {
                "runs-on": "${{ matrix.os }}",
                "strategy": strategy,
                "steps": steps if steps is not None else cell_steps(),
            }
        },
    }
    if permissions == "default":
        workflow["permissions"] = {"contents": "read"}
    elif permissions is not None:
        workflow["permissions"] = permissions
    if shell is not None:
        workflow["defaults"] = {"run": {"shell": shell}}
    if status_job:
        workflow["jobs"]["ci-status"] = {
            "if": status_if,
            "needs": ["lint-and-test"],
            "runs-on": "ubuntu-latest",
            "steps": [{"run": "echo done"}],
        }
    return workflow


def codes(verdict: ci_matrix.AuditVerdict) -> List[str]:
    """Return the finding codes of a verdict, in order."""
    return [finding.code for finding in verdict.findings]


def audit(workflow: Dict[str, Any], **kwargs: Any) -> ci_matrix.AuditVerdict:
    """Audit a synthetic workflow with the pyproject cross-check switched off."""
    kwargs.setdefault("pyproject", None)
    return ci_matrix.audit_workflow(workflow, **kwargs)


def pyproject_with(*versions: str, requires_python: str = ">=3.10") -> Dict[str, Any]:
    """Build a ``pyproject.toml``-shaped mapping with the given classifiers.

    Args:
        *versions: Python versions to claim in ``classifiers``.
        requires_python: The ``requires-python`` specifier.

    Returns:
        A mapping with the ``[project]`` table the audit reads.
    """
    return {
        "project": {
            "requires-python": requires_python,
            "classifiers": [f"Programming Language :: Python :: {version}" for version in versions],
        }
    }


# --- expanding the matrix --------------------------------------------------
def test_expand_matrix_is_the_cartesian_product():
    cells = ci_matrix.expand_matrix(GOOD_MATRIX)

    assert len(cells) == 12
    assert cells[0] == ci_matrix.MatrixCell("macos-latest", "3.10")
    assert ci_matrix.MatrixCell("windows-latest", "3.13") in cells


def test_expand_matrix_accepts_the_python_alias():
    cells = ci_matrix.expand_matrix({"os": ["ubuntu-latest"], "python": ["3.12"]})

    assert cells == (ci_matrix.MatrixCell("ubuntu-latest", "3.12"),)


def test_expand_matrix_accepts_the_platform_alias():
    cells = ci_matrix.expand_matrix({"platform": ["windows-latest"], "python-version": ["3.11"]})

    assert cells == (ci_matrix.MatrixCell("windows-latest", "3.11"),)


def test_expand_matrix_applies_exclude():
    matrix = dict(GOOD_MATRIX)
    matrix["exclude"] = [{"os": "windows-latest", "python-version": "3.10"}]

    cells = ci_matrix.expand_matrix(matrix)

    assert len(cells) == 11
    assert ci_matrix.MatrixCell("windows-latest", "3.10") not in cells


def test_expand_matrix_exclude_ignores_extra_keys():
    matrix = dict(GOOD_MATRIX)
    matrix["exclude"] = [{"os": "macos-latest", "python-version": "3.13", "experimental": True}]

    assert ci_matrix.MatrixCell("macos-latest", "3.13") not in ci_matrix.expand_matrix(matrix)


def test_expand_matrix_exclude_matching_nothing_changes_nothing():
    matrix = dict(GOOD_MATRIX)
    matrix["exclude"] = [{"os": "ubuntu-latest", "python-version": "3.9"}]

    assert len(ci_matrix.expand_matrix(matrix)) == 12


def test_expand_matrix_include_adds_a_cell():
    matrix = dict(GOOD_MATRIX)
    matrix["include"] = [{"os": "ubuntu-latest", "python-version": "3.14"}]

    cells = ci_matrix.expand_matrix(matrix)

    assert len(cells) == 13
    assert ci_matrix.MatrixCell("ubuntu-latest", "3.14") in cells


def test_expand_matrix_include_of_an_existing_cell_does_not_duplicate():
    matrix = dict(GOOD_MATRIX)
    matrix["include"] = [{"os": "ubuntu-latest", "python-version": "3.12"}]

    assert len(ci_matrix.expand_matrix(matrix)) == 12


def test_expand_matrix_partial_include_adds_no_cell():
    """An include naming one key attaches a variable; it creates no runner."""
    matrix = dict(GOOD_MATRIX)
    matrix["include"] = [{"os": "ubuntu-latest", "experimental": True}]

    assert len(ci_matrix.expand_matrix(matrix)) == 12


def test_expand_matrix_normalises_unquoted_versions():
    """A float 3.1 is what YAML makes of an unquoted 3.10; report it as text."""
    cells = ci_matrix.expand_matrix({"os": ["ubuntu-latest"], "python-version": [3.12]})

    assert cells == (ci_matrix.MatrixCell("ubuntu-latest", "3.12"),)


def test_expand_matrix_requires_an_os_key():
    with pytest.raises(ci_matrix.MatrixError, match="os"):
        ci_matrix.expand_matrix({"python-version": ["3.12"]})


def test_expand_matrix_requires_a_python_key():
    with pytest.raises(ci_matrix.MatrixError, match="python"):
        ci_matrix.expand_matrix({"os": ["ubuntu-latest"]})


def test_expand_matrix_rejects_an_empty_axis():
    with pytest.raises(ci_matrix.MatrixError, match="non-empty list"):
        ci_matrix.expand_matrix({"os": [], "python-version": ["3.12"]})


def test_expand_matrix_rejects_a_non_mapping():
    with pytest.raises(ci_matrix.MatrixError, match="not a mapping"):
        ci_matrix.expand_matrix(["ubuntu-latest"])  # type: ignore[arg-type]


# --- the happy path --------------------------------------------------------
def test_a_complete_workflow_passes():
    verdict = audit(make_workflow())

    assert verdict.ok
    assert len(verdict.cells) == 12
    assert verdict.findings == ()


# --- version typing --------------------------------------------------------
def test_unquoted_versions_are_reported():
    verdict = audit(make_workflow({"os": ["ubuntu-latest"], "python-version": [3.1]}))

    assert "matrix.unquoted-version" in codes(verdict)
    finding = next(f for f in verdict.findings if f.code == "matrix.unquoted-version")
    assert "3.1" in finding.message


def test_quoted_versions_are_not_reported():
    assert "matrix.unquoted-version" not in codes(audit(make_workflow()))


# --- the grid --------------------------------------------------------------
def test_a_missing_cell_is_named():
    verdict = audit(make_workflow({"os": ["ubuntu-latest"], "python-version": ["3.12"]}))

    missing = [f for f in verdict.findings if f.code == "matrix.missing-cell"]
    assert len(missing) == 11
    assert any("windows-latest · Python 3.10" in finding.message for finding in missing)
    assert not verdict.ok


def test_an_extra_cell_is_a_warning_not_an_error():
    matrix = dict(GOOD_MATRIX)
    matrix["python-version"] = GOOD_MATRIX["python-version"] + ["3.14"]

    verdict = audit(make_workflow(matrix))

    extra = [f for f in verdict.findings if f.code == "matrix.unexpected-cell"]
    assert len(extra) == 3  # one per OS
    assert all(finding.severity == "warning" for finding in extra)
    assert verdict.ok
    assert not audit(make_workflow(matrix), strict=True).ok


# --- strategy: fail-fast and caching ---------------------------------------
def test_fail_fast_true_is_an_error():
    verdict = audit(make_workflow(fail_fast=True))

    assert "matrix.fail-fast" in codes(verdict)
    assert not verdict.ok


def test_fail_fast_must_be_explicit():
    """Absent means GitHub's default, which is ``true``."""
    assert "matrix.fail-fast" in codes(audit(make_workflow(fail_fast=None)))


def test_missing_pip_cache_is_an_error():
    steps = cell_steps()
    steps[0]["with"] = {"python-version": "${{ matrix.python-version }}"}

    verdict = audit(make_workflow(steps=steps))

    assert "matrix.pip-cache" in codes(verdict)
    assert not verdict.ok


def test_a_non_pip_cache_does_not_count():
    steps = cell_steps()
    steps[0]["with"] = {"python-version": "${{ matrix.python-version }}", "cache": "poetry"}

    assert "matrix.pip-cache" in codes(audit(make_workflow(steps=steps)))


def test_missing_setup_python_is_reported():
    steps = [step for step in cell_steps() if "uses" not in step]

    assert "matrix.setup-python" in codes(audit(make_workflow(steps=steps)))


def test_every_setup_step_needs_the_cache():
    steps = cell_steps()
    steps.append({"uses": "actions/setup-python@v5", "with": {"python-version": "3.12"}})

    assert "matrix.pip-cache" in codes(audit(make_workflow(steps=steps)))


# --- shell portability -----------------------------------------------------
def test_missing_bash_shell_is_an_error():
    verdict = audit(make_workflow(shell=None))

    assert "matrix.shell" in codes(verdict)
    assert not verdict.ok


def test_powershell_default_is_an_error():
    assert "matrix.shell" in codes(audit(make_workflow(shell="pwsh")))


def test_a_job_level_bash_default_counts():
    workflow = make_workflow(shell=None)
    workflow["jobs"]["lint-and-test"]["defaults"] = {"run": {"shell": "bash"}}

    assert "matrix.shell" not in codes(audit(workflow))


def test_a_step_level_bash_shell_counts():
    steps = cell_steps()
    steps[1]["shell"] = "bash"
    workflow = make_workflow(steps=steps, shell=None)

    assert "matrix.shell" not in codes(audit(workflow))


# --- the commands a cell must run -----------------------------------------
def test_a_missing_command_is_reported():
    steps = [step for step in cell_steps() if step.get("run") != "flake8 ."]

    verdict = audit(make_workflow(steps=steps))

    assert "matrix.command" in codes(verdict)
    finding = next(f for f in verdict.findings if f.code == "matrix.command")
    assert "lint" in finding.message


def test_all_four_required_commands_are_checked():
    for label, needle in ci_matrix.REQUIRED_CELL_COMMANDS:
        steps = [step for step in cell_steps() if needle not in step.get("run", "")]
        verdict = audit(make_workflow(steps=steps))
        assert "matrix.command" in codes(verdict), label


# --- permissions and job wiring -------------------------------------------
def test_missing_permissions_block_is_an_error():
    assert "permissions.missing" in codes(audit(make_workflow(permissions=None)))


def test_write_permissions_are_rejected():
    verdict = audit(make_workflow(permissions={"contents": "write"}))

    assert "permissions.write" in codes(verdict)
    assert not verdict.ok


def test_a_job_without_runs_on_is_reported():
    workflow = make_workflow()
    workflow["jobs"]["ci-status"].pop("runs-on")

    assert "job.runs-on" in codes(audit(workflow))


def test_missing_aggregate_job_is_an_error():
    verdict = audit(make_workflow(status_job=False))

    assert "matrix.aggregate" in codes(verdict)
    assert not verdict.ok


def test_aggregate_job_needs_always():
    """Without ``if: always()`` the summary job is skipped when a cell fails."""
    assert "matrix.aggregate" in codes(audit(make_workflow(status_if="success()")))


def test_aggregate_job_must_need_the_matrix():
    workflow = make_workflow()
    workflow["jobs"]["ci-status"]["needs"] = ["something-else"]

    assert "matrix.aggregate" in codes(audit(workflow))


# --- agreement with pyproject.toml ---------------------------------------
def test_pyproject_classifiers_agreeing_are_silent():
    verdict = ci_matrix.audit_workflow(
        make_workflow(), pyproject=pyproject_with("3.10", "3.11", "3.12", "3.13")
    )

    assert verdict.ok
    assert verdict.findings == ()


def test_a_classifier_the_matrix_never_runs_is_reported():
    verdict = ci_matrix.audit_workflow(
        make_workflow(), pyproject=pyproject_with("3.9", "3.10", "3.11", "3.12", "3.13")
    )

    assert "pyproject.classifier" in codes(verdict)
    assert any("never runs" in finding.message for finding in verdict.findings)


def test_a_version_the_matrix_runs_but_does_not_claim_is_reported():
    verdict = ci_matrix.audit_workflow(
        make_workflow(), pyproject=pyproject_with("3.10", "3.11", "3.12")
    )

    assert "pyproject.classifier" in codes(verdict)
    assert any("does not claim" in finding.message for finding in verdict.findings)


def test_a_floor_below_the_oldest_cell_is_reported():
    verdict = ci_matrix.audit_workflow(
        make_workflow(),
        pyproject=pyproject_with("3.10", "3.11", "3.12", "3.13", requires_python=">=3.9"),
    )

    assert "pyproject.floor" in codes(verdict)


def test_a_floor_above_the_oldest_cell_is_reported():
    verdict = ci_matrix.audit_workflow(
        make_workflow(),
        pyproject=pyproject_with("3.10", "3.11", "3.12", "3.13", requires_python=">=3.11"),
    )

    assert "pyproject.floor" in codes(verdict)


# --- structurally broken workflows ---------------------------------------
def test_a_malformed_matrix_becomes_a_finding():
    verdict = audit(make_workflow({"python-version": ["3.12"]}))

    assert "matrix.malformed" in codes(verdict)
    assert verdict.cells == ()
    assert not verdict.ok


def test_a_workflow_without_a_matrix_is_unmeasurable():
    workflow = make_workflow()
    workflow["jobs"]["lint-and-test"].pop("strategy")

    verdict = audit(workflow)

    assert "matrix.missing" in codes(verdict)
    assert verdict.cells == ()
    assert not verdict.ok


def test_matrix_job_detection_accepts_the_python_alias():
    workflow = make_workflow({"os": ["ubuntu-latest"], "python": ["3.12"]})

    assert [job_id for job_id, _ in ci_matrix.find_matrix_jobs(workflow)] == ["lint-and-test"]


# --- the real workflow ----------------------------------------------------
def test_the_real_workflow_declares_twelve_cells():
    verdict = ci_matrix.audit_workflow(
        ci_matrix.load_workflow(WORKFLOW_PATH),
        workflow_path=str(WORKFLOW_PATH),
        pyproject=ci_matrix.load_pyproject(ROOT / "pyproject.toml"),
    )

    assert verdict.ok, [finding.message for finding in verdict.findings]
    assert len(verdict.cells) == 12
    assert {cell.os_name for cell in verdict.cells} == set(ci_matrix.REQUIRED_OPERATING_SYSTEMS)
    assert {cell.python_version for cell in verdict.cells} == set(
        ci_matrix.REQUIRED_PYTHON_VERSIONS
    )


def test_the_real_workflow_quotes_its_versions():
    """The float trap is one edit away at all times."""
    verdict = ci_matrix.audit_workflow(ci_matrix.load_workflow(WORKFLOW_PATH))

    assert "matrix.unquoted-version" not in codes(verdict)


def test_the_real_workflow_keeps_the_coverage_gate():
    """Day 20's floor must survive Day 24's rewrite of the same file."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "tools/coverage_gate.py" in text
    assert "--minimum 90" in text


def test_the_real_workflow_still_smokes_the_console_scripts():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "pip install -e ." in text
    assert "shutil.which" in text, "the smoke test must resolve the command portably"


def test_the_constants_match_the_packaging_declaration():
    pyproject = ci_matrix.load_pyproject(ROOT / "pyproject.toml")
    classifiers = pyproject["project"]["classifiers"]
    declared = {
        item.rsplit(" :: ", 1)[-1]
        for item in classifiers
        if item.startswith("Programming Language :: Python :: 3.")
    }

    assert declared == set(ci_matrix.REQUIRED_PYTHON_VERSIONS)


def test_gitattributes_normalises_line_endings():
    """Without it the Windows cell sees CRLF and black fails on clean code."""
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "* text=auto eol=lf" in text


# --- the command line ------------------------------------------------------
def test_main_passes_on_the_real_workflow(capsys: pytest.CaptureFixture[str]):
    assert ci_matrix.main([]) == ci_matrix.EXIT_OK
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "cells: 12" in out


def test_main_fails_on_a_broken_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    import yaml

    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(make_workflow(fail_fast=True)), encoding="utf-8")

    assert ci_matrix.main(["--workflow", str(path), "--no-pyproject"]) == ci_matrix.EXIT_FINDINGS
    assert "FAIL" in capsys.readouterr().out


def test_main_returns_unmeasurable_for_a_missing_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert ci_matrix.main(["--workflow", str(tmp_path / "nope.yml")]) == ci_matrix.EXIT_UNMEASURABLE
    assert "cannot read" in capsys.readouterr().err


def test_main_returns_unmeasurable_for_invalid_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    path = tmp_path / "ci.yml"
    path.write_text("jobs: [unclosed\n", encoding="utf-8")

    assert ci_matrix.main(["--workflow", str(path)]) == ci_matrix.EXIT_UNMEASURABLE
    assert "not valid YAML" in capsys.readouterr().err


def test_main_returns_unmeasurable_when_the_top_level_is_not_a_mapping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    path = tmp_path / "ci.yml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    assert ci_matrix.main(["--workflow", str(path)]) == ci_matrix.EXIT_UNMEASURABLE
    assert "mapping" in capsys.readouterr().err


def test_main_returns_unmeasurable_when_no_cell_can_be_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    import yaml

    workflow = make_workflow()
    workflow["jobs"]["lint-and-test"].pop("strategy")
    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(workflow), encoding="utf-8")

    assert ci_matrix.main(["--workflow", str(path), "--no-pyproject"]) == (
        ci_matrix.EXIT_UNMEASURABLE
    )
    assert "NO VERDICT" in capsys.readouterr().out


def test_main_prints_json(capsys: pytest.CaptureFixture[str]):
    assert ci_matrix.main(["--json"]) == ci_matrix.EXIT_OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["cell_count"] == 12
    assert payload["cells"][0] == {"os": "macos-latest", "python_version": "3.10"}
    assert payload["findings"] == []


def test_main_can_list_the_cells(capsys: pytest.CaptureFixture[str]):
    assert ci_matrix.main(["--list-cells"]) == ci_matrix.EXIT_OK
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]

    assert len(lines) == 12
    assert "windows-latest · Python 3.13" in lines


def test_main_strict_promotes_warnings(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    import yaml

    matrix = dict(GOOD_MATRIX)
    matrix["python-version"] = GOOD_MATRIX["python-version"] + ["3.14"]
    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(make_workflow(matrix)), encoding="utf-8")

    assert ci_matrix.main(["--workflow", str(path), "--no-pyproject"]) == ci_matrix.EXIT_OK
    capsys.readouterr()
    assert ci_matrix.main(["--workflow", str(path), "--no-pyproject", "--strict"]) == (
        ci_matrix.EXIT_FINDINGS
    )
    assert "WARN" in capsys.readouterr().out


def test_main_require_python_demands_an_extra_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    import yaml

    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(make_workflow()), encoding="utf-8")

    code = ci_matrix.main(["--workflow", str(path), "--no-pyproject", "--require-python", "3.14"])

    assert code == ci_matrix.EXIT_FINDINGS
    assert "3.14" in capsys.readouterr().out


def test_main_require_os_demands_an_extra_runner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    import yaml

    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(make_workflow()), encoding="utf-8")

    code = ci_matrix.main(
        ["--workflow", str(path), "--no-pyproject", "--require-os", "ubuntu-24.04"]
    )

    assert code == ci_matrix.EXIT_FINDINGS
    assert "ubuntu-24.04" in capsys.readouterr().out


def test_main_skips_the_pyproject_cross_check_on_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    import yaml

    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(make_workflow()), encoding="utf-8")
    declared = tmp_path / "pyproject.toml"
    declared.write_text(
        "[project]\n"
        'requires-python = ">=3.9"\n'
        'classifiers = ["Programming Language :: Python :: 3.9"]\n',
        encoding="utf-8",
    )

    assert ci_matrix.main(["--workflow", str(path), "--pyproject", str(declared)]) == (
        ci_matrix.EXIT_FINDINGS
    )
    capsys.readouterr()
    assert (
        ci_matrix.main(["--workflow", str(path), "--pyproject", str(declared), "--no-pyproject"])
        == ci_matrix.EXIT_OK
    )


def test_main_returns_unmeasurable_for_a_broken_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    import yaml

    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(make_workflow()), encoding="utf-8")
    broken = tmp_path / "pyproject.toml"
    broken.write_text("[project\n", encoding="utf-8")

    assert ci_matrix.main(["--workflow", str(path), "--pyproject", str(broken)]) == (
        ci_matrix.EXIT_UNMEASURABLE
    )
    assert "cannot read" in capsys.readouterr().err


# --- reporting -------------------------------------------------------------
def test_render_report_names_the_cells_and_the_verdict():
    report = ci_matrix.render_report(audit(make_workflow()))

    assert "cells: 12" in report
    assert "windows-latest · Python 3.13" in report
    assert report.rstrip().endswith("PASS  12 cells, 0 warning(s)")


def test_render_report_marks_warnings():
    matrix = dict(GOOD_MATRIX)
    matrix["python-version"] = GOOD_MATRIX["python-version"] + ["3.14"]

    report = ci_matrix.render_report(audit(make_workflow(matrix)))

    assert "[WARN] matrix.unexpected-cell" in report


def test_verdict_to_dict_is_serialisable():
    verdict = audit(make_workflow(fail_fast=True))
    payload = json.loads(json.dumps(verdict.to_dict()))

    assert payload["ok"] is False
    assert payload["errors"] == 1
    assert payload["findings"][0]["severity"] == "error"


def test_verdict_without_cells_is_never_ok():
    verdict = ci_matrix.AuditVerdict(workflow_path="x.yml")

    assert verdict.cells == ()
    assert verdict.ok is False


def test_version_key_sorts_numerically():
    assert ci_matrix._version_key("3.10") > ci_matrix._version_key("3.9")
    assert ci_matrix._version_key("3.10") == (3, 10)
