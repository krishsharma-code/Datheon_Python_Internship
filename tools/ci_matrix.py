"""CI matrix audit: prove the build runs where the users actually are (Day 24).

Days 1–23 made the code correct, installable and shippable, and every one of
those verdicts came from a single runner: ``ubuntu-latest``. The projects open
files, create directories, rename temp files over their targets, print box-drawing
characters and register console scripts — four of the five things that behave
differently on Windows, and one (``os.replace`` over a file another handle still
holds) that behaves differently *only* there. A green Ubuntu job says nothing
about any of it.

So the workflow grows a real matrix — ``ubuntu-latest`` / ``macos-latest`` /
``windows-latest`` × Python 3.10–3.13, twelve cells — and this module audits it.
The distinction matters: a matrix is a *declaration*, and a declaration rots
silently. YAML does not validate it, and the ways it breaks are quiet ones:

* a version written unquoted as ``3.10`` is parsed as the float ``3.1``, so the
  cell is silently dropped and the grid looks one row short for no visible
  reason;
* ``fail-fast`` defaults to ``true``, so the first red Windows cell cancels the
  other eleven and the run reports one failure where there are three;
* ``cache: pip`` omitted from ``actions/setup-python`` costs four minutes a cell
  and nobody notices because the job still passes;
* a ``run:`` block written for bash silently becomes PowerShell on Windows —
  ``for tool in …; do "$tool" --version; done`` is a syntax error there, and the
  console scripts it checks are ``.exe`` files anyway;
* Python 3.13 gets added to ``classifiers`` in ``pyproject.toml`` and never to
  the matrix, so the package claims support it has never been tested for.

Each of those is a *finding* with a code, and the suite asserts a named failure
per defect rather than "the audit failed somehow".

Run it directly::

    python tools/ci_matrix.py                       # audit .github/workflows/ci.yml
    python tools/ci_matrix.py --list-cells          # print the expanded grid
    python tools/ci_matrix.py --strict              # warnings become errors too
    python tools/ci_matrix.py --json                # machine-readable verdict
    python tools/ci_matrix.py --require-python 3.14 # demand an extra version

Exit codes: ``0`` every check passed, ``1`` at least one finding, ``2`` the
audit could not reach a verdict (workflow missing or unparsable, PyYAML absent,
or no matrix job to measure). A gate that cannot measure must never report
success.

Requires ``PyYAML`` (``requirements.txt``); it is development tooling only — the
five projects remain standard-library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - exercised only by the missing-dependency test
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The workflow this audit exists for.
DEFAULT_WORKFLOW: Path = ROOT / ".github" / "workflows" / "ci.yml"

#: The packaging declaration the matrix must agree with.
DEFAULT_PYPROJECT: Path = ROOT / "pyproject.toml"

#: Every operating system the suite must run on. The order is the order the
#: findings are reported in, not a priority.
REQUIRED_OPERATING_SYSTEMS: Tuple[str, ...] = (
    "ubuntu-latest",
    "macos-latest",
    "windows-latest",
)

#: Every Python version the suite must run on, lowest first. Kept in step with
#: ``requires-python`` and the ``Programming Language :: Python :: 3.x``
#: classifiers by ``check_python_support()``.
REQUIRED_PYTHON_VERSIONS: Tuple[str, ...] = ("3.10", "3.11", "3.12", "3.13")

#: Commands every matrix cell must run, as ``(label, needle)``. ``needle`` is
#: matched against the cell's combined ``run:`` text, so it may be a fragment.
REQUIRED_CELL_COMMANDS: Tuple[Tuple[str, str], ...] = (
    ("syntax check", "compileall"),
    ("lint", "flake8 ."),
    ("format check", "black --check ."),
    ("test suite", "pytest"),
)

#: Accepted spellings of the matrix key holding the Python version.
PYTHON_VERSION_KEYS: Tuple[str, ...] = ("python-version", "python")

#: Accepted spellings of the matrix key holding the operating system.
OPERATING_SYSTEM_KEYS: Tuple[str, ...] = ("os", "platform")

#: Exit code for "the audit reached a verdict".
EXIT_OK: int = 0

#: Exit code for "the audit found something wrong".
EXIT_FINDINGS: int = 1

#: Exit code for "the audit could not measure anything".
EXIT_UNMEASURABLE: int = 2


class WorkflowError(Exception):
    """Raised when the workflow file cannot be read or parsed at all.

    A workflow that does not parse is not a passing workflow, so this maps to
    :data:`EXIT_UNMEASURABLE` rather than to a finding.
    """


class MatrixError(ValueError):
    """Raised when a ``strategy.matrix`` block is structurally unusable.

    Unlike :class:`WorkflowError` this does *not* stop the audit: the caller
    turns it into a finding, because the rest of the workflow is still worth
    checking.
    """


@dataclass(frozen=True, order=True)
class MatrixCell:
    """One runner in the grid.

    Attributes:
        os_name: The ``runs-on`` image, e.g. ``windows-latest``.
        python_version: The Python version as text, e.g. ``"3.10"``.
    """

    os_name: str
    python_version: str

    @property
    def label(self) -> str:
        """Return the human-readable cell name used in reports."""
        return f"{self.os_name} · Python {self.python_version}"

    def to_dict(self) -> Dict[str, str]:
        """Return a JSON-serialisable view of the cell."""
        return {"os": self.os_name, "python_version": self.python_version}


@dataclass(frozen=True)
class Finding:
    """One thing the audit does not like.

    Attributes:
        code: A stable dotted identifier, e.g. ``matrix.missing-cell``. The
            test suite asserts on these, so they are part of the contract.
        message: A one-line explanation written for whoever has to fix it.
        where: The part of the workflow the finding is about.
        severity: ``"error"`` fails the audit; ``"warning"`` fails it only
            under ``--strict``.
    """

    code: str
    message: str
    where: str = "ci.yml"
    severity: str = "error"

    @property
    def is_error(self) -> bool:
        """Return whether this finding fails a non-strict audit."""
        return self.severity == "error"

    def to_dict(self) -> Dict[str, str]:
        """Return a JSON-serialisable view of the finding."""
        return {
            "code": self.code,
            "message": self.message,
            "where": self.where,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class AuditVerdict:
    """The result of auditing one workflow.

    Attributes:
        workflow_path: The file that was audited.
        cells: The expanded grid, in canonical order.
        findings: Everything wrong with it, in discovery order.
        strict: Whether warnings counted as failures.
    """

    workflow_path: str
    cells: Tuple[MatrixCell, ...] = ()
    findings: Tuple[Finding, ...] = ()
    strict: bool = False

    @property
    def errors(self) -> Tuple[Finding, ...]:
        """Return the findings that fail even a non-strict audit."""
        return tuple(finding for finding in self.findings if finding.is_error)

    @property
    def warnings(self) -> Tuple[Finding, ...]:
        """Return the findings that only ``--strict`` promotes."""
        return tuple(finding for finding in self.findings if not finding.is_error)

    @property
    def ok(self) -> bool:
        """Return whether the workflow passed.

        A verdict with no cells is never ``ok``: an empty grid means the audit
        measured nothing, and silence is not evidence of correctness.
        """
        if not self.cells:
            return False
        if self.errors:
            return False
        return not (self.strict and self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable view of the verdict."""
        return {
            "workflow": self.workflow_path,
            "ok": self.ok,
            "strict": self.strict,
            "cell_count": len(self.cells),
            "cells": [cell.to_dict() for cell in self.cells],
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [finding.to_dict() for finding in self.findings],
        }


# ---------------------------------------------------------------------------
# Reading the workflow
# ---------------------------------------------------------------------------
def load_workflow(path: Path) -> Mapping[str, Any]:
    """Parse a GitHub Actions workflow file.

    Args:
        path: The workflow file to read.

    Returns:
        The parsed document, guaranteed to be a mapping.

    Raises:
        WorkflowError: If the file is missing, is not valid YAML, or its top
            level is not a mapping (a workflow is always a mapping of keys).
    """
    try:
        import yaml  # imported lazily so `--help` works without PyYAML
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise WorkflowError(
            "PyYAML is not installed; run `pip install -r requirements.txt` "
            "before auditing the workflow"
        ) from exc

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(f"cannot read {path}: {exc}") from exc

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(document, Mapping):
        raise WorkflowError(f"{path} does not contain a YAML mapping at the top level")
    return document


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` as a mapping, or an empty mapping when it is not one.

    Args:
        value: Any parsed YAML node.

    Returns:
        The value itself when it is a mapping, else ``{}``. Workflow files use
        ``on:`` as a bare key often enough that defensive access everywhere is
        cheaper than validating every level.
    """
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    """Return ``value`` as a list, or an empty list when it is not one."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _version_text(value: Any) -> str:
    """Normalise a matrix value to the text form a runner expects.

    YAML turns an unquoted ``3.10`` into the float ``3.1``, which is why the
    workflow quotes its versions and why ``check_version_types()`` reports the
    unquoted case as a defect. Normalising anyway keeps the *rest* of the audit
    useful on a broken file instead of aborting at the first cell.

    Args:
        value: A parsed YAML scalar.

    Returns:
        The value as text, without surrounding whitespace.
    """
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def _matrix_key(matrix: Mapping[str, Any], candidates: Sequence[str]) -> Optional[str]:
    """Return the first key in ``matrix`` matching one of ``candidates``."""
    for candidate in candidates:
        if candidate in matrix:
            return candidate
    return None


def expand_matrix(matrix: Mapping[str, Any]) -> Tuple[MatrixCell, ...]:
    """Expand a ``strategy.matrix`` block into the runners it will create.

    Implements the parts of GitHub's expansion rules this repository relies on:
    the Cartesian product of ``os`` and the Python version, minus every entry of
    ``exclude``, plus every entry of ``include`` that names both keys. An
    ``include`` entry that names only one key adds no runner — it attaches extra
    variables to existing ones — so it is ignored here.

    Args:
        matrix: The parsed ``strategy.matrix`` mapping.

    Returns:
        The cells, deduplicated and sorted by OS then version.

    Raises:
        MatrixError: If the block has no ``os`` key, no Python-version key, or
            a key whose value is not a list.
    """
    if not isinstance(matrix, Mapping):
        raise MatrixError("strategy.matrix is not a mapping")

    os_key = _matrix_key(matrix, OPERATING_SYSTEM_KEYS)
    python_key = _matrix_key(matrix, PYTHON_VERSION_KEYS)
    if os_key is None:
        raise MatrixError(f"strategy.matrix has no {' / '.join(OPERATING_SYSTEM_KEYS)} key")
    if python_key is None:
        raise MatrixError(f"strategy.matrix has no {' / '.join(PYTHON_VERSION_KEYS)} key")

    os_values = _as_list(matrix.get(os_key))
    python_values = _as_list(matrix.get(python_key))
    if not os_values:
        raise MatrixError(f"strategy.matrix.{os_key} is not a non-empty list")
    if not python_values:
        raise MatrixError(f"strategy.matrix.{python_key} is not a non-empty list")

    cells = {
        MatrixCell(_version_text(os_name), _version_text(version))
        for os_name in os_values
        for version in python_values
    }

    for entry in _as_list(matrix.get("exclude")):
        if not isinstance(entry, Mapping):
            continue
        wanted_os = _version_text(entry[os_key]) if os_key in entry else None
        wanted_python = _version_text(entry[python_key]) if python_key in entry else None
        cells = {
            cell
            for cell in cells
            if not (
                (wanted_os is None or cell.os_name == wanted_os)
                and (wanted_python is None or cell.python_version == wanted_python)
            )
        }

    for entry in _as_list(matrix.get("include")):
        if not isinstance(entry, Mapping):
            continue
        if os_key not in entry or python_key not in entry:
            continue
        cells.add(MatrixCell(_version_text(entry[os_key]), _version_text(entry[python_key])))

    return tuple(sorted(cells))


def find_matrix_jobs(workflow: Mapping[str, Any]) -> Tuple[Tuple[str, Mapping[str, Any]], ...]:
    """Return every job that declares an OS or Python matrix axis.

    A job is matched when its ``strategy.matrix`` names *either* axis, not both:
    a matrix with only ``python-version`` is exactly the mistake
    :func:`expand_matrix` should report as ``matrix.malformed``, and a detector
    that demanded both keys would hide it behind a generic "no matrix" finding.

    Args:
        workflow: The parsed workflow.

    Returns:
        ``(job_id, job)`` pairs, in file order.
    """
    found: List[Tuple[str, Mapping[str, Any]]] = []
    for job_id, job in _as_mapping(workflow.get("jobs")).items():
        matrix = _as_mapping(_as_mapping(job).get("strategy")).get("matrix")
        if not isinstance(matrix, Mapping):
            continue
        has_os = _matrix_key(matrix, OPERATING_SYSTEM_KEYS) is not None
        has_python = _matrix_key(matrix, PYTHON_VERSION_KEYS) is not None
        if has_os or has_python:
            found.append((str(job_id), _as_mapping(job)))
    return tuple(found)


def job_commands(job: Mapping[str, Any]) -> str:
    """Return every shell command a job runs, joined into one searchable string.

    Args:
        job: One entry of the workflow's ``jobs`` mapping.

    Returns:
        The ``run:`` bodies separated by newlines.
    """
    chunks: List[str] = []
    for step in _as_list(job.get("steps")):
        if not isinstance(step, Mapping):
            continue
        command = step.get("run")
        if isinstance(command, str):
            chunks.append(command)
    return "\n".join(chunks)


def job_shells(workflow: Mapping[str, Any], job: Mapping[str, Any]) -> Tuple[str, ...]:
    """Return the shells a job's ``run:`` steps execute under.

    Args:
        workflow: The parsed workflow, for its top-level ``defaults``.
        job: The job to inspect.

    Returns:
        The shell names in effect, most specific first: the step's own shell,
        then the job default, then the workflow default. Empty when nothing is
        declared, which on Windows means PowerShell.
    """
    shells: List[str] = []
    for step in _as_list(job.get("steps")):
        if isinstance(step, Mapping) and isinstance(step.get("shell"), str):
            shells.append(step["shell"])
    job_default = _as_mapping(_as_mapping(job.get("defaults")).get("run")).get("shell")
    if isinstance(job_default, str):
        shells.append(job_default)
    workflow_default = _as_mapping(_as_mapping(workflow.get("defaults")).get("run")).get("shell")
    if isinstance(workflow_default, str):
        shells.append(workflow_default)
    return tuple(shells)


def setup_python_steps(job: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    """Return the ``actions/setup-python`` steps of a job."""
    steps: List[Mapping[str, Any]] = []
    for step in _as_list(job.get("steps")):
        if not isinstance(step, Mapping):
            continue
        uses = step.get("uses")
        if isinstance(uses, str) and uses.split("@")[0] == "actions/setup-python":
            steps.append(step)
    return tuple(steps)


def needs_of(job: Mapping[str, Any]) -> Tuple[str, ...]:
    """Return the job ids a job declares in ``needs``, as text."""
    needs = job.get("needs")
    if isinstance(needs, str):
        return (needs,)
    return tuple(_version_text(need) for need in _as_list(needs))


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def check_permissions(workflow: Mapping[str, Any]) -> Iterator[Finding]:
    """Check the workflow declares least-privilege token permissions.

    A workflow with no ``permissions`` block gets the repository default, which
    is read/write on older repositories; a matrix job has no reason to hold
    anything but ``contents: read``.

    Args:
        workflow: The parsed workflow.

    Yields:
        One finding per problem.
    """
    permissions = workflow.get("permissions")
    if permissions is None:
        yield Finding(
            "permissions.missing",
            "the workflow declares no `permissions:` block, so it inherits the "
            "repository default; declare `contents: read`",
            "permissions",
        )
        return
    contents = _as_mapping(permissions).get("contents")
    if contents not in (None, "read"):
        yield Finding(
            "permissions.write",
            f"`permissions.contents` is {contents!r}; a build matrix only reads",
            "permissions",
        )


def check_version_types(matrix: Mapping[str, Any]) -> Iterator[Finding]:
    """Check every matrix value is a string, not a number YAML has mangled.

    Args:
        matrix: The parsed ``strategy.matrix`` mapping.

    Yields:
        One finding per key holding a non-string scalar, which is how an
        unquoted ``3.10`` silently becomes ``3.1``.
    """
    for key in OPERATING_SYSTEM_KEYS + PYTHON_VERSION_KEYS:
        if key not in matrix:
            continue
        values = _as_list(matrix.get(key))
        mangled = [value for value in values if not isinstance(value, str)]
        if mangled:
            rendered = ", ".join(repr(value) for value in mangled)
            yield Finding(
                "matrix.unquoted-version",
                f"`matrix.{key}` holds non-string values ({rendered}); quote them, "
                "or YAML parses 3.10 as the float 3.1 and the cell disappears",
                f"jobs.*.strategy.matrix.{key}",
            )


def check_fail_fast(job_id: str, job: Mapping[str, Any]) -> Iterator[Finding]:
    """Check the matrix keeps running after a cell fails.

    Args:
        job_id: The job's id, for the message.
        job: The matrix job.

    Yields:
        A finding when ``fail-fast`` is not explicitly ``false``.
    """
    strategy = _as_mapping(job.get("strategy"))
    if strategy.get("fail-fast") is not False:
        yield Finding(
            "matrix.fail-fast",
            f"job `{job_id}` does not set `fail-fast: false`; the first red cell "
            "cancels the rest, so a run reports one failure where there are three",
            f"jobs.{job_id}.strategy.fail-fast",
        )


def check_pip_cache(job_id: str, job: Mapping[str, Any]) -> Iterator[Finding]:
    """Check every ``setup-python`` step enables the pip cache.

    Args:
        job_id: The job's id, for the message.
        job: The matrix job.

    Yields:
        One finding per setup step without ``cache: pip``, plus one when the job
        never sets Python up at all.
    """
    steps = setup_python_steps(job)
    if not steps:
        yield Finding(
            "matrix.setup-python",
            f"job `{job_id}` never uses `actions/setup-python`, so the matrix "
            "value never reaches the interpreter",
            f"jobs.{job_id}.steps",
        )
        return
    for index, step in enumerate(steps, start=1):
        with_block = _as_mapping(step.get("with"))
        if with_block.get("cache") != "pip":
            yield Finding(
                "matrix.pip-cache",
                f"`actions/setup-python` step {index} in `{job_id}` does not set "
                "`cache: pip`; twelve cells without a cache is twelve downloads "
                "of the same wheels",
                f"jobs.{job_id}.steps[{index}].with.cache",
            )


def check_shell(
    workflow: Mapping[str, Any], job_id: str, job: Mapping[str, Any]
) -> Iterator[Finding]:
    """Check the job's commands run under a shell that exists on every runner.

    Windows runners default to PowerShell, where a bash ``for … done`` loop is a
    syntax error — and where the console scripts are ``.exe`` files anyway.

    Args:
        workflow: The parsed workflow, for its top-level defaults.
        job_id: The job's id, for the message.
        job: The matrix job.

    Yields:
        A finding when no ``bash`` shell is in effect for the job.
    """
    shells = job_shells(workflow, job)
    if not any(shell.split()[0] == "bash" for shell in shells if shell.strip()):
        yield Finding(
            "matrix.shell",
            f"job `{job_id}` does not run under bash; on windows-latest the "
            "default is PowerShell, where the POSIX commands in the steps fail. "
            "Set `defaults: run: shell: bash`",
            f"jobs.{job_id}.defaults.run.shell",
        )


def check_commands(job_id: str, job: Mapping[str, Any]) -> Iterator[Finding]:
    """Check every matrix cell runs the checks the suite depends on.

    Args:
        job_id: The job's id, for the message.
        job: The matrix job.

    Yields:
        One finding per required command missing from the job's steps.
    """
    commands = job_commands(job)
    for label, needle in REQUIRED_CELL_COMMANDS:
        if needle not in commands:
            yield Finding(
                "matrix.command",
                f"job `{job_id}` never runs the {label} (`{needle}`), so a cell "
                "can pass without it",
                f"jobs.{job_id}.steps",
            )


def check_grid(
    job_id: str,
    cells: Sequence[MatrixCell],
    required_os: Sequence[str],
    required_python: Sequence[str],
) -> Iterator[Finding]:
    """Check the expanded grid covers every required runner.

    Args:
        job_id: The job's id, for the message.
        cells: The expanded grid.
        required_os: Operating systems that must be present.
        required_python: Python versions that must be present.

    Yields:
        One finding per missing cell, and one warning per cell nobody asked for.
    """
    present = set(cells)
    for os_name in required_os:
        for version in required_python:
            cell = MatrixCell(os_name, version)
            if cell not in present:
                yield Finding(
                    "matrix.missing-cell",
                    f"job `{job_id}` has no {cell.label} cell",
                    f"jobs.{job_id}.strategy.matrix",
                )
    required = {
        MatrixCell(os_name, version) for os_name in required_os for version in required_python
    }
    for cell in sorted(present - required):
        yield Finding(
            "matrix.unexpected-cell",
            f"job `{job_id}` runs {cell.label}, which is not in the required grid; "
            "that is fine, but it is also untracked",
            f"jobs.{job_id}.strategy.matrix",
            severity="warning",
        )


def check_jobs_runnable(workflow: Mapping[str, Any]) -> Iterator[Finding]:
    """Check every job names a runner.

    Args:
        workflow: The parsed workflow.

    Yields:
        One finding per job without ``runs-on``.
    """
    for job_id, job in _as_mapping(workflow.get("jobs")).items():
        if not _as_mapping(job).get("runs-on"):
            yield Finding(
                "job.runs-on",
                f"job `{job_id}` declares no `runs-on`, so it cannot be scheduled",
                f"jobs.{job_id}.runs-on",
            )


def check_aggregate_job(
    workflow: Mapping[str, Any], matrix_job_ids: Sequence[str]
) -> Iterator[Finding]:
    """Check a single summary job gates the whole grid.

    Twelve independent check runs cannot be one required status check; a job
    that ``needs`` the matrix and runs ``if: always()`` can.

    Args:
        workflow: The parsed workflow.
        matrix_job_ids: The ids of the matrix jobs.

    Yields:
        A finding when no such job exists.
    """
    for job_id, job in _as_mapping(workflow.get("jobs")).items():
        job = _as_mapping(job)
        if str(job_id) in matrix_job_ids:
            continue
        if not set(needs_of(job)) & set(matrix_job_ids):
            continue
        if "always()" in _version_text(job.get("if", "")):
            return
    yield Finding(
        "matrix.aggregate",
        "no job depends on the matrix with `if: always()`, so the twelve cells "
        "cannot be one required status check",
        "jobs",
    )


def check_python_support(
    pyproject: Mapping[str, Any],
    cells: Sequence[MatrixCell],
    required_python: Sequence[str],
) -> Iterator[Finding]:
    """Check the packaging metadata agrees with the matrix.

    Args:
        pyproject: The parsed ``pyproject.toml``.
        cells: The expanded grid.
        required_python: The Python versions the grid is required to cover.

    Yields:
        A finding per disagreement between the declaration and the grid.
    """
    project = _as_mapping(pyproject.get("project"))
    classifiers = [str(item) for item in _as_list(project.get("classifiers"))]

    declared = {
        classifier.rsplit(" :: ", 1)[-1]
        for classifier in classifiers
        if classifier.startswith("Programming Language :: Python :: 3.")
    }
    required = set(required_python)
    for version in sorted(required - declared):
        yield Finding(
            "pyproject.classifier",
            f"the matrix runs Python {version} but `classifiers` does not claim "
            f"`Programming Language :: Python :: {version}`",
            "pyproject.toml [project] classifiers",
        )
    for version in sorted(declared - required):
        yield Finding(
            "pyproject.classifier",
            f"`classifiers` claims Python {version} but the matrix never runs it",
            "pyproject.toml [project] classifiers",
        )

    requires = _version_text(project.get("requires-python", ""))
    floor = requires.lstrip(">=~ ").strip()
    lowest = min(required_python, key=_version_key) if required_python else ""
    if floor and lowest and _version_key(floor) != _version_key(lowest):
        yield Finding(
            "pyproject.floor",
            f"`requires-python` is {requires!r} but the oldest cell is Python "
            f"{lowest}; the floor and the grid must agree",
            "pyproject.toml [project] requires-python",
        )
    if not any(cell.python_version for cell in cells):
        yield Finding(
            "pyproject.floor",
            "the grid has no Python version to compare `requires-python` with",
            "pyproject.toml [project] requires-python",
        )


def _version_key(version: str) -> Tuple[int, ...]:
    """Return a sortable key for a dotted Python version string.

    Args:
        version: A version such as ``"3.10"`` or ``"3.13.1"``.

    Returns:
        The numeric parts as a tuple, so ``"3.10"`` sorts above ``"3.9"``.
    """
    parts: List[int] = []
    for chunk in str(version).split("."):
        digits = "".join(character for character in chunk if character.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


# ---------------------------------------------------------------------------
# Putting the checks together
# ---------------------------------------------------------------------------
def audit_workflow(
    workflow: Mapping[str, Any],
    *,
    workflow_path: str = "ci.yml",
    strict: bool = False,
    required_os: Sequence[str] = REQUIRED_OPERATING_SYSTEMS,
    required_python: Sequence[str] = REQUIRED_PYTHON_VERSIONS,
    pyproject: Optional[Mapping[str, Any]] = None,
) -> AuditVerdict:
    """Audit a parsed workflow against the matrix contract.

    Args:
        workflow: The parsed workflow.
        workflow_path: The path to report in the verdict.
        strict: Whether warnings count as failures.
        required_os: Operating systems the grid must cover.
        required_python: Python versions the grid must cover.
        pyproject: The parsed ``pyproject.toml``, when the caller has one.

    Returns:
        The verdict. It carries the expanded cells even when it fails, because
        "which cells did you think you had" is the first question anyone asks.
    """
    findings: List[Finding] = list(check_permissions(workflow))
    findings.extend(check_jobs_runnable(workflow))

    matrix_jobs = find_matrix_jobs(workflow)
    cells: Tuple[MatrixCell, ...] = ()
    if not matrix_jobs:
        findings.append(
            Finding(
                "matrix.missing",
                "no job declares a strategy.matrix over `os` and `python-version`, "
                "so the suite runs on one runner only",
                "jobs",
            )
        )
    for job_id, job in matrix_jobs:
        matrix = _as_mapping(_as_mapping(job.get("strategy")).get("matrix"))
        findings.extend(check_version_types(matrix))
        try:
            expanded = expand_matrix(matrix)
        except MatrixError as exc:
            findings.append(Finding("matrix.malformed", str(exc), f"jobs.{job_id}.strategy.matrix"))
            continue
        if not cells:
            cells = expanded
        findings.extend(check_grid(job_id, expanded, required_os, required_python))
        findings.extend(check_fail_fast(job_id, job))
        findings.extend(check_pip_cache(job_id, job))
        findings.extend(check_shell(workflow, job_id, job))
        findings.extend(check_commands(job_id, job))

    findings.extend(check_aggregate_job(workflow, [job_id for job_id, _ in matrix_jobs]))

    if pyproject is not None:
        findings.extend(check_python_support(pyproject, cells, required_python))

    return AuditVerdict(
        workflow_path=workflow_path,
        cells=cells,
        findings=tuple(findings),
        strict=strict,
    )


def render_report(verdict: AuditVerdict) -> str:
    """Render a verdict as the text a build log should show.

    Args:
        verdict: The verdict to render.

    Returns:
        A multi-line report: the grid, then each finding, then the verdict.
    """
    lines: List[str] = [
        f"CI matrix audit: {verdict.workflow_path}",
        f"  cells: {len(verdict.cells)}",
    ]
    for cell in verdict.cells:
        lines.append(f"    - {cell.label}")
    if verdict.findings:
        lines.append("")
        for finding in verdict.findings:
            marker = "FAIL" if finding.is_error else "WARN"
            lines.append(f"  [{marker}] {finding.code}: {finding.message}")
            lines.append(f"         at {finding.where}")
    lines.append("")
    errors = len(verdict.errors)
    warnings = len(verdict.warnings)
    if verdict.ok:
        lines.append(f"PASS  {len(verdict.cells)} cells, {warnings} warning(s)")
    elif not verdict.cells:
        lines.append("NO VERDICT  the workflow declares no measurable matrix")
    else:
        lines.append(f"FAIL  {errors} error(s), {warnings} warning(s)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser accepting the audit's options.
    """
    parser = argparse.ArgumentParser(
        prog="ci-matrix",
        description=(
            "Audit the CI workflow's OS × Python matrix: required cells present, "
            "fail-fast off, pip cache on, bash shell, least-privilege permissions, "
            "and agreement with pyproject.toml."
        ),
    )
    parser.add_argument(
        "--workflow",
        default=str(DEFAULT_WORKFLOW),
        help="workflow file to audit (default: .github/workflows/ci.yml)",
    )
    parser.add_argument(
        "--pyproject",
        default=str(DEFAULT_PYPROJECT),
        help="packaging declaration to cross-check (default: pyproject.toml)",
    )
    parser.add_argument(
        "--no-pyproject",
        action="store_true",
        help="skip the pyproject.toml cross-check",
    )
    parser.add_argument(
        "--require-os",
        action="append",
        metavar="IMAGE",
        help="require this runner image (repeatable; replaces the default grid)",
    )
    parser.add_argument(
        "--require-python",
        action="append",
        metavar="VERSION",
        help="require this Python version (repeatable; replaces the default grid)",
    )
    parser.add_argument(
        "--list-cells",
        action="store_true",
        help="print the expanded grid, one cell per line",
    )
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    return parser


def load_pyproject(path: Path) -> Optional[Mapping[str, Any]]:
    """Parse ``pyproject.toml``, returning ``None`` when it is not there.

    Args:
        path: The declaration to read.

    Returns:
        The parsed document, or ``None`` when the file does not exist.
    """
    if not Path(path).is_file():
        return None
    with Path(path).open("rb") as handle:
        return tomllib.load(handle)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit and print its verdict.

    Args:
        argv: Arguments to parse; ``None`` means ``sys.argv[1:]``.

    Returns:
        :data:`EXIT_OK` when the workflow passes, :data:`EXIT_FINDINGS` when it
        does not, :data:`EXIT_UNMEASURABLE` when nothing could be measured.
    """
    args = build_parser().parse_args(argv)

    try:
        workflow = load_workflow(Path(args.workflow))
    except WorkflowError as exc:
        print(f"ci-matrix: {exc}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    try:
        pyproject = None if args.no_pyproject else load_pyproject(Path(args.pyproject))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"ci-matrix: cannot read {args.pyproject}: {exc}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    verdict = audit_workflow(
        workflow,
        workflow_path=args.workflow,
        strict=args.strict,
        required_os=tuple(args.require_os or ()) or REQUIRED_OPERATING_SYSTEMS,
        required_python=tuple(args.require_python or ()) or REQUIRED_PYTHON_VERSIONS,
        pyproject=pyproject,
    )

    if args.list_cells:
        for cell in verdict.cells:
            print(cell.label)
        return EXIT_OK if verdict.cells else EXIT_UNMEASURABLE

    print(json.dumps(verdict.to_dict(), indent=2) if args.json else render_report(verdict))

    if not verdict.cells:
        return EXIT_UNMEASURABLE
    return EXIT_OK if verdict.ok else EXIT_FINDINGS


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
