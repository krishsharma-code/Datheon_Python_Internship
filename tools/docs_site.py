"""Documentation-site audit: prove the docs build is the docs that ship (Day 25).

Every day so far produced a document, and every one of them was written to be
read in a pull request: ``docs/cli_contract.md``, ``docs/persistence.md``,
``docs/ci_matrix.md`` and the rest are accurate, but they are only reachable by
someone who already knows the file names. Day 25 turns that directory into a
site — ``mkdocs.yml`` plus the Material theme, with an API reference generated
from the docstrings that already exist.

The risk is the one every static-site repository carries: a site is a
*declaration* too, and it rots silently. MkDocs is happy to build a site with a
page missing from the nav (it just is not linked), with a module nobody
documented (it just is not there), with ``strict: false`` (warnings scroll past
in a build log nobody reads) and with a deployment workflow that uploads an
artefact no job deploys. None of those produce a failing test. They produce a
site that is quietly smaller than the repository it describes.

So this module checks the declaration against the repository:

* **the configuration** — a name, an absolute Pages ``site_url`` that matches
  the repository, the Material theme, ``strict: true``, an existing ``docs_dir``
  and a ``site_dir`` that git ignores (a committed build output is a copy of the
  docs that will drift);
* **the navigation** — every page named in ``nav`` exists on disk, every ``.md``
  file on disk appears in ``nav``, nothing is listed twice, and the home page is
  first;
* **the API reference** — ``mkdocstrings`` is configured with the Google
  docstring style and the repository root on its import path, every module and
  package that ``pyproject.toml`` ships has a page whose ``:::`` target resolves,
  and no page documents something that is not shipped;
* **the deployment** — ``.github/workflows/docs.yml`` triggers on ``main``, holds
  ``pages: write`` and ``id-token: write``, builds with ``mkdocs build --strict``,
  uploads the Pages artefact and deploys it from a ``github-pages`` environment;
* **the link from the README** — a docs site nobody links to is a docs site
  nobody finds, and the packaging extra that installs the toolchain.

Each defect is a *finding* with a stable code, and the test suite asserts a
named finding per defect rather than "the audit failed somehow".

Run it directly::

    python tools/docs_site.py                    # audit this repository
    python tools/docs_site.py --list-pages       # print the nav, in order
    python tools/docs_site.py --list-modules     # print the documented targets
    python tools/docs_site.py --strict           # warnings become failures too
    python tools/docs_site.py --json             # machine-readable verdict

Exit codes: ``0`` every check passed, ``1`` at least one finding, ``2`` the
audit could not reach a verdict (``mkdocs.yml`` missing or unparsable, PyYAML
absent). A gate that cannot measure must never report success.

Requires ``PyYAML`` (``requirements.txt``); it is development tooling only — the
five projects remain standard-library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - exercised only by the missing-dependency test
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency is in requirements.txt
    yaml = None  # type: ignore[assignment]

try:  # pragma: no cover - exercised only by the missing-dependency test
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

#: Exit code for a site that passes every check.
EXIT_OK = 0

#: Exit code for a site with at least one finding.
EXIT_FINDINGS = 1

#: Exit code for an audit that could not measure anything.
EXIT_UNMEASURABLE = 2

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: The site configuration this audit exists for.
DEFAULT_CONFIG: Path = ROOT / "mkdocs.yml"

#: The packaging declaration that says which modules the site must document.
DEFAULT_PYPROJECT: Path = ROOT / "pyproject.toml"

#: The workflow that publishes the built site.
DEFAULT_WORKFLOW: Path = ROOT / ".github" / "workflows" / "docs.yml"

#: The page that links the live site, so a reader can find it at all.
DEFAULT_README: Path = ROOT / "README.md"

#: The ignore file that must keep the build output out of the repository.
DEFAULT_GITIGNORE: Path = ROOT / ".gitignore"

#: Where the site is published. GitHub Pages for a project repository is
#: ``https://<owner>.github.io/<repo>/``, and it must match ``site_url`` exactly
#: or every canonical link and ``sitemap.xml`` entry points somewhere else.
DEFAULT_SITE_URL = "https://krishsharma-code.github.io/Datheon_Python_Internship/"

#: The theme the deliverable names. Material is not a preference here: the
#: palette, the navigation features and the search UI in ``mkdocs.yml`` are all
#: Material options, and a different theme silently ignores every one of them.
REQUIRED_THEME = "material"

#: The docstring convention the source tree is written in. mkdocstrings renders
#: ``Args:`` / ``Returns:`` / ``Raises:`` sections only when it is told this.
REQUIRED_DOCSTRING_STYLE = "google"

#: Packages that must be installed to build the site. They live in the ``docs``
#: extra of ``pyproject.toml`` so a contributor installs them once, with the
#: version pins the build was verified against.
REQUIRED_DOCS_PACKAGES: Tuple[str, ...] = ("mkdocs-material", "mkdocstrings")

#: A fenced code block. A ``:::`` line inside one is *text a page shows*, not a
#: directive it renders — the guide that documents this syntax would otherwise
#: be reported as documenting the module twice.
_FENCE = re.compile(r"^(?P<fence>```+|~~~+).*?^(?P=fence)", re.DOTALL | re.MULTILINE)

#: A ``:::`` directive, optionally followed by an options block. Only the target
#: matters here; the indented options are mkdocstrings' business.
_DIRECTIVE = re.compile(r"^:::+\s+(?P<target>[A-Za-z_][\w.]*)", re.MULTILINE)


class SiteConfigError(RuntimeError):
    """The site configuration could not be read at all.

    Raised when ``mkdocs.yml`` is missing, unparsable, or is not a mapping. There
    is no point auditing a document that does not exist, so the caller turns
    this into exit code ``2`` rather than a finding.
    """


@dataclass(frozen=True)
class Finding:
    """One thing the audit does not like.

    Attributes:
        code: A stable dotted identifier, e.g. ``nav.orphan``. The test suite
            asserts on these, so they are part of the contract.
        message: A one-line explanation written for whoever has to fix it.
        where: The part of the site the finding is about.
        severity: ``"error"`` fails the audit; ``"warning"`` fails it only
            under ``--strict``.
    """

    code: str
    message: str
    where: str = "mkdocs.yml"
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
class SiteVerdict:
    """The result of auditing one documentation site.

    Attributes:
        config_path: The configuration file that was audited.
        site_url: The published URL the site claims.
        pages: The nav's page paths, in the order a reader meets them.
        modules: Every ``:::`` target the API reference documents.
        findings: Everything wrong with the site, in discovery order.
        strict: Whether warnings counted as failures.
        measurable: Whether the audit reached a verdict at all.
    """

    config_path: str
    site_url: str = ""
    pages: Tuple[str, ...] = ()
    modules: Tuple[str, ...] = ()
    findings: Tuple[Finding, ...] = ()
    strict: bool = False
    measurable: bool = True

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
        """Return whether the site passed.

        A verdict that measured no pages is never ``ok``: a site with an empty
        nav is not a documentation site, and silence is not evidence of
        correctness.
        """
        if not self.measurable or not self.pages:
            return False
        if self.errors:
            return False
        return not (self.strict and self.warnings)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable view of the verdict."""
        return {
            "config": self.config_path,
            "ok": self.ok,
            "strict": self.strict,
            "measurable": self.measurable,
            "site_url": self.site_url,
            "page_count": len(self.pages),
            "pages": list(self.pages),
            "module_count": len(self.modules),
            "modules": list(self.modules),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [finding.to_dict() for finding in self.findings],
        }


# ---------------------------------------------------------------------------
# Reading the declaration
# ---------------------------------------------------------------------------
def load_config(path: Path) -> Mapping[str, Any]:
    """Parse an ``mkdocs.yml``.

    Args:
        path: The configuration to read.

    Returns:
        The parsed document as a mapping.

    Raises:
        SiteConfigError: If PyYAML is unavailable, the file does not exist, is
            not valid YAML, or is not a mapping at the top level.
    """
    if yaml is None:  # pragma: no cover - the dependency is installed in CI
        raise SiteConfigError("PyYAML is not installed; `pip install -r requirements.txt`")
    path = Path(path)
    if not path.is_file():
        raise SiteConfigError(f"{path} does not exist")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise SiteConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:  # pragma: no cover - message differs by version
        raise SiteConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise SiteConfigError(f"{path} is not a YAML mapping")
    return document


def load_yaml_file(path: Path) -> Optional[Mapping[str, Any]]:
    """Parse an optional YAML file, returning ``None`` when it is absent.

    Args:
        path: The file to read.

    Returns:
        The parsed mapping, or ``None`` when the file does not exist or is not a
        mapping. A malformed deployment workflow is reported as a finding rather
        than an exception, because the rest of the site is still worth checking.
    """
    if yaml is None:  # pragma: no cover - the dependency is installed in CI
        return None
    path = Path(path)
    if not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return document if isinstance(document, Mapping) else None


def load_pyproject(path: Path) -> Optional[Mapping[str, Any]]:
    """Parse ``pyproject.toml``, returning ``None`` when it is not there.

    Args:
        path: The declaration to read.

    Returns:
        The parsed document, or ``None`` when the file does not exist.
    """
    path = Path(path)
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return tomllib.load(handle)


def read_text(path: Path) -> Optional[str]:
    """Read a UTF-8 text file, returning ``None`` when it cannot be read.

    Args:
        path: The file to read.

    Returns:
        The file's text, or ``None`` when it is missing or undecodable.
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Small structural helpers
# ---------------------------------------------------------------------------
def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` as a mapping, or an empty one if it is not a mapping."""
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    """Return ``value`` as a list, or an empty one if it is not a list."""
    return list(value) if isinstance(value, (list, tuple)) else []


def nav_entries(nav: Any) -> Tuple[Tuple[str, str], ...]:
    """Flatten a ``nav`` tree into ``(title, path)`` pairs, in document order.

    MkDocs accepts a nav as a list of three shapes: a bare page path, a
    ``title: path`` mapping, or a ``title: [children]`` mapping that nests a
    whole section. Readers meet the pages in the order they appear, so the
    flattening has to preserve that order rather than sort.

    Args:
        nav: The parsed ``nav`` value.

    Returns:
        Every page path with the title it is listed under. Section headings and
        any value that is not a page path are skipped.
    """
    entries: List[Tuple[str, str]] = []
    for item in _as_list(nav):
        if isinstance(item, str):
            entries.append((Path(item).stem, item))
            continue
        if not isinstance(item, Mapping):
            continue
        for title, value in item.items():
            if isinstance(value, str):
                entries.append((str(title), value))
            else:
                for child_title, child_path in nav_entries(value):
                    entries.append((child_title, child_path))
    return tuple(entries)


def nav_pages(nav: Any) -> Tuple[str, ...]:
    """Return the page paths of a nav tree, in document order.

    Args:
        nav: The parsed ``nav`` value.

    Returns:
        The paths exactly as written in the configuration, e.g. ``api/index.md``.
    """
    return tuple(path for _title, path in nav_entries(nav))


def documentation_pages(docs_dir: Optional[Path]) -> Tuple[str, ...]:
    """List the markdown pages on disk, relative to the docs directory.

    Args:
        docs_dir: The directory the site is built from, or ``None`` when it is
            not available.

    Returns:
        Sorted POSIX-style relative paths, e.g. ``api/errors.md``. A missing
        directory yields an empty tuple rather than an exception, because "no
        pages" is a finding the audit can explain better than a traceback.
    """
    if docs_dir is None:
        return ()
    root = Path(docs_dir)
    if not root.is_dir():
        return ()
    return tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*.md")))


def directive_targets(text: str) -> Tuple[str, ...]:
    """Extract the ``:::`` targets from one markdown page.

    Args:
        text: The page's source.

    Returns:
        Every mkdocstrings target in the page, in the order it appears.
    """
    prose = _FENCE.sub("", text)
    return tuple(match.group("target") for match in _DIRECTIVE.finditer(prose))


def api_targets(docs_dir: Optional[Path], pages: Sequence[str]) -> Tuple[str, ...]:
    """Collect every ``:::`` target documented across the site.

    Args:
        docs_dir: The directory the pages live in.
        pages: Page paths relative to ``docs_dir``.

    Returns:
        The targets in nav order, de-duplicated.
    """
    if docs_dir is None:
        return ()
    seen: List[str] = []
    for page in pages:
        text = read_text(Path(docs_dir) / page) or ""
        for target in directive_targets(text):
            if target not in seen:
                seen.append(target)
    return tuple(seen)


def shipped_modules(pyproject: Optional[Mapping[str, Any]]) -> Tuple[str, ...]:
    """Return the importable modules and packages the distribution ships.

    ``pyproject.toml`` is the only place that knows the answer: a root-level
    module listed under ``[tool.setuptools] py-modules``, or a package matched by
    a ``[tool.setuptools.packages.find] include`` pattern. Every one of them is
    public API — if it ships, it is documented.

    Args:
        pyproject: The parsed packaging declaration, or ``None``.

    Returns:
        Importable names, sorted, e.g. ``("caching", "datheon", "errors", …)``.
    """
    if pyproject is None:
        return ()
    tool = _as_mapping(_as_mapping(pyproject.get("tool")).get("setuptools"))
    names: List[str] = [str(name) for name in _as_list(tool.get("py-modules"))]
    find = _as_mapping(_as_mapping(tool.get("packages")).get("find"))
    for pattern in _as_list(find.get("include")):
        name = str(pattern).rstrip("*").rstrip(".")
        if name and name not in names:
            names.append(name)
    return tuple(sorted(set(names)))


def _workflow_triggers(workflow: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the workflow's ``on:`` block.

    PyYAML resolves the unquoted key ``on`` to the boolean ``True`` (YAML 1.1),
    so a workflow read with ``safe_load`` needs both spellings checked.

    Args:
        workflow: The parsed workflow.

    Returns:
        The trigger mapping, or an empty mapping when there is none.
    """
    if "on" in workflow:
        return _as_mapping(workflow["on"])
    return _as_mapping(workflow.get(True))


def _workflow_jobs(workflow: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the workflow's jobs mapping.

    Args:
        workflow: The parsed workflow.

    Returns:
        The ``jobs`` mapping, or an empty mapping when there is none.
    """
    return _as_mapping(workflow.get("jobs"))


def _as_names(value: Any) -> List[str]:
    """Return a YAML value that is either one name or a list of names.

    GitHub Actions accepts ``needs: build`` and ``needs: [build, lint]``
    interchangeably, so an audit that only understood the list form would report
    a correctly wired job as unwired.

    Args:
        value: A string, a list of strings, or something else entirely.

    Returns:
        The names as a list; empty when there are none.
    """
    if isinstance(value, str):
        return [value]
    return [str(item) for item in _as_list(value)]


def _job_commands(job: Mapping[str, Any]) -> str:
    """Join every ``run:`` block of a job into one searchable string.

    Args:
        job: One job from the workflow.

    Returns:
        The job's shell commands, newline-separated.
    """
    commands: List[str] = []
    for step in _as_list(job.get("steps")):
        run = _as_mapping(step).get("run")
        if isinstance(run, str):
            commands.append(run)
    return "\n".join(commands)


def _job_uses(job: Mapping[str, Any]) -> Tuple[str, ...]:
    """List the actions a job invokes.

    Args:
        job: One job from the workflow.

    Returns:
        Every ``uses:`` reference in the job, in step order.
    """
    used: List[str] = []
    for step in _as_list(job.get("steps")):
        uses = _as_mapping(step).get("uses")
        if isinstance(uses, str):
            used.append(uses)
    return tuple(used)


def _job_with_action(workflow: Mapping[str, Any], action: str) -> Optional[Mapping[str, Any]]:
    """Return the first job that invokes ``action``.

    Args:
        workflow: The parsed workflow.
        action: An action name prefix, e.g. ``actions/deploy-pages``.

    Returns:
        The matching job, or ``None`` when no job uses the action.
    """
    for job in _workflow_jobs(workflow).values():
        job = _as_mapping(job)
        if any(uses.startswith(action) for uses in _job_uses(job)):
            return job
    return None


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
def check_configuration(
    config: Mapping[str, Any],
    *,
    site_url: str,
    docs_dir: Optional[Path],
    gitignore: Optional[str],
) -> Iterator[Finding]:
    """Check the parts of ``mkdocs.yml`` that are not the nav or the plugins.

    Args:
        config: The parsed configuration.
        site_url: The URL the site must claim, without a trailing-slash
            ambiguity: both sides are normalised before comparison.
        docs_dir: The directory the site is built from, already resolved.
        gitignore: The repository's ``.gitignore`` text, or ``None``.

    Yields:
        One finding per problem.
    """
    if not str(config.get("site_name") or "").strip():
        yield Finding(
            "config.site-name",
            "no `site_name:` is set, so every page title and the browser tab read " "as 'MkDocs'",
        )

    declared = str(config.get("site_url") or "").strip()
    if not declared:
        yield Finding(
            "config.site-url",
            "no `site_url:` is set; without it the canonical links and "
            "`sitemap.xml` are relative, and the deployed site advertises itself "
            f"as {site_url}",
        )
    elif not declared.startswith(("http://", "https://")):
        yield Finding(
            "config.site-url-relative",
            f"`site_url` is {declared!r}; it must be the absolute published URL " f"({site_url})",
        )
    elif declared.rstrip("/") != site_url.rstrip("/"):
        yield Finding(
            "config.site-url-mismatch",
            f"`site_url` is {declared!r} but the site is published at {site_url!r}; "
            "every canonical link would point at a URL that does not serve it",
        )

    theme = _as_mapping(config.get("theme")).get("name")
    if theme != REQUIRED_THEME:
        yield Finding(
            "config.theme",
            f"`theme.name` is {theme!r}; the palette, navigation and search "
            f"features in this file are {REQUIRED_THEME!r} options and are "
            "silently ignored by any other theme",
            "theme",
        )

    if config.get("strict") is not True:
        yield Finding(
            "config.strict",
            "`strict:` is not `true`, so a broken link, an unknown page or an "
            "unresolvable `:::` target is a warning in a log nobody reads instead "
            "of a failed build",
            "strict",
        )

    if docs_dir is None or not Path(docs_dir).is_dir():
        yield Finding(
            "config.docs-dir",
            f"the docs directory ({docs_dir}) does not exist, so there is nothing " "to build",
            "docs_dir",
        )

    site_dir = str(config.get("site_dir") or "site").rstrip("/")
    ignored = gitignore is not None and any(
        line.strip().rstrip("/") == site_dir
        for line in gitignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    if not ignored:
        yield Finding(
            "config.site-dir-ignored",
            f"the build output `{site_dir}/` is not in .gitignore; a committed "
            "site is a second copy of the docs that drifts from the first",
            "site_dir",
            severity="warning",
        )


def check_nav(
    config: Mapping[str, Any],
    *,
    docs_dir: Optional[Path],
) -> Iterator[Finding]:
    """Check the navigation against the pages that actually exist.

    Args:
        config: The parsed configuration.
        docs_dir: The directory the site is built from, already resolved.

    Yields:
        One finding per problem.
    """
    nav = config.get("nav")
    pages = nav_pages(nav)
    if not pages:
        yield Finding(
            "nav.missing",
            "no `nav:` is declared, so MkDocs derives the navigation from the file "
            "tree: every page is reachable, none is ordered, and this audit has "
            "nothing to check",
            "nav",
        )
        return

    on_disk = set(documentation_pages(docs_dir))
    seen: Dict[str, int] = {}
    for path in pages:
        seen[path] = seen.get(path, 0) + 1
        if path.startswith(("/", "\\")) or "\\" in path:
            yield Finding(
                "nav.path-invalid",
                f"nav entry {path!r} is not a relative POSIX path; MkDocs will "
                "look for it outside the docs directory",
                "nav",
            )
            continue
        if docs_dir is not None and path not in on_disk:
            yield Finding(
                "nav.entry-missing",
                f"nav lists {path!r} but no such page exists under "
                f"{Path(docs_dir).name}/; the build fails only because "
                "`strict: true` is on",
                "nav",
            )

    for path, count in seen.items():
        if count > 1:
            yield Finding(
                "nav.duplicate",
                f"{path!r} is listed {count} times; the second copy is a page " "nobody maintains",
                "nav",
                severity="warning",
            )

    if pages[0] not in ("index.md", "index.md/"):
        yield Finding(
            "nav.index-first",
            f"the first nav entry is {pages[0]!r}; the home page (`index.md`) must "
            "come first, or visitors land in the middle of the site",
            "nav",
            severity="warning",
        )

    for path in sorted(on_disk - set(pages)):
        yield Finding(
            "nav.orphan",
            f"{path!r} exists under the docs directory but is not in the nav; "
            "MkDocs still builds it, so the page is reachable by URL and "
            "unreachable by reader",
            "nav",
        )


def check_api_reference(
    config: Mapping[str, Any],
    *,
    modules: Sequence[str],
    documented: Sequence[str],
    pages: Sequence[str],
    docs_dir: Optional[Path],
) -> Iterator[Finding]:
    """Check that the API reference covers everything the distribution ships.

    Args:
        config: The parsed configuration.
        modules: The importable names from ``pyproject.toml``.
        documented: The ``:::`` targets found across the site.
        pages: The nav's page paths.
        docs_dir: The directory the site is built from, already resolved.

    Yields:
        One finding per problem.
    """
    plugins = _as_list(config.get("plugins"))
    handler: Mapping[str, Any] = {}
    found = False
    for plugin in plugins:
        if isinstance(plugin, str):
            if plugin == "mkdocstrings":
                found = True
        elif isinstance(plugin, Mapping) and "mkdocstrings" in plugin:
            found = True
            handler = _as_mapping(_as_mapping(plugin["mkdocstrings"]).get("handlers")).get("python")
            handler = _as_mapping(handler)
    if not found:
        yield Finding(
            "api.plugin-missing",
            "`mkdocstrings` is not enabled, so there is no API reference at all — "
            "the docstrings that every public callable already carries are only "
            "readable in the source",
            "plugins",
        )
        return

    options = _as_mapping(handler.get("options"))
    style = options.get("docstring_style")
    if style != REQUIRED_DOCSTRING_STYLE:
        yield Finding(
            "api.docstring-style",
            f"`docstring_style` is {style!r}; the source tree is written in "
            f"{REQUIRED_DOCSTRING_STYLE!r} style, so `Args:`/`Returns:`/`Raises:` "
            "sections render as plain paragraphs",
            "plugins",
            severity="warning",
        )

    paths = handler.get("paths")
    if paths is not None and "." not in [str(item) for item in _as_list(paths)]:
        yield Finding(
            "api.paths",
            f"the python handler's `paths` is {paths!r}; it must include '.' so the "
            "handler can import the repository's root-level modules",
            "plugins",
        )

    for module in modules:
        if module not in documented:
            yield Finding(
                "api.page-missing",
                f"`{module}` ships in the distribution but no page documents it; "
                "add a page whose `::: "
                f"{module}` target renders it",
                "nav",
            )

    known = set(modules)
    for target in documented:
        root = target.split(".")[0]
        if root not in known:
            yield Finding(
                "api.unknown-target",
                f"`::: {target}` documents something `pyproject.toml` does not "
                "ship; either the module was removed or the directive is a typo",
                "nav",
            )

    if docs_dir is not None:
        counted: Dict[str, int] = {}
        for page in pages:
            text = read_text(Path(docs_dir) / page) or ""
            for target in directive_targets(text):
                counted[target] = counted.get(target, 0) + 1
    else:
        counted = {target: 1 for target in documented}

    for target in sorted(name for name, count in counted.items() if count > 1):
        yield Finding(
            "api.duplicate-target",
            f"`::: {target}` appears more than once; the second rendering repeats " "the first",
            "nav",
            severity="warning",
        )

    if docs_dir is not None:
        for page in pages:
            text = read_text(Path(docs_dir) / page) or ""
            if page.startswith("api/") and Path(page).stem != "index" and ":::" not in text:
                yield Finding(
                    "api.directive-missing",
                    f"API page {page!r} contains no `:::` directive, so it renders "
                    "as prose with no reference under it",
                    "nav",
                )


def check_deployment(workflow: Optional[Mapping[str, Any]]) -> Iterator[Finding]:
    """Check that the site is built strictly and actually published.

    Args:
        workflow: The parsed ``docs.yml``, or ``None`` when the file is missing.

    Yields:
        One finding per problem.
    """
    if workflow is None:
        yield Finding(
            "deploy.workflow-missing",
            "no deployment workflow was found; the site builds locally and is "
            "never published, so the deliverable's 'deployed to GitHub Pages' is "
            "a claim with nothing behind it",
            ".github/workflows/docs.yml",
        )
        return

    triggers = _workflow_triggers(workflow)
    push = _as_mapping(triggers.get("push"))
    branches = [str(branch) for branch in _as_list(push.get("branches"))]
    if "main" not in branches:
        yield Finding(
            "deploy.trigger",
            f"the workflow does not run on a push to `main` (branches: {branches!r}); "
            "the published site would never follow the merged code",
            "on.push",
        )

    permissions = _as_mapping(workflow.get("permissions"))
    if permissions.get("pages") != "write":
        yield Finding(
            "deploy.permissions-pages",
            "`permissions.pages` is not `write`, so `actions/deploy-pages` cannot " "publish",
            "permissions",
        )
    if permissions.get("id-token") != "write":
        yield Finding(
            "deploy.permissions-id-token",
            "`permissions.id-token` is not `write`; Pages deployment authenticates "
            "with an OIDC token",
            "permissions",
        )

    jobs = _workflow_jobs(workflow)
    build_job: Optional[Tuple[str, Mapping[str, Any]]] = None
    for job_id, raw in jobs.items():
        job = _as_mapping(raw)
        if any(uses.startswith("actions/upload-pages-artifact") for uses in _job_uses(job)):
            build_job = (str(job_id), job)
            break
    if build_job is None:
        yield Finding(
            "deploy.artifact",
            "no job uploads the site with `actions/upload-pages-artifact`, so "
            "`deploy-pages` has nothing to publish",
            "jobs",
        )

    if build_job is not None:
        commands = _job_commands(build_job[1])
        build_lines = [line for line in commands.splitlines() if "mkdocs build" in line]
        if not build_lines:
            yield Finding(
                "deploy.build",
                "the job that uploads the site never runs `mkdocs build`",
                f"jobs.{build_job[0]}",
            )
        elif not any("--strict" in line for line in build_lines):
            yield Finding(
                "deploy.strict-build",
                "the build step runs `mkdocs build` without `--strict`; CI then "
                "publishes the site that a local strict build would have refused",
                f"jobs.{build_job[0]}",
            )
        if "tools/docs_site.py" not in commands:
            yield Finding(
                "deploy.audit",
                "the build job does not run `tools/docs_site.py`, so a nav or "
                "coverage regression is caught only by the test suite",
                f"jobs.{build_job[0]}",
                severity="warning",
            )

    deploy_job = _job_with_action(workflow, "actions/deploy-pages")
    if deploy_job is None:
        yield Finding(
            "deploy.action",
            "no job runs `actions/deploy-pages`; the artefact is uploaded and " "never deployed",
            "jobs",
        )
        return

    environment = _as_mapping(deploy_job.get("environment")).get("name")
    if environment != "github-pages":
        yield Finding(
            "deploy.environment",
            f"the deploy job's environment is {environment!r}; Pages requires "
            "`github-pages`, which is what records the deployment URL and the "
            "branch rule that protects it",
            "jobs",
        )

    needs = _as_names(deploy_job.get("needs"))
    if build_job is not None and build_job[0] not in needs:
        yield Finding(
            "deploy.needs",
            f"the deploy job does not `needs:` the build job ({build_job[0]!r}), so "
            "it can start before there is an artefact to publish",
            "jobs",
        )


def check_links(
    *,
    readme: Optional[str],
    site_url: str,
    pyproject: Optional[Mapping[str, Any]],
) -> Iterator[Finding]:
    """Check the README link and the packaging extra that installs the toolchain.

    Args:
        readme: The README's text, or ``None`` when it is missing.
        site_url: The published URL the README must link.
        pyproject: The parsed packaging declaration, or ``None``.

    Yields:
        One finding per problem.
    """
    if readme is None:
        yield Finding(
            "readme.missing",
            "README.md could not be read, so the live site is linked from nowhere",
            "README.md",
        )
    elif site_url.rstrip("/") not in readme:
        yield Finding(
            "readme.no-site-link",
            f"README.md does not link {site_url}; a published site nobody links "
            "to is a site nobody reads",
            "README.md",
        )

    if readme is not None and "docs.yml/badge.svg" not in readme:
        yield Finding(
            "readme.no-docs-badge",
            "README.md carries no badge for the docs workflow, so a red build is "
            "visible only on the Actions tab",
            "README.md",
            severity="warning",
        )

    if pyproject is None:
        yield Finding(
            "packaging.missing",
            "pyproject.toml could not be read, so the docs toolchain has no " "declared home",
            "pyproject.toml",
        )
        return

    extras = _as_mapping(_as_mapping(pyproject.get("project")).get("optional-dependencies"))
    docs_extra = [str(item).lower() for item in _as_list(extras.get("docs"))]
    if not docs_extra:
        yield Finding(
            "packaging.docs-extra-missing",
            "`pyproject.toml` declares no `docs` extra; the toolchain that builds "
            "the site is then installed ad hoc, at whatever versions happen to "
            "resolve",
            "pyproject.toml",
        )
        return
    for package in REQUIRED_DOCS_PACKAGES:
        if not any(entry.startswith(package) for entry in docs_extra):
            yield Finding(
                "packaging.docs-extra-incomplete",
                f"the `docs` extra does not require `{package}`, so "
                '`pip install -e ".[docs]"` cannot build the site',
                "pyproject.toml",
            )


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------
def audit_site(
    config: Mapping[str, Any],
    *,
    config_path: str = "mkdocs.yml",
    docs_dir: Optional[Path] = None,
    workflow: Optional[Mapping[str, Any]] = None,
    workflow_path: str = ".github/workflows/docs.yml",
    pyproject: Optional[Mapping[str, Any]] = None,
    readme: Optional[str] = None,
    gitignore: Optional[str] = None,
    site_url: str = DEFAULT_SITE_URL,
    strict: bool = False,
) -> SiteVerdict:
    """Audit a documentation site against the repository it describes.

    Args:
        config: The parsed ``mkdocs.yml``.
        config_path: The configuration's path, for the report.
        docs_dir: The directory the site is built from, already resolved.
        workflow: The parsed deployment workflow, or ``None``.
        workflow_path: The workflow's path, for the findings' ``where``.
        pyproject: The parsed packaging declaration, or ``None``.
        readme: The README's text, or ``None``.
        gitignore: The repository's ``.gitignore`` text, or ``None``.
        site_url: The URL the site is published at.
        strict: Whether warnings count as failures.

    Returns:
        The verdict: the nav, the documented targets, and every finding.
    """
    findings: List[Finding] = []
    findings.extend(
        check_configuration(config, site_url=site_url, docs_dir=docs_dir, gitignore=gitignore)
    )
    findings.extend(check_nav(config, docs_dir=docs_dir))

    pages = nav_pages(config.get("nav"))
    documented = api_targets(docs_dir, pages)
    modules = shipped_modules(pyproject)
    findings.extend(
        check_api_reference(
            config,
            modules=modules,
            documented=documented,
            pages=pages,
            docs_dir=docs_dir,
        )
    )

    deployment = tuple(check_deployment(workflow))
    for finding in deployment:
        findings.append(
            finding
            if finding.where != ".github/workflows/docs.yml"
            else Finding(
                finding.code,
                finding.message,
                workflow_path,
                finding.severity,
            )
        )

    findings.extend(check_links(readme=readme, site_url=site_url, pyproject=pyproject))

    return SiteVerdict(
        config_path=config_path,
        site_url=site_url,
        pages=pages,
        modules=documented,
        findings=tuple(findings),
        strict=strict,
    )


def render_report(verdict: SiteVerdict) -> str:
    """Render a verdict as the text a build log should show.

    Args:
        verdict: The verdict to render.

    Returns:
        A multi-line report: the site's shape, then each finding, then the
        verdict.
    """
    lines: List[str] = [
        f"Docs site audit: {verdict.config_path}",
        f"  site_url: {verdict.site_url}",
        f"  pages:    {len(verdict.pages)}",
        f"  modules:  {len(verdict.modules)}",
    ]
    if verdict.modules:
        lines.append("    documented: " + ", ".join(verdict.modules))
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
        lines.append(
            f"PASS  {len(verdict.pages)} pages, {len(verdict.modules)} documented "
            f"modules, {warnings} warning(s)"
        )
    elif not verdict.measurable or not verdict.pages:
        lines.append("NO VERDICT  the site declares no pages to measure")
    else:
        lines.append(f"FAIL  {errors} error(s), {warnings} warning(s)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser accepting the audit's options.
    """
    parser = argparse.ArgumentParser(
        prog="docs-site",
        description=(
            "Audit the documentation site: nav completeness, API coverage of every "
            "shipped module, strict mode, the Pages deployment workflow and the "
            "README link."
        ),
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="site configuration to audit (default: mkdocs.yml)",
    )
    parser.add_argument(
        "--pyproject",
        default=str(DEFAULT_PYPROJECT),
        help="packaging declaration to cross-check (default: pyproject.toml)",
    )
    parser.add_argument(
        "--workflow",
        default=str(DEFAULT_WORKFLOW),
        help="deployment workflow to audit (default: .github/workflows/docs.yml)",
    )
    parser.add_argument(
        "--readme",
        default=str(DEFAULT_README),
        help="README that must link the site (default: README.md)",
    )
    parser.add_argument(
        "--site-url",
        default=DEFAULT_SITE_URL,
        help="the URL the site is published at",
    )
    parser.add_argument(
        "--list-pages",
        action="store_true",
        help="print the nav's pages, one per line",
    )
    parser.add_argument(
        "--list-modules",
        action="store_true",
        help="print the documented API targets, one per line",
    )
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit and print its verdict.

    Args:
        argv: Arguments to parse; ``None`` means ``sys.argv[1:]``.

    Returns:
        :data:`EXIT_OK` when the site passes, :data:`EXIT_FINDINGS` when it does
        not, :data:`EXIT_UNMEASURABLE` when nothing could be measured.
    """
    args = build_parser().parse_args(argv)

    config_path = Path(args.config)
    try:
        config = load_config(config_path)
    except SiteConfigError as exc:
        print(f"docs-site: {exc}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    docs_dir = config_path.parent / str(config.get("docs_dir") or "docs")

    try:
        pyproject = load_pyproject(Path(args.pyproject))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"docs-site: cannot read {args.pyproject}: {exc}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    verdict = audit_site(
        config,
        config_path=str(config_path),
        docs_dir=docs_dir,
        workflow=load_yaml_file(Path(args.workflow)),
        workflow_path=str(args.workflow),
        pyproject=pyproject,
        readme=read_text(Path(args.readme)),
        gitignore=read_text(DEFAULT_GITIGNORE),
        site_url=args.site_url,
        strict=args.strict,
    )

    if args.list_pages:
        for page in verdict.pages:
            print(page)
        return EXIT_OK if verdict.pages else EXIT_UNMEASURABLE

    if args.list_modules:
        for module in verdict.modules:
            print(module)
        return EXIT_OK if verdict.modules else EXIT_UNMEASURABLE

    print(json.dumps(verdict.to_dict(), indent=2) if args.json else render_report(verdict))

    if not verdict.measurable or not verdict.pages:
        return EXIT_UNMEASURABLE
    return EXIT_OK if verdict.ok else EXIT_FINDINGS


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
