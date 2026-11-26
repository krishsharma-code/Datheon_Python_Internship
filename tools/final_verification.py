"""Verify the whole submission in one pass — and freeze it (Day 28).

Every other tool in ``tools/`` audits one claim: the coverage floor, the CI
matrix, the README, the release. On the last day of the internship nobody runs
them one at a time and reads seven tables; the submission is judged as a whole,
so it has to be *verifiable* as a whole.

This module is that single verdict. It answers two different questions, and
keeps them apart on purpose:

**Live checks.** The green run itself — ``compileall``, ``flake8``,
``black --check``, ``pytest``, and the six static audits — executed as
subprocesses, each recorded with its exit code and duration. The suite runs
once, under coverage, and the coverage gate re-reads that same report: one
measurement, two verdicts, no second two-minute pass.

**Static claims.** The facts the submission asserts in prose, read back out of
the files that assert them — the licence holder, the console scripts, the
changelog entry for the declared version, the ledger's PR numbers, one learning
log entry per completed day, the ``.gitignore`` patterns that keep build output
out of the history, and the paths the plan's own checklist names.

A claim that cannot be checked here is not silently passed. Items that need the
network or a human — the repository's public visibility, the CI run on GitHub,
the LinkedIn post, the credential on the Datheon portal — are listed as
``manual`` in the certificate checklist and never counted as evidence.

Finding codes:

===========================  ==================================================
Code                         What it means
===========================  ==================================================
``submission.file``          A file the submission needs is missing.
``submission.licence``       ``LICENSE`` is not the MIT text for the intern.
``submission.metadata``      ``pyproject.toml`` disagrees with what ships.
``submission.entry-point``   A console script has no ``main`` behind it.
``submission.changelog``     No changelog entry for the declared version.
``submission.ledger``        The progress ledger is malformed, or a day that
                             reads ``done`` carries no PR number.
``submission.log``           A completed day has no learning-log entry.
``submission.document``      A path the plan's checklist names does not exist.
``submission.artefact``      ``.gitignore`` does not exclude build output.
``submission.secret``        A token-shaped string, or a secret file, is here.
``submission.freeze``        The freeze manifest is missing, stale, or does not
                             match the tree it pins.
``submission.command``       A live check failed or could not be measured.
===========================  ==================================================

Exit codes follow the rest of the repository: ``0`` everything green, ``1`` at
least one finding, ``2`` a check that could not measure (a gate that passes
when it cannot look is worse than no gate). ``--strict`` turns the warnings —
an open ledger row, a skipped check — into failures.

Run it directly::

    python tools/final_verification.py                 # the full green run
    python tools/final_verification.py --static        # claims only, ~2 s
    python tools/final_verification.py --manifest      # re-cut the freeze
    python tools/final_verification.py --json          # machine-readable
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # pragma: no cover - Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 needs the backport
    import tomli as tomllib  # type: ignore[no-redef]

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: Exit codes, the same three the other audits use.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_UNMEASURABLE = 2

#: The five commands the package installs.
CONSOLE_SCRIPTS: Tuple[str, ...] = ("assistant", "hangman", "inventory", "organizer", "portfolio")

#: The shared core modules that ship as top-level modules.
CORE_MODULES: Tuple[str, ...] = (
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
)

#: Distribution name the repository is published under.
PACKAGE_NAME = "datheon-python-internship"

#: Files the submission cannot do without.
REQUIRED_FILES: Tuple[str, ...] = (
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    ".gitignore",
    ".gitattributes",
    "pyproject.toml",
    "setup.cfg",
    "requirements.txt",
    "mkdocs.yml",
    "conftest.py",
    "datheon/py.typed",
    "docs/index.md",
    "docs/daily_learning_log.md",
    "docs/internship_milestones_plan.md",
    "docs/assets/architecture.svg",
    "docs/assets/demo/quickstart.gif",
    "docs/assets/demo/transcript.json",
    ".github/workflows/ci.yml",
    ".github/workflows/docs.yml",
    ".github/workflows/mutation.yml",
    ".github/workflows/release.yml",
)

#: Phrases the licence must contain: the grant, the year, the holder.
LICENCE_TOKENS: Tuple[str, ...] = ("MIT License", "2026", "Krish Sharma")

#: Build output and local state that must never reach the history.
IGNORED_PATTERNS: Tuple[str, ...] = (
    "__pycache__/",
    "build/",
    "dist/",
    "*.egg-info/",
    ".venv/",
    ".pytest_cache/",
    ".coverage",
    "coverage.json",
    "benchmark-results.json",
    "site/",
    "mutants/",
    ".env",
    "*.pem",
    "secrets.json",
)

#: Filenames that are a secret by definition.
SECRET_FILES: Tuple[str, ...] = (".env", "secrets.json")

#: Where the progress ledger and the daily log live.
PLAN = Path("docs") / "internship_milestones_plan.md"
LEARNING_LOG = Path("docs") / "daily_learning_log.md"

#: Where the freeze manifest is committed.
FREEZE_PATH = Path("docs") / "submission" / "freeze.json"

#: Manifest layout version; bump it when the record's shape changes.
FREEZE_SCHEMA = 1

#: Root-level files the freeze pins.
FREEZE_FILES: Tuple[str, ...] = (
    # Day 34: the commit-time gate is shipped configuration, so the freeze
    # records it like every other root-level declaration.
    ".pre-commit-config.yaml",
    ".gitattributes",
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "conftest.py",
    "mkdocs.yml",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
)

#: Directories the freeze pins, by suffix.
FREEZE_DIRS: Tuple[str, ...] = (".github", "datheon", "docs", "projects", "tests", "tools")

#: Suffixes the freeze hashes. Binary assets are pinned by digest too.
PINNED_SUFFIXES = frozenset(
    {".cfg", ".csv", ".gif", ".json", ".md", ".py", ".svg", ".toml", ".txt", ".yaml", ".yml"}
)

#: Documents that change on every run, so pinning them would make the freeze
#: expire the moment it is written.
FREEZE_MUTABLE = frozenset(
    {
        "docs/daily_learning_log.md",
        "docs/final_verification.md",
        "docs/internship_milestones_plan.md",
        "docs/submission/freeze.json",
    }
)

#: Status values the ledger may carry.
LEDGER_STATUSES = frozenset({"blocked", "done", "in-progress", "pending"})

#: A semver release version, without a leading ``v``.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

#: One ledger row: day, target, theme, `branch`, deliverable, status, PR.
_LEDGER_ROW_RE = re.compile(
    r"^\|\s*(?P<day>\d+)\s*\|"
    r"\s*(?P<target>[^|]*?)\s*\|"
    r"\s*(?P<theme>[^|]*?)\s*\|"
    r"\s*`(?P<branch>[^`]+)`\s*\|"
    r"\s*(?P<deliverable>[^|]*?)\s*\|"
    r"\s*(?P<status>[A-Za-z-]+)\s*\|"
    r"\s*(?P<pr>[^|]*?)\s*\|\s*$"
)

#: A PR reference in the ledger's PR column.
_PR_RE = re.compile(r"^#\d+$")

#: A ``## Day N`` heading in the learning log.
_LOG_HEADING_RE = re.compile(r"^##\s+Day\s+(\d+)\b", re.MULTILINE)

#: A backticked token in the plan's checklist, e.g. ``README.md``.
_BACKTICK_RE = re.compile(r"`([^`\s]+)`")

#: Suffixes that make a backticked token a path claim rather than a word.
PATH_SUFFIXES = (".cfg", ".csv", ".gif", ".json", ".md", ".py", ".svg", ".toml", ".txt", ".yml")

#: Bare filenames the plan may name as a path, with no suffix to spot them by.
BARE_CLAIMS = frozenset({".gitattributes", ".gitignore", "LICENSE"})

#: A runner takes ``(argv, cwd, timeout)`` and returns ``(exit code, output)``.
Runner = Callable[[Sequence[str], Path, int], Tuple[int, str]]


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check.

    Attributes:
        name: Short check name (``licence``, ``tests``, ``freeze``, …).
        status: ``pass``, ``warn``, ``fail``, ``skip`` or ``unmeasurable``.
        detail: Human-readable explanation, safe to print in a table.
        code: Stable finding code, empty when the check passed.
        seconds: Wall-clock duration; ``0.0`` for the static checks.
    """

    name: str
    status: str
    detail: str
    code: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """Whether the check passed or was deliberately skipped.

        Returns:
            ``True`` unless the status is ``fail`` or ``unmeasurable``.
        """
        return self.status in ("pass", "skip")


@dataclass(frozen=True)
class LedgerRow:
    """One row of the progress ledger.

    Attributes:
        day: Day number, as written in the ledger.
        branch: Branch the day is delivered on.
        status: ``pending``, ``in-progress``, ``done`` or ``blocked``.
        pr: PR column verbatim (``#12``, ``—``, …).
    """

    day: int
    branch: str
    status: str
    pr: str

    @property
    def complete(self) -> bool:
        """Whether the day is finished: ``done`` *and* carrying a PR number.

        Returns:
            ``True`` for a row the ledger says is complete.
        """
        return self.status == "done" and bool(_PR_RE.match(self.pr))


@dataclass(frozen=True)
class CommandSpec:
    """A live check to run.

    Attributes:
        name: Check name used in the report.
        argv: The command, as an argument vector.
        slow: Whether the command runs the test suite.
        timeout: Seconds before the command is killed and reported unmeasurable.
    """

    name: str
    argv: Tuple[str, ...]
    slow: bool = False
    timeout: int = 1800


@dataclass(frozen=True)
class Report:
    """Every check from one verification run.

    Attributes:
        results: The checks, in execution order.
        seconds: Total wall-clock time.
    """

    results: Tuple[CheckResult, ...]
    seconds: float = 0.0

    def counts(self) -> Dict[str, int]:
        """Count the results by status.

        Returns:
            A mapping with one entry per status that occurred.
        """
        counts: Dict[str, int] = {}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts

    def blocking(self, strict: bool = False) -> List[CheckResult]:
        """List the results that stop the submission.

        Args:
            strict: Whether warnings and skips count as blocking.

        Returns:
            The offending results, in report order.
        """
        offenders = [r for r in self.results if r.status in ("fail", "unmeasurable")]
        if strict:
            offenders.extend(r for r in self.results if r.status in ("warn", "skip"))
        return offenders

    def verdict(self, strict: bool = False) -> str:
        """Summarise the run as one word.

        Args:
            strict: Whether warnings and skips count as blocking.

        Returns:
            ``fail``, ``unmeasurable`` or ``pass``.
        """
        statuses = {r.status for r in self.blocking(strict)}
        if "fail" in statuses:
            return "fail"
        if "unmeasurable" in statuses:
            return "unmeasurable"
        # Under --strict a warning or a skipped check is blocking too, and the
        # verdict has to say so rather than report the run as green.
        return "fail" if statuses else "pass"

    def exit_code(self, strict: bool = False) -> int:
        """Translate the verdict into a process exit code.

        Args:
            strict: Whether warnings and skips count as blocking.

        Returns:
            ``0``, ``1`` or ``2``.
        """
        verdict = self.verdict(strict)
        if verdict == "fail":
            return EXIT_FINDINGS
        if verdict == "unmeasurable":
            return EXIT_UNMEASURABLE
        return EXIT_OK


# ---------------------------------------------------------------------------
# Live checks
# ---------------------------------------------------------------------------

#: The full green run. The suite is executed once, under coverage; the coverage
#: gate then re-reads that report rather than running the suite a second time.
COMMANDS: Tuple[CommandSpec, ...] = (
    CommandSpec(
        "compile",
        (
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "projects",
            "tests",
            "datheon",
            "tools",
            "conftest.py",
        ),
    ),
    CommandSpec("lint", (sys.executable, "-m", "flake8", ".")),
    CommandSpec("format", (sys.executable, "-m", "black", "--check", ".")),
    CommandSpec(
        "tests",
        (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--cov",
            "--cov-report=json:coverage.json",
        ),
        slow=True,
    ),
    CommandSpec("coverage", (sys.executable, "tools/coverage_gate.py", "--report-only")),
    CommandSpec("readme", (sys.executable, "tools/readme_audit.py", "--strict")),
    CommandSpec("docs-site", (sys.executable, "tools/docs_site.py", "--strict")),
    CommandSpec("release", (sys.executable, "tools/release.py", "--strict")),
    CommandSpec("matrix", (sys.executable, "tools/ci_matrix.py", "--strict")),
    CommandSpec("assets", (sys.executable, "tools/demo_assets.py", "--check")),
    CommandSpec("io-audit", (sys.executable, "tools/io_audit.py", "--strict")),
    CommandSpec("census", (sys.executable, "tools/assertion_census.py", "--minimum", "200")),
)

#: The certificate checklist from the plan, and what proves each line.
#: ``manual`` marks an item no local run can decide — it is never evidence.
CHECKLIST: Tuple[Tuple[str, str, str], ...] = (
    ("Repository", "Public repo named `Datheon_Python_Internship`", "manual"),
    ("Repository", "All five task-pack projects implemented", "files"),
    ("Repository", "README: installation, architecture, quickstart", "readme"),
    ("Repository", "LICENSE — MIT, 2026 Krish Sharma", "licence"),
    ("Repository", "CONTRIBUTING.md and a strict .gitignore", "files, artefacts"),
    ("Repository", "Docstrings and type hints on public callables", "docs-site"),
    ("Quality", "Suite green, 200+ assertions", "tests, census"),
    ("Quality", "Coverage at or above the 90% floor", "coverage"),
    ("Quality", "CI green across the 12-cell matrix", "manual"),
    ("Quality", "flake8 and black --check clean", "lint, format"),
    ("Quality", "No secrets, tokens or credentials", "secrets"),
    ("Delivery", "Installable package with console entry points", "entry-points"),
    ("Delivery", "Tagged release, CHANGELOG, attached artefacts", "release"),
    ("Delivery", "Generated docs site linked from the README", "docs-site"),
    ("Proof", "LinkedIn walkthrough post tagging @Datheon", "manual"),
    ("Proof", "Final LinkedIn post published", "manual"),
    ("Proof", "Credential verified on the Datheon portal", "manual"),
    ("Certification", "Code review completed on the submitted work", "manual"),
)


def run_command(argv: Sequence[str], cwd: Path, timeout: int) -> Tuple[int, str]:
    """Run one command and capture its combined output.

    Args:
        argv: The command, as an argument vector.
        cwd: Working directory for the child process.
        timeout: Seconds before the child is killed.

    Returns:
        ``(exit code, output)``. A missing binary is reported as ``127`` and a
        timeout as ``124`` — the shell's conventions, so callers have one rule.
    """
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return 127, f"{argv[0]}: {exc}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return completed.returncode, f"{completed.stdout}\n{completed.stderr}"


def summarise(output: str, limit: int = 300) -> str:
    """Reduce a command's output to one table-safe line.

    Args:
        output: Combined stdout and stderr.
        limit: Maximum characters of the result.

    Returns:
        The last non-empty line, or ``(no output)``.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    text = lines[-1] if lines else "(no output)"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def run_check(spec: CommandSpec, root: Path, runner: Runner) -> CheckResult:
    """Execute one live check and grade its exit code.

    Args:
        spec: The command to run.
        root: Working directory.
        runner: Callable that executes the command.

    Returns:
        A :class:`CheckResult`; exit ``2`` and ``124`` are reported as
        ``unmeasurable`` rather than as failures, because neither says the code
        is wrong — only that the gate could not see it.
    """
    started = time.monotonic()
    code, output = runner(spec.argv, root, spec.timeout)
    seconds = time.monotonic() - started
    detail = summarise(output)
    if code == 0:
        return CheckResult(spec.name, "pass", detail, seconds=seconds)
    if code in (2, 124):
        return CheckResult(
            spec.name, "unmeasurable", f"exit {code}: {detail}", "submission.command", seconds
        )
    return CheckResult(spec.name, "fail", f"exit {code}: {detail}", "submission.command", seconds)


# ---------------------------------------------------------------------------
# Static claims
# ---------------------------------------------------------------------------


def read_toml(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a TOML document.

    Args:
        path: File to parse.

    Returns:
        The parsed document, or ``None`` when the file is absent or malformed.
    """
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def project_version(root: Path) -> Optional[str]:
    """Read the version declared in ``pyproject.toml``.

    Args:
        root: Repository root.

    Returns:
        The version string, or ``None`` when it cannot be read.
    """
    data = read_toml(root / "pyproject.toml") or {}
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def _finding(name: str, detail: str, code: str, status: str = "fail") -> CheckResult:
    """Build a non-passing result.

    Args:
        name: Check name.
        detail: Explanation for the table.
        code: Stable finding code.
        status: ``fail``, ``warn`` or ``unmeasurable``.

    Returns:
        The result.
    """
    return CheckResult(name, status, detail, code)


def audit_files(root: Path) -> List[CheckResult]:
    """Check that every file the submission needs is there.

    Args:
        root: Repository root.

    Returns:
        One ``submission.file`` finding per missing file, or a single pass.
    """
    missing = [rel for rel in REQUIRED_FILES if not (root / rel).is_file()]
    if missing:
        return [_finding("files", f"{rel} is missing", "submission.file") for rel in missing]
    return [CheckResult("files", "pass", f"{len(REQUIRED_FILES)} required files present")]


def audit_licence(root: Path) -> List[CheckResult]:
    """Check the licence grants MIT terms to the intern.

    Args:
        root: Repository root.

    Returns:
        A pass, or one finding per missing phrase.
    """
    path = root / "LICENSE"
    if not path.is_file():
        return [_finding("licence", "LICENSE is missing", "submission.licence")]
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [token for token in LICENCE_TOKENS if token not in text]
    if missing:
        return [
            _finding("licence", f"LICENSE does not mention {token!r}", "submission.licence")
            for token in missing
        ]
    return [CheckResult("licence", "pass", "MIT License, 2026 Krish Sharma")]


def audit_metadata(root: Path) -> List[CheckResult]:
    """Check that ``pyproject.toml`` describes what the repository ships.

    Args:
        root: Repository root.

    Returns:
        One finding per disagreement, or a single pass.
    """
    data = read_toml(root / "pyproject.toml")
    if data is None:
        return [
            _finding(
                "metadata", "pyproject.toml is missing or not valid TOML", "submission.metadata"
            )
        ]
    project = data.get("project", {})
    setuptools = data.get("tool", {}).get("setuptools", {})
    scripts = project.get("scripts", {})
    py_modules = set(setuptools.get("py-modules", []))
    package_data = set(setuptools.get("package-data", {}).get("*", []))
    classifiers = set(project.get("classifiers", []))
    extras = project.get("optional-dependencies", {}).get("dev", [])

    results: List[CheckResult] = []

    def check(ok: bool, detail: str) -> None:
        """Record one metadata assertion.

        Args:
            ok: Whether the assertion holds.
            detail: Explanation used when it does not.
        """
        if not ok:
            results.append(_finding("metadata", detail, "submission.metadata"))

    check(project.get("name") == PACKAGE_NAME, f"name is {project.get('name')!r}")
    version = project.get("version", "")
    check(bool(VERSION_RE.match(str(version))), f"version {version!r} is not SemVer")
    check(project.get("requires-python") == ">=3.10", "requires-python is not '>=3.10'")
    check(project.get("license") == "MIT", f"license is {project.get('license')!r}")
    check(
        sorted(scripts) == sorted(CONSOLE_SCRIPTS),
        f"console scripts are {sorted(scripts)}, expected {sorted(CONSOLE_SCRIPTS)}",
    )
    for name in CONSOLE_SCRIPTS:
        expected = f"datheon.cli.{name}:main"
        check(scripts.get(name) == expected, f"script {name!r} does not target {expected}")
    missing_modules = [name for name in CORE_MODULES if name not in py_modules]
    check(not missing_modules, f"py-modules is missing {', '.join(missing_modules)}")
    check("py.typed" in package_data, "package-data does not ship py.typed")
    check("Typing :: Typed" in classifiers, "the Typing :: Typed classifier is absent")
    check(not project.get("dependencies"), "the runtime declares third-party dependencies")
    for required in ("pytest", "flake8", "black", "hypothesis", "pyyaml"):
        check(
            any(required in requirement for requirement in extras),
            f"the dev extra does not require {required}",
        )
    if results:
        return results
    summary = (
        f"{PACKAGE_NAME} {version}, {len(scripts)} scripts, "
        f"{len(CORE_MODULES)} modules, stdlib-only"
    )
    return [CheckResult("metadata", "pass", summary)]


def audit_entry_points(root: Path) -> List[CheckResult]:
    """Check that every console script has a ``main`` behind it.

    Args:
        root: Repository root.

    Returns:
        One ``submission.entry-point`` finding per missing or empty shim.
    """
    results: List[CheckResult] = []
    for name in CONSOLE_SCRIPTS:
        path = root / "datheon" / "cli" / f"{name}.py"
        if not path.is_file():
            results.append(
                _finding(
                    "entry-points", f"datheon/cli/{name}.py is missing", "submission.entry-point"
                )
            )
        elif "def main(" not in path.read_text(encoding="utf-8", errors="replace"):
            results.append(
                _finding(
                    "entry-points",
                    f"datheon/cli/{name}.py defines no main()",
                    "submission.entry-point",
                )
            )
    if results:
        return results
    return [CheckResult("entry-points", "pass", f"{len(CONSOLE_SCRIPTS)} shims expose main()")]


def audit_changelog(root: Path) -> List[CheckResult]:
    """Check the changelog carries an entry for the declared version.

    Args:
        root: Repository root.

    Returns:
        A pass, or one finding per missing heading.
    """
    version = project_version(root)
    if version is None:
        return [_finding("changelog", "no version to check against", "submission.changelog")]
    path = root / "CHANGELOG.md"
    if not path.is_file():
        return [_finding("changelog", "CHANGELOG.md is missing", "submission.changelog")]
    text = path.read_text(encoding="utf-8", errors="replace")
    results: List[CheckResult] = []
    if f"## [{version}]" not in text:
        results.append(_finding("changelog", f"no '## [{version}]' entry", "submission.changelog"))
    if "## [Unreleased]" not in text:
        results.append(
            _finding("changelog", "no '## [Unreleased]' section", "submission.changelog")
        )
    if results:
        return results
    return [CheckResult("changelog", "pass", f"entries for Unreleased and {version}")]


def parse_ledger(text: str) -> List[LedgerRow]:
    """Parse the progress ledger out of the milestones plan.

    Only §1 is read: §2 repeats the day numbers in a different table and §5 is a
    backlog, so a parser that scanned the whole file would count them as days.

    Args:
        text: The plan's full text.

    Returns:
        The ledger rows in file order; an unparseable line is skipped.
    """
    start = text.find("## 1. Progress Ledger")
    end = text.find("## 2.", start if start >= 0 else 0)
    section = text[start:end] if start >= 0 else text
    rows: List[LedgerRow] = []
    for line in section.splitlines():
        match = _LEDGER_ROW_RE.match(line.strip())
        if match is None:
            continue
        status = match.group("status").strip().lower()
        if status not in LEDGER_STATUSES:
            continue
        rows.append(
            LedgerRow(
                day=int(match.group("day")),
                branch=match.group("branch").strip(),
                status=status,
                pr=match.group("pr").strip(),
            )
        )
    return rows


def audit_ledger(root: Path) -> Tuple[List[CheckResult], List[LedgerRow]]:
    """Check the ledger's numbering and its PR evidence.

    Args:
        root: Repository root.

    Returns:
        ``(results, rows)`` — the findings and the parsed rows, which the log
        audit reuses.
    """
    path = root / PLAN
    if not path.is_file():
        return [_finding("ledger", f"{PLAN.as_posix()} is missing", "submission.ledger")], []
    rows = parse_ledger(path.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        return [_finding("ledger", "the ledger has no rows", "submission.ledger")], []

    results: List[CheckResult] = []
    days = [row.day for row in rows]
    if days != list(range(1, len(rows) + 1)):
        results.append(
            _finding("ledger", f"days are {days}, expected 1..{len(rows)}", "submission.ledger")
        )
    for row in rows:
        if row.status == "done" and not _PR_RE.match(row.pr):
            results.append(
                _finding(
                    "ledger",
                    f"Day {row.day} reads done but its PR column is {row.pr!r}",
                    "submission.ledger",
                )
            )
    open_rows = [row for row in rows if not row.complete]
    if open_rows:
        results.append(
            _finding(
                "ledger",
                f"{len(open_rows)} row(s) still open: "
                + ", ".join(f"Day {row.day} ({row.status})" for row in open_rows),
                "submission.ledger",
                status="warn",
            )
        )
    if results:
        return results, rows
    return [
        CheckResult(
            "ledger",
            "pass",
            f"{len(rows)} days, every row done with a PR number",
        )
    ], rows


def audit_learning_log(root: Path, rows: Sequence[LedgerRow]) -> List[CheckResult]:
    """Check every completed day has a learning-log entry.

    Args:
        root: Repository root.
        rows: The ledger rows to require entries for.

    Returns:
        One ``submission.log`` finding per missing day, or a single pass.
    """
    path = root / LEARNING_LOG
    if not path.is_file():
        return [_finding("log", f"{LEARNING_LOG.as_posix()} is missing", "submission.log")]
    text = path.read_text(encoding="utf-8", errors="replace")
    documented = {int(match) for match in _LOG_HEADING_RE.findall(text)}
    complete = [row.day for row in rows if row.complete]
    missing = [day for day in complete if day not in documented]
    if missing:
        return [
            _finding("log", f"Day {day} has no learning-log entry", "submission.log")
            for day in missing
        ]
    return [CheckResult("log", "pass", f"{len(complete)} entries, one per completed day")]


def plan_path_claims(text: str) -> List[str]:
    """Extract the paths the plan's §4 checklist names.

    Args:
        text: The plan's full text.

    Returns:
        Deduplicated path-looking tokens, in first-seen order. Only tokens with
        a known suffix or a dotfile name count — ``main`` and
        ``feat/branch-name`` are words, not file claims.
    """
    start = text.find("## 4.")
    end = text.find("## 5.", start if start >= 0 else 0)
    section = text[start:end] if start >= 0 else ""
    claims: List[str] = []
    for token in _BACKTICK_RE.findall(section):
        is_path = token.endswith(PATH_SUFFIXES) or token in BARE_CLAIMS
        if is_path and token not in claims:
            claims.append(token)
    return claims


def audit_plan_documents(root: Path) -> List[CheckResult]:
    """Check that every path the checklist names actually exists.

    Args:
        root: Repository root.

    Returns:
        One ``submission.document`` finding per missing path, or a single pass.
    """
    path = root / PLAN
    if not path.is_file():
        return [_finding("documents", f"{PLAN.as_posix()} is missing", "submission.document")]
    claims = plan_path_claims(path.read_text(encoding="utf-8", errors="replace"))
    missing = [claim for claim in claims if not (root / claim).exists()]
    if missing:
        return [
            _finding(
                "documents",
                f"the checklist names {claim}, which is not here",
                "submission.document",
            )
            for claim in missing
        ]
    return [CheckResult("documents", "pass", f"{len(claims)} checklist paths resolve")]


def audit_gitignore(root: Path) -> List[CheckResult]:
    """Check the ignore rules that keep build output out of the history.

    Args:
        root: Repository root.

    Returns:
        One ``submission.artefact`` finding per unignored pattern, or a pass.
    """
    path = root / ".gitignore"
    if not path.is_file():
        return [_finding("artefacts", ".gitignore is missing", "submission.artefact")]
    declared = {
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    missing = [pattern for pattern in IGNORED_PATTERNS if pattern not in declared]
    if missing:
        return [
            _finding("artefacts", f".gitignore does not exclude {pattern!r}", "submission.artefact")
            for pattern in missing
        ]
    return [CheckResult("artefacts", "pass", f"{len(IGNORED_PATTERNS)} ignore rules declared")]


def secret_patterns() -> Tuple[re.Pattern[str], ...]:
    """The token shapes that must never be committed.

    Returns:
        The patterns :mod:`tools.readme_audit` already rejects, so the two
        gates cannot drift apart. Imported lazily because this module runs both
        as ``python tools/final_verification.py`` and as ``tools.final_verification``.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.readme_audit import SECRET_PATTERNS

    return SECRET_PATTERNS


def audit_secrets(root: Path) -> List[CheckResult]:
    """Scan the pinned files for token-shaped strings and secret files.

    Args:
        root: Repository root.

    Returns:
        One ``submission.secret`` finding per match, or a single pass.
    """
    results: List[CheckResult] = []
    for name in SECRET_FILES:
        if (root / name).exists():
            results.append(
                _finding("secrets", f"{name} is present in the working tree", "submission.secret")
            )
    for path in sorted(root.rglob("*.pem")):
        if ".git" not in path.parts:
            rel = path.relative_to(root).as_posix()
            results.append(_finding("secrets", f"{rel} is a private key file", "submission.secret"))
    patterns = secret_patterns()
    scanned = 0
    for rel in pinned_paths(root):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                results.append(
                    _finding(
                        "secrets",
                        f"{rel} contains a token-shaped string {match.group(0)[:8]}…",
                        "submission.secret",
                    )
                )
    if results:
        return results
    return [CheckResult("secrets", "pass", f"no token shapes in {scanned} files")]


# ---------------------------------------------------------------------------
# The freeze
# ---------------------------------------------------------------------------


def gitignore_patterns(root: Path) -> Tuple[str, ...]:
    """Return the path patterns the root ``.gitignore`` excludes.

    Only the entries that name a path survive: comments, blank lines and
    negations are dropped, a leading ``/`` is removed so an anchored pattern is
    matched against the whole relative path, and a trailing ``/`` is stripped so
    a directory entry covers the files beneath it. This is a deliberately small
    subset of git's rules — enough to keep generated output out of the freeze,
    and no more.

    Args:
        root: Repository root.

    Returns:
        Patterns, in file order. Empty when there is no ``.gitignore``.
    """
    ignore_file = root / ".gitignore"
    if not ignore_file.is_file():
        return ()
    patterns: List[str] = []
    for raw_line in ignore_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line.strip("/").lstrip("/") or line)
    return tuple(patterns)


def is_gitignored(rel: str, patterns: Sequence[str]) -> bool:
    """Report whether a repository-relative path is excluded by ``patterns``.

    A pattern containing a slash is matched against the path and each of its
    parent directories, so ``site/`` covers ``site/index.html``. A pattern with
    no slash is matched against every path component, which is how git treats
    ``*.log`` and ``.env``.

    Args:
        rel: Repository-relative POSIX path.
        patterns: Patterns from :func:`gitignore_patterns`.

    Returns:
        ``True`` when the path would not be tracked.
    """
    parts = PurePosixPath(rel).parts
    prefixes = ["/".join(parts[: index + 1]) for index in range(len(parts))]
    for pattern in patterns:
        if "/" in pattern:
            if any(fnmatch.fnmatchcase(prefix, pattern) for prefix in prefixes):
                return True
        elif any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False


def pinned_paths(root: Path) -> List[str]:
    """List the files the freeze manifest pins, as relative POSIX paths.

    Args:
        root: Repository root.

    Returns:
        Sorted paths. Governance documents that change on every run are left
        out — a freeze that expires the moment it is cut is not a freeze — and
        so is everything ``.gitignore`` excludes. Pinning an ignored path was a
        real defect: the tools write ``portfolio_summary.csv`` next to the
        portfolio tracker, the freeze recorded it, and every clean clone then
        failed the audit with "pinned but gone". A manifest may only pin what
        the repository actually carries.
    """
    ignored = gitignore_patterns(root)
    paths: List[str] = []
    for rel in FREEZE_FILES:
        if (root / rel).is_file() and not is_gitignored(rel, ignored):
            paths.append(rel)
    for rel_dir in FREEZE_DIRS:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in PINNED_SUFFIXES:
                continue
            rel = path.relative_to(root).as_posix()
            if "__pycache__" in rel or rel in FREEZE_MUTABLE or is_gitignored(rel, ignored):
                continue
            paths.append(rel)
    return sorted(set(paths))


def sha256_of(path: Path) -> str:
    """Hash one file.

    Args:
        path: File to hash.

    Returns:
        The lowercase hex SHA-256 digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_manifest(root: Path, version: str, generated: str) -> Dict[str, Any]:
    """Build the freeze manifest for the current tree.

    Args:
        root: Repository root.
        version: The released version being frozen.
        generated: ISO date the manifest is cut, passed in so the record is
            reproducible in tests.

    Returns:
        The manifest document.
    """
    paths = pinned_paths(root)
    return {
        "schema": FREEZE_SCHEMA,
        "version": version,
        "generated": generated,
        "file_count": len(paths),
        "sha256": {rel: sha256_of(root / rel) for rel in paths},
    }


def write_manifest(root: Path, manifest: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Write a freeze manifest, creating its directory.

    Args:
        root: Repository root.
        manifest: The document to write.
        path: Destination; defaults to :data:`FREEZE_PATH` under ``root``.

    Returns:
        The path written.
    """
    target = Path(path) if path is not None else root / FREEZE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def audit_freeze(root: Path, path: Optional[Path] = None) -> List[CheckResult]:
    """Check the committed manifest still describes the tree it pins.

    Args:
        root: Repository root.
        path: Manifest to read; defaults to :data:`FREEZE_PATH` under ``root``.

    Returns:
        One ``submission.freeze`` finding per drift, or a single pass.
    """
    target = Path(path) if path is not None else root / FREEZE_PATH
    rel_target = FREEZE_PATH.as_posix()
    if not target.is_file():
        return [
            _finding("freeze", f"{rel_target} is missing — run --manifest", "submission.freeze")
        ]
    try:
        manifest = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [_finding("freeze", f"cannot read the manifest: {exc}", "submission.freeze")]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sha256"), dict):
        return [_finding("freeze", "the manifest is not a freeze record", "submission.freeze")]

    results: List[CheckResult] = []
    if manifest.get("schema") != FREEZE_SCHEMA:
        results.append(
            _finding(
                "freeze",
                f"manifest schema {manifest.get('schema')!r} is not {FREEZE_SCHEMA}",
                "submission.freeze",
            )
        )
    version = project_version(root)
    if version is not None and manifest.get("version") != version:
        results.append(
            _finding(
                "freeze",
                f"manifest was cut for {manifest.get('version')!r}, pyproject declares {version!r}",
                "submission.freeze",
            )
        )

    recorded: Dict[str, str] = manifest["sha256"]
    current = set(pinned_paths(root))
    for rel, digest in sorted(recorded.items()):
        path_in_tree = root / rel
        if not path_in_tree.is_file():
            results.append(_finding("freeze", f"{rel} is pinned but gone", "submission.freeze"))
        elif sha256_of(path_in_tree) != digest:
            results.append(
                _finding("freeze", f"{rel} changed since the freeze", "submission.freeze")
            )
    for rel in sorted(current - set(recorded)):
        results.append(_finding("freeze", f"{rel} is not pinned", "submission.freeze"))

    if results:
        return results
    return [
        CheckResult(
            "freeze",
            "pass",
            f"{len(recorded)} files pinned at {manifest.get('version')}, all digests match",
        )
    ]


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def verify(
    root: Optional[Path] = None,
    runner: Runner = run_command,
    run_commands: bool = True,
    skip_slow: bool = False,
    manifest_path: Optional[Path] = None,
) -> Report:
    """Run every check and collect the verdict.

    Args:
        root: Repository root; defaults to :data:`ROOT`.
        runner: Callable used for the live checks.
        run_commands: Whether to run the live checks at all.
        skip_slow: Whether to skip the commands that run the test suite.
        manifest_path: Freeze manifest to verify; defaults to the committed one.

    Returns:
        The :class:`Report`.
    """
    root = Path(root) if root is not None else ROOT
    started = time.monotonic()
    results: List[CheckResult] = []
    results.extend(audit_files(root))
    results.extend(audit_licence(root))
    results.extend(audit_metadata(root))
    results.extend(audit_entry_points(root))
    results.extend(audit_changelog(root))
    ledger_results, rows = audit_ledger(root)
    results.extend(ledger_results)
    results.extend(audit_learning_log(root, rows))
    results.extend(audit_plan_documents(root))
    results.extend(audit_gitignore(root))
    results.extend(audit_secrets(root))
    results.extend(audit_freeze(root, manifest_path))
    if run_commands:
        for spec in COMMANDS:
            if skip_slow and spec.slow:
                results.append(CheckResult(spec.name, "skip", "--skip-slow", "submission.command"))
                continue
            results.append(run_check(spec, root, runner))
    return Report(tuple(results), time.monotonic() - started)


def checklist_rows(report: Report) -> List[Tuple[str, str, str, str]]:
    """Map each certificate checklist line onto the checks that prove it.

    Args:
        report: The run to read.

    Returns:
        ``(section, item, status, evidence)`` per line, where status is the
        worst status among the named checks, or ``manual``.
    """
    by_name: Dict[str, List[CheckResult]] = {}
    for result in report.results:
        by_name.setdefault(result.name, []).append(result)

    rows: List[Tuple[str, str, str, str]] = []
    for section, item, evidence in CHECKLIST:
        if evidence == "manual":
            rows.append((section, item, "manual", "not verifiable offline"))
            continue
        names = [name.strip() for name in evidence.split(",")]
        statuses = [r.status for name in names for r in by_name.get(name, [])]
        if not statuses:
            status = "not run"
        elif "fail" in statuses:
            status = "fail"
        elif "unmeasurable" in statuses:
            status = "unmeasurable"
        elif "warn" in statuses:
            status = "warn"
        elif "skip" in statuses:
            status = "skip"
        else:
            status = "pass"
        rows.append((section, item, status, ", ".join(names)))
    return rows


def render_report(report: Report, strict: bool = False) -> str:
    """Render the run as a table plus the certificate checklist.

    Args:
        report: The run to render.
        strict: Whether warnings and skips count as blocking.

    Returns:
        The report text.
    """
    counts = report.counts()
    header = (
        f"final-verification: {len(report.results)} checks — "
        f"{counts.get('pass', 0)} passed, {counts.get('fail', 0)} failed, "
        f"{counts.get('warn', 0)} warning(s), {counts.get('skip', 0)} skipped, "
        f"{counts.get('unmeasurable', 0)} unmeasurable ({report.seconds:.1f} s)"
    )
    lines = [header, ""]
    lines.append(f"{'status':<14} {'code':<22} {'check':<14} detail")
    lines.append("-" * 112)
    for result in report.results:
        lines.append(f"{result.status:<14} {result.code:<22} {result.name:<14} {result.detail}")
    lines.append("")
    lines.append("Certificate checklist")
    lines.append(f"{'section':<15} {'status':<14} item")
    lines.append("-" * 112)
    for section, item, status, evidence in checklist_rows(report):
        suffix = "" if status == "manual" else f"   [{evidence}]"
        lines.append(f"{section:<15} {status:<14} {item}{suffix}")
    lines.append("")
    verdict = report.verdict(strict)
    lines.append(f"{verdict.upper()}: {counts.get('pass', 0)}/{len(report.results)} checks green.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser exposing ``--root``, ``--static``, ``--skip-slow``,
        ``--manifest``, ``--json`` and ``--strict``.
    """
    parser = argparse.ArgumentParser(
        prog="final-verification",
        description="Verify the whole submission in one pass, and freeze it.",
    )
    parser.add_argument("--root", default=str(ROOT), help="repository root to verify")
    parser.add_argument("--static", action="store_true", help="check the claims only, run nothing")
    parser.add_argument(
        "--skip-slow", action="store_true", help="skip the commands that run pytest"
    )
    parser.add_argument(
        "--manifest",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="re-cut the freeze manifest (default: docs/submission/freeze.json)",
    )
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings and skips as failures"
    )
    parser.add_argument("--version", action="version", version="final-verification 1.0")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the verification.

    Args:
        argv: Argument list; ``None`` means :data:`sys.argv`.

    Returns:
        ``0`` when every check is green, ``1`` on findings, ``2`` when nothing
        could be measured.
    """
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / "pyproject.toml").is_file():
        print(
            f"final-verification: {root} is not the repository — nothing to measure",
            file=sys.stderr,
        )
        return EXIT_UNMEASURABLE

    if args.manifest is not None:
        version = project_version(root)
        if version is None:
            print("final-verification: no version to freeze", file=sys.stderr)
            return EXIT_UNMEASURABLE
        manifest = compute_manifest(root, version, dt.date.today().isoformat())
        target = write_manifest(root, manifest, Path(args.manifest) if args.manifest else None)
        print(
            f"final-verification: froze {manifest['file_count']} files at {version} -> {target}",
            file=sys.stderr,
        )

    report = verify(
        root,
        run_commands=not args.static,
        skip_slow=args.skip_slow,
        manifest_path=Path(args.manifest) if args.manifest else None,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root),
                    "verdict": report.verdict(args.strict),
                    "seconds": round(report.seconds, 2),
                    "counts": report.counts(),
                    "results": [
                        {
                            "name": result.name,
                            "status": result.status,
                            "code": result.code,
                            "detail": result.detail,
                            "seconds": round(result.seconds, 2),
                        }
                        for result in report.results
                    ],
                    "checklist": [
                        {"section": section, "item": item, "status": status, "evidence": evidence}
                        for section, item, status, evidence in checklist_rows(report)
                    ],
                },
                indent=2,
            )
        )
    else:
        print(render_report(report, args.strict))
    return report.exit_code(args.strict)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
