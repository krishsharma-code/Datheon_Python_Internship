"""Pre-commit audit: do the hooks guard what CI guards, before the commit exists? (Day 34)

``.pre-commit-config.yaml`` is the one file in this repository that decides
*when* every other gate runs. ``black``, ``flake8`` and the whitespace rules
already existed — in ``.github/workflows/ci.yml``, in ``pyproject.toml``, in
``setup.cfg`` — but they ran after a push, in a review thread, on a runner. The
configuration moves them to the commit, and like every other declaration here it
rots quietly if nothing reads it: YAML does not validate it, and a hook that is
mis-pinned, mis-scoped or absent simply does less work while still exiting 0.

This module is the reader. It audits the configuration as text and as data —
no network, no hook installation, no ``git`` required for the config checks —
against the repository's own declarations:

* **every remote hook is pinned to a release.** ``rev: main`` means the gate is
  a different program tomorrow without a commit here. A pin must carry a
  version, and a branch name is not one.
* **every pin satisfies the floor the project declares.** ``black>=24.1`` in
  ``pyproject.toml`` and ``rev: 24.10.0`` here have to agree, or a commit can be
  clean locally and red on the runner.
* **the required hooks are present, once each.** ``black``, ``flake8`` and
  ``end-of-file-fixer`` are the three gates the plan names; a configuration that
  omits one is a configuration that only looks like a gate.
* **the hook arguments agree with the configuration files.** A ``--line-length``
  passed to the hook overrides ``[tool.black]``; two numbers that disagree is
  how a file is formatted one way locally and checked another way in CI.
* **no hook is scoped out of the code it exists to check.** An ``exclude`` that
  matches ``datheon/`` or ``tools/`` disables the gate for the files that matter
  and reports success while doing it.
* **the whitespace hooks would be satisfied today.** The three hygiene hooks —
  final newline, trailing whitespace, line endings — are checked over the files
  git tracks, in this process, so the answer is known without installing
  anything. Markdown's two-space hard break is preserved, exactly as the hook's
  ``--markdown-linebreak-ext=md`` argument preserves it.
* **the page that explains all of this exists and is in the site navigation.**
  A gate nobody can read about is a gate nobody enables.

Finding codes:

===========================  ==================================================
Code                         What it means
===========================  ==================================================
``precommit.file``           The configuration is missing, empty or not a mapping.
``precommit.repo``           A repo entry has no URL, no hooks, or a hook no id.
``precommit.rev``            A ``rev`` is missing, empty, or a branch, not a pin.
``precommit.hook``           A required hook (black, flake8, end-of-file-fixer)
                             is absent.
``precommit.duplicate``      The same hook id is declared more than once.
``precommit.version``        A pinned rev does not satisfy the floor declared in
                             ``pyproject.toml``.
``precommit.args``           A hook argument contradicts the repository's own
                             configuration, or a required argument is missing.
``precommit.scope``          A ``files``/``exclude`` pattern hides shipped code.
``precommit.stage``          A stage name pre-commit does not know.
``precommit.secret``         A token-shaped string is in the configuration.
``precommit.whitespace``     A tracked file would fail a hygiene hook.
``precommit.docs``           ``docs/precommit.md`` is missing, not in the nav, or
                             does not document every finding code.
===========================  ==================================================

Run it directly::

    python tools/precommit_config.py                 # the whole audit
    python tools/precommit_config.py --json          # machine-readable verdict
    python tools/precommit_config.py --root /tmp/x   # audit another tree

Exit codes follow the rest of the repository: ``0`` every check clean, ``1`` at
least one finding, ``2`` the audit could not measure — there is no configuration
to read. A gate that cannot look never reports success.

Development tooling only: nothing under ``projects/`` imports this module, and
the five tools remain standard-library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import yaml

try:  # pragma: no cover - Python 3.11+ always takes the first branch
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - the 3.10 cell needs the backport
    import tomli as tomllib  # type: ignore[no-redef]

__all__ = [
    "CONFIG",
    "DOC",
    "FINDING_CODES",
    "KNOWN_STAGES",
    "MKDOCS",
    "PYPROJECT",
    "REQUIRED_HOOKS",
    "Finding",
    "Hook",
    "Repo",
    "Report",
    "audit_config",
    "audit_docs",
    "audit_tree",
    "audit_whitespace",
    "build_parser",
    "config_line_length",
    "flake8_line_length",
    "is_floating_rev",
    "load_document",
    "main",
    "nav_pages",
    "option_value",
    "parse_repos",
    "parse_version",
    "project_floors",
    "read_text",
    "render_report",
    "report_to_dict",
    "requirement_floor",
    "requirement_name",
    "tracked_files",
    "version_at_least",
]

#: Exit code for a clean audit.
EXIT_OK = 0

#: Exit code for an audit with at least one finding.
EXIT_FINDINGS = 1

#: Exit code for an audit that could not measure.
EXIT_UNMEASURABLE = 2

#: The hook configuration itself.
CONFIG = ".pre-commit-config.yaml"

#: Where the formatter and linter floors are declared.
PYPROJECT = "pyproject.toml"

#: Where flake8's own settings live, and therefore what the hook will read.
SETUP_CFG = "setup.cfg"

#: The page that documents the gate.
DOC = "docs/precommit.md"

#: The site configuration the page must be listed in.
MKDOCS = "mkdocs.yml"

#: The hooks the plan names as mandatory. Without them there is no gate.
REQUIRED_HOOKS: Tuple[str, ...] = ("black", "flake8", "end-of-file-fixer")

#: Hook id -> the distribution whose floor in ``pyproject.toml`` it must meet.
PINNED_HOOKS: Dict[str, str] = {"black": "black", "flake8": "flake8"}

#: Every stage name pre-commit accepts. A typo here means a hook that never
#: runs, which is indistinguishable from a hook that passes.
KNOWN_STAGES = frozenset(
    {
        "commit",
        "commit-msg",
        "merge-commit",
        "post-checkout",
        "post-commit",
        "post-merge",
        "post-rewrite",
        "prepare-commit-msg",
        "manual",
    }
)

#: Revisions that are a moving target rather than a pin.
BRANCH_REVS = frozenset(
    {"main", "master", "head", "trunk", "develop", "development", "dev", "latest", "stable"}
)

#: Files the gates exist to protect. A pattern that excludes one of them is a
#: pattern that disables the gate for the code that matters most.
SHIPPED_CODE: Tuple[str, ...] = (
    "caching.py",
    "datheon/__init__.py",
    "datheon/cli/_registry.py",
    "projects/01_hangman_game/main.py",
    "tools/precommit_config.py",
    "tests/test_precommit_config.py",
)

#: Suffixes a hygiene hook cannot read as text. They are binaries by
#: convention, and ``.gitattributes`` marks most of them ``binary`` too.
BINARY_SUFFIXES = frozenset(
    {".gif", ".png", ".jpg", ".jpeg", ".ico", ".pdf", ".zip", ".gz", ".whl", ".ttf", ".woff"}
)

#: Directories that are generated or vendored, never source.
SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "build",
        "dist",
        "site",
        "mutants",
        ".pytest_cache",
        ".mypy_cache",
        ".hypothesis",
        ".tox",
    }
)

#: Shapes a credential takes. The configuration is a text file that a
#: contributor edits by hand, so it is exactly where a pasted token ends up.
SECRET_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
)

#: Every code this module can emit. ``precommit.docs`` fails when the page does
#: not name one of them, so the documentation cannot fall behind the gate.
FINDING_CODES: Tuple[str, ...] = (
    "precommit.file",
    "precommit.repo",
    "precommit.rev",
    "precommit.hook",
    "precommit.duplicate",
    "precommit.version",
    "precommit.args",
    "precommit.scope",
    "precommit.stage",
    "precommit.secret",
    "precommit.whitespace",
    "precommit.docs",
)

#: How many offending lines a whitespace finding names before it summarises.
MAX_NAMED_LINES = 3


@dataclass(frozen=True)
class Finding:
    """One rule the configuration breaks.

    Attributes:
        code: A stable dotted identifier (``precommit.rev``).
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


@dataclass(frozen=True)
class Hook:
    """One hook entry, flattened out of its repository.

    Attributes:
        id: The hook id (``black``, ``end-of-file-fixer``, …).
        repo: The repository URL the hook comes from.
        rev: The revision the repository is pinned to.
        args: The ``args`` list, exactly as written.
        files: The ``files`` include pattern, or an empty string.
        exclude: The ``exclude`` pattern, or an empty string.
        stages: The ``stages`` list, empty when the hook runs at every stage.
    """

    id: str
    repo: str
    rev: str
    args: Tuple[str, ...] = ()
    files: str = ""
    exclude: str = ""
    stages: Tuple[str, ...] = ()

    def render(self) -> str:
        """Return the hook as ``id@rev``, for a finding message.

        Returns:
            The id and the pin, joined by an at-sign.
        """
        return f"{self.id}@{self.rev}"


@dataclass(frozen=True)
class Repo:
    """One ``repos`` entry, with its hooks.

    Attributes:
        url: The repository URL, as written.
        rev: The pinned revision, as written.
        hooks: The hooks the entry declares.
    """

    url: str
    rev: str
    hooks: Tuple[Hook, ...] = ()


@dataclass
class Report:
    """The verdict of one audit run.

    Attributes:
        findings: Every rule broken, in the order the checks ran.
        repos: How many hook repositories the configuration declares.
        hooks: How many hooks it installs.
        checked: How many files the hygiene audit read.
        unmeasurable: ``True`` when a check could not run at all.
        reason: Why the audit could not measure, when it could not.
    """

    findings: List[Finding] = field(default_factory=list)
    repos: int = 0
    hooks: int = 0
    checked: int = 0
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


def load_document(text: str) -> Optional[Mapping[str, Any]]:
    """Parse the configuration's YAML into a mapping.

    Args:
        text: The file's contents.

    Returns:
        The document as a mapping, or ``None`` when it is not a YAML mapping —
        an empty file, a list at the top level, or a syntax error.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    return document


def load_yaml_file(path: Path) -> Optional[Mapping[str, Any]]:
    """Read and parse a YAML mapping from disk.

    Args:
        path: The file to read.

    Returns:
        The mapping, or ``None`` when the file is missing or not a mapping.
    """
    text = read_text(path)
    if text is None:
        return None
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    return document


# --- versions and requirement strings --------------------------------------
def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """Turn ``v24.10.0``, ``24.10`` or ``7.1.1`` into a comparable tuple.

    Args:
        text: A version string, with or without a leading ``v``.

    Returns:
        The numeric components, or ``None`` when the string does not start with
        a number — ``main`` and ``latest`` are not versions.
    """
    match = re.match(r"^v?(\d+(?:\.\d+)*)", str(text).strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def version_at_least(pin: Tuple[int, ...], floor: Tuple[int, ...]) -> bool:
    """Compare a pin with a declared floor, component by component.

    The floor is padded rather than the pin, so ``24.10.0`` satisfies
    ``>=24.1`` (``(24, 10) >= (24, 1)``) while ``23.12.1`` does not.

    Args:
        pin: The pinned version.
        floor: The lowest version the project accepts.

    Returns:
        ``True`` when the pin is at or above the floor.
    """
    width = len(floor)
    head = tuple(pin[:width]) + (0,) * (width - len(pin[:width]))
    return head >= floor


def requirement_name(spec: str) -> str:
    """Return the distribution name from a requirement string.

    Args:
        spec: A PEP 508 requirement such as ``black>=24.1`` or
            ``tomli>=2.0; python_version < '3.11'``.

    Returns:
        The lower-cased name, with any extras and whitespace removed.
    """
    head = re.split(r"[<>=!~;\[\s]", spec.strip(), maxsplit=1)[0]
    return head.strip().lower().replace("_", "-")


def requirement_floor(spec: str) -> Optional[Tuple[int, ...]]:
    """Return the lowest version a requirement string allows.

    Args:
        spec: A PEP 508 requirement such as ``black>=24.1``.

    Returns:
        The version components, or ``None`` when the string carries no version
        constraint (``pytest``) or the constraint is not a floor (``black<25``).
    """
    match = re.search(r"(>=|==|~=|>)\s*v?(\d+(?:\.\d+)*)", spec)
    if match is None:
        return None
    return parse_version(match.group(2))


def project_floors(root: Path) -> Dict[str, Tuple[int, ...]]:
    """Read the dev-extra floors out of ``pyproject.toml``.

    Args:
        root: The repository root.

    Returns:
        A mapping of distribution name to its declared floor. A requirement
        without a version constraint is omitted: it declares no floor to meet.
    """
    text = read_text(root / PYPROJECT)
    if text is None:  # pragma: no cover - every audit tree has one
        return {}
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:  # pragma: no cover - a broken file is a finding elsewhere
        return {}
    project = document.get("project")
    if not isinstance(project, dict):  # pragma: no cover - defensive
        return {}
    extras = project.get("optional-dependencies")
    if not isinstance(extras, dict):  # pragma: no cover - defensive
        return {}
    dev = extras.get("dev")
    if not isinstance(dev, list):  # pragma: no cover - defensive
        return {}
    floors: Dict[str, Tuple[int, ...]] = {}
    for spec in dev:
        if not isinstance(spec, str):  # pragma: no cover - defensive
            continue
        floor = requirement_floor(spec)
        if floor is not None:
            floors[requirement_name(spec)] = floor
    return floors


def config_line_length(root: Path) -> Optional[int]:
    """Read ``[tool.black] line-length`` from ``pyproject.toml``.

    Args:
        root: The repository root.

    Returns:
        The declared line length, or ``None`` when it is not declared.
    """
    text = read_text(root / PYPROJECT)
    if text is None:  # pragma: no cover - every audit tree has one
        return None
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:  # pragma: no cover - reported as a finding elsewhere
        return None
    tool = document.get("tool")
    if not isinstance(tool, dict):  # pragma: no cover - defensive
        return None
    black = tool.get("black")
    if not isinstance(black, dict):  # pragma: no cover - defensive
        return None
    value = black.get("line-length")
    return value if isinstance(value, int) else None


def flake8_line_length(root: Path) -> Optional[int]:
    """Read ``max-line-length`` from the ``[flake8]`` section of ``setup.cfg``.

    Args:
        root: The repository root.

    Returns:
        The declared line length, or ``None`` when the section does not set one.
    """
    text = read_text(root / SETUP_CFG)
    if text is None:
        return None
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section != "flake8" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "max-line-length":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


# --- argument helpers ------------------------------------------------------
def option_value(args: Sequence[str], *names: str) -> Optional[str]:
    """Return the value given to one of ``names`` in an argument list.

    Handles both spellings pre-commit accepts: ``--line-length 100`` and
    ``--line-length=100``.

    Args:
        args: The hook's ``args`` list.
        *names: The option spellings to look for, in priority order.

    Returns:
        The value, an empty string when the option is present without one, or
        ``None`` when the option is absent.
    """
    for index, item in enumerate(args):
        for name in names:
            if item == name:
                if index + 1 < len(args):
                    return args[index + 1]
                return ""
            prefix = f"{name}="
            if item.startswith(prefix):
                return item[len(prefix) :]
    return None


def is_floating_rev(rev: str) -> bool:
    """Whether a revision is a moving target rather than a pin.

    Args:
        rev: The ``rev`` value from a repo entry.

    Returns:
        ``True`` for a branch name, ``HEAD``, ``latest``, or anything that does
        not carry a version number.
    """
    text = str(rev).strip()
    if not text:
        return True
    if text.lower() in BRANCH_REVS or text.lower().startswith("refs/"):
        return True
    return parse_version(text) is None


# --- parsing the configuration --------------------------------------------
def _as_sequence(value: Any) -> List[Any]:
    """Coerce a YAML value into a list, without inventing entries.

    Args:
        value: Whatever the document held under a key.

    Returns:
        The list itself when it is one, an empty list otherwise.
    """
    return list(value) if isinstance(value, list) else []


def parse_repos(document: Mapping[str, Any]) -> Tuple[List[Repo], List[Finding]]:
    """Flatten ``repos:`` into :class:`Repo` and :class:`Hook` objects.

    Malformed entries are not skipped silently: each one produces a finding, so
    a configuration that pre-commit would reject is also a configuration this
    audit rejects.

    Args:
        document: The parsed configuration.

    Returns:
        The repositories, and the findings raised while parsing them.
    """
    repos: List[Repo] = []
    findings: List[Finding] = []
    entries = _as_sequence(document.get("repos"))
    if not entries:
        findings.append(Finding("precommit.file", "the configuration declares no `repos:` list"))
        return repos, findings
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            findings.append(Finding("precommit.repo", f"repos[{index}] is not a mapping"))
            continue
        url = str(entry.get("repo", "")).strip()
        rev = str(entry.get("rev", "")).strip()
        if not url:
            findings.append(Finding("precommit.repo", f"repos[{index}] has no `repo:` URL"))
        hooks: List[Hook] = []
        raw_hooks = entry.get("hooks")
        if not isinstance(raw_hooks, list) or not raw_hooks:
            findings.append(
                Finding("precommit.repo", f"repos[{index}] ({url or '?'}) declares no hooks")
            )
        else:
            for position, raw in enumerate(raw_hooks, start=1):
                if not isinstance(raw, dict) or not str(raw.get("id", "")).strip():
                    findings.append(
                        Finding(
                            "precommit.repo",
                            f"repos[{index}].hooks[{position}] has no `id:`",
                        )
                    )
                    continue
                hooks.append(
                    Hook(
                        id=str(raw["id"]).strip(),
                        repo=url,
                        rev=rev,
                        args=tuple(str(arg) for arg in _as_sequence(raw.get("args"))),
                        files=str(raw.get("files", "") or ""),
                        exclude=str(raw.get("exclude", "") or ""),
                        stages=tuple(str(stage) for stage in _as_sequence(raw.get("stages"))),
                    )
                )
        repos.append(Repo(url=url, rev=rev, hooks=tuple(hooks)))
    return repos, findings


def all_hooks(repos: Sequence[Repo]) -> List[Hook]:
    """Return every hook in the configuration, in declaration order.

    Args:
        repos: The parsed repositories.

    Returns:
        A flat list of hooks.
    """
    return [hook for repo in repos for hook in repo.hooks]


# --- checks ----------------------------------------------------------------
def _check_pins(report: Report, repos: Sequence[Repo]) -> None:
    """Check that every repository is pinned to a released version.

    Args:
        report: The verdict being built.
        repos: The parsed repositories.
    """
    for repo in repos:
        if not repo.rev:
            report.add(
                "precommit.rev",
                f"{repo.url or 'repos entry'} has no `rev:` — pin it to a release",
            )
        elif is_floating_rev(repo.rev):
            report.add(
                "precommit.rev",
                f"{repo.url or 'repos entry'} is pinned to `{repo.rev}`, which moves; "
                "pin a released version",
            )


def _check_required(report: Report, hooks: Sequence[Hook]) -> None:
    """Check that the required hooks are present, once each.

    Args:
        report: The verdict being built.
        hooks: Every hook in the configuration.
    """
    seen: Dict[str, int] = {}
    for hook in hooks:
        seen[hook.id] = seen.get(hook.id, 0) + 1
    for required in REQUIRED_HOOKS:
        if required not in seen:
            report.add(
                "precommit.hook",
                f"the `{required}` hook is missing — the commit is not gated on it",
            )
    for hook_id, count in sorted(seen.items()):
        if count > 1:
            report.add(
                "precommit.duplicate",
                f"the `{hook_id}` hook is declared {count} times",
            )


def _check_versions(
    report: Report, hooks: Sequence[Hook], floors: Mapping[str, Tuple[int, ...]]
) -> None:
    """Check every pin against the floor ``pyproject.toml`` declares.

    Args:
        report: The verdict being built.
        hooks: Every hook in the configuration.
        floors: Distribution name to declared floor.
    """
    for hook in hooks:
        distribution = PINNED_HOOKS.get(hook.id)
        if distribution is None:
            continue
        pin = parse_version(hook.rev)
        floor = floors.get(distribution)
        if floor is None:
            report.add(
                "precommit.version",
                f"`{distribution}` has no version floor in {PYPROJECT}'s dev extra, "
                f"so `{hook.render()}` cannot be compared with it",
            )
            continue
        if pin is None:
            continue  # already reported as a floating revision
        if not version_at_least(pin, floor):
            wanted = ".".join(str(part) for part in floor)
            report.add(
                "precommit.version",
                f"`{hook.render()}` is below the `{distribution}>={wanted}` floor "
                f"declared in {PYPROJECT}",
            )


def _check_args(
    report: Report,
    hooks: Sequence[Hook],
    black_length: Optional[int],
    flake8_length: Optional[int],
    ships_markdown: bool,
) -> None:
    """Check that hook arguments do not contradict the repository's settings.

    Args:
        report: The verdict being built.
        hooks: Every hook in the configuration.
        black_length: ``[tool.black] line-length``, when declared.
        flake8_length: ``[flake8] max-line-length``, when declared.
        ships_markdown: Whether the tree has markdown a hook will read.
    """
    for hook in hooks:
        if hook.id == "black":
            value = option_value(hook.args, "--line-length", "-l")
            if value is not None and black_length is not None:
                if not value.strip().isdigit() or int(value) != black_length:
                    report.add(
                        "precommit.args",
                        f"black is passed `--line-length {value}` but "
                        f"{PYPROJECT} declares {black_length}; drop the argument "
                        "so the hook reads the project's setting",
                    )
        if hook.id == "flake8":
            value = option_value(hook.args, "--max-line-length")
            if value is not None and flake8_length is not None:
                if not value.strip().isdigit() or int(value) != flake8_length:
                    report.add(
                        "precommit.args",
                        f"flake8 is passed `--max-line-length {value}` but "
                        f"{SETUP_CFG} declares {flake8_length}; drop the argument "
                        "so the hook reads the project's setting",
                    )
        if hook.id == "trailing-whitespace" and ships_markdown:
            value = option_value(hook.args, "--markdown-linebreak-ext")
            if value is None:
                report.add(
                    "precommit.args",
                    "the `trailing-whitespace` hook will strip markdown's two-space "
                    "hard break; pass `--markdown-linebreak-ext=md`",
                )
            elif "md" not in [part.strip().lstrip(".") for part in value.split(",")]:
                report.add(
                    "precommit.args",
                    f"`--markdown-linebreak-ext={value}` does not cover `md`, so the "
                    "hook will rewrite every hard line break in the documentation",
                )


def _pattern_hits(pattern: str, paths: Sequence[str]) -> Optional[str]:
    """Return the first path a regex pattern matches.

    Args:
        pattern: A Python regular expression, as pre-commit reads it.
        paths: Candidate repository-relative paths.

    Returns:
        The first matching path, or ``None`` when the pattern matches none of
        them — or is not a valid regular expression.
    """
    if not pattern.strip():
        return None
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None
    for path in paths:
        if compiled.search(path):
            return path
    return None


def _check_scope(report: Report, repos: Sequence[Repo], document: Mapping[str, Any]) -> None:
    """Check that no hook is scoped out of the code it exists to check.

    Args:
        report: The verdict being built.
        repos: The parsed repositories.
        document: The parsed configuration, for the top-level patterns.
    """
    top_exclude = str(document.get("exclude", "") or "")
    hit = _pattern_hits(top_exclude, SHIPPED_CODE)
    if hit is not None:
        report.add(
            "precommit.scope",
            f"the top-level `exclude: {top_exclude}` skips `{hit}`, which the hooks exist to check",
        )
    top_files = str(document.get("files", "") or "")
    if top_files.strip():
        try:
            compiled = re.compile(top_files)
        except re.error:
            report.add("precommit.scope", f"the top-level `files: {top_files}` is not a regex")
            return
        if not any(compiled.search(path) for path in SHIPPED_CODE):
            report.add(
                "precommit.scope",
                f"the top-level `files: {top_files}` matches none of the shipped code",
            )
    for hook in all_hooks(repos):
        hit = _pattern_hits(hook.exclude, SHIPPED_CODE)
        if hit is not None:
            report.add(
                "precommit.scope",
                f"`{hook.id}` excludes `{hit}` via `exclude: {hook.exclude}`",
            )
        if hook.files.strip() and hook.id in REQUIRED_HOOKS:
            try:
                compiled = re.compile(hook.files)
            except re.error:
                report.add(
                    "precommit.scope",
                    f"`{hook.id}` has `files: {hook.files}`, which is not a regex",
                )
                continue
            if not any(compiled.search(path) for path in SHIPPED_CODE):
                report.add(
                    "precommit.scope",
                    f"`{hook.id}` only runs on `files: {hook.files}`, which matches "
                    "none of the shipped code",
                )


def _check_stages(report: Report, repos: Sequence[Repo], document: Mapping[str, Any]) -> None:
    """Check that every declared stage is one pre-commit knows.

    Args:
        report: The verdict being built.
        repos: The parsed repositories.
        document: The parsed configuration, for ``default_stages``.
    """
    for stage in _as_sequence(document.get("default_stages")):
        name = str(stage)
        if name not in KNOWN_STAGES:
            report.add(
                "precommit.stage",
                f"`default_stages: {name}` is not a pre-commit stage — the hooks "
                "would never run",
            )
    for hook in all_hooks(repos):
        for name in hook.stages:
            if name not in KNOWN_STAGES:
                report.add(
                    "precommit.stage",
                    f"`{hook.id}` declares stage `{name}`, which pre-commit does not know",
                )


def _check_secrets(report: Report, text: str) -> None:
    """Check the configuration text for token-shaped strings.

    Args:
        report: The verdict being built.
        text: The configuration's raw contents.
    """
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern in SECRET_PATTERNS:
            if pattern.search(line):
                report.add(
                    "precommit.secret",
                    f"line {number} looks like a credential; the configuration is "
                    "committed, so a token here is a token in the history",
                )
                break


def audit_config(root: Path, path: Optional[Path] = None) -> Report:
    """Audit the hook configuration against the repository's own declarations.

    Args:
        root: The repository root.
        path: The configuration to read, when it is not ``root/CONFIG``.

    Returns:
        The verdict, with ``unmeasurable`` set when there is nothing to read.
    """
    target = path if path is not None else root / CONFIG
    report = Report()
    text = read_text(target)
    if text is None:
        report.unmeasurable = True
        report.reason = f"{CONFIG} is missing or unreadable"
        report.add("precommit.file", f"{target.name} is missing or unreadable")
        return report
    document = load_document(text)
    if document is None:
        report.unmeasurable = True
        report.reason = f"{CONFIG} is not a YAML mapping"
        report.add("precommit.file", f"{target.name} is not a YAML mapping")
        return report

    repos, parse_findings = parse_repos(document)
    report.findings.extend(parse_findings)
    report.repos = len(repos)
    hooks = all_hooks(repos)
    report.hooks = len(hooks)

    _check_pins(report, repos)
    _check_required(report, hooks)
    _check_versions(report, hooks, project_floors(root))
    _check_args(
        report,
        hooks,
        config_line_length(root),
        flake8_line_length(root),
        ships_markdown(root),
    )
    _check_scope(report, repos, document)
    _check_stages(report, repos, document)
    _check_secrets(report, text)
    return report


def ships_markdown(root: Path) -> bool:
    """Whether the tree holds markdown the hygiene hooks will read.

    Args:
        root: The repository root.

    Returns:
        ``True`` when at least one markdown file is present.
    """
    for path in root.glob("*.md"):
        if path.is_file():
            return True
    docs = root / "docs"
    return docs.is_dir() and any(docs.rglob("*.md"))


# --- documentation ---------------------------------------------------------
def nav_pages(nav: Any) -> List[str]:
    """Collect every page path from a ``mkdocs.yml`` nav tree.

    Args:
        nav: The ``nav`` value: a nested list of strings and mappings.

    Returns:
        Every ``*.md`` path named anywhere in the tree, in document order.
    """
    pages: List[str] = []
    if isinstance(nav, str):
        pages.append(nav)
    elif isinstance(nav, dict):
        for value in nav.values():
            pages.extend(nav_pages(value))
    elif isinstance(nav, list):
        for item in nav:
            pages.extend(nav_pages(item))
    return pages


def audit_docs(root: Path) -> List[Finding]:
    """Check that the page explaining the gate exists, is linked, and is current.

    Args:
        root: The repository root.

    Returns:
        One finding per problem, or an empty list.
    """
    findings: List[Finding] = []
    text = read_text(root / DOC)
    if text is None:
        return [
            Finding(
                "precommit.docs",
                f"{DOC} is missing — a gate nobody can read about is a gate nobody enables",
            )
        ]
    config = load_yaml_file(root / MKDOCS)
    if config is None:
        findings.append(Finding("precommit.docs", f"{MKDOCS} is missing or is not a YAML mapping"))
    else:
        pages = nav_pages(config.get("nav"))
        if DOC.removeprefix("docs/") not in pages:
            findings.append(Finding("precommit.docs", f"{DOC} is not listed in the {MKDOCS} nav"))
    for code in FINDING_CODES:
        if code not in text:
            findings.append(
                Finding("precommit.docs", f"{DOC} does not document the `{code}` finding")
            )
    return findings


# --- file hygiene ----------------------------------------------------------
def tracked_files(root: Path, runner: Any = None) -> Optional[List[str]]:
    """List the files git tracks under ``root``.

    The hygiene hooks only ever see tracked files, so the audit looks at the
    same set. A repository without git (a fixture, an exported tarball) returns
    ``None`` and the caller falls back to walking the tree.

    Args:
        root: The repository root.
        runner: A callable with :func:`subprocess.run`'s signature, injected by
            the tests so no test needs a real repository.

    Returns:
        Repository-relative POSIX paths, or ``None`` when git could not answer.
    """
    run = subprocess.run if runner is None else runner
    try:
        completed = run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):  # pragma: no cover - no git binary at all
        return None
    if completed.returncode != 0:
        return None
    return [item for item in completed.stdout.split("\0") if item]


def walk_files(root: Path) -> Iterator[Path]:
    """Yield every file under ``root``, skipping generated directories.

    Args:
        root: The repository root.

    Yields:
        The files, in a stable order.
    """
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        yield path


def _line_problems(text: str, markdown: bool) -> List[Tuple[int, str]]:
    """Find the lines a hygiene hook would rewrite.

    Args:
        text: The file's decoded contents.
        markdown: Whether the file is markdown, where two trailing spaces are a
            hard line break rather than noise.

    Returns:
        A pair of line number and problem for each offending line.
    """
    problems: List[Tuple[int, str]] = []
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.rstrip("\r")
        if stripped == "":
            continue
        if stripped != stripped.rstrip():
            if markdown and stripped.endswith("  ") and not stripped.endswith("   "):
                continue
            problems.append((number, "trailing whitespace"))
    return problems


def audit_whitespace(
    root: Path,
    files: Optional[Sequence[str]] = None,
    runner: Any = None,
) -> List[Finding]:
    """Check the tracked files against the three hygiene hooks.

    Args:
        root: The repository root.
        files: Repository-relative paths to check. Defaults to git's index.
        runner: A callable with :func:`subprocess.run`'s signature, injected by
            the tests.

    Returns:
        One finding per file that a hygiene hook would rewrite.
    """
    if files is None:
        listed = tracked_files(root, runner)
        if listed is None:
            listed = [path.relative_to(root).as_posix() for path in walk_files(root)]
        files = listed
    findings: List[Finding] = []
    for relative in files:
        path = root / relative
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(Finding("precommit.whitespace", f"{relative} is not valid UTF-8"))
            continue
        problems: List[str] = []
        if b"\r\n" in data:
            problems.append("CRLF line endings")
        if text and not text.endswith("\n"):
            problems.append("no final newline")
        lines = _line_problems(text, path.suffix.lower() == ".md")
        if lines:
            named = ", ".join(f"line {number}" for number, _ in lines[:MAX_NAMED_LINES])
            more = (
                "" if len(lines) <= MAX_NAMED_LINES else f" (+{len(lines) - MAX_NAMED_LINES} more)"
            )
            problems.append(f"trailing whitespace at {named}{more}")
        if problems:
            findings.append(
                Finding(
                    "precommit.whitespace",
                    f"{relative}: {'; '.join(problems)} — `pre-commit run --all-files` "
                    "would rewrite it",
                )
            )
    return findings


# --- the whole audit -------------------------------------------------------
def audit_tree(root: Path, runner: Any = None) -> Report:
    """Run every check over one tree.

    Args:
        root: The repository root.
        runner: A callable with :func:`subprocess.run`'s signature, injected by
            the tests.

    Returns:
        The combined verdict.
    """
    report = audit_config(root)
    report.findings.extend(audit_docs(root))
    files: List[str] = []
    if not report.unmeasurable:
        listed = tracked_files(root, runner)
        if listed is None:
            listed = [path.relative_to(root).as_posix() for path in walk_files(root)]
        files = listed
    report.findings.extend(audit_whitespace(root, files, runner))
    report.checked = len(files)
    return report


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
        "tool": "precommit_config",
        "schema": 1,
        "ok": report.passed,
        "root": str(root),
        "repos": report.repos,
        "hooks": report.hooks,
        "files_checked": report.checked,
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
        "Pre-commit configuration audit",
        f"  tree:      {root}",
        f"  repos:     {report.repos}",
        f"  hooks:     {report.hooks}",
        f"  files:     {report.checked}",
        "",
    ]
    if report.unmeasurable:
        lines.append(f"UNMEASURABLE — {report.reason}")
    elif report.passed:
        lines.append("PASS — the hooks are pinned, scoped and would pass on this tree")
    else:
        lines.append(f"FAIL — {len(report.findings)} finding(s)")
    for finding in report.findings:
        lines.append(f"  {finding.render()}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        The parser for ``python tools/precommit_config.py``.
    """
    parser = argparse.ArgumentParser(
        prog="precommit_config",
        description=(
            "Audit .pre-commit-config.yaml: pins, floors, scopes, stages and the "
            "whitespace the hooks would rewrite."
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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit and return the process exit code.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        ``0`` clean, ``1`` at least one finding, ``2`` the audit could not
        measure — there is no configuration to read.
    """
    args = build_parser().parse_args(None if argv is None else list(argv))
    root = Path(args.root).resolve()
    report = audit_tree(root)

    if args.json:
        print(json.dumps(report_to_dict(report, root), indent=2))
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
