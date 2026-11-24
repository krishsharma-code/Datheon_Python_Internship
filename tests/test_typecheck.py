"""Tests for the Day 31 static type gate (``tools/typecheck.py``).

A gate is only worth its exit code if it fails when it should and refuses to
pass when it cannot measure. Both halves are proved here without depending on
today's tree being type-clean:

* the **parser** is fed mypy's real output shapes — a column, no column, a note,
  a summary line, an error code — because everything downstream trusts it;
* the **audit** is fed hand-written ``pyproject.toml`` files and workflows that
  each break one rule, so every finding code is reachable by a test;
* the **phases** run a stub checker (a three-line script that prints what the
  test asked for), so no test needs mypy installed, and none of them type-checks
  the repository twice;
* two integration tests do read the real declaration, because the claim "the
  scope matches what ships" is worth exactly as much as its weakest run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import typecheck  # noqa: E402  (path set up above)


# --- helpers --------------------------------------------------------------
def stub_checker(tmp_path: Path, stdout: str = "", exit_code: int = 0) -> List[str]:
    """Write a fake ``mypy`` that prints ``stdout`` and exits with ``exit_code``.

    Args:
        tmp_path: Where to write the stub.
        stdout: Exactly what the stub should print.
        exit_code: The status the stub should exit with.

    Returns:
        The command prefix, ready for :func:`typecheck.run_phase`.
    """
    script = tmp_path / "fake_mypy.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        "sys.stderr.write('')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def write_pyproject(
    path: Path,
    *,
    files: Sequence[str] = ("caching.py", "datheon"),
    strict: bool = True,
    python_version: str = "3.10",
    requires_python: str = ">=3.10",
    py_modules: Sequence[str] = ("caching",),
    packages: Sequence[str] = ("datheon*", "projects*"),
) -> Path:
    """Write a minimal ``pyproject.toml`` with the tables the gate reads.

    Args:
        path: The file to write.
        files: ``[tool.mypy] files``.
        strict: ``[tool.mypy] strict``.
        python_version: ``[tool.mypy] python_version``.
        requires_python: ``[project] requires-python``.
        py_modules: ``[tool.setuptools] py-modules``.
        packages: ``[tool.setuptools.packages.find] include``.

    Returns:
        ``path``, for chaining.
    """
    files_toml = ", ".join(json.dumps(entry) for entry in files)
    modules_toml = ", ".join(json.dumps(entry) for entry in py_modules)
    packages_toml = ", ".join(json.dumps(entry) for entry in packages)
    path.write_text(
        "[project]\n"
        f'name = "fixture"\nrequires-python = "{requires_python}"\n'
        "\n[tool.setuptools]\n"
        f"py-modules = [{modules_toml}]\n"
        "\n[tool.setuptools.packages.find]\n"
        f"include = [{packages_toml}]\n"
        "\n[tool.mypy]\n"
        f"strict = {str(strict).lower()}\n"
        f'python_version = "{python_version}"\n'
        f"files = [{files_toml}]\n",
        encoding="utf-8",
    )
    return path


def repo_root(tmp_path: Path, *, with_py_typed: bool = True, with_projects: bool = True) -> Path:
    """Build a miniature repository the audit can inspect.

    Args:
        tmp_path: The directory to build in.
        with_py_typed: Whether to create ``datheon/py.typed``.
        with_projects: Whether to create the ``projects/`` directory the
            per-project phases enumerate.

    Returns:
        The miniature root.
    """
    root = tmp_path / "repo"
    (root / "datheon").mkdir(parents=True)
    if with_projects:
        (root / "projects").mkdir()
    (root / "datheon" / "__init__.py").write_text("", encoding="utf-8")
    if with_py_typed:
        (root / "datheon" / "py.typed").write_text("", encoding="utf-8")
    (root / "caching.py").write_text("", encoding="utf-8")
    workflow = root / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text(
        "jobs:\n  type-check:\n    steps:\n      - run: python tools/typecheck.py\n",
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("mypy>=1.11\n", encoding="utf-8")
    return root


# --- parsing mypy's output ------------------------------------------------
def test_parse_output_reads_an_error_with_a_column_and_a_code():
    text = "caching.py:439:16: error: Returning Any from function  [no-any-return]\n"

    (diagnostic,) = typecheck.parse_output(text)

    assert diagnostic.path == "caching.py"
    assert (diagnostic.line, diagnostic.column) == (439, 16)
    assert diagnostic.severity == "error"
    assert diagnostic.message == "Returning Any from function"
    assert diagnostic.code == "no-any-return"


def test_parse_output_reads_an_error_without_a_column():
    (diagnostic,) = typecheck.parse_output("errors.py: error: Source file found twice\n")

    assert diagnostic.column == 0
    assert diagnostic.location == "errors.py"
    assert diagnostic.code == ""


def test_parse_output_keeps_notes_because_they_explain_errors():
    text = (
        'main.py:82:5: note: Revealed type is "builtins.str"\n'
        "main.py:82:5: error: bad return  [return-value]\n"
    )

    diagnostics = typecheck.parse_output(text)

    assert [diagnostic.severity for diagnostic in diagnostics] == ["note", "error"]
    assert diagnostics[0].code == ""


def test_parse_output_drops_summary_and_blank_lines():
    text = (
        "Found 2 errors in 1 file (checked 3 source files)\n"
        "\n"
        "Success: no issues found in 21 source files\n"
        "this line is not a diagnostic at all\n"
    )

    assert typecheck.parse_output(text) == ()


def test_parse_output_keeps_brackets_that_are_not_an_error_code():
    (diagnostic,) = typecheck.parse_output('x.py:1:1: error: Missing "["  [syntax]\n')

    assert diagnostic.code == "syntax"
    assert diagnostic.message == 'Missing "["'


def test_diagnostic_render_includes_the_code():
    diagnostic = typecheck.Diagnostic("a.py", 3, 7, "error", "boom", "arg-type")

    assert diagnostic.render() == "a.py:3:7: error: boom [arg-type]"
    assert diagnostic.is_error is True


def test_error_summary_returns_the_last_summary_line():
    text = "a.py:1:1: error: x  [misc]\nFound 1 error in 1 file (checked 1 source file)\n"

    assert typecheck.error_summary(text).startswith("Found 1 error")


def test_error_summary_is_empty_when_mypy_printed_no_summary():
    assert typecheck.error_summary("") == ""
    assert typecheck.error_summary("a.py:1:1: error: x  [misc]") == ""


# --- names, paths and the declared scope ----------------------------------
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("01_hangman_game", "project_01_hangman_game"),
        ("hangman", "hangman"),
        ("03-file-organizer", "project_03_file_organizer"),
        ("", "project_"),
    ],
)
def test_sanitise_project_name_makes_an_identifier(name: str, expected: str):
    assert typecheck.sanitise_project_name(name) == expected


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("caching.py", "caching"),
        ("datheon", "datheon"),
        ("projects*", "projects"),
        ("datheon/", "datheon"),
    ],
)
def test_entry_stem_names_the_module_or_package(entry: str, expected: str):
    assert typecheck.entry_stem(entry) == expected


@pytest.mark.parametrize(
    ("specifier", "expected"),
    [(">=3.10", "3.10"), (">=3.10,<4", "3.10"), ("~=3.11", ""), ("", "")],
)
def test_requires_python_floor_reads_the_lower_bound(specifier: str, expected: str):
    assert typecheck.requires_python_floor(specifier) == expected


def test_load_config_reads_the_real_declaration():
    config = typecheck.load_config()

    assert config.strict is True
    assert config.python_version == "3.10"
    assert "datheon" in config.files
    assert "caching.py" in config.files
    assert config.shipped_modules, "py-modules must be declared"
    assert "projects*" in config.shipped_packages


def test_load_config_reads_a_synthetic_declaration(tmp_path: Path):
    path = write_pyproject(tmp_path / "pyproject.toml", files=("a.py",), strict=False)

    config = typecheck.load_config(path)

    assert config.files == ("a.py",)
    assert config.strict is False
    assert config.shipped_modules == ("caching",)


def test_load_config_tolerates_a_file_with_no_mypy_table(tmp_path: Path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "x"\n', encoding="utf-8")

    config = typecheck.load_config(path)

    assert config.files == ()
    assert config.strict is False
    assert config.python_version == ""


def test_load_config_reports_an_unreadable_file(tmp_path: Path):
    with pytest.raises(ValueError):
        typecheck.load_config(tmp_path / "missing.toml")


def test_load_config_reports_invalid_toml(tmp_path: Path):
    path = tmp_path / "pyproject.toml"
    path.write_text("this is not toml = = =\n", encoding="utf-8")

    with pytest.raises(ValueError):
        typecheck.load_config(path)


def test_project_directories_finds_the_five_task_packs():
    directories = typecheck.project_directories()

    names = [directory.name for directory in directories]
    assert names == sorted(names)
    assert len(names) == 5
    assert names[0] == "01_hangman_game"
    assert "__pycache__" not in names


def test_project_directories_is_empty_without_a_projects_dir(tmp_path: Path):
    assert typecheck.project_directories(tmp_path / "nope") == ()


def test_python_files_lists_only_top_level_modules(tmp_path: Path):
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "c.py").write_text("", encoding="utf-8")

    assert [path.name for path in typecheck.python_files(tmp_path)] == ["a.py", "b.py"]


# --- the shadow tree ------------------------------------------------------
def test_build_shadow_tree_flattens_the_runtime_import_graph(tmp_path: Path):
    project = tmp_path / "projects" / "01_hangman_game"
    project.mkdir(parents=True)
    (project / "hangman.py").write_text("", encoding="utf-8")
    (project / "main.py").write_text("", encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "words.txt").write_text("word\n", encoding="utf-8")

    root = tmp_path / "root"
    root.mkdir()
    (root / "caching.py").write_text("", encoding="utf-8")
    (root / "errors.py").write_text("", encoding="utf-8")

    shadow = typecheck.build_shadow_tree(project, tmp_path / "shadow", root)

    assert shadow == tmp_path / "shadow"
    assert sorted(path.name for path in shadow.iterdir()) == [
        "caching.py",
        "errors.py",
        "hangman.py",
        "main.py",
    ]


def test_build_shadow_tree_creates_a_missing_destination(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()

    shadow = typecheck.build_shadow_tree(project, tmp_path / "deep" / "shadow", root)

    assert (shadow / "main.py").is_file()


# --- running a phase ------------------------------------------------------
def test_build_command_pins_the_config_and_package_bases(tmp_path: Path):
    command = typecheck.build_command(
        ["a.py"],
        tmp_path / "pyproject.toml",
        [sys.executable, "-m", "mypy"],
        explicit_package_bases=True,
    )

    assert command[:3] == [sys.executable, "-m", "mypy"]
    assert "--config-file" in command
    assert "--explicit-package-bases" in command
    assert command[-1] == "a.py"


def test_build_command_without_a_config_file_leaves_mypy_to_search(tmp_path: Path):
    command = typecheck.build_command(["a.py"], None, ["mypy"])

    assert "--config-file" not in command
    assert command == ["mypy", "a.py"]


def test_run_phase_reports_a_clean_verdict(tmp_path: Path):
    executable = stub_checker(tmp_path, "Success: no issues found in 2 source files\n")

    phase = typecheck.run_phase("core", ["a.py"], tmp_path, None, executable)

    assert phase.exit_code == 0
    assert phase.clean is True
    assert phase.measured is True


def test_run_phase_collects_errors_from_a_failing_check(tmp_path: Path):
    output = (
        "a.py:4:1: error: Returning Any  [no-any-return]\n"
        "a.py:9:2: error: Missing type arguments  [type-arg]\n"
        "Found 2 errors in 1 file (checked 1 source file)\n"
    )
    executable = stub_checker(tmp_path, output, exit_code=1)

    phase = typecheck.run_phase("core", ["a.py"], tmp_path, None, executable)

    assert phase.exit_code == 1
    assert phase.clean is False
    assert len(phase.errors) == 2
    assert phase.measured is True


def test_run_phase_treats_a_crashing_checker_as_unmeasurable(tmp_path: Path):
    executable = stub_checker(tmp_path, "mypy: error: cannot find module\n", exit_code=2)

    phase = typecheck.run_phase("core", ["a.py"], tmp_path, None, executable)

    assert phase.exit_code == 2
    assert phase.measured is False
    assert phase.clean is False


def test_run_phase_survives_a_missing_executable(tmp_path: Path):
    phase = typecheck.run_phase("core", ["a.py"], tmp_path, None, ["definitely-not-mypy"])

    assert phase.exit_code == typecheck.EXIT_UNMEASURABLE
    assert phase.measured is False
    assert "could not run mypy" in phase.stderr


def test_mypy_available_is_false_without_a_checker(tmp_path: Path):
    assert typecheck.mypy_available(["definitely-not-mypy"]) is False


def test_mypy_available_is_true_for_a_working_checker(tmp_path: Path):
    assert typecheck.mypy_available(stub_checker(tmp_path, "mypy 1.11.2\n")) is True


def test_mypy_version_reads_the_first_line(tmp_path: Path):
    executable = stub_checker(tmp_path, "mypy 1.11.2 (compiled: yes)\nextra\n")

    assert typecheck.mypy_version(executable) == "mypy 1.11.2 (compiled: yes)"


def test_mypy_version_is_unknown_without_a_checker(tmp_path: Path):
    assert typecheck.mypy_version(["definitely-not-mypy"]) == "unknown"


# --- the verdict ----------------------------------------------------------
def test_evaluate_passes_when_every_phase_is_clean():
    phase = typecheck.PhaseResult("core", ("a.py",), 0)

    verdict = typecheck.evaluate([phase], [])

    assert verdict.passed is True
    assert verdict.errors == ()
    assert verdict.unmeasurable is False


def test_evaluate_fails_on_a_declaration_finding():
    phase = typecheck.PhaseResult("core", ("a.py",), 0)
    finding = typecheck.Finding("typecheck.strict", "pyproject.toml", "not strict")

    verdict = typecheck.evaluate([phase], [finding])

    assert verdict.passed is False
    assert verdict.findings == (finding,)


def test_evaluate_fails_on_a_type_error():
    diagnostic = typecheck.Diagnostic("a.py", 1, 1, "error", "boom", "misc")
    phase = typecheck.PhaseResult("core", ("a.py",), 1, (diagnostic,))

    verdict = typecheck.evaluate([phase], [])

    assert verdict.passed is False
    assert verdict.errors == (diagnostic,)


def test_evaluate_marks_a_failed_phase_as_unmeasurable():
    phase = typecheck.PhaseResult("core", ("a.py",), 2, stderr="boom")

    verdict = typecheck.evaluate([phase], [])

    assert verdict.unmeasurable is True
    assert verdict.passed is False


def test_phase_result_separates_warnings_from_errors():
    diagnostics = (
        typecheck.Diagnostic("a.py", 1, 1, "warning", "w", "misc"),
        typecheck.Diagnostic("a.py", 2, 1, "error", "e", "misc"),
    )
    phase = typecheck.PhaseResult("core", ("a.py",), 1, diagnostics)

    assert len(phase.warnings) == 1
    assert len(phase.errors) == 1


# --- auditing the declaration ---------------------------------------------
def test_audit_config_accepts_the_real_declaration():
    assert typecheck.audit_config(typecheck.load_config()) == []


def test_audit_config_accepts_a_consistent_fixture(tmp_path: Path):
    root = repo_root(tmp_path)
    config = typecheck.load_config(
        write_pyproject(tmp_path / "pyproject.toml", py_modules=("caching",))
    )

    assert typecheck.audit_config(config, root) == []


def test_audit_config_flags_a_missing_scope(tmp_path: Path):
    root = repo_root(tmp_path)
    config = typecheck.load_config(write_pyproject(tmp_path / "p.toml", files=()))

    codes = [finding.code for finding in typecheck.audit_config(config, root)]

    assert "typecheck.config" in codes


def test_audit_config_flags_a_non_strict_declaration(tmp_path: Path):
    root = repo_root(tmp_path)
    config = typecheck.load_config(write_pyproject(tmp_path / "p.toml", strict=False))

    codes = [finding.code for finding in typecheck.audit_config(config, root)]

    assert "typecheck.strict" in codes


def test_audit_config_flags_a_missing_python_version(tmp_path: Path):
    root = repo_root(tmp_path)
    config = typecheck.load_config(write_pyproject(tmp_path / "p.toml", python_version=""))

    codes = [finding.code for finding in typecheck.audit_config(config, root)]

    assert "typecheck.python" in codes


def test_audit_config_flags_a_python_version_that_drifts_from_the_floor(tmp_path: Path):
    root = repo_root(tmp_path)
    config = typecheck.load_config(
        write_pyproject(tmp_path / "p.toml", python_version="3.9", requires_python=">=3.10")
    )

    codes = [finding.code for finding in typecheck.audit_config(config, root)]

    assert "typecheck.python" in codes


def test_audit_config_flags_a_shipped_module_outside_the_scope(tmp_path: Path):
    root = repo_root(tmp_path)
    config = typecheck.load_config(
        write_pyproject(tmp_path / "p.toml", files=("datheon",), py_modules=("caching", "errors"))
    )

    findings = typecheck.audit_config(config, root)

    subjects = [finding.subject for finding in findings if finding.code == "typecheck.scope"]
    assert subjects == ["caching", "errors"]


def test_audit_config_flags_a_missing_py_typed_marker(tmp_path: Path):
    root = repo_root(tmp_path, with_py_typed=False)
    config = typecheck.load_config(write_pyproject(tmp_path / "p.toml"))

    codes = [finding.code for finding in typecheck.audit_config(config, root)]

    assert "typecheck.pytyped" in codes


def test_audit_config_flags_a_shipped_projects_package_without_a_directory(tmp_path: Path):
    root = repo_root(tmp_path, with_projects=False)
    config = typecheck.load_config(
        write_pyproject(tmp_path / "p.toml", packages=("datheon*", "projects*"))
    )

    codes = [finding.code for finding in typecheck.audit_config(config, root)]

    assert "typecheck.scope" in codes


def test_audit_ci_accepts_the_real_workflow():
    assert typecheck.audit_ci() == []


def test_audit_ci_flags_a_workflow_without_the_gate(tmp_path: Path):
    workflow = tmp_path / "ci.yml"
    workflow.write_text("jobs:\n  lint:\n    steps:\n      - run: flake8 .\n", encoding="utf-8")

    findings = typecheck.audit_ci(tmp_path, workflow)

    assert [finding.code for finding in findings] == ["typecheck.ci"]


def test_audit_ci_flags_a_missing_workflow(tmp_path: Path):
    findings = typecheck.audit_ci(tmp_path, tmp_path / "nope.yml")

    assert [finding.code for finding in findings] == ["typecheck.ci"]


def test_audit_requirements_accepts_the_real_file():
    assert typecheck.audit_requirements() == []


def test_audit_requirements_flags_a_missing_mypy(tmp_path: Path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pytest>=7.4\n# mypy>=1.11\n", encoding="utf-8")

    findings = typecheck.audit_requirements(tmp_path, requirements)

    assert [finding.code for finding in findings] == ["typecheck.requirement"]


def test_audit_requirements_accepts_a_pinned_mypy(tmp_path: Path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("mypy==1.11.2\n", encoding="utf-8")

    assert typecheck.audit_requirements(tmp_path, requirements) == []


def test_audit_requirements_flags_a_missing_file(tmp_path: Path):
    findings = typecheck.audit_requirements(tmp_path, tmp_path / "nope.txt")

    assert [finding.code for finding in findings] == ["typecheck.requirement"]


# --- rendering ------------------------------------------------------------
def test_render_report_announces_a_pass():
    verdict = typecheck.evaluate([typecheck.PhaseResult("core", ("a.py",), 0)], [])

    report = typecheck.render_report(verdict)

    assert "PASS" in report
    assert "core" in report
    assert "errors   : 0" in report


def test_render_report_lists_findings_and_errors():
    diagnostic = typecheck.Diagnostic("a.py", 1, 2, "error", "boom", "misc")
    phase = typecheck.PhaseResult("core", ("a.py",), 1, (diagnostic,))
    finding = typecheck.Finding("typecheck.strict", "pyproject.toml", "not strict")
    verdict = typecheck.evaluate([phase], [finding])

    report = typecheck.render_report(verdict)

    assert "FAIL" in report
    assert "a.py:1:2: error: boom [misc]" in report
    assert "typecheck.strict" in report


def test_render_report_hides_many_diagnostics_unless_verbose():
    diagnostics = tuple(
        typecheck.Diagnostic("a.py", number, 1, "error", "boom", "misc") for number in range(1, 40)
    )
    verdict = typecheck.evaluate([typecheck.PhaseResult("core", ("a.py",), 1, diagnostics)], [])

    assert "a.py:39:1" not in typecheck.render_report(verdict)
    assert "a.py:39:1" in typecheck.render_report(verdict, verbose=True)


def test_verdict_to_dict_is_json_serialisable():
    diagnostic = typecheck.Diagnostic("a.py", 1, 2, "error", "boom", "misc")
    phase = typecheck.PhaseResult("core", ("a.py",), 1, (diagnostic,))
    verdict = typecheck.evaluate([phase], [])

    payload = typecheck.verdict_to_dict(verdict)

    assert json.loads(json.dumps(payload))["passed"] is False
    assert payload["diagnostics"][0]["code"] == "misc"
    assert payload["phases"][0]["name"] == "core"


# --- the gate as a whole --------------------------------------------------
def test_run_gate_passes_with_a_clean_checker(tmp_path: Path):
    config = write_pyproject(
        tmp_path / "pyproject.toml", files=("caching.py", "datheon"), py_modules=("caching",)
    )
    root = repo_root(tmp_path)
    options = typecheck.RunOptions(
        config_file=config,
        core_only=True,
        executable=stub_checker(tmp_path, "Success: no issues found\n"),
    )

    verdict = typecheck.run_gate(options, root)

    assert verdict.passed is True
    assert [phase.name for phase in verdict.phases] == ["core"]


def test_run_gate_reports_a_failing_core_phase(tmp_path: Path):
    config = write_pyproject(
        tmp_path / "pyproject.toml", files=("caching.py", "datheon"), py_modules=("caching",)
    )
    root = repo_root(tmp_path)
    options = typecheck.RunOptions(
        config_file=config,
        core_only=True,
        executable=stub_checker(tmp_path, "caching.py:1:1: error: boom  [misc]\n", exit_code=1),
    )

    verdict = typecheck.run_gate(options, root)

    assert verdict.passed is False
    assert len(verdict.errors) == 1


def test_run_gate_checks_every_project_from_a_shadow_tree(tmp_path: Path):
    root = repo_root(tmp_path)
    for name in ("01_hangman_game", "02_portfolio_tracker"):
        project = root / "projects" / name
        project.mkdir(parents=True)
        (project / "main.py").write_text("", encoding="utf-8")
    config = write_pyproject(
        tmp_path / "pyproject.toml", files=("caching.py", "datheon"), py_modules=("caching",)
    )
    options = typecheck.RunOptions(
        config_file=config,
        executable=stub_checker(tmp_path, "Success: no issues found\n"),
    )

    verdict = typecheck.run_gate(options, root)

    assert [phase.name for phase in verdict.phases] == [
        "core",
        "01_hangman_game",
        "02_portfolio_tracker",
    ]
    assert all(phase.clean for phase in verdict.phases)


def test_run_gate_keeps_the_shadow_tree_when_asked(tmp_path: Path):
    root = repo_root(tmp_path)
    project = root / "projects" / "01_hangman_game"
    project.mkdir(parents=True)
    (project / "main.py").write_text("", encoding="utf-8")
    shadow_root = tmp_path / "shadows"
    config = write_pyproject(
        tmp_path / "pyproject.toml", files=("caching.py", "datheon"), py_modules=("caching",)
    )
    options = typecheck.RunOptions(
        config_file=config,
        keep_shadow=True,
        shadow_root=shadow_root,
        executable=stub_checker(tmp_path, "Success: no issues found\n"),
    )

    typecheck.run_gate(options, root)

    assert (shadow_root / "project_01_hangman_game" / "main.py").is_file()


def test_run_gate_reports_an_unreadable_declaration(tmp_path: Path):
    options = typecheck.RunOptions(config_file=tmp_path / "nope.toml")

    verdict = typecheck.run_gate(options, repo_root(tmp_path))

    assert verdict.passed is False
    assert verdict.phases == ()
    assert verdict.findings[0].code == "typecheck.config"


# --- the command line -----------------------------------------------------
def test_main_refuses_to_pass_without_mypy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(typecheck, "mypy_available", lambda *args: False)

    assert typecheck.main([]) == typecheck.EXIT_UNMEASURABLE


def test_main_returns_zero_on_a_clean_verdict(monkeypatch: pytest.MonkeyPatch, capsys):
    verdict = typecheck.evaluate([typecheck.PhaseResult("core", ("a.py",), 0)], [])
    monkeypatch.setattr(typecheck, "mypy_available", lambda *args: True)
    monkeypatch.setattr(typecheck, "run_gate", lambda options: verdict)

    assert typecheck.main([]) == typecheck.EXIT_CLEAN
    assert "PASS" in capsys.readouterr().out


def test_main_returns_one_on_a_finding(monkeypatch: pytest.MonkeyPatch, capsys):
    finding = typecheck.Finding("typecheck.ci", "ci.yml", "not wired up")
    verdict = typecheck.evaluate([typecheck.PhaseResult("core", ("a.py",), 0)], [finding])
    monkeypatch.setattr(typecheck, "mypy_available", lambda *args: True)
    monkeypatch.setattr(typecheck, "run_gate", lambda options: verdict)

    assert typecheck.main([]) == typecheck.EXIT_FINDINGS
    assert "typecheck.ci" in capsys.readouterr().err


def test_main_returns_two_when_a_phase_could_not_measure(monkeypatch: pytest.MonkeyPatch, capsys):
    phase = typecheck.PhaseResult("core", ("a.py",), 2, stderr="mypy crashed")
    verdict = typecheck.evaluate([phase], [])
    monkeypatch.setattr(typecheck, "mypy_available", lambda *args: True)
    monkeypatch.setattr(typecheck, "run_gate", lambda options: verdict)

    assert typecheck.main([]) == typecheck.EXIT_UNMEASURABLE
    assert "no verdict" in capsys.readouterr().err


def test_main_emits_json_when_asked(monkeypatch: pytest.MonkeyPatch, capsys):
    verdict = typecheck.evaluate([typecheck.PhaseResult("core", ("a.py",), 0)], [])
    monkeypatch.setattr(typecheck, "mypy_available", lambda *args: True)
    monkeypatch.setattr(typecheck, "run_gate", lambda options: verdict)

    assert typecheck.main(["--json"]) == typecheck.EXIT_CLEAN
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_main_passes_the_flags_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    captured = {}
    verdict = typecheck.evaluate([typecheck.PhaseResult("core", ("a.py",), 0)], [])

    def fake_run_gate(options: typecheck.RunOptions) -> typecheck.Verdict:
        captured["options"] = options
        return verdict

    monkeypatch.setattr(typecheck, "mypy_available", lambda *args: True)
    monkeypatch.setattr(typecheck, "run_gate", fake_run_gate)

    typecheck.main(["--core-only", "--keep-shadow", "--shadow-root", str(tmp_path)])

    options = captured["options"]
    assert options.core_only is True
    assert options.keep_shadow is True
    assert options.shadow_root == tmp_path
