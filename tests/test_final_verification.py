"""Tests for the Day 28 submission verification (``tools/final_verification.py``).

Two things have to be proven separately, as for every gate in this repository:
that each finding *fires* when the defect is present, and that the committed
tree currently passes. The first half runs against a synthetic repository built
in ``tmp_path`` — small enough to read, complete enough that every audit finds
something to approve — and the second runs the real thing with ``--static``, so
the suite never shells out to pytest from inside pytest.

The live checks are exercised through an injected runner rather than by running
commands: what is under test is the *grading* of an exit code, and a test that
starts ``flake8`` to check that ``1`` means fail would be measuring the wrong
thing slowly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import final_verification as fv  # noqa: E402  (path set up above)

#: A ledger with one completed day, and the log entry that goes with it.
PLAN_TEXT = """# Plan

## 1. Progress Ledger (authoritative)

| Day | Target date | Theme | Branch | Deliverable | Status | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-10-21 | Scaffold | `feat/day-1` | Repo and tests | done | #1 |

---

## 2. Week-by-week detail

| Day | Objective | Key work |
| --- | --- | --- |
| 1 | Scaffold | Repo |

---

## 4. Final submission deliverables checklist

- [ ] Public repository named **`Datheon_Python_Internship`**.
- [ ] `README.md` with installation.
- [ ] `LICENSE` (MIT).
- [ ] `CONTRIBUTING.md` and a strict `.gitignore`.
- [ ] `CHANGELOG.md` present.

---

## 5. Backlog

- Add a `SECURITY.md` and a `CODE_OF_CONDUCT.md`.
"""

#: A pyproject that satisfies every metadata assertion.
PYPROJECT = """[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "datheon-python-internship"
version = "1.1.0"
description = "Five standard-library Python tools."
requires-python = ">=3.10"
license = "MIT"
dependencies = []
classifiers = ["Typing :: Typed"]

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-cov>=4.1",
    "flake8>=6.1",
    "black>=24.1",
    "hypothesis>=6.98",
    "pyyaml>=6.0",
]

[project.scripts]
hangman = "datheon.cli.hangman:main"
portfolio = "datheon.cli.portfolio:main"
organizer = "datheon.cli.organizer:main"
assistant = "datheon.cli.assistant:main"
inventory = "datheon.cli.inventory:main"

[tool.setuptools]
py-modules = [
    "caching",
    "cli_contract",
    "dataset_loader",
    "edge_cases",
    "error_catalogue",
    "errors",
    "fuzzing",
    "logging_config",
    "observability",
    "persistence",
    "profiling",
    "structured_logging",
    "validators",
]

[tool.setuptools.package-data]
"*" = ["data/*.txt", "py.typed"]
"""

#: An ignore file that covers every artefact the audit requires.
GITIGNORE = "\n".join(("__pycache__/", *fv.IGNORED_PATTERNS)) + "\n"

#: A changelog with both headings the audit wants.
CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Something.

## [1.1.0] - 2026-11-14

### Added

- The release.
"""

#: A licence that grants the right terms to the right person.
LICENCE = """MIT License

Copyright (c) 2026 Krish Sharma

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software.
"""

#: A log with the entry for the one completed day.
LEARNING_LOG = """# Daily Learning Log

## Day 1 — Repository scaffold

**Branch:** `feat/day-1`
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


def _make_repo(root: Path) -> Path:
    """Build a small repository that passes every static check.

    Args:
        root: Directory to build in.

    Returns:
        The repository root.
    """
    _write(root / "pyproject.toml", PYPROJECT)
    _write(root / "README.md", "# Datheon\n")
    _write(root / "LICENSE", LICENCE)
    _write(root / "CONTRIBUTING.md", "# Contributing\n")
    _write(root / "CHANGELOG.md", CHANGELOG)
    _write(root / ".gitignore", GITIGNORE)
    _write(root / ".gitattributes", "* text=auto eol=lf\n")
    _write(root / "mkdocs.yml", "site_name: Datheon\n")
    _write(root / "requirements.txt", "-e .\n")
    _write(root / "setup.cfg", "[flake8]\nmax-line-length = 100\n")
    _write(root / "conftest.py", "")
    _write(root / "datheon/py.typed", "")
    _write(root / "datheon/cli/_registry.py", "")
    for name in fv.CONSOLE_SCRIPTS:
        _write(root / "datheon" / "cli" / f"{name}.py", "def main() -> int:\n    return 0\n")
    for rel in ("docs/index.md", "docs/daily_learning_log.md"):
        _write(root / rel, LEARNING_LOG if "learning" in rel else "# Docs\n")
    _write(root / "docs/internship_milestones_plan.md", PLAN_TEXT)
    _write(root / "docs/assets/architecture.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
    _write(root / "docs/assets/demo/quickstart.gif", "GIF89a")
    _write(root / "docs/assets/demo/transcript.json", '{"sessions": []}\n')
    for name in ("ci", "docs", "mutation", "release"):
        _write(root / ".github" / "workflows" / f"{name}.yml", "name: workflow\n")
    return root


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository that passes the static audit.

    Args:
        tmp_path: Pytest's temporary directory.

    Returns:
        The repository root.
    """
    return _make_repo(tmp_path / "repo")


def _freeze(repo: Path) -> Path:
    """Cut a freeze manifest for a repository.

    Args:
        repo: Repository root.

    Returns:
        The manifest path.
    """
    manifest = fv.compute_manifest(repo, "1.1.0", "2026-11-17")
    return fv.write_manifest(repo, manifest)


def _codes(results: list) -> list:
    """Collect the finding codes from a list of results.

    Args:
        results: Results to read.

    Returns:
        The codes, in order.
    """
    return [result.code for result in results]


# ---------------------------------------------------------------------------
# Files, licence, metadata
# ---------------------------------------------------------------------------


def test_complete_repository_passes_every_static_check(repo: Path) -> None:
    """A repository with nothing wrong produces no finding."""
    _freeze(repo)
    report = fv.verify(repo, run_commands=False)
    assert report.verdict() == "pass"
    assert [r.detail for r in report.results if r.status != "pass"] == []


def test_missing_required_file_is_reported(repo: Path) -> None:
    """A missing README is a finding, not a pass."""
    (repo / "README.md").unlink()
    results = fv.audit_files(repo)
    assert _codes(results) == ["submission.file"]
    assert "README.md" in results[0].detail


def test_licence_must_name_the_intern_and_the_year(repo: Path) -> None:
    """The grant, the year and the holder are all checked."""
    _write(repo / "LICENSE", "MIT License\n\nCopyright (c) 2025 Someone Else\n")
    details = [result.detail for result in fv.audit_licence(repo)]
    assert any("2026" in detail for detail in details)
    assert any("Krish Sharma" in detail for detail in details)


def test_licence_absent_is_a_finding(tmp_path: Path) -> None:
    """No licence file at all fails the check."""
    assert _codes(fv.audit_licence(tmp_path)) == ["submission.licence"]


def test_metadata_agrees_with_what_ships(repo: Path) -> None:
    """The declared scripts, modules and marker are all asserted."""
    assert fv.audit_metadata(repo)[0].status == "pass"


def test_metadata_catches_a_script_pointing_nowhere(repo: Path) -> None:
    """A console script with the wrong target cannot install."""
    _write(repo / "pyproject.toml", PYPROJECT.replace("hangman:main", "hangman:run"))
    results = fv.audit_metadata(repo)
    assert _codes(results) == ["submission.metadata"]
    assert "hangman" in results[0].detail


def test_metadata_catches_a_runtime_dependency(repo: Path) -> None:
    """The five tools are standard-library only; a dependency breaks that."""
    _write(
        repo / "pyproject.toml", PYPROJECT.replace("dependencies = []", 'dependencies = ["rich"]')
    )
    results = fv.audit_metadata(repo)
    assert any("third-party" in result.detail for result in results)


def test_metadata_catches_a_missing_py_typed(repo: Path) -> None:
    """Without the marker the annotations are invisible to a type checker."""
    _write(repo / "pyproject.toml", PYPROJECT.replace('"data/*.txt", "py.typed"', '"data/*.txt"'))
    results = fv.audit_metadata(repo)
    assert any("py.typed" in result.detail for result in results)


def test_metadata_reports_unparseable_toml(tmp_path: Path) -> None:
    """A pyproject that is not TOML is a finding, not a crash."""
    _write(tmp_path / "pyproject.toml", "[project\nname =")
    assert _codes(fv.audit_metadata(tmp_path)) == ["submission.metadata"]


def test_entry_point_without_main_is_a_finding(repo: Path) -> None:
    """A shim that lost its entry function is caught."""
    _write(repo / "datheon/cli/hangman.py", "VALUE = 1\n")
    results = fv.audit_entry_points(repo)
    assert _codes(results) == ["submission.entry-point"]
    assert "main()" in results[0].detail


def test_entry_point_file_absent_is_a_finding(repo: Path) -> None:
    """A console script with no shim at all is caught."""
    (repo / "datheon/cli/inventory.py").unlink()
    assert _codes(fv.audit_entry_points(repo)) == ["submission.entry-point"]


def test_changelog_needs_the_released_version(repo: Path) -> None:
    """The declared version must have its own entry."""
    _write(repo / "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n")
    results = fv.audit_changelog(repo)
    assert _codes(results) == ["submission.changelog"]
    assert "1.1.0" in results[0].detail


def test_changelog_needs_an_unreleased_section(repo: Path) -> None:
    """A changelog with no open section has stopped being maintained."""
    _write(repo / "CHANGELOG.md", "# Changelog\n\n## [1.1.0] - 2026-11-14\n")
    results = fv.audit_changelog(repo)
    assert "Unreleased" in results[0].detail


# ---------------------------------------------------------------------------
# The ledger and the log
# ---------------------------------------------------------------------------


def test_ledger_parses_the_table_and_ignores_the_other_sections(repo: Path) -> None:
    """§2 repeats the day numbers; only §1 is the ledger."""
    rows = fv.parse_ledger((repo / fv.PLAN).read_text(encoding="utf-8"))
    assert [(row.day, row.status, row.pr) for row in rows] == [(1, "done", "#1")]
    assert rows[0].complete


def test_ledger_done_without_a_pr_is_a_finding(repo: Path) -> None:
    """A day counts as complete only with a PR number."""
    _write(repo / fv.PLAN, PLAN_TEXT.replace("| done | #1 |", "| done | — |"))
    results, rows = fv.audit_ledger(repo)
    assert "submission.ledger" in _codes(results)
    assert any(result.status == "fail" and "reads done" in result.detail for result in results)
    assert not rows[0].complete


def test_ledger_with_a_gap_is_a_finding(repo: Path) -> None:
    """Days are contiguous; a gap means a day was skipped."""
    text = PLAN_TEXT.replace(
        "| 1 | 2026-10-21 | Scaffold | `feat/day-1` | Repo and tests | done | #1 |",
        "| 1 | 2026-10-21 | Scaffold | `feat/day-1` | Repo and tests | done | #1 |\n"
        "| 3 | 2026-10-23 | Skip | `feat/day-3` | Something | done | #3 |",
    )
    _write(repo / fv.PLAN, text)
    results, _ = fv.audit_ledger(repo)
    assert any("expected 1..2" in result.detail for result in results)


def test_ledger_open_row_is_a_warning(repo: Path) -> None:
    """An unfinished day is not a defect — but it is not evidence either."""
    _write(
        repo / fv.PLAN,
        PLAN_TEXT.replace(
            "| 1 | 2026-10-21 | Scaffold | `feat/day-1` | Repo and tests | done | #1 |",
            "| 1 | 2026-10-21 | Scaffold | `feat/day-1` | Repo and tests | done | #1 |\n"
            "| 2 | 2026-10-22 | Next | `feat/day-2` | More | pending | — |",
        ),
    )
    results, rows = fv.audit_ledger(repo)
    assert [result.status for result in results] == ["warn"]
    assert [row.day for row in rows] == [1, 2]


def test_ledger_absent_is_a_finding(tmp_path: Path) -> None:
    """No plan, no ledger."""
    results, rows = fv.audit_ledger(tmp_path)
    assert _codes(results) == ["submission.ledger"]
    assert rows == []


def test_ledger_without_rows_is_a_finding(repo: Path) -> None:
    """A plan with no table cannot say what is done."""
    _write(repo / fv.PLAN, "# Plan\n\n## 1. Progress Ledger\n\nNothing here.\n\n## 2. Detail\n")
    results, _ = fv.audit_ledger(repo)
    assert "no rows" in results[0].detail


def test_learning_log_needs_an_entry_per_completed_day(repo: Path) -> None:
    """A merged day with no entry means the log has drifted from the ledger."""
    _write(repo / fv.LEARNING_LOG, "# Daily Learning Log\n")
    results = fv.audit_learning_log(repo, [fv.LedgerRow(1, "feat/day-1", "done", "#1")])
    assert _codes(results) == ["submission.log"]
    assert "Day 1" in results[0].detail


def test_learning_log_absent_is_a_finding(tmp_path: Path) -> None:
    """The log is a deliverable, not a convenience."""
    results = fv.audit_learning_log(tmp_path, [])
    assert _codes(results) == ["submission.log"]


# ---------------------------------------------------------------------------
# The plan's own claims, the ignore file and secrets
# ---------------------------------------------------------------------------


def test_plan_claims_only_count_paths_in_the_checklist() -> None:
    """Branch names and backlog wishes are not file claims."""
    claims = fv.plan_path_claims(PLAN_TEXT)
    assert claims == ["README.md", "LICENSE", "CONTRIBUTING.md", ".gitignore", "CHANGELOG.md"]
    assert "feat/day-1" not in claims
    assert "SECURITY.md" not in claims


def test_plan_naming_a_missing_document_is_a_finding(repo: Path) -> None:
    """The checklist pointed at a file that was never committed."""
    _write(repo / fv.PLAN, PLAN_TEXT.replace("- [ ] `LICENSE` (MIT).", "- [ ] `MISSING.md`."))
    results = fv.audit_plan_documents(repo)
    assert _codes(results) == ["submission.document"]
    assert "MISSING.md" in results[0].detail


def test_plan_documents_resolve(repo: Path) -> None:
    """Every path the checklist names is present."""
    assert fv.audit_plan_documents(repo)[0].status == "pass"


def test_gitignore_must_exclude_build_output(repo: Path) -> None:
    """An unignored artefact is one `git add -A` from the history."""
    _write(repo / ".gitignore", "__pycache__/\n")
    results = fv.audit_gitignore(repo)
    assert set(_codes(results)) == {"submission.artefact"}
    assert any("dist/" in result.detail for result in results)


def test_gitignore_absent_is_a_finding(tmp_path: Path) -> None:
    """No ignore file at all."""
    assert _codes(fv.audit_gitignore(tmp_path)) == ["submission.artefact"]


def test_secrets_scan_finds_a_token_shape(repo: Path) -> None:
    """A GitHub token pasted into a doc is caught."""
    _write(repo / "docs/index.md", "# Docs\n\ntoken: ghp_" + "a" * 20 + "\n")
    results = fv.audit_secrets(repo)
    assert _codes(results) == ["submission.secret"]
    assert "docs/index.md" in results[0].detail


def test_secrets_scan_finds_a_secret_file(repo: Path) -> None:
    """An environment file in the tree is a finding even when ignored."""
    _write(repo / ".env", "TOKEN=abc\n")
    results = fv.audit_secrets(repo)
    assert _codes(results) == ["submission.secret"]
    assert ".env" in results[0].detail


def test_secrets_scan_finds_a_private_key(repo: Path) -> None:
    """A stray PEM file is never an accident worth keeping."""
    _write(repo / "deploy" / "key.pem", "-----BEGIN PRIVATE KEY-----\n")
    results = fv.audit_secrets(repo)
    assert any("key.pem" in result.detail for result in results)


def test_secrets_scan_passes_a_clean_tree(repo: Path) -> None:
    """The fixture repository carries no token shapes."""
    assert fv.audit_secrets(repo)[0].status == "pass"


# ---------------------------------------------------------------------------
# The freeze
# ---------------------------------------------------------------------------


def test_freeze_round_trip(repo: Path) -> None:
    """A manifest cut from the tree verifies against that same tree."""
    target = _freeze(repo)
    manifest = json.loads(target.read_text(encoding="utf-8"))
    assert manifest["schema"] == fv.FREEZE_SCHEMA
    assert manifest["version"] == "1.1.0"
    assert manifest["generated"] == "2026-11-17"
    assert manifest["file_count"] == len(manifest["sha256"])
    assert fv.audit_freeze(repo)[0].status == "pass"


def test_freeze_excludes_the_documents_that_change_every_run(repo: Path) -> None:
    """Pinning the ledger would make the freeze expire as it is written."""
    pinned = fv.pinned_paths(repo)
    assert fv.FREEZE_MUTABLE.isdisjoint(pinned)
    assert "docs/internship_milestones_plan.md" not in pinned
    assert "docs/index.md" in pinned


def test_freeze_detects_an_edited_file(repo: Path) -> None:
    """A pinned file that changed after the freeze is drift."""
    _freeze(repo)
    _write(repo / "docs/index.md", "# Docs, edited\n")
    results = fv.audit_freeze(repo)
    assert _codes(results) == ["submission.freeze"]
    assert "changed since the freeze" in results[0].detail


def test_freeze_detects_an_unpinned_file(repo: Path) -> None:
    """A file added after the freeze is not covered by it."""
    _freeze(repo)
    _write(repo / "tools" / "new_tool.py", "VALUE = 1\n")
    results = fv.audit_freeze(repo)
    assert any("is not pinned" in result.detail for result in results)


def test_freeze_detects_a_deleted_file(repo: Path) -> None:
    """A pinned file that vanished is drift too."""
    _freeze(repo)
    (repo / "docs/index.md").unlink()
    results = fv.audit_freeze(repo)
    assert any("pinned but gone" in result.detail for result in results)


def test_freeze_detects_a_version_bump(repo: Path) -> None:
    """The freeze records a release; a new version needs a new freeze."""
    _freeze(repo)
    _write(repo / "pyproject.toml", PYPROJECT.replace('version = "1.1.0"', 'version = "1.2.0"'))
    results = fv.audit_freeze(repo)
    assert any("1.1.0" in result.detail and "1.2.0" in result.detail for result in results)


def test_freeze_missing_is_a_finding(repo: Path) -> None:
    """No manifest means nothing is frozen."""
    assert _codes(fv.audit_freeze(repo)) == ["submission.freeze"]


def test_freeze_unreadable_is_a_finding(repo: Path) -> None:
    """A manifest that is not JSON cannot be evidence."""
    _write(repo / fv.FREEZE_PATH, "{not json")
    assert _codes(fv.audit_freeze(repo)) == ["submission.freeze"]


def test_freeze_with_the_wrong_shape_is_a_finding(repo: Path) -> None:
    """A JSON document that is not a freeze record is rejected."""
    _write(repo / fv.FREEZE_PATH, json.dumps({"schema": fv.FREEZE_SCHEMA}))
    assert _codes(fv.audit_freeze(repo)) == ["submission.freeze"]


def test_freeze_with_an_old_schema_is_a_finding(repo: Path) -> None:
    """The record's layout is versioned like everything else."""
    manifest = fv.compute_manifest(repo, "1.1.0", "2026-11-17")
    manifest["schema"] = fv.FREEZE_SCHEMA + 1
    fv.write_manifest(repo, manifest)
    results = fv.audit_freeze(repo)
    assert any("schema" in result.detail for result in results)


# ---------------------------------------------------------------------------
# Live checks
# ---------------------------------------------------------------------------


def test_command_grades_a_success() -> None:
    """Exit 0 is a pass, and the last output line is kept as evidence."""
    spec = fv.CommandSpec("demo", ("true",))
    result = fv.run_check(spec, Path("."), lambda argv, cwd, timeout: (0, "all good\n"))
    assert (result.status, result.detail) == ("pass", "all good")


def test_command_grades_a_failure() -> None:
    """A non-zero exit fails, with the exit code in the detail."""
    spec = fv.CommandSpec("demo", ("false",))
    result = fv.run_check(spec, Path("."), lambda argv, cwd, timeout: (1, "boom\n"))
    assert result.status == "fail"
    assert result.code == "submission.command"
    assert result.detail == "exit 1: boom"


@pytest.mark.parametrize("code", [2, 124])
def test_command_cannot_measure(code: int) -> None:
    """Exit 2 and a timeout say "unknown", not "wrong"."""
    spec = fv.CommandSpec("demo", ("gate",))
    result = fv.run_check(spec, Path("."), lambda argv, cwd, timeout: (code, "no idea\n"))
    assert result.status == "unmeasurable"
    assert result.code == "submission.command"


def test_command_reports_a_missing_binary() -> None:
    """A missing tool is 127, the shell's convention."""
    code, output = fv.run_command(("definitely-not-a-binary-xyz",), Path("."), 5)
    assert code == 127
    assert "definitely-not-a-binary-xyz" in output


def test_summarise_keeps_the_last_line_and_truncates() -> None:
    """One table-safe line, whatever the command printed."""
    assert fv.summarise("\n\nfirst\nsecond\n\n") == "second"
    assert fv.summarise("") == "(no output)"
    assert fv.summarise("x" * 500, limit=10) == "x" * 9 + "…"


def test_verify_marks_a_skipped_suite_as_skipped(repo: Path) -> None:
    """``--skip-slow`` records the omission instead of hiding it."""
    _freeze(repo)
    report = fv.verify(repo, runner=lambda argv, cwd, timeout: (0, "ok"), skip_slow=True)
    skipped = [result.name for result in report.results if result.status == "skip"]
    assert skipped == ["tests"]
    assert report.verdict() == "pass"
    assert report.verdict(strict=True) == "fail"


def test_verify_runs_every_command_through_the_runner(repo: Path) -> None:
    """Each live check is executed once, with the repository as its cwd."""
    _freeze(repo)
    seen: list = []

    def runner(argv: list, cwd: Path, timeout: int) -> tuple:
        """Record the call and succeed.

        Args:
            argv: Command vector.
            cwd: Working directory.
            timeout: Timeout in seconds.

        Returns:
            A successful exit.
        """
        seen.append((tuple(argv), cwd))
        return 0, "ok"

    report = fv.verify(repo, runner=runner)
    assert len(seen) == len(fv.COMMANDS)
    assert {cwd for _, cwd in seen} == {repo}
    assert all(result.status == "pass" for result in report.results)


def test_verify_fails_when_a_command_fails(repo: Path) -> None:
    """One red check is a red submission."""
    _freeze(repo)
    report = fv.verify(repo, runner=lambda argv, cwd, timeout: (1, "boom"))
    assert report.verdict() == "fail"
    assert report.exit_code() == fv.EXIT_FINDINGS


def test_report_exit_codes() -> None:
    """The three exit codes follow the repository's convention."""
    green = fv.Report((fv.CheckResult("a", "pass", ""),))
    red = fv.Report((fv.CheckResult("a", "fail", "", "x"),))
    blind = fv.Report((fv.CheckResult("a", "unmeasurable", "", "x"),))
    assert green.exit_code() == fv.EXIT_OK
    assert red.exit_code() == fv.EXIT_FINDINGS
    assert blind.exit_code() == fv.EXIT_UNMEASURABLE


def test_strict_turns_a_warning_into_a_failure() -> None:
    """An open ledger row blocks a strict run and not a normal one."""
    report = fv.Report((fv.CheckResult("a", "warn", "", "x"),))
    assert report.verdict() == "pass"
    assert report.verdict(strict=True) == "fail"


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_main_passes_on_a_good_repository(repo: Path, capsys: pytest.CaptureFixture) -> None:
    """A clean tree exits 0 and prints the checklist."""
    _freeze(repo)
    assert fv.main(["--root", str(repo), "--static"]) == fv.EXIT_OK
    output = capsys.readouterr().out
    assert "Certificate checklist" in output
    assert "PASS:" in output


def test_main_fails_on_a_defect(repo: Path) -> None:
    """A defect in the tree is exit 1."""
    _freeze(repo)
    (repo / "README.md").unlink()
    assert fv.main(["--root", str(repo), "--static"]) == fv.EXIT_FINDINGS


def test_main_refuses_a_directory_that_is_not_the_repository(tmp_path: Path) -> None:
    """Nothing to measure is exit 2, not a pass."""
    assert fv.main(["--root", str(tmp_path), "--static"]) == fv.EXIT_UNMEASURABLE


def test_main_json_is_machine_readable(repo: Path, capsys: pytest.CaptureFixture) -> None:
    """``--json`` carries the verdict, the checks and the checklist."""
    _freeze(repo)
    assert fv.main(["--root", str(repo), "--static", "--json"]) == fv.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "pass"
    assert payload["counts"]["pass"] == len(payload["results"])
    assert {row["status"] for row in payload["checklist"]} >= {"pass", "manual"}


def test_main_cuts_a_manifest(repo: Path) -> None:
    """``--manifest`` writes the freeze record and then verifies it."""
    assert fv.main(["--root", str(repo), "--static", "--manifest"]) == fv.EXIT_OK
    manifest = json.loads((repo / fv.FREEZE_PATH).read_text(encoding="utf-8"))
    assert manifest["file_count"] > 0
    assert fv.audit_freeze(repo)[0].status == "pass"


def test_main_cuts_a_manifest_at_a_chosen_path(repo: Path) -> None:
    """The destination is an argument, so a re-cut can land elsewhere."""
    target = repo / "freeze-copy.json"
    assert fv.main(["--root", str(repo), "--static", "--manifest", str(target)]) == fv.EXIT_OK
    assert json.loads(target.read_text(encoding="utf-8"))["schema"] == fv.FREEZE_SCHEMA


def test_main_manifest_without_a_version_is_unmeasurable(tmp_path: Path) -> None:
    """Nothing to freeze is exit 2."""
    _write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
    assert fv.main(["--root", str(tmp_path), "--static", "--manifest"]) == fv.EXIT_UNMEASURABLE


def test_parser_documents_every_flag() -> None:
    """The help text is the contract, so every flag is registered."""
    text = fv.build_parser().format_help()
    for flag in ("--root", "--static", "--skip-slow", "--manifest", "--json", "--strict"):
        assert flag in text


# ---------------------------------------------------------------------------
# The committed repository
# ---------------------------------------------------------------------------


def test_the_committed_repository_passes_the_static_audit() -> None:
    """The submission itself is clean, and the freeze matches the tree.

    The one tolerated non-pass is the ledger row for the day that is still
    being delivered: until this pull request has a number there is nothing to
    write in its PR column, and a gate that demanded it would be unable to
    pass on the very branch that closes it.
    """
    report = fv.verify(ROOT, run_commands=False)
    offenders = [
        f"{r.code}: {r.detail}" for r in report.results if r.status in ("fail", "unmeasurable")
    ]
    assert offenders == []
    warnings = [result for result in report.results if result.status == "warn"]
    assert [result.code for result in warnings if result.code != "submission.ledger"] == []


def test_the_committed_checklist_has_no_manual_item_with_evidence() -> None:
    """A manual line never claims a local check as proof."""
    rows = fv.checklist_rows(fv.verify(ROOT, run_commands=False))
    assert rows
    for section, item, status, evidence in rows:
        assert section and item
        assert status in ("pass", "fail", "warn", "skip", "unmeasurable", "not run", "manual")
        assert (status == "manual") == (evidence == "not verifiable offline")


def test_the_frozen_version_matches_the_release() -> None:
    """The manifest records the version the package actually declares."""
    manifest = json.loads((ROOT / fv.FREEZE_PATH).read_text(encoding="utf-8"))
    assert manifest["version"] == fv.project_version(ROOT)
