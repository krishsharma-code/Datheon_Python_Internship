"""Tests for the Day 25 documentation-site audit (``tools/docs_site.py``).

A documentation site is a *declaration*: ``mkdocs.yml`` says which pages exist,
in which order, and which modules the API reference covers. MkDocs will happily
build a site that quietly disagrees with the repository — a page left out of the
nav is still generated but unreachable, a module nobody documented is simply
absent, ``strict: false`` turns every warning into a line in a log nobody reads,
and a deployment workflow can upload an artefact that no job ever publishes.

None of those break a test. So each test below breaks one thing in a synthetic
site and asserts the *named* finding that results, rather than "the audit failed
somehow". A finding code that can be renamed without a test failing is a code
nobody depends on.

The last section runs the audit against this repository's real ``mkdocs.yml``,
``pyproject.toml``, ``README.md`` and ``.github/workflows/docs.yml``, because the
point of the day is that the site and the source tree cannot disagree.
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

from tools import docs_site  # noqa: E402  (path set up above)

CONFIG_PATH = ROOT / "mkdocs.yml"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "docs.yml"

#: Sentinel meaning "use the good default for this argument".
DEFAULT = object()


# ---------------------------------------------------------------------------
# A site that passes, for the tests to then break
# ---------------------------------------------------------------------------
def default_pages() -> Dict[str, str]:
    """Build the page set of a site that passes the audit.

    Returns:
        Page paths mapped to their source. The API section documents every
        module the default ``pyproject.toml`` ships, so the API checks are
        satisfied before a test removes one.
    """
    return {
        "index.md": "# Home\n\nWelcome to the site.\n",
        "api/index.md": "# API reference\n\nOverview of the generated reference.\n",
        "api/errors.md": "# `errors`\n\n::: errors\n",
        "api/validators.md": "# `validators`\n\n::: validators\n",
        "api/datheon.md": "# `datheon`\n\n::: datheon\n",
    }


def good_config() -> Dict[str, Any]:
    """Build an ``mkdocs.yml``-shaped mapping that passes the audit.

    Returns:
        A fresh mapping; callers mutate their copy rather than the module state.
    """
    return {
        "site_name": "Datheon Python Internship",
        "site_url": docs_site.DEFAULT_SITE_URL,
        "docs_dir": "docs",
        "site_dir": "site",
        "strict": True,
        "theme": {"name": "material"},
        "plugins": [
            "search",
            {
                "mkdocstrings": {
                    "handlers": {
                        "python": {
                            "paths": ["."],
                            "options": {"docstring_style": "google"},
                        }
                    }
                }
            },
        ],
        "nav": [
            "index.md",
            {"API reference": ["api/index.md", "api/errors.md", "api/validators.md"]},
            {"datheon": "api/datheon.md"},
        ],
    }


def good_workflow() -> Dict[str, Any]:
    """Build a ``docs.yml``-shaped mapping that passes the audit.

    ``needs`` is deliberately the *scalar* form (``needs: build``), because
    GitHub Actions accepts both spellings and an audit that only understood the
    list form would report a correctly wired job as unwired.

    Returns:
        A fresh workflow mapping.
    """
    return {
        "name": "Docs",
        "on": {"push": {"branches": ["main"]}, "pull_request": {"branches": ["main"]}},
        "permissions": {"contents": "read", "pages": "write", "id-token": "write"},
        "jobs": {
            "build": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"uses": "actions/checkout@v4"},
                    {
                        "run": (
                            'pip install -e ".[docs]"\n'
                            "python tools/docs_site.py --strict\n"
                            "mkdocs build --strict\n"
                        )
                    },
                    {"uses": "actions/upload-pages-artifact@v3", "with": {"path": "site/"}},
                ],
            },
            "deploy": {
                "needs": "build",
                "environment": {"name": "github-pages"},
                "runs-on": "ubuntu-latest",
                "steps": [{"id": "deployment", "uses": "actions/deploy-pages@v4"}],
            },
        },
    }


def good_pyproject() -> Dict[str, Any]:
    """Build a ``pyproject.toml``-shaped mapping that passes the audit.

    Returns:
        A declaration shipping two root-level modules, one package and a ``docs``
        extra that names both required packages.
    """
    return {
        "project": {
            "optional-dependencies": {
                "docs": ["mkdocs-material>=9.5", "mkdocstrings[python]>=0.26"]
            }
        },
        "tool": {
            "setuptools": {
                "py-modules": ["errors", "validators"],
                "packages": {"find": {"include": ["datheon*"]}},
            }
        },
    }


def good_readme() -> str:
    """Build a README that links the site and carries the docs badge.

    Returns:
        Markdown text.
    """
    return (
        "# Datheon\n\n"
        "[![Docs](https://example.test/docs.yml/badge.svg)]"
        f"({docs_site.DEFAULT_SITE_URL})\n\n"
        f"The site is live at {docs_site.DEFAULT_SITE_URL}.\n"
    )


def config_without(*keys: str) -> Dict[str, Any]:
    """Return the good configuration with top-level keys removed.

    Args:
        *keys: The keys to delete.

    Returns:
        A fresh configuration mapping without those keys.
    """
    config = good_config()
    for key in keys:
        config.pop(key, None)
    return config


def audit(
    tmp_path: Path,
    *,
    config: Optional[Dict[str, Any]] = None,
    workflow: Any = DEFAULT,
    pyproject: Any = DEFAULT,
    readme: Any = DEFAULT,
    gitignore: Optional[str] = "site/\n.cache/\n",
    pages: Optional[Dict[str, str]] = None,
    site_url: str = docs_site.DEFAULT_SITE_URL,
) -> docs_site.SiteVerdict:
    """Write a synthetic site to ``tmp_path`` and audit it.

    Args:
        tmp_path: The test's temporary directory.
        config: The configuration to audit; ``None`` means :func:`good_config`.
        workflow: The deployment workflow, or ``None`` for a site with none.
        pyproject: The packaging declaration, or ``None`` for a site without one.
        readme: The README text, or ``None`` when there is no README.
        gitignore: The repository's ignore file, or ``None`` when there is none.
        pages: The pages to write, relative to the docs directory.
        site_url: The URL the site is published at.

    Returns:
        The audit's verdict.
    """
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (default_pages() if pages is None else pages).items():
        target = docs_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    return docs_site.audit_site(
        good_config() if config is None else config,
        config_path=str(tmp_path / "mkdocs.yml"),
        docs_dir=docs_dir,
        workflow=good_workflow() if workflow is DEFAULT else workflow,
        pyproject=good_pyproject() if pyproject is DEFAULT else pyproject,
        readme=good_readme() if readme is DEFAULT else readme,
        gitignore=gitignore,
        site_url=site_url,
    )


def codes(verdict: docs_site.SiteVerdict) -> List[str]:
    """Return the finding codes of a verdict, in order."""
    return [finding.code for finding in verdict.findings]


# ---------------------------------------------------------------------------
# The baseline
# ---------------------------------------------------------------------------
def test_a_good_site_passes(tmp_path: Path):
    verdict = audit(tmp_path)

    assert verdict.findings == ()
    assert verdict.ok is True
    assert len(verdict.pages) == 5


def test_the_verdict_lists_the_documented_modules(tmp_path: Path):
    verdict = audit(tmp_path)

    assert verdict.modules == ("errors", "validators", "datheon")


def test_a_site_with_no_pages_is_never_ok(tmp_path: Path):
    verdict = audit(tmp_path, config=config_without("nav"), pages={"index.md": "# Home\n"})

    assert verdict.ok is False
    assert verdict.pages == ()


# ---------------------------------------------------------------------------
# The configuration
# ---------------------------------------------------------------------------
def test_a_missing_site_name_is_a_finding(tmp_path: Path):
    verdict = audit(tmp_path, config=config_without("site_name"))

    assert "config.site-name" in codes(verdict)
    assert verdict.ok is False


def test_a_blank_site_name_is_a_finding(tmp_path: Path):
    config = good_config()
    config["site_name"] = "   "

    assert "config.site-name" in codes(audit(tmp_path, config=config))


def test_a_missing_site_url_is_a_finding(tmp_path: Path):
    verdict = audit(tmp_path, config=config_without("site_url"))

    assert "config.site-url" in codes(verdict)


def test_a_relative_site_url_is_a_finding(tmp_path: Path):
    config = good_config()
    config["site_url"] = "/Datheon_Python_Internship/"

    assert "config.site-url-relative" in codes(audit(tmp_path, config=config))


def test_a_site_url_that_points_somewhere_else_is_a_finding(tmp_path: Path):
    config = good_config()
    config["site_url"] = "https://someone-else.github.io/other-project/"

    verdict = audit(tmp_path, config=config)

    assert "config.site-url-mismatch" in codes(verdict)


def test_a_trailing_slash_difference_is_not_a_mismatch(tmp_path: Path):
    config = good_config()
    config["site_url"] = docs_site.DEFAULT_SITE_URL.rstrip("/")

    assert "config.site-url-mismatch" not in codes(audit(tmp_path, config=config))


def test_a_theme_other_than_material_is_a_finding(tmp_path: Path):
    config = good_config()
    config["theme"] = {"name": "readthedocs"}

    verdict = audit(tmp_path, config=config)

    assert "config.theme" in codes(verdict)


def test_a_missing_theme_block_is_a_finding(tmp_path: Path):
    assert "config.theme" in codes(audit(tmp_path, config=config_without("theme")))


def test_strict_mode_being_off_is_a_finding(tmp_path: Path):
    config = good_config()
    config["strict"] = False

    verdict = audit(tmp_path, config=config)

    assert "config.strict" in codes(verdict)
    assert verdict.ok is False


def test_a_missing_docs_directory_is_a_finding(tmp_path: Path):
    verdict = docs_site.audit_site(
        good_config(),
        config_path="mkdocs.yml",
        docs_dir=tmp_path / "nowhere",
        workflow=good_workflow(),
        pyproject=good_pyproject(),
        readme=good_readme(),
        gitignore="site/\n",
    )

    assert "config.docs-dir" in codes(verdict)


def test_an_unignored_build_output_is_a_warning(tmp_path: Path):
    verdict = audit(tmp_path, gitignore="*.pyc\n")

    assert "config.site-dir-ignored" in codes(verdict)
    assert verdict.ok is True  # a warning, not an error


def test_an_unignored_build_output_fails_a_strict_audit(tmp_path: Path):
    verdict = audit(tmp_path, gitignore=None)

    strict = docs_site.SiteVerdict(
        config_path=verdict.config_path,
        site_url=verdict.site_url,
        pages=verdict.pages,
        modules=verdict.modules,
        findings=verdict.findings,
        strict=True,
    )

    assert strict.ok is False


def test_the_site_directory_is_ignored_when_the_comment_is_stripped(tmp_path: Path):
    verdict = audit(tmp_path, gitignore="# site/\nsite/\n")

    assert "config.site-dir-ignored" not in codes(verdict)


# ---------------------------------------------------------------------------
# The navigation
# ---------------------------------------------------------------------------
def test_a_missing_nav_is_a_finding(tmp_path: Path):
    verdict = audit(tmp_path, config=config_without("nav"))

    assert "nav.missing" in codes(verdict)
    assert verdict.ok is False


def test_a_nav_entry_with_no_page_on_disk_is_a_finding(tmp_path: Path):
    config = good_config()
    config["nav"] = ["index.md", "api/errors.md", "api/validators.md", "api/datheon.md", "ghost.md"]

    verdict = audit(tmp_path, config=config)

    assert "nav.entry-missing" in codes(verdict)


def test_a_page_outside_the_nav_is_a_finding(tmp_path: Path):
    pages = default_pages()
    pages["orphan.md"] = "# Orphan\n"

    verdict = audit(tmp_path, pages=pages)

    assert "nav.orphan" in codes(verdict)
    assert verdict.ok is False


def test_a_page_listed_twice_is_a_warning(tmp_path: Path):
    config = good_config()
    config["nav"] = list(config["nav"]) + ["index.md"]

    verdict = audit(tmp_path, config=config)

    assert "nav.duplicate" in codes(verdict)
    assert verdict.ok is True


def test_a_home_page_that_is_not_first_is_a_warning(tmp_path: Path):
    config = good_config()
    config["nav"] = [
        "api/errors.md",
        "index.md",
        "api/index.md",
        "api/validators.md",
        "api/datheon.md",
    ]

    verdict = audit(tmp_path, config=config)

    assert "nav.index-first" in codes(verdict)
    assert verdict.ok is True


def test_an_absolute_nav_path_is_a_finding(tmp_path: Path):
    config = good_config()
    config["nav"] = ["index.md", "/etc/passwd.md"]

    verdict = audit(tmp_path, config=config)

    assert "nav.path-invalid" in codes(verdict)


def test_nav_entries_flatten_in_reading_order():
    nav = [
        "index.md",
        {"Guides": [{"Logging": "logging.md"}, "persistence.md"]},
        {"About": "about.md"},
    ]

    entries = docs_site.nav_entries(nav)

    assert entries == (
        ("index", "index.md"),
        ("Logging", "logging.md"),
        ("persistence", "persistence.md"),
        ("About", "about.md"),
    )


def test_nav_entries_ignore_values_that_are_not_pages():
    assert docs_site.nav_entries([1, None, {"Broken": 2}]) == ()


def test_nav_pages_keeps_the_declared_order():
    nav = [{"B": "b.md"}, "a.md"]

    assert docs_site.nav_pages(nav) == ("b.md", "a.md")


# ---------------------------------------------------------------------------
# The API reference
# ---------------------------------------------------------------------------
def test_a_missing_mkdocstrings_plugin_is_a_finding(tmp_path: Path):
    config = good_config()
    config["plugins"] = ["search"]

    verdict = audit(tmp_path, config=config)

    assert "api.plugin-missing" in codes(verdict)


def test_a_bare_mkdocstrings_plugin_name_still_counts(tmp_path: Path):
    config = good_config()
    config["plugins"] = ["search", "mkdocstrings"]

    verdict = audit(tmp_path, config=config)

    assert "api.plugin-missing" not in codes(verdict)


def test_a_docstring_style_other_than_google_is_a_warning(tmp_path: Path):
    config = good_config()
    config["plugins"][1]["mkdocstrings"]["handlers"]["python"]["options"] = {
        "docstring_style": "sphinx"
    }

    verdict = audit(tmp_path, config=config)

    assert "api.docstring-style" in codes(verdict)
    assert verdict.ok is True


def test_an_import_path_without_the_repository_root_is_a_finding(tmp_path: Path):
    config = good_config()
    config["plugins"][1]["mkdocstrings"]["handlers"]["python"]["paths"] = ["src"]

    verdict = audit(tmp_path, config=config)

    assert "api.paths" in codes(verdict)


def test_a_shipped_module_with_no_page_is_a_finding(tmp_path: Path):
    pyproject = good_pyproject()
    pyproject["tool"]["setuptools"]["py-modules"] = ["errors", "validators", "persistence"]

    verdict = audit(tmp_path, pyproject=pyproject)

    assert "api.page-missing" in codes(verdict)


def test_a_directive_for_something_not_shipped_is_a_finding(tmp_path: Path):
    pages = default_pages()
    pages["api/ghost.md"] = "# `ghost`\n\n::: ghost_module\n"
    config = good_config()
    config["nav"] = list(config["nav"]) + ["api/ghost.md"]

    verdict = audit(tmp_path, config=config, pages=pages)

    assert "api.unknown-target" in codes(verdict)


def test_documenting_the_same_module_twice_is_a_warning(tmp_path: Path):
    pages = default_pages()
    pages["api/errors-again.md"] = "# `errors` again\n\n::: errors\n"
    config = good_config()
    config["nav"] = list(config["nav"]) + ["api/errors-again.md"]

    verdict = audit(tmp_path, config=config, pages=pages)

    assert "api.duplicate-target" in codes(verdict)
    assert verdict.ok is True


def test_an_api_page_without_a_directive_is_a_finding(tmp_path: Path):
    pages = default_pages()
    pages["api/prose.md"] = "# Prose\n\nNo directive here.\n"
    config = good_config()
    config["nav"] = list(config["nav"]) + ["api/prose.md"]

    verdict = audit(tmp_path, config=config, pages=pages)

    assert "api.directive-missing" in codes(verdict)


def test_an_api_overview_page_needs_no_directive(tmp_path: Path):
    # `api/index.md` is an overview: it explains the reference instead of
    # rendering a module, so it must not be reported.
    assert "api.directive-missing" not in codes(audit(tmp_path))


def test_directive_targets_read_the_target_and_skip_the_options():
    text = "Intro\n\n::: errors\n    options:\n      show_source: false\n\n::: datheon.cli\n"

    assert docs_site.directive_targets(text) == ("errors", "datheon.cli")


def test_directive_targets_ignore_a_fenced_code_block():
    # The guide that explains `::: module` shows the syntax inside a fence. That
    # is text, not a directive, and must not count as a second rendering.
    text = "Example:\n\n```markdown\n::: errors\n```\n\n::: datheon\n"

    assert docs_site.directive_targets(text) == ("datheon",)


def test_directive_targets_ignore_a_tilde_fence():
    text = "~~~\n::: errors\n~~~\n"

    assert docs_site.directive_targets(text) == ()


def test_directive_targets_ignore_prose_that_mentions_the_syntax():
    assert docs_site.directive_targets("Use `::: module` in a page.\n") == ()


def test_shipped_modules_reads_modules_and_packages():
    assert docs_site.shipped_modules(good_pyproject()) == ("datheon", "errors", "validators")


def test_shipped_modules_is_empty_without_a_declaration():
    assert docs_site.shipped_modules(None) == ()


def test_shipped_modules_ignores_a_missing_setuptools_table():
    assert docs_site.shipped_modules({"project": {}}) == ()


# ---------------------------------------------------------------------------
# The deployment
# ---------------------------------------------------------------------------
def test_a_missing_deployment_workflow_is_a_finding(tmp_path: Path):
    verdict = audit(tmp_path, workflow=None)

    assert "deploy.workflow-missing" in codes(verdict)


def test_a_workflow_that_does_not_run_on_main_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    workflow["on"] = {"push": {"branches": ["develop"]}}

    verdict = audit(tmp_path, workflow=workflow)

    assert "deploy.trigger" in codes(verdict)


def test_a_workflow_read_with_the_boolean_on_key_still_counts(tmp_path: Path):
    workflow = good_workflow()
    workflow[True] = workflow.pop("on")

    assert "deploy.trigger" not in codes(audit(tmp_path, workflow=workflow))


def test_missing_pages_permission_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    workflow["permissions"] = {"contents": "read", "id-token": "write"}

    assert "deploy.permissions-pages" in codes(audit(tmp_path, workflow=workflow))


def test_missing_id_token_permission_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    workflow["permissions"] = {"contents": "read", "pages": "write"}

    assert "deploy.permissions-id-token" in codes(audit(tmp_path, workflow=workflow))


def test_a_workflow_without_the_artefact_upload_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    steps = workflow["jobs"]["build"]["steps"]
    workflow["jobs"]["build"]["steps"] = [step for step in steps if "uses" not in step]

    assert "deploy.artifact" in codes(audit(tmp_path, workflow=workflow))


def test_a_build_that_never_runs_mkdocs_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    workflow["jobs"]["build"]["steps"][1]["run"] = "python tools/docs_site.py --strict\n"

    assert "deploy.build" in codes(audit(tmp_path, workflow=workflow))


def test_a_build_without_strict_mode_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    workflow["jobs"]["build"]["steps"][1][
        "run"
    ] = "python tools/docs_site.py --strict\nmkdocs build\n"

    verdict = audit(tmp_path, workflow=workflow)

    assert "deploy.strict-build" in codes(verdict)


def test_a_build_that_skips_the_audit_is_a_warning(tmp_path: Path):
    workflow = good_workflow()
    workflow["jobs"]["build"]["steps"][1]["run"] = "mkdocs build --strict\n"

    verdict = audit(tmp_path, workflow=workflow)

    assert "deploy.audit" in codes(verdict)
    assert verdict.ok is True


def test_a_workflow_that_never_deploys_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    workflow["jobs"]["deploy"]["steps"] = [{"run": "echo nothing to deploy"}]

    assert "deploy.action" in codes(audit(tmp_path, workflow=workflow))


def test_a_deploy_job_without_the_pages_environment_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    workflow["jobs"]["deploy"]["environment"] = {"name": "production"}

    assert "deploy.environment" in codes(audit(tmp_path, workflow=workflow))


def test_a_deploy_job_that_does_not_need_the_build_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    del workflow["jobs"]["deploy"]["needs"]

    assert "deploy.needs" in codes(audit(tmp_path, workflow=workflow))


def test_a_deploy_job_needing_the_build_as_a_list_is_accepted(tmp_path: Path):
    workflow = good_workflow()
    workflow["jobs"]["deploy"]["needs"] = ["build"]

    assert "deploy.needs" not in codes(audit(tmp_path, workflow=workflow))


def test_a_deploy_job_needing_only_the_lint_job_is_a_finding(tmp_path: Path):
    workflow = good_workflow()
    workflow["jobs"]["lint"] = {"runs-on": "ubuntu-latest", "steps": []}
    workflow["jobs"]["deploy"]["needs"] = ["lint"]

    assert "deploy.needs" in codes(audit(tmp_path, workflow=workflow))


def test_a_workflow_with_no_jobs_is_reported_rather_than_crashing(tmp_path: Path):
    verdict = audit(tmp_path, workflow={"name": "Docs"})

    assert "deploy.artifact" in codes(verdict)
    assert "deploy.action" in codes(verdict)


# ---------------------------------------------------------------------------
# The README link and the docs extra
# ---------------------------------------------------------------------------
def test_a_missing_readme_is_a_finding(tmp_path: Path):
    verdict = audit(tmp_path, readme=None)

    assert "readme.missing" in codes(verdict)


def test_a_readme_that_does_not_link_the_site_is_a_finding(tmp_path: Path):
    verdict = audit(tmp_path, readme="# Datheon\n\nNo link here.\n")

    assert "readme.no-site-link" in codes(verdict)
    assert verdict.ok is False


def test_a_readme_without_the_docs_badge_is_a_warning(tmp_path: Path):
    verdict = audit(tmp_path, readme=f"Live at {docs_site.DEFAULT_SITE_URL}\n")

    assert "readme.no-docs-badge" in codes(verdict)
    assert verdict.ok is True


def test_a_site_with_no_packaging_declaration_is_a_finding(tmp_path: Path):
    verdict = audit(tmp_path, pyproject=None)

    assert "packaging.missing" in codes(verdict)


def test_a_missing_docs_extra_is_a_finding(tmp_path: Path):
    pyproject = {"project": {"optional-dependencies": {"dev": ["pytest>=7.4"]}}}

    verdict = audit(tmp_path, pyproject=pyproject)

    assert "packaging.docs-extra-missing" in codes(verdict)


def test_a_docs_extra_without_the_theme_is_a_finding(tmp_path: Path):
    pyproject = good_pyproject()
    pyproject["project"]["optional-dependencies"]["docs"] = ["mkdocstrings[python]>=0.26"]

    verdict = audit(tmp_path, pyproject=pyproject)

    assert "packaging.docs-extra-incomplete" in codes(verdict)


def test_a_docs_extra_without_mkdocstrings_is_a_finding(tmp_path: Path):
    pyproject = good_pyproject()
    pyproject["project"]["optional-dependencies"]["docs"] = ["mkdocs-material>=9.5"]

    verdict = audit(tmp_path, pyproject=pyproject)

    assert "packaging.docs-extra-incomplete" in codes(verdict)


# ---------------------------------------------------------------------------
# Reading the files
# ---------------------------------------------------------------------------
def test_load_config_reads_a_mapping(tmp_path: Path):
    path = tmp_path / "mkdocs.yml"
    path.write_text("site_name: Datheon\n", encoding="utf-8")

    assert docs_site.load_config(path) == {"site_name": "Datheon"}


def test_load_config_rejects_a_missing_file(tmp_path: Path):
    with pytest.raises(docs_site.SiteConfigError, match="does not exist"):
        docs_site.load_config(tmp_path / "nope.yml")


def test_load_config_rejects_invalid_yaml(tmp_path: Path):
    path = tmp_path / "mkdocs.yml"
    path.write_text("nav: [unclosed\n", encoding="utf-8")

    with pytest.raises(docs_site.SiteConfigError):
        docs_site.load_config(path)


def test_load_config_rejects_a_document_that_is_not_a_mapping(tmp_path: Path):
    path = tmp_path / "mkdocs.yml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(docs_site.SiteConfigError, match="not a YAML mapping"):
        docs_site.load_config(path)


def test_load_yaml_file_returns_none_for_a_missing_file(tmp_path: Path):
    assert docs_site.load_yaml_file(tmp_path / "nope.yml") is None


def test_load_yaml_file_returns_none_for_a_malformed_file(tmp_path: Path):
    path = tmp_path / "broken.yml"
    path.write_text("jobs: [unclosed\n", encoding="utf-8")

    assert docs_site.load_yaml_file(path) is None


def test_load_yaml_file_returns_none_for_a_non_mapping(tmp_path: Path):
    path = tmp_path / "list.yml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    assert docs_site.load_yaml_file(path) is None


def test_load_pyproject_returns_none_for_a_missing_file(tmp_path: Path):
    assert docs_site.load_pyproject(tmp_path / "pyproject.toml") is None


def test_read_text_returns_none_for_a_missing_file(tmp_path: Path):
    assert docs_site.read_text(tmp_path / "nope.md") is None


def test_documentation_pages_is_empty_without_a_directory():
    assert docs_site.documentation_pages(None) == ()
    assert docs_site.documentation_pages(Path("/nonexistent-docs-dir")) == ()


def test_documentation_pages_lists_markdown_only(tmp_path: Path):
    docs_dir = tmp_path / "docs"
    (docs_dir / "api").mkdir(parents=True)
    (docs_dir / "index.md").write_text("# Home\n", encoding="utf-8")
    (docs_dir / "api" / "errors.md").write_text("# errors\n", encoding="utf-8")
    (docs_dir / "notes.txt").write_text("ignore me\n", encoding="utf-8")

    assert docs_site.documentation_pages(docs_dir) == ("api/errors.md", "index.md")


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------
def test_main_passes_on_the_real_site(capsys: pytest.CaptureFixture[str]):
    assert docs_site.main(["--strict"]) == docs_site.EXIT_OK

    out = capsys.readouterr().out
    assert "PASS" in out
    assert "NO VERDICT" not in out


def test_main_returns_unmeasurable_for_a_missing_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    code = docs_site.main(["--config", str(tmp_path / "nope.yml")])

    assert code == docs_site.EXIT_UNMEASURABLE
    assert "does not exist" in capsys.readouterr().err


def test_main_returns_unmeasurable_for_invalid_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    path = tmp_path / "mkdocs.yml"
    path.write_text("nav: [unclosed\n", encoding="utf-8")

    assert docs_site.main(["--config", str(path)]) == docs_site.EXIT_UNMEASURABLE
    assert "docs-site:" in capsys.readouterr().err


def test_main_returns_unmeasurable_when_no_page_can_be_measured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    path = tmp_path / "mkdocs.yml"
    path.write_text("site_name: Empty\nsite_url: https://example.test/\nnav: []\n", "utf-8")

    assert docs_site.main(["--config", str(path)]) == docs_site.EXIT_UNMEASURABLE
    assert "NO VERDICT" in capsys.readouterr().out


def test_main_reports_findings_on_a_broken_site(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = tmp_path / "mkdocs.yml"
    path.write_text("site_name: Broken\nnav:\n  - index.md\n", encoding="utf-8")

    code = docs_site.main(["--config", str(path), "--pyproject", str(tmp_path / "nope.toml")])

    assert code == docs_site.EXIT_FINDINGS
    assert "FAIL" in capsys.readouterr().out


def test_main_prints_a_json_verdict(capsys: pytest.CaptureFixture[str]):
    assert docs_site.main(["--json"]) == docs_site.EXIT_OK

    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["page_count"] == len(payload["pages"])
    assert payload["errors"] == 0


def test_main_can_list_the_pages(capsys: pytest.CaptureFixture[str]):
    assert docs_site.main(["--list-pages"]) == docs_site.EXIT_OK

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]

    assert lines[0] == "index.md"
    assert "api/index.md" in lines


def test_main_can_list_the_modules(capsys: pytest.CaptureFixture[str]):
    assert docs_site.main(["--list-modules"]) == docs_site.EXIT_OK

    modules = [line for line in capsys.readouterr().out.splitlines() if line.strip()]

    assert "errors" in modules
    assert "datheon" in modules


def test_main_strict_promotes_warnings(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    readme = tmp_path / "README.md"
    readme.write_text(f"Live at {docs_site.DEFAULT_SITE_URL}\n", encoding="utf-8")

    assert docs_site.main(["--readme", str(readme)]) == docs_site.EXIT_OK
    assert "WARN" in capsys.readouterr().out
    assert docs_site.main(["--readme", str(readme), "--strict"]) == docs_site.EXIT_FINDINGS
    assert "FAIL" in capsys.readouterr().out


def test_main_returns_unmeasurable_for_an_unreadable_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("this is not [ valid toml\n", encoding="utf-8")

    code = docs_site.main(["--pyproject", str(pyproject)])

    assert code == docs_site.EXIT_UNMEASURABLE
    assert "cannot read" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# This repository
# ---------------------------------------------------------------------------
def test_the_real_configuration_parses():
    config = docs_site.load_config(CONFIG_PATH)

    assert config["strict"] is True
    assert config["theme"]["name"] == "material"


def test_the_real_site_documents_every_shipped_module():
    config = docs_site.load_config(CONFIG_PATH)
    pyproject = docs_site.load_pyproject(ROOT / "pyproject.toml")
    docs_dir = ROOT / "docs"
    pages = docs_site.nav_pages(config["nav"])
    documented = docs_site.api_targets(docs_dir, pages)

    assert set(docs_site.shipped_modules(pyproject)) <= set(documented)


def test_every_real_nav_page_exists():
    config = docs_site.load_config(CONFIG_PATH)
    on_disk = set(docs_site.documentation_pages(ROOT / "docs"))

    missing = [page for page in docs_site.nav_pages(config["nav"]) if page not in on_disk]

    assert missing == []


def test_the_real_site_passes_strict():
    exit_code = docs_site.main(["--strict"])

    assert exit_code == docs_site.EXIT_OK


def test_the_real_workflow_deploys_to_pages():
    workflow = docs_site.load_yaml_file(WORKFLOW_PATH)

    assert workflow is not None
    deploy = docs_site._job_with_action(workflow, "actions/deploy-pages")
    assert deploy is not None
    assert deploy["environment"]["name"] == "github-pages"


def test_the_real_readme_links_the_live_site():
    readme = docs_site.read_text(ROOT / "README.md")

    assert readme is not None
    assert docs_site.DEFAULT_SITE_URL.rstrip("/") in readme


def test_the_real_site_directory_is_gitignored():
    gitignore = docs_site.read_text(ROOT / ".gitignore")

    assert gitignore is not None
    assert "site/" in gitignore.splitlines()


def test_the_audit_is_idempotent_over_the_real_site():
    first = docs_site.main(["--json", "--strict"])
    second = docs_site.main(["--json", "--strict"])

    assert first == second == docs_site.EXIT_OK
