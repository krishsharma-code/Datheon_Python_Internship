"""Container audit: does the Dockerfile still build the image we documented?

Day 33 packages the organizer as a cron-friendly container. A Dockerfile is
unusual among the artefacts in this repository in that **nothing else here
executes it**: ``pytest`` imports the modules, ``black`` reads them, ``mypy``
type-checks them, and a broken ``COPY`` or a container that silently runs as
root survives every one of those gates. The first person to find out is whoever
types ``docker build`` — which, on a project like this one, may be nobody for
months.

This module is the cheap half of that gate. It reads the four files that make
the container what it is — ``Dockerfile``, ``.dockerignore``,
``docker/crontab.example`` and ``.github/workflows/docker.yml`` — and checks the
claims made in ``docs/docker.md`` against them, without a daemon:

* **the image is reproducible.** Both stages pin a minor Python version and a
  Debian release. ``latest`` (or an untagged base) means the image is different
  tomorrow without a commit, which is the one property a nightly job cannot
  tolerate.
* **the build is two-staged and the runtime is clean.** A compiler, ``apt`` and
  a pip cache in the final stage are how an image that was 60 MB becomes 900 MB
  and starts shipping a toolchain an organizer never uses. ``--no-index`` on the
  runtime install is what makes "the build needs the network, the run does not"
  a fact rather than a hope.
* **it runs unprivileged, and says so numerically.** ``USER 10001:10001`` is the
  form an orchestrator can verify without resolving a name.
* **the entry point is exec-form.** A shell-form ``ENTRYPOINT organizer`` puts
  ``/bin/sh`` at PID 1, which swallows ``SIGTERM`` and can rewrite the exit code
  — so the Day 3 contract (0 success, 1 user/data error, 2 usage error) would
  stop at the container boundary.
* **every ``COPY`` source exists *and* survives ``.dockerignore``.** This is the
  defect a build reports as an empty layer rather than an error: a pattern that
  ignores a directory the Dockerfile copies produces an image that builds and
  then fails at run time.
* **the schedule is safe.** The example crontab must lock against overlap
  (``flock``), use ``--rm``, mount absolute host paths, redirect its output and
  never ask for a TTY. Cron has no terminal, and a job that can run twice at
  once can move the same file twice.

Run it directly::

    python tools/docker_packaging.py                 # the whole audit
    python tools/docker_packaging.py --json          # machine-readable verdict
    python tools/docker_packaging.py --build         # also build the image
    python tools/docker_packaging.py --root /tmp/x   # audit another tree

Exit codes follow the rest of the repository: ``0`` every check clean, ``1`` at
least one finding, ``2`` the audit could not measure — no ``Dockerfile``, or
``--build`` was asked for on a host without a Docker daemon. A gate that cannot
measure never reports success.

Development tooling only: nothing under ``projects/`` imports this module, and
the five tools remain standard-library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "CRONTAB",
    "DOCKERFILE",
    "DOCKERIGNORE",
    "DOC",
    "REQUIRED_IGNORES",
    "REQUIRED_LABELS",
    "WORKFLOW",
    "Finding",
    "Instruction",
    "Report",
    "Stage",
    "audit_cron",
    "audit_dockerignore",
    "audit_docs",
    "audit_tree",
    "audit_workflow",
    "build_image",
    "copy_sources",
    "dockerignore_patterns",
    "env_map",
    "is_ignored",
    "main",
    "parse_dockerfile",
    "render_report",
    "report_to_dict",
    "split_stages",
]

#: Exit code for a clean audit.
EXIT_OK = 0

#: Exit code for an audit with at least one finding.
EXIT_FINDINGS = 1

#: Exit code for an audit that could not measure.
EXIT_UNMEASURABLE = 2

#: The build recipe.
DOCKERFILE = "Dockerfile"

#: What never enters the build context.
DOCKERIGNORE = ".dockerignore"

#: The example schedule, installed with ``crontab docker/crontab.example``.
CRONTAB = "docker/crontab.example"

#: The workflow that builds and runs the image on GitHub's runners.
WORKFLOW = ".github/workflows/docker.yml"

#: The page that documents the image.
DOC = "docs/docker.md"

#: The site configuration the page must be listed in.
NAV = "mkdocs.yml"

#: Patterns ``.dockerignore`` must keep: history, caches, developer trees and
#: secrets. Each one is either a leak or a build-context cost, and none of them
#: is needed by the eleven paths the builder copies.
REQUIRED_IGNORES: Tuple[str, ...] = (
    ".git",
    ".github",
    "__pycache__",
    ".venv",
    "dist",
    "build",
    "mutants",
    "tests",
    "docs",
    "tools",
    ".env",
)

#: OCI labels the image must carry, so a registry page and a scanner can both
#: say what this image is, where it came from and under which licence.
REQUIRED_LABELS: Tuple[str, ...] = (
    "org.opencontainers.image.title",
    "org.opencontainers.image.description",
    "org.opencontainers.image.source",
    "org.opencontainers.image.version",
    "org.opencontainers.image.licenses",
)

#: Environment the runtime stage must set, and the value it must have.
REQUIRED_ENV: Tuple[Tuple[str, str], ...] = (
    ("PYTHONUNBUFFERED", "1"),
    ("PYTHONDONTWRITEBYTECODE", "1"),
)

#: Names that look like a credential. An ``ENV``/``ARG`` with one of these in
#: its name is baked into the image's metadata and readable with ``docker
#: inspect``, so it is a finding whether or not a value was supplied.
_SECRET_NAME = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|access[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)

#: Files that are a credential by convention.
_SECRET_FILES = (".env", ".env.local", "id_rsa", "id_ed25519", ".netrc")

#: Packages that mean a compiler ended up in the runtime stage.
_BUILD_TOOLS = (
    "build-essential",
    "gcc",
    "g++",
    "make",
    "cmake",
    "musl-dev",
    "python3-dev",
    "libffi-dev",
)

#: What a cron line must not do: a scheduled job has no terminal.
_INTERACTIVE_FLAGS = ("-it", "-ti", "--interactive", "--tty", "-i -t")


@dataclass(frozen=True)
class Instruction:
    """One Dockerfile instruction, after line continuations are joined.

    Attributes:
        keyword: The upper-cased instruction word (``FROM``, ``COPY``, …).
        value: Everything after the keyword, on one line.
        line: The 1-based line the instruction started on, for messages.
        flags: The ``--flag=value`` words that precede the arguments.
    """

    keyword: str
    value: str
    line: int
    flags: str = ""

    def render(self) -> str:
        """Return the instruction as a single line, for a finding message.

        Returns:
            The instruction with its keyword, flags and value, joined by spaces.
        """
        parts = [self.keyword]
        if self.flags:
            parts.append(self.flags)
        if self.value:
            parts.append(self.value)
        return " ".join(parts)


@dataclass(frozen=True)
class Stage:
    """One build stage, from its ``FROM`` to the next one.

    Attributes:
        name: The alias from ``AS <name>``, or ``stage-<index>`` when unnamed.
        base: The base image reference, exactly as written.
        index: Zero-based position in the Dockerfile.
        instructions: Every instruction in the stage, including its ``FROM``.
    """

    name: str
    base: str
    index: int
    instructions: Tuple[Instruction, ...]

    @property
    def is_builder(self) -> bool:
        """Whether the stage's alias marks it as the build stage.

        Returns:
            ``True`` when the alias contains ``build``.
        """
        return "build" in self.name.lower()


@dataclass(frozen=True)
class Finding:
    """One rule the container breaks.

    Attributes:
        code: A stable dotted identifier (``docker.user.root``).
        message: What is wrong and what to do about it.
    """

    code: str
    message: str

    def render(self) -> str:
        """Return the finding as one line, code first.

        Returns:
            The code and message joined by a colon and a space.
        """
        return f"{self.code}: {self.message}"


@dataclass
class Report:
    """The verdict of one audit run.

    Attributes:
        findings: Every rule broken, in the order the checks ran.
        stages: How many build stages the Dockerfile declares.
        image: The base image of the final stage, for the summary line.
        unmeasurable: ``True`` when a check could not run at all.
        reason: Why the audit could not measure, when it could not.
    """

    findings: List[Finding] = field(default_factory=list)
    stages: int = 0
    image: str = ""
    unmeasurable: bool = False
    reason: str = ""

    @property
    def passed(self) -> bool:
        """Whether the tree is clean.

        Returns:
            ``True`` when there are no findings and nothing blocked the audit.
        """
        return not self.findings and not self.unmeasurable

    def add(self, code: str, message: str) -> None:
        """Record a finding.

        Args:
            code: The stable finding code.
            message: What is wrong and what to do about it.
        """
        self.findings.append(Finding(code=code, message=message))


# --- reading ---------------------------------------------------------------
def read_text(path: Path) -> Optional[str]:
    """Read a UTF-8 text file, or return ``None`` when it is not there.

    Args:
        path: The file to read.

    Returns:
        The file's text, or ``None`` when it does not exist or cannot be read.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# --- Dockerfile parsing ----------------------------------------------------
def parse_dockerfile(text: str) -> List[Instruction]:
    """Parse a Dockerfile into instructions, joining continuations.

    Comment lines and blank lines are dropped, including inside a continuation
    (which is what Docker itself does). A trailing backslash joins the next
    line onto the current one, and the instruction is reported at the line it
    started on, because that is the line an editor will open.

    Args:
        text: The whole Dockerfile.

    Returns:
        The instructions in file order.
    """
    instructions: List[Instruction] = []
    buffer: List[str] = []
    start = 0

    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not buffer:
            start = number
        if stripped.endswith("\\"):
            buffer.append(stripped[:-1].strip())
            continue
        buffer.append(stripped)
        joined = " ".join(part for part in buffer if part).strip()
        buffer = []
        if not joined:
            continue
        instructions.append(split_instruction(joined, start))
    if buffer:  # a continuation that ends the file
        joined = " ".join(part for part in buffer if part).strip()
        instructions.append(split_instruction(joined, start))
    return instructions


def split_instruction(joined: str, line: int) -> Instruction:
    """Split one joined instruction line into keyword, flags and arguments.

    The flags matter: ``COPY --from=builder /wheels /wheels`` copies from
    another stage, so its ``/wheels`` is not a build-context path. Leaving
    ``--from=builder`` in the argument list would make the audit report two
    missing files for a correct instruction.

    Args:
        joined: One instruction, with its continuations already joined.
        line: The line the instruction started on.

    Returns:
        The parsed instruction.
    """
    keyword, _, rest = joined.partition(" ")
    rest = rest.strip()
    flags: List[str] = []
    while rest.startswith("--"):
        token, _, remainder = rest.partition(" ")
        flags.append(token)
        rest = remainder.strip()
    return Instruction(
        keyword=keyword.upper(),
        value=rest,
        line=line,
        flags=" ".join(flags),
    )


def split_stages(instructions: Sequence[Instruction]) -> Tuple[List[Instruction], List[Stage]]:
    """Split parsed instructions into global ones and build stages.

    Args:
        instructions: The parsed Dockerfile.

    Returns:
        A pair: the instructions before the first ``FROM`` (``ARG``, a global
        ``ENV``, the parser directive), and the stages in order.
    """
    global_instructions: List[Instruction] = []
    stages: List[Stage] = []
    current: List[Instruction] = []
    name = ""
    base = ""

    for instruction in instructions:
        if instruction.keyword == "FROM":
            if current:
                stages.append(
                    Stage(
                        name=name or f"stage-{len(stages)}",
                        base=base,
                        index=len(stages),
                        instructions=tuple(current),
                    )
                )
                current = []
            base = instruction.value.split()[0] if instruction.value.split() else ""
            parts = instruction.value.split()
            name = parts[2] if len(parts) >= 3 and parts[1].upper() == "AS" else ""
            current.append(instruction)
            continue
        if not stages and not current:
            global_instructions.append(instruction)
        else:
            current.append(instruction)

    if current:
        stages.append(
            Stage(
                name=name or f"stage-{len(stages)}",
                base=base,
                index=len(stages),
                instructions=tuple(current),
            )
        )
    return global_instructions, stages


def copy_sources(instruction: Instruction) -> Tuple[Optional[str], List[str]]:
    """Return a ``COPY``/``ADD`` instruction's source stage and paths.

    Args:
        instruction: A ``COPY`` or ``ADD`` instruction.

    Returns:
        A pair: the ``--from`` stage name when the instruction has one, and the
        source paths. The destination is not included.
    """
    match = re.search(r"--from=(\S+)", instruction.flags or "")
    origin = match.group(1) if match else None
    value = instruction.value.strip()
    if value.startswith("["):
        try:
            parts = json.loads(value)
        except json.JSONDecodeError:
            parts = value.strip("[]").replace('"', "").split(",")
        paths = [str(part).strip() for part in parts]
    else:
        paths = value.split()
    if not paths:
        return origin, []
    return origin, paths[:-1]


def env_map(instructions: Sequence[Instruction]) -> Dict[str, str]:
    """Collect the ``ENV`` declarations of a set of instructions.

    Both Docker forms are understood: ``ENV KEY=value OTHER=value`` and the
    legacy ``ENV KEY value``.

    Args:
        instructions: The instructions to read.

    Returns:
        A mapping of variable name to value, later declarations winning.
    """
    values: Dict[str, str] = {}
    for instruction in instructions:
        if instruction.keyword != "ENV":
            continue
        tokens = instruction.value.split()
        if not tokens:
            continue
        if "=" not in tokens[0]:
            key, _, rest = instruction.value.partition(" ")
            values[key] = rest.strip().strip('"')
            continue
        for token in tokens:
            key, _, value = token.partition("=")
            if key:
                values[key] = value.strip().strip('"')
    return values


def _base_tag(base: str) -> str:
    """Return the tag of an image reference, or an empty string.

    Args:
        base: A base image reference such as ``python:3.12-slim-bookworm``.

    Returns:
        The part after the last colon, ignoring a registry port; empty when the
        reference carries no tag.
    """
    last = base.rsplit("/", 1)[-1]
    if ":" not in last:
        return ""
    return last.split(":", 1)[1]


# --- .dockerignore ---------------------------------------------------------
def dockerignore_patterns(text: str) -> List[str]:
    """Return the active patterns of a ``.dockerignore`` file.

    Args:
        text: The file's contents.

    Returns:
        The non-comment, non-blank patterns, in file order, ``!`` included.
    """
    patterns: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _pattern_hits(path: str, pattern: str) -> bool:
    """Whether one ``.dockerignore`` pattern matches a path or its ancestors.

    Args:
        path: A POSIX path relative to the build context.
        pattern: One pattern, without a leading ``!``.

    Returns:
        ``True`` when the pattern matches the path or one of its parent
        directories, which is how Docker decides a path is excluded.
    """
    cleaned = pattern.strip().rstrip("/")
    if not cleaned:
        return False
    parts = path.split("/")
    ancestors = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
    if "/" not in cleaned:
        return any(fnmatch.fnmatchcase(part, cleaned) for part in parts)
    return any(
        fnmatch.fnmatchcase(candidate, cleaned) or fnmatch.fnmatchcase(candidate, f"{cleaned}/**")
        for candidate in ancestors
    )


def is_ignored(path: str, patterns: Sequence[str]) -> bool:
    """Whether a build-context path is excluded, honouring ``!`` re-includes.

    Args:
        path: A POSIX path relative to the build context.
        patterns: The patterns from :func:`dockerignore_patterns`.

    Returns:
        ``True`` when the last pattern that matches excludes the path.
    """
    ignored = False
    for raw in patterns:
        negate = raw.startswith("!")
        pattern = raw[1:] if negate else raw
        if _pattern_hits(path, pattern):
            ignored = not negate
    return ignored


# --- checks ---------------------------------------------------------------
def _check_stages(report: Report, stages: Sequence[Stage]) -> None:
    """Check the build shape: pinned bases and a clean runtime stage.

    Args:
        report: The report to add findings to.
        stages: The parsed stages.
    """
    if len(stages) < 2:
        report.add(
            "docker.build.single_stage",
            "the Dockerfile builds in one stage; compile the wheel in a builder "
            "stage and copy only the wheel into the runtime stage",
        )
    for stage in stages:
        tag = _base_tag(stage.base)
        if not tag:
            report.add(
                "docker.base.untagged",
                f"stage {stage.name!r} uses the untagged base {stage.base!r}; pin a "
                "minor version and a Debian release (python:3.12-slim-bookworm)",
            )
        elif tag == "latest" or not re.search(r"\d+\.\d+", tag):
            report.add(
                "docker.base.floating",
                f"stage {stage.name!r} uses the floating tag {stage.base!r}; a "
                "scheduled job must not get a different image tomorrow",
            )
    if stages and stages[-1].is_builder:
        report.add(
            "docker.build.no_runtime_stage",
            "the final stage is the builder, so the image ships a compiler and a "
            "pip cache; the runtime stage must come last",
        )


def _check_runtime(report: Report, stages: Sequence[Stage]) -> None:
    """Check the runtime stage's environment, user, labels and entry point.

    Args:
        report: The report to add findings to.
        stages: The parsed stages.
    """
    if not stages:
        return
    final = stages[-1]
    instructions = final.instructions

    environment = env_map(instructions)
    for key, expected in REQUIRED_ENV:
        if environment.get(key) != expected:
            report.add(
                "docker.env.missing",
                f"the runtime stage does not set {key}={expected}; "
                f"found {environment.get(key, 'nothing')!r}",
            )

    users = [item.value.strip() for item in instructions if item.keyword == "USER"]
    if not users:
        report.add(
            "docker.user.missing",
            "the image has no USER instruction, so it runs as root; add "
            "`USER 10001:10001` after creating the account",
        )
    else:
        user = users[-1]
        if user.split(":")[0] in {"root", "0"}:
            report.add(
                "docker.user.root",
                f"the image runs as {user!r}; use a numeric unprivileged uid "
                "such as 10001:10001",
            )

    labels: Dict[str, str] = {}
    for instruction in instructions:
        if instruction.keyword != "LABEL":
            continue
        for token in re.findall(r"([\w.\-/]+)=", instruction.value):
            labels[token] = token
    for label in REQUIRED_LABELS:
        if label not in labels:
            report.add(
                "docker.label.missing",
                f"the runtime stage does not set the OCI label {label!r}",
            )

    entrypoints = [item for item in instructions if item.keyword == "ENTRYPOINT"]
    if not entrypoints:
        report.add(
            "docker.entrypoint.missing",
            "the image has no ENTRYPOINT, so `docker run <image> --version` tries "
            "to execute the flag",
        )
    else:
        entrypoint = entrypoints[-1]
        value = entrypoint.value.strip()
        if not value.startswith("["):
            report.add(
                "docker.entrypoint.shell_form",
                f"the ENTRYPOINT is shell-form ({entrypoint.render()}); /bin/sh at "
                "PID 1 swallows SIGTERM and can rewrite the exit code — use the "
                'exec form ["organizer"]',
            )
        else:
            try:
                argv = json.loads(value)
            except json.JSONDecodeError:
                argv = []
            names = [str(item).rsplit("/", 1)[-1] for item in argv]
            if "organizer" not in names:
                report.add(
                    "docker.entrypoint.not_console_script",
                    f"the ENTRYPOINT {argv!r} is not the `organizer` console "
                    "script, so the container does not run the tool it packages",
                )


def _check_commands(report: Report, stages: Sequence[Stage]) -> None:
    """Check ``RUN`` and ``ADD`` instructions for cost and credential leaks.

    Args:
        report: The report to add findings to.
        stages: The parsed stages.
    """
    for stage in stages:
        final = stage is stages[-1]
        for instruction in stage.instructions:
            if instruction.keyword == "RUN":
                command = instruction.value
                if re.search(r"\bpip3? (install|wheel)\b", command):
                    if "--no-cache-dir" not in command:
                        report.add(
                            "docker.pip.no_cache",
                            f"line {instruction.line}: pip without --no-cache-dir "
                            "writes a download cache into the layer",
                        )
                    if final and "install" in command and "--no-index" not in command:
                        report.add(
                            "docker.pip.network_in_runtime",
                            f"line {instruction.line}: the runtime stage installs "
                            "from an index, so the image build needs the network "
                            "at run time — install from the builder's wheel with "
                            "--no-index",
                        )
                if final and "apt-get install" in command:
                    offenders = [tool for tool in _BUILD_TOOLS if tool in command]
                    if offenders:
                        report.add(
                            "docker.runtime.build_tools",
                            f"line {instruction.line}: the runtime stage installs "
                            f"{', '.join(offenders)}; compilers belong in the "
                            "builder stage only",
                        )
            elif instruction.keyword in {"ADD", "COPY"}:
                if instruction.keyword == "ADD" and re.search(r"https?://", instruction.value):
                    report.add(
                        "docker.remote.add",
                        f"line {instruction.line}: ADD fetches a URL, which is "
                        "unpinned and unverifiable; download and COPY a file with "
                        "a known digest instead",
                    )

    for stage in stages:
        for instruction in stage.instructions:
            if instruction.keyword not in {"ENV", "ARG"}:
                continue
            for token in instruction.value.split():
                key = token.partition("=")[0]
                if key and _SECRET_NAME.search(key):
                    report.add(
                        "docker.secret.env",
                        f"line {instruction.line}: {key!r} looks like a credential; "
                        "anything in ENV or ARG is readable with `docker inspect`",
                    )


def _check_copies(report: Report, root: Path, stages: Sequence[Stage]) -> None:
    """Check every ``COPY`` source against the tree and the ignore file.

    Args:
        report: The report to add findings to.
        root: The repository root, which is the build context.
        stages: The parsed stages.
    """
    ignore_text = read_text(root / DOCKERIGNORE) or ""
    patterns = dockerignore_patterns(ignore_text)
    names = {stage.name for stage in stages}

    for stage in stages:
        for instruction in stage.instructions:
            if instruction.keyword != "COPY":
                continue
            origin, sources = copy_sources(instruction)
            if origin is not None:
                if origin not in names and not origin.isdigit():
                    report.add(
                        "docker.copy.unknown_stage",
                        f"line {instruction.line}: COPY --from={origin} names a "
                        "stage that does not exist",
                    )
                continue
            for source in sources:
                relative = source.lstrip("./")
                if relative in {"", "."}:
                    continue
                if any(char in relative for char in "*?["):
                    matches = sorted(root.glob(relative))
                    if not matches:
                        report.add(
                            "docker.copy.missing",
                            f"line {instruction.line}: COPY pattern {source!r} "
                            "matches nothing in the build context",
                        )
                    continue
                if not (root / relative).exists():
                    report.add(
                        "docker.copy.missing",
                        f"line {instruction.line}: COPY source {source!r} is not in "
                        "the build context",
                    )
                elif is_ignored(relative, patterns):
                    report.add(
                        "docker.copy.ignored",
                        f"line {instruction.line}: COPY source {source!r} is "
                        "excluded by .dockerignore, so the layer would be empty",
                    )


def audit_dockerfile(root: Path) -> Report:
    """Audit the ``Dockerfile`` in a tree.

    Args:
        root: The repository root.

    Returns:
        A report carrying every Dockerfile finding, or an unmeasurable report
        when there is no Dockerfile to read.
    """
    text = read_text(root / DOCKERFILE)
    if text is None:
        report = Report(unmeasurable=True, reason=f"{DOCKERFILE} is missing")
        report.add("docker.file.missing", f"there is no {DOCKERFILE} in {root}")
        return report

    instructions = parse_dockerfile(text)
    if not instructions:
        report = Report(unmeasurable=True, reason=f"{DOCKERFILE} is empty")
        report.add("docker.file.empty", f"{DOCKERFILE} contains no instructions")
        return report

    globals_, stages = split_stages(instructions)
    report = Report(
        stages=len(stages),
        image=stages[-1].base if stages else "",
    )
    if not stages:
        report.add("docker.build.no_stage", "the Dockerfile has no FROM instruction")
        return report

    _check_stages(report, stages)
    _check_runtime(report, stages)
    _check_commands(report, stages)
    _check_copies(report, root, stages)

    if env_map(globals_) and not env_map(stages[-1].instructions):
        report.add(
            "docker.env.not_in_runtime",
            "the environment is declared before the first FROM, so it applies to "
            "the builder stage only; declare it in the runtime stage",
        )
    return report


def audit_dockerignore(root: Path) -> List[Finding]:
    """Check that ``.dockerignore`` keeps history, caches and secrets out.

    Args:
        root: The repository root.

    Returns:
        One finding per required pattern that is missing, plus one when the
        file itself is absent.
    """
    text = read_text(root / DOCKERIGNORE)
    if text is None:
        return [
            Finding(
                code="dockerignore.missing",
                message=f"there is no {DOCKERIGNORE}, so the whole tree — history "
                "included — is sent to the daemon on every build",
            )
        ]
    patterns = dockerignore_patterns(text)
    findings: List[Finding] = []
    for required in REQUIRED_IGNORES:
        if not any(
            _pattern_hits(required, pattern.lstrip("!")) and not pattern.startswith("!")
            for pattern in patterns
        ):
            findings.append(
                Finding(
                    code="dockerignore.pattern_missing",
                    message=f"{DOCKERIGNORE} does not exclude {required!r}",
                )
            )
    return findings


def _cron_logical_lines(text: str) -> List[str]:
    """Return a crontab's non-comment lines, joining backslash continuations.

    Cron itself has no line continuation, so the committed example is one long
    line — but a file that has been wrapped for reading must not be judged on
    its first physical line only, which is how a wrapped job would pass a
    ``2>&1`` check while the redirect sat on a line nobody looked at.

    Args:
        text: The crontab's contents.

    Returns:
        One entry per logical line, comments and blanks removed.
    """
    logical: List[str] = []
    buffer = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1].strip() + " "
            continue
        buffer += stripped
        if buffer.strip():
            logical.append(buffer.strip())
        buffer = ""
    if buffer.strip():
        logical.append(buffer.strip())
    return logical


def audit_cron(root: Path) -> List[Finding]:
    """Check the example crontab for the ways a scheduled container run fails.

    Args:
        root: The repository root.

    Returns:
        The findings for the crontab example; a single ``cron.missing`` when the
        file is absent.
    """
    text = read_text(root / CRONTAB)
    if text is None:
        return [
            Finding(
                code="cron.missing",
                message=f"there is no {CRONTAB}; the container has no documented " "schedule",
            )
        ]
    jobs = [line for line in _cron_logical_lines(text) if "docker run" in line]
    if not jobs:
        return [
            Finding(
                code="cron.no_job",
                message=f"{CRONTAB} has no line that runs `docker run`",
            )
        ]

    findings: List[Finding] = []
    for job in jobs:
        if any(flag in f" {job} " for flag in _INTERACTIVE_FLAGS):
            findings.append(
                Finding(
                    code="cron.interactive",
                    message="the cron line asks for a TTY (-it/--interactive); cron "
                    "has no terminal, and the flag fails under it",
                )
            )
        if "--rm" not in job:
            findings.append(
                Finding(
                    code="cron.no_rm",
                    message="the cron line does not pass --rm, so every run leaves a "
                    "stopped container behind",
                )
            )
        if "flock" not in job:
            findings.append(
                Finding(
                    code="cron.no_lock",
                    message="the cron line does not lock, so a slow run and the next "
                    "one can move the same files at once — wrap it in `flock -n`",
                )
            )
        if "2>&1" not in job:
            findings.append(
                Finding(
                    code="cron.no_log",
                    message="the cron line does not redirect stderr, so a failing run "
                    "reports only to the mail spool",
                )
            )
        for mount in re.findall(r"(?:-v|--volume)\s+(\S+)", job):
            host = mount.split(":")[0]
            if not host.startswith("/"):
                findings.append(
                    Finding(
                        code="cron.relative_mount",
                        message=f"the cron line mounts {host!r}, which resolves against "
                        "cron's working directory; use an absolute host path",
                    )
                )
        for image in re.findall(r"\bdatheon-organizer:[^\s]+", job):
            if image.endswith(":latest"):
                findings.append(
                    Finding(
                        code="cron.latest",
                        message="the cron line runs the :latest tag; pin the release "
                        "version so a schedule cannot change under the host",
                    )
                )
    return findings


def audit_workflow(root: Path) -> List[Finding]:
    """Check that CI builds the image and asserts what the docs promise.

    Args:
        root: The repository root.

    Returns:
        The findings for the workflow; a single ``ci.missing`` when the file is
        absent.
    """
    text = read_text(root / WORKFLOW)
    if text is None:
        return [
            Finding(
                code="ci.missing",
                message=f"there is no {WORKFLOW}, so nothing builds the image before "
                "a user does",
            )
        ]
    findings: List[Finding] = []
    if not re.search(r"docker build", text):
        findings.append(
            Finding(
                code="ci.no_build",
                message=f"{WORKFLOW} never runs `docker build`, so a broken "
                "Dockerfile reaches main",
            )
        )
    if "--version" not in text:
        findings.append(
            Finding(
                code="ci.no_smoke",
                message=f"{WORKFLOW} never runs the image's --version, so an image "
                "that builds but cannot start passes",
            )
        )
    if "10001" not in text:
        findings.append(
            Finding(
                code="ci.no_nonroot",
                message=f"{WORKFLOW} never asserts the uid inside the image, so the "
                "non-root promise is untested",
            )
        )
    return findings


def audit_docs(root: Path) -> List[Finding]:
    """Check that the container is documented and reachable from the site.

    Args:
        root: The repository root.

    Returns:
        A finding when the page is missing or absent from the navigation.
    """
    findings: List[Finding] = []
    if read_text(root / DOC) is None:
        findings.append(
            Finding(
                code="doc.missing",
                message=f"there is no {DOC}; the image has no documented usage",
            )
        )
    nav = read_text(root / NAV) or ""
    if Path(DOC).name not in nav:
        findings.append(
            Finding(
                code="doc.unlisted",
                message=f"{Path(DOC).name} is not in the {NAV} navigation, so the "
                "page is not reachable from the site",
            )
        )
    return findings


def audit_tree(root: Path) -> Report:
    """Audit the container packaging of a tree, without a daemon.

    Args:
        root: The repository root.

    Returns:
        The combined report. ``unmeasurable`` is set only when the Dockerfile
        itself cannot be read, because every other check has a definite answer
        for a missing file.
    """
    report = audit_dockerfile(root)
    if report.unmeasurable:
        return report
    report.findings.extend(audit_dockerignore(root))
    report.findings.extend(audit_cron(root))
    report.findings.extend(audit_workflow(root))
    report.findings.extend(audit_docs(root))
    return report


# --- the optional build ----------------------------------------------------
def build_image(
    root: Path,
    tag: str,
    runner: Any = None,
) -> Tuple[Optional[bool], str]:
    """Build the image, so the audit can see what only a daemon can answer.

    Args:
        root: The repository root, used as the build context.
        tag: The tag to give the built image.
        runner: A callable with :func:`subprocess.run`'s signature, injected by
            the tests so no test needs a Docker daemon.

    Returns:
        A pair: ``True`` when the build succeeded, ``False`` when it failed, and
        ``None`` when there is no Docker binary to run; plus the daemon's
        combined output, trimmed to the last 40 lines.
    """
    if runner is None:
        if shutil.which("docker") is None:
            return None, "docker is not installed on this host"
        runner = subprocess.run
    completed = runner(
        ["docker", "build", "--tag", tag, str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    tail = "\n".join(output.splitlines()[-40:])
    return completed.returncode == 0, tail


# --- output ----------------------------------------------------------------
def report_to_dict(report: Report, root: Path) -> Dict[str, Any]:
    """Render a report as the machine-readable document ``--json`` prints.

    Args:
        report: The verdict.
        root: The tree that was audited.

    Returns:
        A JSON-serialisable mapping.
    """
    return {
        "tool": "docker_packaging",
        "schema": 1,
        "ok": report.passed,
        "root": str(root),
        "stages": report.stages,
        "image": report.image,
        "unmeasurable": report.unmeasurable,
        "reason": report.reason,
        "findings": [
            {"code": finding.code, "message": finding.message} for finding in report.findings
        ],
    }


def render_report(report: Report, root: Path) -> str:
    """Render a report as the table the CLI prints.

    Args:
        report: The verdict.
        root: The tree that was audited.

    Returns:
        A multi-line report, ending in a summary line.
    """
    lines = [
        "Docker packaging audit",
        f"  tree:      {root}",
        f"  stages:    {report.stages}",
        f"  base:      {report.image or '—'}",
        "",
    ]
    if report.unmeasurable:
        lines.append(f"UNMEASURABLE — {report.reason}")
    elif report.passed:
        lines.append("PASS — the image, its ignore file, its schedule and its CI agree")
    else:
        lines.append(f"FAIL — {len(report.findings)} finding(s)")
    for finding in report.findings:
        lines.append(f"  {finding.render()}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        The parser for ``python tools/docker_packaging.py``.
    """
    parser = argparse.ArgumentParser(
        prog="docker_packaging",
        description=(
            "Audit the Dockerfile, .dockerignore, example crontab and container "
            "workflow without a Docker daemon."
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to audit (default: the current directory).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one machine-readable JSON document instead of the table.",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Also build the image; needs a Docker daemon and exits 2 without one.",
    )
    parser.add_argument(
        "--tag",
        default="datheon-organizer:audit",
        help="Tag for --build (default: datheon-organizer:audit).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit and return the process exit code.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        ``0`` clean, ``1`` at least one finding, ``2`` the audit could not
        measure — no Dockerfile, or ``--build`` on a host without Docker.
    """
    args = build_parser().parse_args(None if argv is None else list(argv))
    root = Path(args.root).resolve()
    report = audit_tree(root)

    build_ok: Optional[bool] = None
    if args.build and not report.unmeasurable:
        build_ok, output = build_image(root, args.tag)
        if build_ok is None:
            report.unmeasurable = True
            report.reason = output
        elif not build_ok:
            report.add("build.failed", f"`docker build` failed:\n{output}")

    if args.json:
        payload = report_to_dict(report, root)
        if args.build:
            payload["build"] = build_ok
        print(json.dumps(payload, indent=2))
    else:
        print(render_report(report, root), end="")

    if report.unmeasurable:
        print(f"cannot measure: {report.reason}", file=sys.stderr)
        return EXIT_UNMEASURABLE
    if report.passed:
        return EXIT_OK
    for finding in report.findings:
        print(finding.render(), file=sys.stderr)
    return EXIT_FINDINGS


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
