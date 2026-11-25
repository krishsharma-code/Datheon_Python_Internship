"""Tests for the Day 33 container audit (``tools/docker_packaging.py``).

Two halves, and they prove different things.

* **Every finding code is reachable.** A gate that cannot fail is a comment, so
  each check is fed a tree that breaks exactly one rule — a floating base tag, a
  root user, a shell-form entry point, a ``COPY`` of an ignored path — and the
  test asserts that code and no other. The Dockerfile parser gets the same
  treatment: continuations, comments, ``--from`` flags and the legacy ``ENV``
  form are the shapes a real file uses, and everything downstream trusts them.
* **The real tree is clean.** Two integration tests read the committed
  ``Dockerfile``, ``.dockerignore``, ``docker/crontab.example`` and
  ``.github/workflows/docker.yml``, because "the container agrees with its
  documentation" is worth exactly as much as its weakest run.

No test needs a Docker daemon: the one function that would talk to one,
:func:`docker_packaging.build_image`, takes an injectable runner, and the tests
pass a stub that records its arguments.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import docker_packaging as dp  # noqa: E402  (path set up above)

# --- fixtures -------------------------------------------------------------
VALID_DOCKERFILE = """\
# a comment, ignored by the parser
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY pyproject.toml ./
RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="datheon-organizer" \\
      org.opencontainers.image.description="organizer" \\
      org.opencontainers.image.source="https://example.invalid/repo" \\
      org.opencontainers.image.version="1.1.0" \\
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --uid 10001 datheon
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels datheon

USER 10001:10001
WORKDIR /data
ENTRYPOINT ["organizer"]
CMD ["/data"]
"""

VALID_CRONTAB = (
    "# a comment\n"
    "15 2 * * * flock -n /var/lock/o.lock docker run --rm --network none "
    "-v /srv/inbox:/data:rw datheon-organizer:1.1.0 --dry-run "
    ">> /var/log/o.log 2>&1\n"
)

VALID_WORKFLOW = """\
name: Container image
on:
  pull_request:
jobs:
  image:
    steps:
      - run: docker build --tag datheon-organizer:ci .
      - run: docker run --rm datheon-organizer:ci --version
      - run: test "$(docker run --rm --entrypoint id datheon-organizer:ci -u)" = "10001"
"""

VALID_DOC = "# Container image\n\nDocker packaging for the organizer.\n"


def make_tree(
    root: Path,
    dockerfile: Optional[str] = VALID_DOCKERFILE,
    dockerignore: Optional[str] = None,
    crontab: Optional[str] = VALID_CRONTAB,
    workflow: Optional[str] = VALID_WORKFLOW,
    doc: Optional[str] = VALID_DOC,
    nav: Optional[str] = "nav:\n  - Container: docker.md\n",
    extra: Optional[Sequence[str]] = None,
) -> Path:
    """Write a build-context-shaped tree.

    Args:
        root: Where to build the tree.
        dockerfile: The Dockerfile's text, or ``None`` to leave it out.
        dockerignore: The ignore file's text; ``None`` writes the required
            patterns, so the tree is clean by default.
        crontab: The example schedule, or ``None`` to leave it out.
        workflow: The container workflow, or ``None`` to leave it out.
        doc: The documentation page, or ``None`` to leave it out.
        nav: The site configuration, or ``None`` to leave it out.
        extra: Additional files to create, as relative POSIX paths.

    Returns:
        ``root``, for chaining.
    """
    root.mkdir(parents=True, exist_ok=True)
    if dockerfile is not None:
        (root / dp.DOCKERFILE).write_text(dockerfile, encoding="utf-8")
    if dockerignore is None:
        dockerignore = "\n".join(dp.REQUIRED_IGNORES) + "\n"
    if dockerignore is not False:
        (root / dp.DOCKERIGNORE).write_text(str(dockerignore), encoding="utf-8")
    for relative, text in (
        (dp.CRONTAB, crontab),
        (dp.WORKFLOW, workflow),
        (dp.DOC, doc),
        (dp.NAV, nav),
    ):
        if text is None:
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    for relative in extra or ():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    return root


def codes(report: dp.Report) -> List[str]:
    """Return the finding codes of a report, in order.

    Args:
        report: The audit verdict.

    Returns:
        The codes, one per finding.
    """
    return [finding.code for finding in report.findings]


def audit_with(root: Path, **overrides: Any) -> dp.Report:
    """Build a tree with overrides and audit it.

    Args:
        root: Where to build the tree.
        **overrides: Keyword overrides for :func:`make_tree`.

    Returns:
        The audit verdict for that tree.
    """
    return dp.audit_tree(make_tree(root, **overrides))


# --- the parser -----------------------------------------------------------
def test_parse_joins_continuations() -> None:
    """A backslash joins the next line onto the instruction."""
    instructions = dp.parse_dockerfile("ENV A=1 \\\n    B=2\n")

    assert len(instructions) == 1
    assert instructions[0].keyword == "ENV"
    assert instructions[0].value == "A=1 B=2"


def test_parse_skips_comments_and_blanks() -> None:
    """Comments and blank lines produce no instructions."""
    instructions = dp.parse_dockerfile("# syntax=1\n\n# note\nFROM python:3.12-slim\n")

    assert [item.keyword for item in instructions] == ["FROM"]


def test_parse_skips_comments_inside_a_continuation() -> None:
    """A comment line inside a continuation is dropped, as Docker does."""
    instructions = dp.parse_dockerfile("RUN echo a \\\n# why\n  && echo b\n")

    assert instructions[0].value == "echo a && echo b"


def test_parse_records_the_starting_line() -> None:
    """The instruction reports the line it started on, not the line it ended."""
    instructions = dp.parse_dockerfile("# one\nFROM python:3.12-slim\n\nRUN a \\\n b\n")

    assert instructions[0].line == 2
    assert instructions[1].line == 4


def test_parse_splits_flags_from_arguments() -> None:
    """``--from=builder`` is a flag, not a source path."""
    instruction = dp.parse_dockerfile("COPY --from=builder /wheels /wheels\n")[0]

    assert instruction.flags == "--from=builder"
    assert instruction.value == "/wheels /wheels"


def test_parse_keeps_a_trailing_continuation() -> None:
    """A continuation that ends the file still yields its instruction."""
    instructions = dp.parse_dockerfile("RUN echo a \\\n  && echo b \\\n")

    assert len(instructions) == 1
    assert instructions[0].value == "echo a && echo b"


def test_instruction_render_includes_flags() -> None:
    """The rendered form keeps the flags, so a message is reproducible."""
    instruction = dp.parse_dockerfile("COPY --chown=1:1 a b\n")[0]

    assert instruction.render() == "COPY --chown=1:1 a b"


# --- stages ---------------------------------------------------------------
def test_split_stages_names_and_bases() -> None:
    """Each stage keeps its alias and base image."""
    _, stages = dp.split_stages(dp.parse_dockerfile(VALID_DOCKERFILE))

    assert [stage.name for stage in stages] == ["builder", "runtime"]
    assert stages[-1].base == "python:3.12-slim-bookworm"
    assert stages[0].is_builder is True
    assert stages[1].is_builder is False


def test_split_stages_names_an_unaliased_stage() -> None:
    """A stage without ``AS`` gets a positional name."""
    _, stages = dp.split_stages(dp.parse_dockerfile("FROM python:3.12-slim\nRUN true\n"))

    assert stages[0].name == "stage-0"


def test_split_stages_collects_global_instructions() -> None:
    """An instruction before the first FROM belongs to no stage."""
    globals_, stages = dp.split_stages(dp.parse_dockerfile("ARG V=1\nFROM python:3.12-slim\n"))

    assert [item.keyword for item in globals_] == ["ARG"]
    assert len(stages) == 1


# --- COPY sources and ENV -------------------------------------------------
def test_copy_sources_shell_form() -> None:
    """The last word of a shell-form COPY is the destination."""
    origin, sources = dp.copy_sources(dp.parse_dockerfile("COPY a b /dest\n")[0])

    assert origin is None
    assert sources == ["a", "b"]


def test_copy_sources_json_form() -> None:
    """The JSON form is parsed as a list, not split on spaces."""
    origin, sources = dp.copy_sources(dp.parse_dockerfile('COPY ["a b", "/dest"]\n')[0])

    assert origin is None
    assert sources == ["a b"]


def test_copy_sources_reports_the_from_stage() -> None:
    """A ``--from`` copy names its origin stage and keeps its paths."""
    origin, sources = dp.copy_sources(
        dp.parse_dockerfile("COPY --from=builder /wheels /wheels\n")[0]
    )

    assert origin == "builder"
    assert sources == ["/wheels"]


def test_copy_sources_handles_an_empty_instruction() -> None:
    """An instruction with no arguments yields no sources."""
    origin, sources = dp.copy_sources(dp.Instruction(keyword="COPY", value="", line=1))

    assert origin is None
    assert sources == []


def test_env_map_reads_key_value_form() -> None:
    """``ENV A=1 B=2`` sets two variables."""
    values = dp.env_map(dp.parse_dockerfile("ENV A=1 B=2\n"))

    assert values == {"A": "1", "B": "2"}


def test_env_map_reads_legacy_form() -> None:
    """``ENV A 1`` is the legacy single-variable form."""
    values = dp.env_map(dp.parse_dockerfile("ENV PYTHONUNBUFFERED 1\n"))

    assert values == {"PYTHONUNBUFFERED": "1"}


def test_env_map_lets_the_last_declaration_win() -> None:
    """A later ENV overrides an earlier one, as Docker resolves it."""
    values = dp.env_map(dp.parse_dockerfile("ENV A=1\nENV A=2\n"))

    assert values == {"A": "2"}


def test_env_map_ignores_other_instructions() -> None:
    """Only ENV is read."""
    assert dp.env_map(dp.parse_dockerfile("RUN ENV A=1\n")) == {}


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("python:3.12-slim-bookworm", "3.12-slim-bookworm"),
        ("ghcr.io/org/image:1.2", "1.2"),
        ("python", ""),
        ("localhost:5000/image", ""),
    ],
)
def test_base_tag(base: str, expected: str) -> None:
    """The tag is read from the last path segment only."""
    assert dp._base_tag(base) == expected


# --- .dockerignore matching ----------------------------------------------
def test_dockerignore_patterns_skip_comments() -> None:
    """Comments and blank lines are not patterns."""
    patterns = dp.dockerignore_patterns("# why\n\n.git\n!keep\n")

    assert patterns == [".git", "!keep"]


def test_pattern_hits_a_path_component() -> None:
    """A bare name matches a directory anywhere in the path."""
    assert dp._pattern_hits("a/b/__pycache__/c.pyc", "__pycache__") is True


def test_pattern_hits_a_relative_path() -> None:
    """A pattern with a slash matches from the context root."""
    assert dp._pattern_hits("docs/docker.md", "docs") is True
    assert dp._pattern_hits("src/docs/docker.md", "docs") is True


def test_pattern_hits_a_glob() -> None:
    """A wildcard pattern matches the file it names."""
    assert dp._pattern_hits("coverage.xml", "*.xml") is True
    assert dp._pattern_hits("coverage.json", "*.xml") is False


def test_pattern_hits_ignores_an_empty_pattern() -> None:
    """An empty pattern matches nothing."""
    assert dp._pattern_hits("docs/x.md", "") is False


def test_is_ignored_honours_a_negation() -> None:
    """A later ``!`` re-includes a path an earlier pattern excluded."""
    assert dp.is_ignored("docs/keep.md", ["docs", "!docs/keep.md"]) is False
    assert dp.is_ignored("docs/drop.md", ["docs", "!docs/keep.md"]) is True


# --- the clean tree -------------------------------------------------------
def test_a_clean_tree_passes(tmp_path: Path) -> None:
    """The reference tree produces no findings at all."""
    report = dp.audit_tree(make_tree(tmp_path))

    assert report.findings == []
    assert report.passed is True
    assert report.stages == 2
    assert report.image == "python:3.12-slim-bookworm"


def test_a_clean_tree_renders_pass(tmp_path: Path) -> None:
    """The table says PASS and names the base image."""
    text = dp.render_report(dp.audit_tree(make_tree(tmp_path)), tmp_path)

    assert "PASS" in text
    assert "python:3.12-slim-bookworm" in text


# --- Dockerfile findings --------------------------------------------------
def test_missing_dockerfile_is_unmeasurable(tmp_path: Path) -> None:
    """No Dockerfile is a gate that cannot measure, not a pass."""
    report = audit_with(tmp_path, dockerfile=None)

    assert report.unmeasurable is True
    assert "docker.file.missing" in codes(report)
    assert report.passed is False


def test_empty_dockerfile_is_unmeasurable(tmp_path: Path) -> None:
    """A Dockerfile with only comments has no instructions to audit."""
    report = audit_with(tmp_path, dockerfile="# nothing here\n")

    assert report.unmeasurable is True
    assert codes(report) == ["docker.file.empty"]


def test_a_single_stage_is_a_finding(tmp_path: Path) -> None:
    """One stage means the runtime ships whatever the build installed."""
    dockerfile = VALID_DOCKERFILE.split("FROM python:3.12-slim-bookworm AS runtime")[0]
    report = audit_with(tmp_path, dockerfile=dockerfile + "USER 10001\n")

    assert "docker.build.single_stage" in codes(report)


def test_an_untagged_base_is_a_finding(tmp_path: Path) -> None:
    """``FROM python`` moves with every upstream push."""
    report = audit_with(
        tmp_path, dockerfile=VALID_DOCKERFILE.replace("python:3.12-slim-bookworm", "python")
    )

    assert "docker.base.untagged" in codes(report)


def test_a_latest_base_is_a_finding(tmp_path: Path) -> None:
    """``latest`` is the tag a scheduled job must never use."""
    report = audit_with(
        tmp_path,
        dockerfile=VALID_DOCKERFILE.replace("python:3.12-slim-bookworm", "python:latest"),
    )

    assert "docker.base.floating" in codes(report)


def test_a_tag_without_a_version_is_floating(tmp_path: Path) -> None:
    """``python:slim`` names a moving target as surely as ``latest``."""
    report = audit_with(
        tmp_path, dockerfile=VALID_DOCKERFILE.replace("python:3.12-slim-bookworm", "python:slim")
    )

    assert "docker.base.floating" in codes(report)


def test_a_builder_last_stage_is_a_finding(tmp_path: Path) -> None:
    """Ending on a builder stage ships the compiler and the pip cache."""
    dockerfile = (
        VALID_DOCKERFILE + "\nFROM python:3.12-slim-bookworm AS builder2\nUSER 10001:10001\n"
    )

    report = audit_with(tmp_path, dockerfile=dockerfile)

    assert "docker.build.no_runtime_stage" in codes(report)


def test_missing_unbuffered_env_is_a_finding(tmp_path: Path) -> None:
    """Without PYTHONUNBUFFERED cron's log capture loses the last lines."""
    dockerfile = VALID_DOCKERFILE.replace("ENV PYTHONUNBUFFERED=1 \\\n", "ENV ")
    report = audit_with(tmp_path, dockerfile=dockerfile)

    assert "docker.env.missing" in codes(report)


def test_env_declared_before_the_first_from_is_a_finding(tmp_path: Path) -> None:
    """A global ENV applies to the builder, not to the image."""
    runtime_env = "ENV PYTHONUNBUFFERED=1 \\\n    PYTHONDONTWRITEBYTECODE=1\n\n"
    dockerfile = (
        "ENV PYTHONUNBUFFERED=1\nENV PYTHONDONTWRITEBYTECODE=1\n"
        + VALID_DOCKERFILE.replace(runtime_env, "")
    )
    report = audit_with(tmp_path, dockerfile=dockerfile)

    assert "docker.env.not_in_runtime" in codes(report)


def test_a_missing_user_is_a_finding(tmp_path: Path) -> None:
    """No USER instruction means the container runs as root."""
    report = audit_with(tmp_path, dockerfile=VALID_DOCKERFILE.replace("USER 10001:10001\n", ""))

    assert "docker.user.missing" in codes(report)


@pytest.mark.parametrize("user", ["root", "0", "0:0"])
def test_running_as_root_is_a_finding(tmp_path: Path, user: str) -> None:
    """Root by any spelling is root."""
    report = audit_with(
        tmp_path, dockerfile=VALID_DOCKERFILE.replace("USER 10001:10001", f"USER {user}")
    )

    assert "docker.user.root" in codes(report)


def test_missing_labels_are_findings(tmp_path: Path) -> None:
    """Each required OCI label is checked separately."""
    dockerfile = VALID_DOCKERFILE.replace(
        'LABEL org.opencontainers.image.title="datheon-organizer" \\\n'
        '      org.opencontainers.image.description="organizer" \\\n'
        '      org.opencontainers.image.source="https://example.invalid/repo" \\\n'
        '      org.opencontainers.image.version="1.1.0" \\\n'
        '      org.opencontainers.image.licenses="MIT"',
        'LABEL org.opencontainers.image.title="datheon-organizer"',
    )
    report = audit_with(tmp_path, dockerfile=dockerfile)

    found = codes(report).count("docker.label.missing")
    assert found == len(dp.REQUIRED_LABELS) - 1


def test_a_missing_entrypoint_is_a_finding(tmp_path: Path) -> None:
    """Without an entry point ``docker run image --version`` executes the flag."""
    report = audit_with(
        tmp_path, dockerfile=VALID_DOCKERFILE.replace('ENTRYPOINT ["organizer"]\n', "")
    )

    assert "docker.entrypoint.missing" in codes(report)


def test_a_shell_form_entrypoint_is_a_finding(tmp_path: Path) -> None:
    """/bin/sh at PID 1 swallows SIGTERM."""
    report = audit_with(
        tmp_path,
        dockerfile=VALID_DOCKERFILE.replace('ENTRYPOINT ["organizer"]', "ENTRYPOINT organizer"),
    )

    assert "docker.entrypoint.shell_form" in codes(report)


def test_an_entrypoint_that_is_not_the_organizer_is_a_finding(tmp_path: Path) -> None:
    """An image that runs something else does not package the tool."""
    report = audit_with(
        tmp_path, dockerfile=VALID_DOCKERFILE.replace('["organizer"]', '["hangman"]')
    )

    assert "docker.entrypoint.not_console_script" in codes(report)


def test_a_broken_json_entrypoint_is_a_finding(tmp_path: Path) -> None:
    """An exec-form entry point that does not parse is not exec-form."""
    report = audit_with(
        tmp_path, dockerfile=VALID_DOCKERFILE.replace('["organizer"]', '["organizer"')
    )

    assert "docker.entrypoint.not_console_script" in codes(report)


def test_pip_without_no_cache_is_a_finding(tmp_path: Path) -> None:
    """A pip cache in a layer is megabytes nobody asked for."""
    report = audit_with(
        tmp_path,
        dockerfile=VALID_DOCKERFILE.replace(
            "python -m pip wheel --no-cache-dir", "python -m pip wheel"
        ),
    )

    assert "docker.pip.no_cache" in codes(report)


def test_a_runtime_install_from_an_index_is_a_finding(tmp_path: Path) -> None:
    """The runtime stage must install from the builder's wheel."""
    report = audit_with(
        tmp_path,
        dockerfile=VALID_DOCKERFILE.replace(" --no-index", ""),
    )

    assert "docker.pip.network_in_runtime" in codes(report)


def test_build_tools_in_the_runtime_are_a_finding(tmp_path: Path) -> None:
    """A compiler in the final stage is the cost two stages exist to avoid."""
    dockerfile = VALID_DOCKERFILE.replace(
        "RUN useradd --uid 10001 datheon",
        "RUN apt-get update && apt-get install -y gcc make",
    )
    report = audit_with(tmp_path, dockerfile=dockerfile)

    assert "docker.runtime.build_tools" in codes(report)


def test_a_remote_add_is_a_finding(tmp_path: Path) -> None:
    """ADD of a URL is unpinned and unverifiable."""
    dockerfile = VALID_DOCKERFILE.replace(
        "COPY pyproject.toml ./",
        "ADD https://example.invalid/tool.tar.gz /tmp/tool.tar.gz",
    )
    report = audit_with(tmp_path, dockerfile=dockerfile)

    assert "docker.remote.add" in codes(report)


@pytest.mark.parametrize("key", ["API_TOKEN", "SECRET_KEY", "DB_PASSWORD", "GITHUB_TOKEN"])
def test_a_credential_in_env_is_a_finding(tmp_path: Path, key: str) -> None:
    """Anything in ENV is readable with ``docker inspect``."""
    dockerfile = VALID_DOCKERFILE.replace("ENV PYTHONUNBUFFERED=1 \\", f"ENV {key}=x \\")
    report = audit_with(tmp_path, dockerfile=dockerfile)

    assert "docker.secret.env" in codes(report)


def test_a_missing_copy_source_is_a_finding(tmp_path: Path) -> None:
    """A COPY of a path that is not there cannot build."""
    dockerfile = VALID_DOCKERFILE.replace("COPY pyproject.toml ./", "COPY setup.cfg ./")
    report = audit_with(tmp_path, dockerfile=dockerfile)

    assert "docker.copy.missing" in codes(report)


def test_a_copy_of_an_ignored_path_is_a_finding(tmp_path: Path) -> None:
    """The layer builds empty and the image fails at run time."""
    ignore = "\n".join(dp.REQUIRED_IGNORES) + "\npyproject.toml\n"
    report = audit_with(tmp_path, dockerignore=ignore)

    assert "docker.copy.ignored" in codes(report)


def test_a_copy_glob_is_resolved(tmp_path: Path) -> None:
    """A wildcard source is checked against what it matches."""
    dockerfile = VALID_DOCKERFILE.replace("COPY pyproject.toml ./", "COPY *.toml ./")
    assert "docker.copy.missing" not in codes(audit_with(tmp_path, dockerfile=dockerfile))
    assert "docker.copy.missing" in codes(
        audit_with(tmp_path, dockerfile=dockerfile.replace("*.toml", "*.cfg"))
    )


def test_a_copy_from_an_unknown_stage_is_a_finding(tmp_path: Path) -> None:
    """``--from`` must name a stage that exists."""
    report = audit_with(
        tmp_path, dockerfile=VALID_DOCKERFILE.replace("--from=builder", "--from=build")
    )

    assert "docker.copy.unknown_stage" in codes(report)


# --- .dockerignore findings ----------------------------------------------
def test_a_missing_dockerignore_is_a_finding(tmp_path: Path) -> None:
    """Without one, the whole tree — history included — goes to the daemon."""
    report = audit_with(tmp_path, dockerignore=False)

    assert "dockerignore.missing" in codes(report)


def test_a_missing_required_pattern_is_a_finding(tmp_path: Path) -> None:
    """Each required pattern is checked by name."""
    report = audit_with(tmp_path, dockerignore=".git\n__pycache__\n")

    assert "dockerignore.pattern_missing" in codes(report)
    messages = " ".join(f.message for f in report.findings)
    assert "'tests'" in messages


# --- crontab findings -----------------------------------------------------
def test_a_missing_crontab_is_a_finding(tmp_path: Path) -> None:
    """The schedule is part of the deliverable."""
    report = audit_with(tmp_path, crontab=None)

    assert codes(report) == ["cron.missing"]


def test_a_crontab_without_a_job_is_a_finding(tmp_path: Path) -> None:
    """A file of comments schedules nothing."""
    report = audit_with(tmp_path, crontab="# only a comment\n")

    assert "cron.no_job" in codes(report)


def test_an_interactive_cron_line_is_a_finding(tmp_path: Path) -> None:
    """Cron has no terminal, so ``-it`` fails under it."""
    report = audit_with(tmp_path, crontab=VALID_CRONTAB.replace("--rm", "-it --rm"))

    assert "cron.interactive" in codes(report)


def test_a_cron_line_without_rm_is_a_finding(tmp_path: Path) -> None:
    """A nightly job must not leave a container per night."""
    report = audit_with(tmp_path, crontab=VALID_CRONTAB.replace("--rm ", ""))

    assert "cron.no_rm" in codes(report)


def test_a_cron_line_without_a_lock_is_a_finding(tmp_path: Path) -> None:
    """Two overlapping runs can move the same file twice."""
    report = audit_with(tmp_path, crontab=VALID_CRONTAB.replace("flock -n /var/lock/o.lock ", ""))

    assert "cron.no_lock" in codes(report)


def test_a_cron_line_without_a_redirect_is_a_finding(tmp_path: Path) -> None:
    """Without the redirect a failure reports only to the mail spool."""
    report = audit_with(tmp_path, crontab=VALID_CRONTAB.replace(" 2>&1", ""))

    assert "cron.no_log" in codes(report)


def test_a_relative_mount_is_a_finding(tmp_path: Path) -> None:
    """Cron resolves a relative path against its own working directory."""
    report = audit_with(tmp_path, crontab=VALID_CRONTAB.replace("-v /srv/inbox", "-v inbox"))

    assert "cron.relative_mount" in codes(report)


def test_a_latest_tag_in_cron_is_a_finding(tmp_path: Path) -> None:
    """A schedule that changes without a commit is a surprise."""
    report = audit_with(
        tmp_path,
        crontab=VALID_CRONTAB.replace("datheon-organizer:1.1.0", "datheon-organizer:latest"),
    )

    assert "cron.latest" in codes(report)


# --- workflow and docs findings ------------------------------------------
def test_a_missing_workflow_is_a_finding(tmp_path: Path) -> None:
    """Nothing builds the image before a user does."""
    report = audit_with(tmp_path, workflow=None)

    assert codes(report) == ["ci.missing"]


def test_a_workflow_without_a_build_is_a_finding(tmp_path: Path) -> None:
    """A workflow that only runs a container never builds one."""
    report = audit_with(tmp_path, workflow=VALID_WORKFLOW.replace("docker build", "echo"))

    assert "ci.no_build" in codes(report)


def test_a_workflow_without_a_smoke_run_is_a_finding(tmp_path: Path) -> None:
    """An image that builds but cannot start passes a build-only workflow."""
    report = audit_with(tmp_path, workflow=VALID_WORKFLOW.replace("--version", "--help"))

    assert "ci.no_smoke" in codes(report)


def test_a_workflow_without_a_nonroot_assertion_is_a_finding(tmp_path: Path) -> None:
    """The uid promise is untested without the assertion."""
    report = audit_with(tmp_path, workflow=VALID_WORKFLOW.replace("10001", "0"))

    assert "ci.no_nonroot" in codes(report)


def test_a_missing_doc_is_a_finding(tmp_path: Path) -> None:
    """The image has no documented usage."""
    report = audit_with(tmp_path, doc=None)

    assert "doc.missing" in codes(report)


def test_a_doc_outside_the_nav_is_a_finding(tmp_path: Path) -> None:
    """A page the site cannot reach is a page nobody reads."""
    report = audit_with(tmp_path, nav="nav:\n  - Home: index.md\n")

    assert "doc.unlisted" in codes(report)


# --- the optional build ---------------------------------------------------
def test_build_image_reports_success(tmp_path: Path) -> None:
    """A zero exit status is a successful build."""
    calls: List[Sequence[str]] = []

    def runner(command: Sequence[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="built", stderr="")

    ok, output = dp.build_image(tmp_path, "img:test", runner=runner)

    assert ok is True
    assert output == "built"
    assert calls[0][:2] == ["docker", "build"]
    assert "img:test" in calls[0]


def test_build_image_reports_failure_with_the_tail(tmp_path: Path) -> None:
    """A failed build returns the daemon's last lines, not all of them."""
    noise = "\n".join(f"step {index}" for index in range(100))

    def runner(command: Sequence[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=noise, stderr="boom")

    ok, output = dp.build_image(tmp_path, "img:test", runner=runner)

    assert ok is False
    assert "boom" in output
    assert len(output.splitlines()) <= 41


def test_build_image_without_docker_is_unmeasurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No daemon means no verdict, never a pass."""
    monkeypatch.setattr(dp.shutil, "which", lambda name: None)

    ok, output = dp.build_image(tmp_path, "img:test")

    assert ok is None
    assert "docker" in output


# --- the command line -----------------------------------------------------
def test_main_returns_zero_for_a_clean_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clean tree exits 0 and prints PASS."""
    make_tree(tmp_path)

    assert dp.main(["--root", str(tmp_path)]) == dp.EXIT_OK
    assert "PASS" in capsys.readouterr().out


def test_main_returns_one_for_findings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A finding exits 1 and is repeated on stderr."""
    make_tree(tmp_path, doc=None)

    assert dp.main(["--root", str(tmp_path)]) == dp.EXIT_FINDINGS
    captured = capsys.readouterr()
    assert "FAIL" in captured.out
    assert "doc.missing" in captured.err


def test_main_returns_two_when_unmeasurable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing Dockerfile cannot be graded, so it cannot pass."""
    make_tree(tmp_path, dockerfile=None)

    assert dp.main(["--root", str(tmp_path)]) == dp.EXIT_UNMEASURABLE
    assert "cannot measure" in capsys.readouterr().err


def test_main_json_is_one_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` prints a single parseable object with the envelope."""
    make_tree(tmp_path, doc=None)

    code = dp.main(["--root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == dp.EXIT_FINDINGS
    assert payload["tool"] == "docker_packaging"
    assert payload["ok"] is False
    assert payload["stages"] == 2
    assert [item["code"] for item in payload["findings"]] == ["doc.missing"]


def test_main_build_without_docker_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--build`` on a host without Docker is unmeasurable, not a pass."""
    make_tree(tmp_path)
    monkeypatch.setattr(dp.shutil, "which", lambda name: None)

    assert dp.main(["--root", str(tmp_path), "--build"]) == dp.EXIT_UNMEASURABLE
    assert "cannot measure" in capsys.readouterr().err


def test_main_records_a_failed_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A daemon that fails the build is a finding, not an exception."""
    make_tree(tmp_path)
    monkeypatch.setattr(dp, "build_image", lambda root, tag: (False, "boom"))

    assert dp.main(["--root", str(tmp_path), "--build"]) == dp.EXIT_FINDINGS
    assert "build.failed" in capsys.readouterr().err


def test_main_json_records_the_build_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The JSON document carries the build result when one was asked for."""
    make_tree(tmp_path)
    monkeypatch.setattr(dp, "build_image", lambda root, tag: (True, "built"))

    assert dp.main(["--root", str(tmp_path), "--build", "--json"]) == dp.EXIT_OK
    assert json.loads(capsys.readouterr().out)["build"] is True


def test_report_to_dict_shape(tmp_path: Path) -> None:
    """The document names the tree it audited."""
    report = dp.audit_tree(make_tree(tmp_path))

    payload = dp.report_to_dict(report, tmp_path)

    assert payload["root"] == str(tmp_path)
    assert payload["schema"] == 1
    assert payload["unmeasurable"] is False


def test_render_report_marks_unmeasurable(tmp_path: Path) -> None:
    """An unmeasurable report says so instead of claiming a pass."""
    report = dp.Report(unmeasurable=True, reason="nothing to read")

    text = dp.render_report(report, tmp_path)

    assert "UNMEASURABLE" in text
    assert "nothing to read" in text


def test_build_parser_defaults() -> None:
    """The defaults are the current tree and no build."""
    args = dp.build_parser().parse_args([])

    assert args.root == "."
    assert args.build is False
    assert args.tag == "datheon-organizer:audit"


# --- the real tree --------------------------------------------------------
def test_the_committed_tree_passes() -> None:
    """The repository's own container packaging is clean."""
    report = dp.audit_tree(ROOT)

    assert report.findings == [], "\n".join(f.render() for f in report.findings)
    assert report.passed is True


def test_the_committed_image_is_two_stages_on_a_pinned_base() -> None:
    """The shape the documentation promises, read from the real Dockerfile."""
    report = dp.audit_tree(ROOT)
    instructions = dp.parse_dockerfile((ROOT / dp.DOCKERFILE).read_text(encoding="utf-8"))
    _, stages = dp.split_stages(instructions)

    assert report.stages == 2
    assert stages[0].name == "builder"
    assert stages[-1].name == "runtime"
    assert all("slim-bookworm" in stage.base for stage in stages)


def test_the_committed_entrypoint_is_the_organizer() -> None:
    """The image runs the tool it packages, in exec form."""
    text = (ROOT / dp.DOCKERFILE).read_text(encoding="utf-8")
    instructions = dp.parse_dockerfile(text)
    entrypoints = [item for item in instructions if item.keyword == "ENTRYPOINT"]

    assert json.loads(entrypoints[-1].value) == ["organizer"]


def test_the_committed_schedule_locks_and_redirects() -> None:
    """The example schedule carries the four properties a cron job needs."""
    text = (ROOT / dp.CRONTAB).read_text(encoding="utf-8")

    assert "flock -n" in text
    assert "--rm" in text
    assert "2>&1" in text
    assert "-v /" in text


def test_the_committed_workflow_builds_and_asserts_the_uid() -> None:
    """CI builds the image and checks the non-root promise."""
    text = (ROOT / dp.WORKFLOW).read_text(encoding="utf-8")

    assert "docker build" in text
    assert "10001" in text
    assert "--version" in text


def test_the_documentation_page_is_listed_in_the_nav() -> None:
    """The page exists and the site can reach it."""
    assert (ROOT / dp.DOC).is_file()
    assert Path(dp.DOC).name in (ROOT / dp.NAV).read_text(encoding="utf-8")


def test_every_documented_finding_code_is_implemented() -> None:
    """The code table in ``docs/docker.md`` matches the module's codes.

    A code that exists only in the documentation is a promise the gate does not
    keep, and a code the documentation omits is one nobody can look up.
    """
    text = (ROOT / dp.DOC).read_text(encoding="utf-8")
    documented = set(re.findall(r"`((?:docker|dockerignore|cron|ci|doc|build)\.[a-z_.]+)`", text))
    source = Path(dp.__file__).read_text(encoding="utf-8")
    implemented = set(re.findall(r'"(docker|dockerignore|cron|ci|doc|build)\.[a-z_.]+"', source))
    implemented = {
        match
        for match in re.findall(r'"((?:docker|dockerignore|cron|ci|doc|build)\.[a-z_.]+)"', source)
    }

    assert documented, "the documentation table names no codes"
    assert implemented - documented == set()
    assert documented - implemented == set()
