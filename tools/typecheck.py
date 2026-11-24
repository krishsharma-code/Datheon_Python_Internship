"""Type gate: ``mypy --strict`` over the shipped surface, or no merge (Day 31).

Every other gate in this repository asks a question about *execution* — is the
code covered, do the tests survive mutation, does the fuzzer find a crash. None
of them asks the question a type checker asks, which is the one that costs
nothing to answer on every commit: **can a call be wrong without a test ever
running?** A misspelled attribute, a callback whose return value nobody
declared, an ``asyncio.run`` handed an awaitable instead of a coroutine — all
three were live in this tree, and all three were invisible to 1,400 passing
tests. ``mypy --strict`` reported them on its first run.

Three things make this a gate rather than a report:

* **The scope is derived, not duplicated.** ``[tool.setuptools] py-modules`` in
  ``pyproject.toml`` is the list of modules that actually ship; the audit fails
  when one of them is missing from ``[tool.mypy] files``. A new core module
  cannot be added and left untyped by accident, because the packaging table and
  the type-checking table are compared, not trusted.
* **The project directories are checked, not excused.** ``projects/01_hangman_game``
  is not a valid Python package name, so mypy cannot derive a module name for
  the files inside it and refuses to look at them in place. Rather than skip the
  five tools — the largest part of the codebase — the gate builds a *shadow
  tree* per project: the shared core modules and that project's own files, flat
  in one directory, which is exactly the module graph the CLIs create at runtime
  by putting their directory on ``sys.path``.
* **A missing checker is never a pass.** Exit ``2`` means "could not measure"
  — mypy is absent, or it crashed on a file — and CI treats that as a failure,
  the same contract ``tools/coverage_gate.py`` follows.

Run it directly::

    python tools/typecheck.py                  # the whole gate
    python tools/typecheck.py --json           # machine-readable verdict
    python tools/typecheck.py --core-only      # skip the five project phases
    python tools/typecheck.py --keep-shadow    # keep the shadow trees for triage

Exit codes: ``0`` every phase is clean and the declaration is honest, ``1`` at
least one type error or one audit finding, ``2`` the gate could not reach a
verdict (no mypy, or mypy itself failed to run).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # pragma: no cover - the interpreter on 3.11+ takes this branch
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 needs `tomli`
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - no TOML reader at all
        tomllib = None  # type: ignore[assignment]

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The packaging and type-checking declaration, read as the source of truth for
#: what is in scope.
PYPROJECT_PATH: Path = ROOT / "pyproject.toml"

#: The workflow whose ``type-check`` job is audited.
CI_WORKFLOW_PATH: Path = ROOT / ".github" / "workflows" / "ci.yml"

#: Where the five tools live. One subdirectory per task pack.
PROJECTS_DIR: Path = ROOT / "projects"

#: The marker that makes a type checker look inside the installed package, as a
#: path under the audited root. The audit fails without it: complete annotations
#: behind a missing ``py.typed`` are invisible to every consumer, which is the
#: defect Day 23 fixed.
PY_TYPED_RELPATH: Path = Path("datheon") / "py.typed"

#: Directories under ``projects/`` that are not task packs.
IGNORED_PROJECT_DIRS = frozenset({"__pycache__"})

#: Files copied into every shadow tree: the shared core modules the tools import
#: by name at runtime.
SHADOW_CORE_SUFFIXES = (".py",)

#: Seconds one mypy phase may take before the gate gives up on it. The core
#: phase is a couple of seconds; a cold, uncached project phase is slower.
PHASE_TIMEOUT_SECONDS: float = 240.0

#: Exit codes, matching the rest of the repository.
EXIT_CLEAN: int = 0
EXIT_FINDINGS: int = 1
EXIT_UNMEASURABLE: int = 2

#: ``path:line:col: severity: message  [code]``. mypy prints a column only when
#: it knows one, so the column group is optional.
_DIAGNOSTIC_RE = re.compile(
    r"^(?P<path>.+?):(?:(?P<line>\d+):(?:(?P<column>\d+):)?)?\s*"
    r"(?P<severity>error|warning|note): (?P<message>.*?)"
    r"(?:\s+\[(?P<code>[a-z][a-z0-9-]*)\])?$"
)

#: The floor from ``[project] requires-python``, as mypy spells it.
_REQUIRES_PYTHON_RE = re.compile(r">=\s*(?P<version>\d+\.\d+)")

#: What a ``type-check`` CI job must contain for the audit to call it wired up.
CI_GATE_MARKER = "tools/typecheck.py"

#: The package that holds the task packs. It ships as ``projects*`` but is never
#: listed in ``[tool.mypy] files``: the numbered directory names are not valid
#: module names, so each project is checked from its own shadow tree instead.
SHADOW_PACKAGE = "projects"


@dataclass(frozen=True)
class Diagnostic:
    """One line of mypy output that is not a summary.

    Attributes:
        path: The file the diagnostic points at, as mypy printed it.
        line: One-based line number, or ``0`` when mypy reported a file-level
            problem without one (``errors.py: error: Source file found twice``).
        column: One-based column number, or ``0`` when mypy omitted it.
        severity: ``error``, ``warning`` or ``note``.
        message: The diagnostic text, without the trailing error code.
        code: mypy's stable error code (``arg-type``, ``no-any-return``, …),
            empty when the checker printed none.
    """

    path: str
    line: int
    column: int
    severity: str
    message: str
    code: str

    @property
    def location(self) -> str:
        """Return ``path:line:column``, or ``path:line`` without a column."""
        if not self.line:
            return self.path
        if self.column:
            return f"{self.path}:{self.line}:{self.column}"
        return f"{self.path}:{self.line}"

    @property
    def is_error(self) -> bool:
        """Return whether mypy called this an error."""
        return self.severity == "error"

    def render(self) -> str:
        """Return the diagnostic as one readable line."""
        code = f" [{self.code}]" if self.code else ""
        return f"{self.location}: {self.severity}: {self.message}{code}"


@dataclass(frozen=True)
class PhaseResult:
    """What one mypy invocation reported.

    Attributes:
        name: ``core`` for the in-place phase, otherwise the project directory
            name the phase checked.
        targets: The paths handed to mypy, as the command line spelled them.
        exit_code: mypy's own exit status: ``0`` clean, ``1`` errors,
            ``2`` the checker itself failed.
        diagnostics: Errors and warnings it printed, in output order.
        stderr: Whatever mypy wrote to standard error; empty on a clean run.
    """

    name: str
    targets: Tuple[str, ...]
    exit_code: int
    diagnostics: Tuple[Diagnostic, ...] = ()
    stderr: str = ""

    @property
    def errors(self) -> Tuple[Diagnostic, ...]:
        """Return the diagnostics mypy called errors."""
        return tuple(diagnostic for diagnostic in self.diagnostics if diagnostic.is_error)

    @property
    def warnings(self) -> Tuple[Diagnostic, ...]:
        """Return the diagnostics mypy called warnings."""
        return tuple(
            diagnostic for diagnostic in self.diagnostics if diagnostic.severity == "warning"
        )

    @property
    def clean(self) -> bool:
        """Return whether the phase measured a clean tree."""
        return self.exit_code == 0 and not self.diagnostics

    @property
    def measured(self) -> bool:
        """Return whether mypy produced a verdict rather than failing to run."""
        return self.exit_code in (0, 1)


@dataclass(frozen=True)
class Finding:
    """One problem with the declaration rather than with the code.

    Attributes:
        code: Stable finding code, e.g. ``typecheck.scope``.
        subject: The thing the finding is about (a module, a file, a job).
        message: One sentence a reviewer can act on.
    """

    code: str
    subject: str
    message: str


@dataclass(frozen=True)
class Verdict:
    """The gate's decision, with everything that produced it.

    Attributes:
        passed: Whether every phase was clean and every audit check held.
        findings: Declaration problems, empty when the declaration is honest.
        phases: One entry per mypy invocation.
        mypy_version: The checker's reported version, or ``"unknown"``.
    """

    passed: bool
    findings: Tuple[Finding, ...]
    phases: Tuple[PhaseResult, ...]
    mypy_version: str = "unknown"

    @property
    def errors(self) -> Tuple[Diagnostic, ...]:
        """Return every error diagnostic across every phase."""
        collected: List[Diagnostic] = []
        for phase in self.phases:
            collected.extend(phase.errors)
        return tuple(collected)

    @property
    def unmeasurable(self) -> bool:
        """Return whether any phase failed to produce a verdict."""
        return any(not phase.measured for phase in self.phases)


@dataclass(frozen=True)
class TypecheckConfig:
    """The parts of ``pyproject.toml`` this gate reads.

    Attributes:
        files: ``[tool.mypy] files`` — the declared type-checking scope.
        strict: Whether ``[tool.mypy] strict`` is on.
        python_version: ``[tool.mypy] python_version``, empty when unset.
        requires_python: ``[project] requires-python``, empty when unset.
        shipped_modules: ``[tool.setuptools] py-modules`` — the modules that
            actually ship, and therefore must be in ``files``.
        shipped_packages: The package patterns from
            ``[tool.setuptools.packages.find] include``.
    """

    files: Tuple[str, ...] = ()
    strict: bool = False
    python_version: str = ""
    requires_python: str = ""
    shipped_modules: Tuple[str, ...] = ()
    shipped_packages: Tuple[str, ...] = ()


# --- reading the declaration ----------------------------------------------
def load_config(path: Path = PYPROJECT_PATH) -> TypecheckConfig:
    """Read the type-checking and packaging declaration from ``pyproject.toml``.

    Args:
        path: The declaration file to read.

    Returns:
        The declared scope, the strictness switch and the shipped module list.

    Raises:
        RuntimeError: If no TOML reader is available (Python 3.10 without
            ``tomli``).
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the file is not valid TOML.
    """
    if tomllib is None:  # pragma: no cover - only on a bare Python 3.10
        raise RuntimeError("no TOML reader available; install 'tomli' on Python 3.10")
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc

    tool = data.get("tool", {}) if isinstance(data, dict) else {}
    mypy_table = tool.get("mypy", {}) if isinstance(tool, dict) else {}
    setuptools_table = tool.get("setuptools", {}) if isinstance(tool, dict) else {}
    project_table = data.get("project", {}) if isinstance(data, dict) else {}
    find_table = setuptools_table.get("packages", {}).get("find", {})

    def _strings(value: Any) -> Tuple[str, ...]:
        """Return ``value`` as a tuple of strings, or an empty tuple."""
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        return ()

    return TypecheckConfig(
        files=_strings(mypy_table.get("files")),
        strict=bool(mypy_table.get("strict", False)),
        python_version=str(mypy_table.get("python_version", "")),
        requires_python=str(project_table.get("requires-python", "")),
        shipped_modules=_strings(setuptools_table.get("py-modules")),
        shipped_packages=_strings(find_table.get("include")),
    )


def entry_stem(entry: str) -> str:
    """Return the module or package name an ``[tool.mypy] files`` entry names.

    ``"caching.py"`` and ``"caching"`` both name the module ``caching``;
    ``"datheon"`` names the package. Comparing the raw strings would report
    every module as missing from the scope.

    Args:
        entry: One entry from ``files``, ``py-modules`` or ``packages.find``.

    Returns:
        The stem: no suffix, no trailing wildcard, no trailing slash.
    """
    cleaned = entry.rstrip("*").rstrip("/")
    return Path(cleaned).stem if cleaned.endswith(".py") else Path(cleaned).name


def requires_python_floor(requires_python: str) -> str:
    """Return the ``>=`` floor of a ``requires-python`` specifier.

    Args:
        requires_python: A specifier such as ``">=3.10"``.

    Returns:
        The floor as ``"3.10"``, or an empty string when there is no ``>=``.
    """
    match = _REQUIRES_PYTHON_RE.search(requires_python)
    return match.group("version") if match else ""


# --- parsing mypy's output ------------------------------------------------
def parse_output(text: str) -> Tuple[Diagnostic, ...]:
    """Parse mypy's standard output into diagnostics.

    Notes (``note:``) are kept: a strict run's notes explain an error, and the
    suite asserts that they do not crash the parser. Summary lines — ``Found 3
    errors in 2 files``, ``Success: no issues found`` — are dropped.

    Args:
        text: Everything mypy printed.

    Returns:
        The diagnostics, in the order mypy printed them.
    """
    diagnostics: List[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("Success: "):
            continue
        match = _DIAGNOSTIC_RE.match(line)
        if match is None:
            continue
        diagnostics.append(
            Diagnostic(
                path=match.group("path"),
                line=int(match.group("line") or 0),
                column=int(match.group("column") or 0),
                severity=match.group("severity"),
                message=match.group("message"),
                code=match.group("code") or "",
            )
        )
    return tuple(diagnostics)


def error_summary(text: str) -> str:
    """Return the final summary line of a mypy run, or an empty string.

    Args:
        text: Everything mypy printed.

    Returns:
        The ``Found …`` or ``Success: …`` line, which is the last line mypy
        writes; empty when the output has none.
    """
    for raw in reversed(text.splitlines()):
        line = raw.strip()
        if line.startswith("Found ") or line.startswith("Success: "):
            return line
    return ""


# --- the shadow tree ------------------------------------------------------
def sanitise_project_name(name: str) -> str:
    """Turn a task-pack directory name into a valid Python identifier.

    ``01_hangman_game`` is not an identifier — it starts with a digit — which is
    exactly why mypy refuses to derive a module name for the files inside it.

    Args:
        name: The directory name, e.g. ``"03_file_organizer_automation"``.

    Returns:
        ``"project_03_file_organizer_automation"``.
    """
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"project_{cleaned}"
    return cleaned


def project_directories(root: Path = PROJECTS_DIR) -> Tuple[Path, ...]:
    """Return the task-pack directories under ``projects/``, sorted by name.

    Args:
        root: The ``projects`` directory.

    Returns:
        One path per task pack. An absent directory yields an empty tuple.
    """
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            (
                child
                for child in root.iterdir()
                if child.is_dir() and child.name not in IGNORED_PROJECT_DIRS
            ),
            key=lambda child: child.name,
        )
    )


def python_files(directory: Path) -> Tuple[Path, ...]:
    """Return the ``.py`` files directly inside ``directory``, sorted.

    Args:
        directory: The directory to list.

    Returns:
        The module files, in name order. Subdirectories are not descended into:
        the tools are flat module sets by design.
    """
    return tuple(sorted(path for path in directory.glob("*.py") if path.is_file()))


def build_shadow_tree(
    project: Path,
    destination: Path,
    root: Path = ROOT,
) -> Path:
    """Lay out one project's module graph flat in ``destination``.

    The result mirrors what the CLIs do at runtime: they insert their own
    directory and the repository root on ``sys.path`` and then import each other
    by bare name (``from words import load_words_from_file``). Copying the core
    modules next to the project's files reproduces that graph without touching
    the checkout.

    Args:
        project: The task-pack directory to mirror.
        destination: The directory to create and fill. Created if missing.
        root: The repository root the core modules come from.

    Returns:
        ``destination``, for chaining.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for suffix in SHADOW_CORE_SUFFIXES:
        for core in sorted(root.glob(f"*{suffix}")):
            if core.is_file():
                shutil.copyfile(core, destination / core.name)
    for module in python_files(project):
        shutil.copyfile(module, destination / module.name)
    return destination


# --- running mypy ---------------------------------------------------------
def mypy_available(executable: Optional[Sequence[str]] = None) -> bool:
    """Return whether mypy can be imported by the interpreter that runs the gate.

    Args:
        executable: The command prefix to test. Defaults to the current
            interpreter, ``python -m mypy``.

    Returns:
        ``True`` when ``mypy --version`` exits ``0``.
    """
    command = list(executable or [sys.executable, "-m", "mypy"])
    try:
        completed = subprocess.run(
            [*command, "--version"],
            capture_output=True,
            text=True,
            timeout=60.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no mypy at all
        return False
    return completed.returncode == 0


def mypy_version(executable: Optional[Sequence[str]] = None) -> str:
    """Return mypy's reported version, or ``"unknown"``.

    Args:
        executable: The command prefix to use. Defaults to ``python -m mypy``.

    Returns:
        Something like ``"mypy 1.11.2 (compiled: yes)"``, first line only.
    """
    command = list(executable or [sys.executable, "-m", "mypy"])
    try:
        completed = subprocess.run(
            [*command, "--version"],
            capture_output=True,
            text=True,
            timeout=60.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no mypy at all
        return "unknown"
    first = completed.stdout.strip().splitlines()
    return first[0] if first else "unknown"


def build_command(
    targets: Sequence[str],
    config_file: Optional[Path] = None,
    executable: Optional[Sequence[str]] = None,
    *,
    explicit_package_bases: bool = False,
) -> List[str]:
    """Build the mypy command line for one phase.

    Args:
        targets: Files or packages to check.
        config_file: The declaration to read strictness from. ``None`` uses
            mypy's own search, which finds ``pyproject.toml`` in the checkout.
        executable: The command prefix. Defaults to ``python -m mypy``.
        explicit_package_bases: Pass mypy's ``--explicit-package-bases``, which
            pins module names to the current directory. Required in a shadow
            tree: without it mypy derives a name from the shadow directory and
            then reports every file as "found twice under different module
            names".

    Returns:
        The command as an argument list, ready for :func:`subprocess.run`.
    """
    command = list(executable or [sys.executable, "-m", "mypy"])
    if config_file is not None:
        command.extend(["--config-file", str(config_file)])
    if explicit_package_bases:
        command.append("--explicit-package-bases")
    command.extend(targets)
    return command


def run_phase(
    name: str,
    targets: Sequence[str],
    cwd: Path,
    config_file: Optional[Path] = None,
    executable: Optional[Sequence[str]] = None,
    *,
    explicit_package_bases: bool = False,
) -> PhaseResult:
    """Run mypy once and collect its verdict.

    Args:
        name: The phase label (``core``, or a project directory name).
        targets: Files or packages to check, relative to ``cwd``.
        cwd: The directory to run in.
        config_file: The declaration to read strictness from.
        executable: The command prefix. Defaults to ``python -m mypy``.
        explicit_package_bases: Pin module names to ``cwd``.

    Returns:
        The phase result. A mypy that cannot run at all yields exit code ``2``,
        which :attr:`PhaseResult.measured` reports as "no verdict".
    """
    command = build_command(
        targets,
        config_file,
        executable,
        explicit_package_bases=explicit_package_bases,
    )
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=PHASE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - no mypy
        return PhaseResult(
            name=name,
            targets=tuple(targets),
            exit_code=EXIT_UNMEASURABLE,
            stderr=f"could not run mypy: {exc}",
        )
    return PhaseResult(
        name=name,
        targets=tuple(targets),
        exit_code=completed.returncode,
        diagnostics=parse_output(completed.stdout),
        stderr=completed.stderr.strip(),
    )


# --- auditing the declaration ---------------------------------------------
def audit_config(config: TypecheckConfig, root: Path = ROOT) -> List[Finding]:
    """Check that the declaration describes what the gate actually enforces.

    Args:
        config: The declaration read from ``pyproject.toml``.
        root: The repository root, for the files the audit looks for.

    Returns:
        One :class:`Finding` per problem, empty when the declaration is honest.
    """
    findings: List[Finding] = []

    if not config.files:
        findings.append(
            Finding(
                "typecheck.config",
                "pyproject.toml",
                "[tool.mypy] declares no `files`: the scope of the gate would be "
                "whatever happens to be on the command line.",
            )
        )
    if not config.strict:
        findings.append(
            Finding(
                "typecheck.strict",
                "pyproject.toml",
                "[tool.mypy] does not set `strict = true`; untyped defs and implicit "
                "`Any` would pass the gate.",
            )
        )

    floor = requires_python_floor(config.requires_python)
    if not config.python_version:
        findings.append(
            Finding(
                "typecheck.python",
                "pyproject.toml",
                "[tool.mypy] declares no `python_version`; mypy would assume its own "
                "default instead of the floor the package supports.",
            )
        )
    elif floor and config.python_version != floor:
        findings.append(
            Finding(
                "typecheck.python",
                "pyproject.toml",
                f"[tool.mypy] python_version is {config.python_version!r} but "
                f"requires-python says {floor!r}: the gate would type-check against a "
                "different language version than the package claims to support.",
            )
        )

    declared = {entry_stem(entry) for entry in config.files}
    for module in config.shipped_modules:
        if entry_stem(module) not in declared:
            findings.append(
                Finding(
                    "typecheck.scope",
                    module,
                    f"{module} ships (`[tool.setuptools] py-modules`) but is not in "
                    "[tool.mypy] files: it would never be type-checked.",
                )
            )
    for package in config.shipped_packages:
        stem = package.rstrip("*").rstrip("/")
        if not stem or stem == SHADOW_PACKAGE:
            # The task packs are not checked through `files`: their numbered
            # directory names are not importable, so the gate checks them from a
            # shadow tree, one phase per project. The audit verifies that the
            # directories exist instead, which is what the phases enumerate.
            if stem == SHADOW_PACKAGE and not (root / "projects").is_dir():
                findings.append(
                    Finding(
                        "typecheck.scope",
                        package,
                        "`projects*` ships but there is no projects/ directory for the "
                        "per-project phases to check.",
                    )
                )
            continue
        if stem not in declared:
            findings.append(
                Finding(
                    "typecheck.scope",
                    package,
                    f"package {package!r} ships but no entry in [tool.mypy] files " "covers it.",
                )
            )

    marker = root / PY_TYPED_RELPATH
    if not marker.is_file() and (root / "datheon").is_dir():
        findings.append(
            Finding(
                "typecheck.pytyped",
                "datheon/py.typed",
                "the `py.typed` marker is missing: consumers' type checkers would "
                "ignore every annotation this gate just proved.",
            )
        )

    findings.extend(audit_ci(root))
    findings.extend(audit_requirements(root))
    return findings


def audit_ci(root: Path = ROOT, workflow: Optional[Path] = None) -> List[Finding]:
    """Check that CI runs the gate.

    The workflow is read as text rather than through a YAML parser: this audit
    must work on a checkout with no third-party packages installed, and the
    question — "does any step run ``tools/typecheck.py``?" — needs nothing more.

    Args:
        root: The repository root.
        workflow: The workflow file to read. Defaults to
            :data:`CI_WORKFLOW_PATH`.

    Returns:
        A single finding when the gate is not wired into CI.
    """
    path = Path(workflow) if workflow is not None else root / ".github" / "workflows" / "ci.yml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [
            Finding(
                "typecheck.ci",
                str(path.name),
                "the CI workflow is missing, so nothing runs the type gate on a pull " "request.",
            )
        ]
    if CI_GATE_MARKER not in text:
        return [
            Finding(
                "typecheck.ci",
                str(path.name),
                f"no CI step runs `python {CI_GATE_MARKER}`: the gate exists but never "
                "runs on a pull request.",
            )
        ]
    return []


def audit_requirements(root: Path = ROOT, path: Optional[Path] = None) -> List[Finding]:
    """Check that mypy is declared as a development requirement.

    Args:
        root: The repository root.
        path: The requirements file to read. Defaults to
            ``<root>/requirements.txt``.

    Returns:
        A single finding when mypy is absent from the requirements file.
    """
    requirements = Path(path) if path is not None else root / "requirements.txt"
    try:
        text = requirements.read_text(encoding="utf-8")
    except OSError:
        return [
            Finding(
                "typecheck.requirement",
                requirements.name,
                "requirements.txt is missing, so CI has no declared way to install mypy.",
            )
        ]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.split(">")[0].split("=")[0].split("[")[0].strip() == "mypy":
            return []
    return [
        Finding(
            "typecheck.requirement",
            requirements.name,
            "mypy is not in requirements.txt: the gate would exit 2 (unmeasurable) on "
            "every CI run.",
        )
    ]


# --- the gate -------------------------------------------------------------
def evaluate(phases: Sequence[PhaseResult], findings: Sequence[Finding]) -> Verdict:
    """Combine the phase results and the audit findings into one verdict.

    Args:
        phases: One result per mypy invocation.
        findings: Problems with the declaration.

    Returns:
        A verdict that passes only when every phase measured a clean tree and
        the declaration raised no finding.
    """
    collected = tuple(findings)
    passed = not collected and all(phase.clean for phase in phases)
    return Verdict(passed=passed, findings=collected, phases=tuple(phases))


def render_report(verdict: Verdict, *, verbose: bool = False) -> str:
    """Render the verdict as a text report.

    Args:
        verdict: The gate's decision.
        verbose: When ``True``, list every diagnostic instead of a count.

    Returns:
        The report, ending in a newline.
    """
    checked = sum(len(phase.targets) for phase in verdict.phases)
    lines = [
        "Type gate",
        f"  checker  : {verdict.mypy_version}",
        f"  phases   : {len(verdict.phases)} ({checked} target(s))",
        f"  errors   : {len(verdict.errors)}",
        "",
        f"  {'phase':<34}{'targets':>8}{'errors':>8}{'warnings':>10}",
    ]
    for phase in verdict.phases:
        lines.append(
            f"  {phase.name:<34}{len(phase.targets):>8}{len(phase.errors):>8}"
            f"{len(phase.warnings):>10}"
        )

    if verdict.errors and (verbose or len(verdict.errors) <= 20):
        lines.append("")
        for diagnostic in verdict.errors:
            lines.append(f"  {diagnostic.render()}")

    if verdict.findings:
        lines.append("")
        lines.append("Declaration findings:")
        for finding in verdict.findings:
            lines.append(f"  - [{finding.code}] {finding.subject}: {finding.message}")

    lines.append("")
    if verdict.passed:
        lines.append("PASS — every shipped module type-checks under strict mypy.")
    else:
        lines.append("FAIL — the type gate found problems:")
        if verdict.errors:
            lines.append(
                f"  - {len(verdict.errors)} type error(s) across " f"{len(verdict.phases)} phase(s)"
            )
        for finding in verdict.findings:
            lines.append(f"  - {finding.message}")
    return "\n".join(lines) + "\n"


def verdict_to_dict(verdict: Verdict) -> Dict[str, Any]:
    """Convert a verdict into a JSON-serialisable mapping.

    Args:
        verdict: The gate's decision.

    Returns:
        A mapping with the verdict, the per-phase counts, the diagnostics and
        the declaration findings.
    """
    return {
        "passed": verdict.passed,
        "mypy": verdict.mypy_version,
        "errors": len(verdict.errors),
        "unmeasurable": verdict.unmeasurable,
        "phases": [
            {
                "name": phase.name,
                "targets": list(phase.targets),
                "exit_code": phase.exit_code,
                "errors": len(phase.errors),
                "warnings": len(phase.warnings),
            }
            for phase in verdict.phases
        ],
        "diagnostics": [
            {
                "path": diagnostic.path,
                "line": diagnostic.line,
                "column": diagnostic.column,
                "severity": diagnostic.severity,
                "message": diagnostic.message,
                "code": diagnostic.code,
            }
            for diagnostic in verdict.errors
        ],
        "findings": [
            {"code": finding.code, "subject": finding.subject, "message": finding.message}
            for finding in verdict.findings
        ],
    }


@dataclass
class RunOptions:
    """Options that shape one gate run.

    Attributes:
        config_file: The declaration to read strictness from.
        core_only: When ``True``, skip the five project phases.
        keep_shadow: When ``True``, leave the shadow trees on disk.
        shadow_root: Where to build the shadow trees; a temporary directory when
            ``None``.
        executable: The mypy command prefix. Defaults to ``python -m mypy``.
    """

    config_file: Path = field(default=PYPROJECT_PATH)
    core_only: bool = False
    keep_shadow: bool = False
    shadow_root: Optional[Path] = None
    executable: Optional[Sequence[str]] = None


def run_gate(
    options: Optional[RunOptions] = None,
    root: Path = ROOT,
) -> Verdict:
    """Run every phase and the audit, and return the verdict.

    Args:
        options: What to check and how. Defaults to the whole gate.
        root: The repository root.

    Returns:
        The verdict. When mypy cannot be found at all, the verdict is a single
        unmeasured phase, so the caller can report "no verdict" rather than a
        pass.
    """
    settings = options or RunOptions()
    try:
        config = load_config(settings.config_file)
    except (OSError, ValueError, RuntimeError) as exc:
        return Verdict(
            passed=False,
            findings=(Finding("typecheck.config", "pyproject.toml", str(exc)),),
            phases=(),
        )

    findings = audit_config(config, root)
    targets = tuple(config.files)
    phases: List[PhaseResult] = []
    phases.append(
        run_phase(
            "core",
            targets,
            root,
            settings.config_file,
            settings.executable,
        )
    )

    if not settings.core_only:
        scratch = settings.shadow_root or Path(tempfile.mkdtemp(prefix="datheon-typecheck-"))
        try:
            for project in project_directories(root / "projects"):
                shadow = build_shadow_tree(
                    project, scratch / sanitise_project_name(project.name), root
                )
                project_targets = tuple(module.name for module in python_files(project))
                if not project_targets:
                    continue
                phases.append(
                    run_phase(
                        project.name,
                        project_targets,
                        shadow,
                        settings.config_file,
                        settings.executable,
                        explicit_package_bases=True,
                    )
                )
        finally:
            if not settings.keep_shadow and settings.shadow_root is None:
                shutil.rmtree(scratch, ignore_errors=True)

    return replace(evaluate(phases, findings), mypy_version=mypy_version(settings.executable))


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the type gate.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="typecheck.py",
        description="Fail when the shipped code does not type-check under strict mypy.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the verdict as JSON instead of a table",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="list every diagnostic, however many there are",
    )
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="check the core modules and the datheon package, skipping the projects",
    )
    parser.add_argument(
        "--keep-shadow",
        action="store_true",
        help="keep the shadow trees on disk for triage",
    )
    parser.add_argument(
        "--shadow-root",
        type=Path,
        default=None,
        metavar="PATH",
        help="build the shadow trees under PATH instead of a temporary directory",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the gate and return the process exit code.

    Args:
        argv: Command-line arguments, without the program name. Defaults to
            :data:`sys.argv` without the program name.

    Returns:
        ``0`` when every phase is clean and the declaration is honest, ``1``
        when a type error or a finding was reported, and ``2`` when the gate
        could not measure — mypy is absent, or a phase failed to run.
    """
    args = build_parser().parse_args(None if argv is None else list(argv))

    if not mypy_available():
        print(
            "mypy is not installed in this environment; install it with "
            "`pip install -r requirements.txt`",
            file=sys.stderr,
        )
        return EXIT_UNMEASURABLE

    options = RunOptions(
        core_only=args.core_only,
        keep_shadow=args.keep_shadow,
        shadow_root=Path(args.shadow_root) if args.shadow_root else None,
    )
    verdict = run_gate(options)

    if args.json:
        print(json.dumps(verdict_to_dict(verdict), indent=2))
    else:
        print(render_report(verdict, verbose=args.verbose), end="")

    if verdict.unmeasurable:
        for phase in verdict.phases:
            if not phase.measured:
                print(
                    f"phase {phase.name!r} produced no verdict: {phase.stderr or 'mypy failed'}",
                    file=sys.stderr,
                )
        return EXIT_UNMEASURABLE
    if verdict.passed:
        return EXIT_CLEAN
    for diagnostic in verdict.errors:
        print(diagnostic.render(), file=sys.stderr)
    for finding in verdict.findings:
        print(f"{finding.code}: {finding.message}", file=sys.stderr)
    return EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
