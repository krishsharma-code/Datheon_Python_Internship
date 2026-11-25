# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Day 33 — the organizer, as a cron-friendly container.
#
# Every other delivery in this repository assumes a Python interpreter on the
# host: `pip install -e .`, then `organizer ~/Downloads`. A nightly cleanup job
# on a machine that is not a developer's laptop does not have that assumption,
# and the usual answer — "install Python 3.12 and the package, then edit the
# crontab" — is a set of steps that quietly differs on every host.
#
# This image is one artefact instead: the same wheel the release workflow
# publishes (Day 23/26), installed into a slim runtime with no compiler, no
# pip cache and no network, running as a fixed non-root uid, with an exec-form
# entry point so the exit code and SIGTERM reach the CLI and the scheduler.
#
#   docker build -t datheon-organizer:1.1.0 .
#   docker run --rm -v /srv/inbox:/data:rw datheon-organizer:1.1.0 --dry-run
#
# The build is two stages on purpose: the wheel is compiled in `builder`, and
# nothing from that stage but the wheel crosses into `runtime`. The final image
# therefore contains no setuptools, no pip download cache and no compiler —
# `tools/docker_packaging.py` fails the build if that stops being true.
#
# Both stages pin a minor Python version and a Debian release. `latest` would
# make the image different tomorrow without a commit here, which is the one
# property a scheduled job cannot tolerate.
# ---------------------------------------------------------------------------

# --- build stage -----------------------------------------------------------
# Compiles the wheel from the source tree. This is the only stage that needs
# the network (for the setuptools build backend) and the only one with pip's
# cache available to it — and `--no-cache-dir` keeps even that off disk.
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# The package surface, explicitly: the metadata, the shim package, the five
# task packs and the thirteen shared core modules that `pyproject.toml` ships.
# `COPY . .` would work, but then a new top-level file would join the image by
# accident — and the audit could not tell which paths the build actually needs.
COPY pyproject.toml README.md LICENSE ./
COPY datheon ./datheon
COPY projects ./projects
COPY caching.py cli_contract.py dataset_loader.py edge_cases.py \
     error_catalogue.py errors.py fuzzing.py logging_config.py \
     observability.py persistence.py profiling.py structured_logging.py \
     validators.py ./

# `--no-deps` because the tools are standard-library only: there is nothing to
# resolve, so the wheel is built without a resolver round trip.
RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

# --- runtime stage ---------------------------------------------------------
# The image a scheduler actually runs. No compiler, no build cache, no shell
# toolchain — only the interpreter, the installed wheel and an unprivileged
# account.
FROM python:3.12-slim-bookworm AS runtime

ARG IMAGE_VERSION=1.1.0

LABEL org.opencontainers.image.title="datheon-organizer" \
      org.opencontainers.image.description="Cron-friendly container for the Datheon file organizer (Task 3)." \
      org.opencontainers.image.source="https://github.com/krishsharma-code/Datheon_Python_Internship" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="Krish Sharma"

# `PYTHONUNBUFFERED` is what makes cron's log capture honest: without it a run
# that ends in an error can reach the mail spool with its last lines still in
# the interpreter's buffer. `DATHEON_LOG_DIR` moves the rotating log onto the
# state volume, so a run's log survives the container that wrote it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATHEON_LOG_DIR=/state/logs

# A fixed uid/gid rather than a generated one: the host can `chown -R
# 10001:10001` an inbox once and every later run keeps the same ownership.
# `--shell /usr/sbin/nologin` means the account cannot be used to get a shell.
RUN groupadd --gid 10001 datheon \
    && useradd --uid 10001 --gid datheon --create-home \
       --shell /usr/sbin/nologin datheon \
    && mkdir -p /data /state/logs \
    && chown -R 10001:10001 /data /state

# `--no-index` is the point of the two stages: the runtime stage installs from
# the wheel beside it and never reaches the network, so a build cannot depend
# on PyPI being reachable and a run cannot either.
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index \
       --find-links=/wheels datheon-python-internship \
    && rm -rf /wheels

# Numeric uid:gid, so an orchestrator that refuses to run as root can verify it
# without resolving a name against the image's /etc/passwd.
USER 10001:10001

# The default target is the mounted inbox. `WORKDIR /data` also means a run
# with no argument organizes the mount rather than the image's filesystem.
WORKDIR /data

# Exec form, not shell form: the CLI is PID 1, so `docker run` returns the
# tool's own exit code (0/1/2 — the Day 3 contract) and a `docker stop` sends
# SIGTERM to Python instead of to a `sh -c` wrapper that would ignore it.
ENTRYPOINT ["organizer"]
CMD ["/data"]
