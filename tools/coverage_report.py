"""Audit the coverage-reporting pipeline (Day 36).

``tools/coverage_gate.py`` (Day 20) answers *is the number high enough* on the
runner that runs it, and then throws the report away. Nobody outside that build
log ever sees it: a reviewer cannot tell whether a pull request dropped coverage
from 94% to 91%, a maintainer cannot see the trend, and the badge a README wants
to carry has no data behind it. Day 36 publishes the measurement — the workflow
in ``.github/workflows/coverage.yml`` measures, gates and uploads
``coverage.xml`` to Codecov, and ``codecov.yml`` declares what "good" means.

Publishing a number is where a repository starts trusting a *third party* to
state a fact about its own code, so the declaration has to be checked like every
other declaration here:

* an **upload that silently finds nothing** is worse than no upload, because the
  badge keeps showing the last good number;
* a **project target** that disagrees with ``[tool.coverage.report] fail_under``
  means the local gate and the badge enforce two different floors;
* an **ignore list** that disagrees with ``[tool.coverage.run] omit`` means the
  two places measure different files, so "90%" means two things;
* a **floating action tag** (``@master``) lets the uploader change without a
  commit here — the same rule ``tools/precommit_config.py`` applies to hooks;
* a **`fail_ci_if_error: true`** hands a third party the power to redden a build
  that passed every local gate;
* a **`contents: write`** token on a job that only reads the checkout is more
  authority than the job can use.

So this module reads the workflow, ``codecov.yml``, ``pyproject.toml``,
``.gitignore`` and the tool's own page as data, and reports a stable finding
code for each defect:

========================  ====================================================
Code                      What it means
========================  ====================================================
``coverage.workflow``     The coverage workflow is missing, or has no job steps.
``coverage.trigger``      The workflow does not run on push and pull request
                          against ``main``, so the badge stops tracking.
``coverage.action``       No Codecov upload step, or the action floats on a tag
                          that carries no version (``@master``).
``coverage.files``        The upload names no report the job actually produces,
                          so it can upload nothing and still report success.
``coverage.token``        A literal token where a ``${{ secrets.… }}`` reference
                          belongs.
``coverage.fail-ci``      ``fail_ci_if_error`` is not ``false``: a Codecov
                          outage can redden a build every local gate passed.
``coverage.artifact``     The job keeps no copy of the report, so a failed upload
                          leaves nothing to inspect.
``coverage.permissions``  The job grants more than it uses, or omits the
                          ``id-token: write`` a tokenless upload needs.
``coverage.config``       ``codecov.yml`` is missing or unparsable.
``coverage.target``       The Codecov project target disagrees with
                          ``fail_under`` in ``pyproject.toml``.
``coverage.ignore``       The Codecov ignore list disagrees with
                          ``[tool.coverage.run] omit``, or hides shipped code.
``coverage.flags``        A flag is uploaded but undeclared, or declared and
                          never uploaded.
``coverage.gitignore``    A report artefact is not ignored, so the next
                          ``git add .`` commits it.
``coverage.docs``         ``docs/coverage_reporting.md`` is missing, or no
                          longer names a code this module can emit.
========================  ====================================================

The last code is the trick ``tools/community_health.py`` uses on its own page:
the documentation fails the build when it stops describing the tool, so the
table of codes cannot drift behind the checks.

Run it directly::

    python tools/coverage_report.py              # human-readable report
    python tools/coverage_report.py --json       # machine-readable
    python tools/coverage_report.py --strict     # warnings count as failures
    python tools/coverage_report.py --root .     # audit another checkout

Exit codes: ``0`` the pipeline passes, ``1`` at least one finding, ``2`` the
audit could not reach a verdict (the root is not a directory, PyYAML is absent,
the workflow is unparsable, or there is no ``pyproject.toml`` to compare
against). A gate that cannot measure must never report success.

Requires ``PyYAML`` (``requirements.txt``), like ``tools/ci_matrix.py``; it is
development tooling only — the five projects remain standard-library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - exercised only by the missing-dependency test
    import yaml
except ModuleNotFoundError:  # pragma: no cover - PyYAML is in requirements.txt
    yaml = None  # type: ignore[assignment]

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on a bare Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The workflow that measures, gates and publishes the report.
WORKFLOW = Path(".github") / "workflows" / "coverage.yml"

#: The Codecov declaration: what "good" means, and what is not measured.
CODECOV_CONFIG = Path("codecov.yml")

#: The page that documents this tool, and must name every code below.
DOCS_PAGE = Path("docs") / "coverage_reporting.md"

#: The two declarations the workflow has to agree with.
PYPROJECT = Path("pyproject.toml")
GITIGNORE = Path(".gitignore")

#: The uploader, by repository, and the shape a pin must have: ``@v5`` or
#: ``@v5.4.0`` — a major tag or a full version, never a branch name.
CODECOV_ACTION = "codecov/codecov-action"
ACTION_PIN_RE = re.compile(r"^v\d+(?:\.\d+){0,2}$")

#: The artefact the workflow must produce, upload and keep.
REPORT_XML = "coverage.xml"

#: The machine-readable report the local gate reads.
REPORT_JSON = "coverage.json"

#: Both reports, in the order a finding names them.
REPORTS: Tuple[str, ...] = (REPORT_XML, REPORT_JSON)

#: A ``--cov-report=xml:path`` argument, with or without the explicit path.
_COV_REPORT_RE = re.compile(r"--cov-report=(?P<kind>xml|json)(?::(?P<path>[^\s\\]+))?")

#: The event that publishes a coverage claim, and the event that checks one.
REQUIRED_EVENTS: Tuple[str, ...] = ("push", "pull_request")

#: Branch names that mean "the default branch" in a trigger filter.
MAIN_NAMES = frozenset({"main", "*", "**"})

#: Token shapes that must never be written into a workflow. Only lines that
#: mention a token are scanned, so a commit SHA pinned as ``@<40 hex>`` is not
#: mistaken for a credential.
SECRET_PATTERNS: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    re.compile(r"\b[0-9a-f]{32,}\b"),
)

#: Every finding code this module can emit, in documentation order.
CODES: Tuple[str, ...] = (
    "coverage.workflow",
    "coverage.trigger",
    "coverage.action",
    "coverage.files",
    "coverage.token",
    "coverage.fail-ci",
    "coverage.artifact",
    "coverage.permissions",
    "coverage.config",
    "coverage.target",
    "coverage.ignore",
    "coverage.flags",
    "coverage.gitignore",
    "coverage.docs",
)


@dataclass(frozen=True)
class Finding:
    """One defect in the coverage-reporting pipeline.

    Attributes:
        code: Stable identifier (``coverage.files``, ``coverage.target``, …).
        location: File the finding is about, relative to the repository root.
        detail: Human-readable explanation naming what is wrong.
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


def _location(root: Path, path: Path) -> str:
    """Render a path the way findings name it.

    Args:
        root: Repository root.
        path: The file a finding is about.

    Returns:
        The path relative to ``root`` when it is inside it, else the bare name.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:  # a file outside the repository, as in the tests
        return path.name


def load_yaml(path: Path) -> Optional[Any]:
    """Parse a YAML file into Python data.

    Args:
        path: File to read.

    Returns:
        The parsed document, or ``None`` when PyYAML is absent, the file cannot
        be read, or it is not valid YAML.
    """
    if yaml is None:  # pragma: no cover - exercised by the missing-dependency test
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def workflow_triggers(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the ``on:`` block, whatever key type the YAML parser produced.

    YAML 1.1 — which PyYAML implements — turns the unquoted key ``on`` into the
    boolean ``True``. Both spellings are the same declaration.

    Args:
        document: Parsed workflow.

    Returns:
        The trigger block as a mapping; empty when there is none.
    """
    value = document.get("on", document.get(True))
    if isinstance(value, str):
        return {value: None}
    if isinstance(value, list):
        return {str(event): None for event in value}
    return value if isinstance(value, Mapping) else {}


def event_config(triggers: Mapping[str, Any], event: str) -> Tuple[bool, Any]:
    """Look up one trigger event.

    Args:
        triggers: The ``on:`` block from :func:`workflow_triggers`.
        event: Event name, e.g. ``push``.

    Returns:
        ``(present, config)`` — whether the event is declared at all, and its
        configuration (``None`` for a bare event name).
    """
    if event not in triggers:
        return False, None
    return True, triggers[event]


def branch_filter(config: Any) -> Optional[List[str]]:
    """Read the branch names a trigger event is limited to.

    Args:
        config: One event's configuration.

    Returns:
        The declared branch names, or ``None`` when the event has no filter and
        therefore runs on every branch.
    """
    if not isinstance(config, Mapping):
        return None
    branches = config.get("branches")
    if isinstance(branches, str):
        return [branches]
    if isinstance(branches, list):
        return [str(branch) for branch in branches]
    return None


def job_steps(document: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """Collect every step of every job, in document order.

    Args:
        document: Parsed workflow.

    Returns:
        The step mappings; empty when the workflow declares no jobs.
    """
    jobs = document.get("jobs")
    if not isinstance(jobs, Mapping):
        return []
    steps: List[Mapping[str, Any]] = []
    for job in jobs.values():
        if not isinstance(job, Mapping):
            continue
        declared = job.get("steps")
        if isinstance(declared, list):
            steps.extend(step for step in declared if isinstance(step, Mapping))
    return steps


def run_text(steps: Iterable[Mapping[str, Any]]) -> str:
    """Join the shell every step runs into one searchable string.

    Args:
        steps: Step mappings from :func:`job_steps`.

    Returns:
        The concatenated ``run:`` bodies.
    """
    return "\n".join(str(step.get("run", "")) for step in steps)


def produced_reports(text: str) -> Dict[str, str]:
    """Read the report paths the shell commands ask ``pytest-cov`` to write.

    Args:
        text: Concatenated ``run:`` bodies.

    Returns:
        ``{"xml": path, "json": path}`` for each report the commands request,
        with ``pytest-cov``'s own default filled in when no path is given.
    """
    produced: Dict[str, str] = {}
    for match in _COV_REPORT_RE.finditer(text):
        kind = match.group("kind")
        path = match.group("path") or (REPORT_XML if kind == "xml" else REPORT_JSON)
        produced.setdefault(kind, path.lstrip("./"))
    return produced


def step_input(step: Mapping[str, Any], key: str) -> Optional[str]:
    """Read one ``with:`` input of a step.

    Args:
        step: A step mapping.
        key: Input name, e.g. ``files``.

    Returns:
        The input as text (lists joined by commas), or ``None`` when absent.
    """
    inputs = step.get("with")
    if not isinstance(inputs, Mapping) or key not in inputs:
        return None
    value = inputs[key]
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def uses_of(step: Mapping[str, Any]) -> str:
    """Read a step's ``uses:`` reference.

    Args:
        step: A step mapping.

    Returns:
        The reference, or an empty string for a ``run:`` step.
    """
    return str(step.get("uses", ""))


def codecov_steps(steps: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """Select the steps that invoke the Codecov uploader.

    Args:
        steps: Step mappings from :func:`job_steps`.

    Returns:
        The matching steps, in document order.
    """
    return [step for step in steps if uses_of(step).startswith(CODECOV_ACTION)]


def uploaded_paths(step: Mapping[str, Any]) -> List[str]:
    """Read the report paths an upload step names.

    Args:
        step: A Codecov step.

    Returns:
        The paths, normalised without a leading ``./``; empty when the step
        declares no ``files`` input at all.
    """
    declared = step_input(step, "files")
    if declared is None:
        return []
    paths: List[str] = []
    for raw in re.split(r"[,\s]+", declared):
        candidate = raw.strip().lstrip("./")
        if candidate and candidate not in paths:
            paths.append(candidate)
    return paths


def _base_of(pattern: str) -> str:
    """Reduce a coverage path pattern to the directory or file it names.

    Args:
        pattern: A glob such as ``tests/*`` or ``./tools/**``.

    Returns:
        The leading directory or file name, without glob characters.
    """
    reduced = pattern.strip().lstrip("./")
    reduced = re.sub(r"/?\*+.*$", "", reduced)
    return reduced or pattern.strip()


def pyproject_coverage(root: Path) -> Optional[Tuple[float, List[str], List[str]]]:
    """Read the coverage floor, the omit list and the shipped module names.

    Args:
        root: Repository root.

    Returns:
        ``(fail_under, omit, py_modules)``, or ``None`` when
        ``pyproject.toml`` is missing, unparsable, or declares no coverage
        floor — in which case there is nothing to compare against.
    """
    path = root / PYPROJECT
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    tool = data.get("tool", {})
    floor = tool.get("coverage", {}).get("report", {}).get("fail_under")
    if not isinstance(floor, (int, float)):
        return None
    omit = tool.get("coverage", {}).get("run", {}).get("omit", [])
    modules = tool.get("setuptools", {}).get("py-modules", [])
    return (
        float(floor),
        [str(item) for item in omit if isinstance(item, str)],
        [str(item) for item in modules if isinstance(item, str)],
    )


def codecov_coverage(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the ``coverage:`` block of ``codecov.yml``.

    Args:
        document: Parsed ``codecov.yml``.

    Returns:
        The block as a mapping; empty when it is absent or malformed.
    """
    block = document.get("coverage")
    return block if isinstance(block, Mapping) else {}


def project_target(document: Mapping[str, Any]) -> Optional[float]:
    """Read the Codecov project target, the floor the badge enforces.

    Args:
        document: Parsed ``codecov.yml``.

    Returns:
        The target as a number, or ``None`` when it is absent or is ``auto``
        (a target read off the previous commit, which no local gate can match).
    """
    status = codecov_coverage(document).get("status")
    if not isinstance(status, Mapping):
        return None
    project = status.get("project")
    if not isinstance(project, Mapping):
        return None
    default = project.get("default")
    if not isinstance(default, Mapping):
        return None
    target = default.get("target")
    if isinstance(target, (int, float)):
        return float(target)
    return None


def ignore_entries(document: Mapping[str, Any]) -> List[str]:
    """Read the paths ``codecov.yml`` tells Codecov not to measure.

    Args:
        document: Parsed ``codecov.yml``.

    Returns:
        The entries as written; empty when there is no ignore list.
    """
    entries = codecov_coverage(document).get("ignore")
    if not isinstance(entries, list):
        return []
    return [str(entry) for entry in entries]


def declared_flags(document: Mapping[str, Any]) -> List[str]:
    """Read the flag names ``codecov.yml`` declares.

    Args:
        document: Parsed ``codecov.yml``.

    Returns:
        The flag names, in declaration order.
    """
    flags = document.get("flags")
    if not isinstance(flags, Mapping):
        return []
    return [str(name) for name in flags]


def workflow_flags(steps: Sequence[Mapping[str, Any]]) -> List[str]:
    """Read the flags the workflow uploads with.

    Args:
        steps: Step mappings from :func:`job_steps`.

    Returns:
        The flag names named by the upload steps, deduplicated.
    """
    names: List[str] = []
    for step in codecov_steps(steps):
        declared = step_input(step, "flags")
        if not declared:
            continue
        for raw in re.split(r"[,\s]+", declared):
            name = raw.strip()
            if name and name not in names:
                names.append(name)
    return names


def gitignore_rules(root: Path) -> List[str]:
    """Read the patterns ``.gitignore`` declares.

    Args:
        root: Repository root.

    Returns:
        The patterns, comments and blank lines removed; empty when the file is
        missing.
    """
    path = root / GITIGNORE
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _token_findings(text: str, location: str) -> List[Finding]:
    """Look for a literal token written where a secret reference belongs.

    Only lines that mention a token are scanned: a commit SHA pinned as
    ``@<40 hex>`` is a pin, not a credential, and flagging it would teach the
    reader to ignore this check.

    Args:
        text: Workflow text.
        location: File name used in the findings.

    Returns:
        One finding per token-shaped literal.
    """
    findings: List[Finding] = []
    for line in text.splitlines():
        lowered = line.lower()
        if "token" not in lowered or "secrets." in lowered:
            continue
        if lowered.lstrip().startswith("#"):
            continue
        for pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(
                    Finding(
                        "coverage.token",
                        location,
                        f"literal token {match.group(0)[:8]}… where "
                        f"${{{{ secrets.CODECOV_TOKEN }}}} belongs",
                    )
                )
                break
    return findings


def audit_workflow(root: Path, document: Mapping[str, Any], text: str) -> List[Finding]:
    """Check the workflow that measures, gates and uploads the report.

    Args:
        root: Repository root.
        document: Parsed workflow.
        text: The workflow's raw text, for the token scan.

    Returns:
        Findings for triggers, the uploader, the report paths, tokens, the
        failure policy, the retained artefact and the granted permissions.
    """
    location = _location(root, root / WORKFLOW)
    findings: List[Finding] = []
    triggers = workflow_triggers(document)
    steps = job_steps(document)
    if not steps:
        return [Finding("coverage.workflow", location, "declares no job steps")]

    for event in REQUIRED_EVENTS:
        present, config = event_config(triggers, event)
        if not present:
            findings.append(Finding("coverage.trigger", location, f"does not run on {event!r}"))
            continue
        branches = branch_filter(config)
        if branches is None:
            # Noisy rather than wrong: every branch publishes a report, so a
            # feature branch's number can land where a reader expects main's.
            findings.append(
                Finding(
                    "coverage.trigger",
                    location,
                    f"the {event!r} trigger names no branch filter, so every branch "
                    "publishes a report",
                    severity="warning",
                )
            )
        elif not MAIN_NAMES.intersection(branches):
            findings.append(
                Finding(
                    "coverage.trigger",
                    location,
                    f"the {event!r} trigger filters to {', '.join(branches)}, not main",
                )
            )

    uploads = codecov_steps(steps)
    if not uploads:
        findings.append(Finding("coverage.action", location, f"no step uses {CODECOV_ACTION}"))
    for step in uploads:
        reference = uses_of(step)
        pin = reference.split("@", 1)[1] if "@" in reference else ""
        if not ACTION_PIN_RE.match(pin):
            findings.append(
                Finding(
                    "coverage.action",
                    location,
                    f"{reference!r} is not pinned to a version — @{pin or 'nothing'} "
                    "can change without a commit here",
                )
            )

    produced = produced_reports(run_text(steps))
    for kind, path in produced.items():
        if kind == "xml" and path != REPORT_XML:
            findings.append(
                Finding(
                    "coverage.files",
                    location,
                    f"writes the XML report to {path!r}; the uploader is checked "
                    f"against {REPORT_XML!r}",
                )
            )
    for step in uploads:
        named = uploaded_paths(step)
        if not named:
            findings.append(
                Finding(
                    "coverage.files",
                    location,
                    "names no report to upload, so the uploader searches and may "
                    "find nothing while reporting success",
                )
            )
            continue
        known = set(produced.values())
        for path in named:
            if path not in known:
                findings.append(
                    Finding(
                        "coverage.files",
                        location,
                        f"uploads {path!r}, which no step of this job writes",
                    )
                )

    for step in uploads:
        fail_ci = step_input(step, "fail_ci_if_error")
        if fail_ci is None or fail_ci.strip().lower() != "false":
            findings.append(
                Finding(
                    "coverage.fail-ci",
                    location,
                    "fail_ci_if_error is not false — a third-party outage would "
                    "redden a build every local gate passed",
                )
            )
        token = step_input(step, "token")
        if token is not None and "secrets." not in token:
            findings.append(
                Finding(
                    "coverage.token",
                    location,
                    "the token input is not a ${{ secrets.… }} reference",
                )
            )
        oidc = (step_input(step, "use_oidc") or "").strip().lower()
        if token is None and oidc != "true":
            findings.append(
                Finding(
                    "coverage.permissions",
                    location,
                    "no token and use_oidc is not true, so a tokenless upload "
                    "cannot authenticate",
                )
            )

    keepers = [step for step in steps if uses_of(step).startswith("actions/upload-artifact")]
    kept = "\n".join(step_input(step, "path") or "" for step in keepers)
    if REPORT_XML not in kept:
        findings.append(
            Finding(
                "coverage.artifact",
                location,
                f"keeps no copy of {REPORT_XML}, so a failed upload leaves no evidence",
            )
        )

    permissions = document.get("permissions")
    if not isinstance(permissions, Mapping):
        findings.append(
            Finding(
                "coverage.permissions",
                location,
                "declares no permissions block, so every job gets the default scope",
            )
        )
    else:
        if str(permissions.get("contents", "")).lower() == "write":
            findings.append(
                Finding(
                    "coverage.permissions",
                    location,
                    "grants contents: write to a job that only reads the checkout",
                )
            )
        if str(permissions.get("id-token", "")).lower() != "write":
            findings.append(
                Finding(
                    "coverage.permissions",
                    location,
                    "does not grant id-token: write, which the tokenless upload needs",
                )
            )

    findings.extend(_token_findings(text, location))
    return findings


def audit_config(
    root: Path,
    document: Mapping[str, Any],
    floor: float,
    omit: Sequence[str],
    modules: Sequence[str],
    flags: Sequence[str],
) -> List[Finding]:
    """Check ``codecov.yml`` against the local coverage declaration.

    Args:
        root: Repository root.
        document: Parsed ``codecov.yml``.
        floor: ``fail_under`` from ``pyproject.toml``.
        omit: ``[tool.coverage.run] omit`` from ``pyproject.toml``.
        modules: The shipped top-level modules from ``pyproject.toml``.
        flags: The flags the workflow uploads.

    Returns:
        Findings for the target, the ignore list and the flags.
    """
    location = _location(root, root / CODECOV_CONFIG)
    findings: List[Finding] = []

    target = project_target(document)
    if target is None:
        findings.append(
            Finding(
                "coverage.target",
                location,
                f"declares no numeric project target, so {floor:g}% is enforced "
                "only on the runner",
            )
        )
    elif abs(target - floor) > 0.05:
        findings.append(
            Finding(
                "coverage.target",
                location,
                f"the project target is {target:g}%, but fail_under in "
                f"pyproject.toml is {floor:g}%",
            )
        )

    entries = ignore_entries(document)
    bases = {_base_of(entry) for entry in entries}
    for pattern in omit:
        base = _base_of(pattern)
        if base == "__pycache__" or "*" in base:
            continue
        if base not in bases:
            findings.append(
                Finding(
                    "coverage.ignore",
                    location,
                    f"does not ignore {pattern!r}, which pyproject.toml omits — the "
                    "badge would measure files the local gate does not",
                )
            )
    shipped = {"projects", "datheon", *modules}
    for entry in entries:
        base = _base_of(entry)
        # A core module is a top-level file, so ``caching.py`` and ``caching``
        # name the same shipped code.
        stem = base[:-3] if base.endswith(".py") else base
        if base in shipped or stem in shipped:
            findings.append(
                Finding(
                    "coverage.ignore",
                    location,
                    f"ignores {entry!r}, which hides shipped code from the badge",
                )
            )

    declared = declared_flags(document)
    for flag in flags:
        if flag not in declared:
            findings.append(
                Finding(
                    "coverage.flags",
                    location,
                    f"the workflow uploads flag {flag!r}, which this file does not declare",
                )
            )
    for flag in declared:
        if flag not in flags:
            findings.append(
                Finding(
                    "coverage.flags",
                    location,
                    f"declares flag {flag!r}, which no upload step sends",
                )
            )

    return findings


def audit_gitignore(root: Path) -> List[Finding]:
    """Check that the report artefacts cannot be committed by accident.

    Args:
        root: Repository root.

    Returns:
        One finding per report no ignore rule covers.
    """
    location = _location(root, root / GITIGNORE)
    rules = set(gitignore_rules(root))
    return [
        Finding(
            "coverage.gitignore",
            location,
            f"{report} is not ignored, so the next `git add .` commits a report",
        )
        for report in REPORTS
        if report not in rules
    ]


def audit_docs(root: Path) -> List[Finding]:
    """Check that the tool's own page exists and documents every code.

    Args:
        root: Repository root.

    Returns:
        A finding when the page is missing, plus one per code it omits.
    """
    path = root / DOCS_PAGE
    if not path.is_file():
        return [
            Finding(
                "coverage.docs",
                DOCS_PAGE.as_posix(),
                "the tool's documentation page is missing",
            )
        ]
    text = path.read_text(encoding="utf-8")
    return [
        Finding("coverage.docs", DOCS_PAGE.as_posix(), f"does not document the {code} finding")
        for code in CODES
        if code not in text
    ]


def audit_repository(root: Path = ROOT) -> List[Finding]:
    """Run every coverage-reporting check against a checkout.

    Args:
        root: Repository root.

    Returns:
        Findings in report order. An empty list means the pipeline is sound.

    Raises:
        ValueError: When the workflow or ``codecov.yml`` cannot be parsed, or
            ``pyproject.toml`` declares no floor — the audit cannot reach a
            verdict, and "cannot measure" must never read as "passes".
    """
    findings: List[Finding] = []

    workflow_path = root / WORKFLOW
    if not workflow_path.is_file():
        findings.append(
            Finding(
                "coverage.workflow",
                WORKFLOW.as_posix(),
                "the coverage workflow is missing, so nothing publishes a report",
            )
        )
        document: Optional[Mapping[str, Any]] = None
    else:
        document = load_yaml(workflow_path)
        if not isinstance(document, Mapping):
            raise ValueError(f"{WORKFLOW.as_posix()} is not a YAML mapping")

    config_path = root / CODECOV_CONFIG
    config: Optional[Mapping[str, Any]] = None
    if not config_path.is_file():
        findings.append(
            Finding(
                "coverage.config",
                CODECOV_CONFIG.as_posix(),
                "the Codecov declaration is missing, so Codecov applies its defaults",
            )
        )
    else:
        loaded = load_yaml(config_path)
        if not isinstance(loaded, Mapping):
            raise ValueError(f"{CODECOV_CONFIG.as_posix()} is not a YAML mapping")
        config = loaded

    declared = pyproject_coverage(root)
    if declared is None:
        raise ValueError(f"{PYPROJECT.as_posix()} declares no [tool.coverage.report] fail_under")
    floor, omit, modules = declared

    if document is not None:
        text = workflow_path.read_text(encoding="utf-8")
        findings.extend(audit_workflow(root, document, text))
        flags = workflow_flags(job_steps(document))
    else:
        flags = []

    if config is not None:
        findings.extend(audit_config(root, config, floor, omit, modules, flags))

    findings.extend(audit_gitignore(root))
    findings.extend(audit_docs(root))
    return findings


def summary(root: Path, findings: Sequence[Finding]) -> Dict[str, object]:
    """Build the machine-readable verdict.

    Args:
        root: Repository root the audit ran against.
        findings: Findings from :func:`audit_repository`.

    Returns:
        A JSON-serialisable document describing what was found and where.
    """
    declared = pyproject_coverage(root)
    errors = sum(1 for finding in findings if finding.is_error)
    return {
        "root": str(root),
        "floor": declared[0] if declared else None,
        "workflow": WORKFLOW.as_posix() if (root / WORKFLOW).is_file() else None,
        "config": CODECOV_CONFIG.as_posix() if (root / CODECOV_CONFIG).is_file() else None,
        "findings": [asdict(finding) for finding in findings],
        "errors": errors,
        "warnings": len(findings) - errors,
        "ok": not findings,
    }


def render_table(root: Path, findings: Sequence[Finding]) -> str:
    """Render the verdict as a fixed-width table.

    Args:
        root: Repository root the audit ran against.
        findings: Findings to render.

    Returns:
        The table, or a one-line pass message naming what was measured.
    """
    if not findings:
        declared = pyproject_coverage(root)
        floor = f"{declared[0]:g}%" if declared else "unknown"
        return (
            "coverage-report: "
            f"{WORKFLOW.as_posix()} + {CODECOV_CONFIG.as_posix()} pass — "
            f"project target matches fail_under {floor}, {REPORT_XML} is kept and "
            f"uploaded, {len(CODES)} finding codes documented."
        )
    lines = [f"{'severity':<8} {'code':<22} {'location':<28} detail"]
    lines.append("-" * 110)
    for finding in findings:
        lines.append(
            f"{finding.severity:<8} {finding.code:<22} {finding.location:<28} {finding.detail}"
        )
    errors = sum(1 for finding in findings if finding.is_error)
    lines.append("")
    lines.append(f"{errors} error(s), {len(findings) - errors} warning(s).")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser exposing ``--root``, ``--json``, ``--strict`` and ``--version``.
    """
    parser = argparse.ArgumentParser(
        prog="coverage-report",
        description="Audit the workflow that publishes coverage, and what it claims.",
    )
    parser.add_argument("--root", default=str(ROOT), help="repository root to audit")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    parser.add_argument("--version", action="version", version="coverage-report 1.0")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit.

    Args:
        argv: Argument list; ``None`` means :data:`sys.argv`.

    Returns:
        ``0`` when the pipeline passes, ``1`` when there are findings, ``2``
        when the audit cannot reach a verdict at all.
    """
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(
            f"coverage-report: {root} is not a directory — nothing to measure",
            file=sys.stderr,
        )
        return 2
    if yaml is None:  # pragma: no cover - PyYAML is in requirements.txt
        print(
            "coverage-report: PyYAML is not installed — nothing to measure",
            file=sys.stderr,
        )
        return 2

    try:
        findings = audit_repository(root)
    except ValueError as exc:
        print(f"coverage-report: {exc} — nothing to measure", file=sys.stderr)
        return 2

    blocking = [finding for finding in findings if finding.is_error or args.strict]

    if args.json:
        print(json.dumps(summary(root, findings), indent=2))
    else:
        print(render_table(root, findings))

    return 1 if blocking else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
