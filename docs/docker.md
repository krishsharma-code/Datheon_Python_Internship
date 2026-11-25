# Container image: the organizer on a schedule

**Day 33 — maintenance, backlog §5: “add Docker packaging for the organizer as a
cron-friendly container.”**

Everything this repository ships assumes an interpreter on the host. Day 22 made
that one command (`pip install -e .`), Day 23 proved the wheel installs into a
clean virtualenv, Day 24 proved it works on three operating systems — and all of
it assumes a machine where someone can install a Python.

A nightly cleanup job is not that machine. It is a box that already has a
`crontab` and a directory filling up, and the usual answer — install Python 3.12,
install the package, write the schedule — is a sequence of steps that quietly
differs on every host, breaks when the host's Python is upgraded, and has to be
repeated the next time.

This image replaces that sequence with one artefact: the same wheel the release
workflow publishes, installed into a slim runtime, running as a fixed
unprivileged uid, with an exec-form entry point so the tool's own exit codes and
signals survive the container boundary.

```bash
docker build -t datheon-organizer:1.1.0 .

docker run --rm -v /srv/inbox:/data:rw datheon-organizer:1.1.0 --dry-run
docker run --rm -v /srv/inbox:/data:rw datheon-organizer:1.1.0
```

`/data` is the mount, and it is also the image's `WORKDIR`, so a run with no
argument organizes what you mounted rather than the image's own filesystem.

## What is in the image

Two stages, and only one file crosses between them.

| Stage | Base | What it does |
| --- | --- | --- |
| `builder` | `python:3.12-slim-bookworm` | Copies the package surface and runs `pip wheel --no-cache-dir --no-deps` into `/wheels`. The only stage that reaches the network. |
| `runtime` | `python:3.12-slim-bookworm` | Creates uid/gid `10001`, installs the wheel from `/wheels` with `--no-index`, deletes the wheels, then runs as `10001:10001`. |

The runtime stage therefore contains **no compiler, no `apt` packages, no pip
cache and no build backend** — and `--no-index` is what makes “the run does not
need the network” a property of the image rather than a hope about the build
environment. `tools/docker_packaging.py` fails if a compiler, an `apt-get
install` or an index-based install ever appears in the final stage.

Both stages pin a **minor** Python version and a Debian release. `latest` would
mean the image is different tomorrow without a commit here, which is the one
property a scheduled job cannot tolerate.

### Labels

`docker inspect` answers what the image is, where it came from and under which
licence, from the OCI labels the runtime stage sets:

| Label | Value |
| --- | --- |
| `org.opencontainers.image.title` | `datheon-organizer` |
| `org.opencontainers.image.description` | cron-friendly container for Task 3 |
| `org.opencontainers.image.source` | the repository URL |
| `org.opencontainers.image.version` | the `IMAGE_VERSION` build argument |
| `org.opencontainers.image.licenses` | `MIT` |

### Why the entry point is exec-form

```dockerfile
ENTRYPOINT ["organizer"]
CMD ["/data"]
```

A shell-form `ENTRYPOINT organizer` puts `/bin/sh` at PID 1. That process
forwards the exit status of the last command it ran, but it does not forward
`SIGTERM`, so `docker stop` waits out the grace period and then kills a
half-finished move. The Day 3 contract — `0` success, `1` user/data error, `2`
usage error — is only worth something if it survives the boundary:

```bash
docker run --rm datheon-organizer:1.1.0 --not-a-flag   ; echo $?   # 2
docker run --rm datheon-organizer:1.1.0 /missing       ; echo $?   # 1
```

`.github/workflows/docker.yml` asserts both of those numbers on every change to
the image.

## Volumes and the unprivileged uid

| Path | Purpose | Mount |
| --- | --- | --- |
| `/data` | the directory being organized | `-v /srv/inbox:/data:rw` |
| `/state/logs` | rotating log (`DATHEON_LOG_DIR`) | `-v /srv/datheon-state:/state:rw` |

The uid is fixed at `10001:10001` rather than generated, so the host can chown
an inbox once and every later run keeps the same ownership:

```bash
sudo chown -R 10001:10001 /srv/inbox /srv/datheon-state
```

The account is created with `--shell /usr/sbin/nologin`, so it cannot be used to
get a shell inside the image.

## Running it from cron

`docker/crontab.example` is the line to install, commented with the reason for
every flag:

```cron
15 2 * * * flock -n /var/lock/datheon-organizer.lock docker run --rm --network none --read-only --tmpfs /tmp -v /srv/inbox:/data:rw -v /srv/datheon-state:/state:rw datheon-organizer:1.1.0 --dry-run >> /var/log/datheon-organizer.log 2>&1
```

It is one physical line because cron has no line continuation: a trailing
backslash is not joined, it is part of the command. The audit joins
continuations when it reads the file, so a wrapped entry is still judged whole,
but the file you install must not wrap.

```bash
sudo crontab docker/crontab.example      # after replacing the paths
```

Each part exists for a way a scheduled container run fails:

| Flag | What it prevents |
| --- | --- |
| `flock -n` | two runs moving the same file at once. Cron does not know how long a run takes, so overlap is a matter of when, not if. |
| `--rm` | one stopped container per night, forever. |
| `--network none` | an organizer that reads local files reaching a network it does not need. |
| `--read-only` + `--tmpfs /tmp` | a bug writing into the image's filesystem instead of failing where you can see it. |
| `-v /abs/path:/data:rw` | a relative mount, which cron resolves against its own working directory rather than yours. |
| `>> … 2>&1` | a failing run that reports only to the mail spool. |
| `--dry-run` | the example moving files the first time it is installed. Remove it once the log shows the plan you expected. |
| `datheon-organizer:1.1.0` | a schedule whose behaviour changes without a commit on the host. |

The example is a **plan**, not a default: it prints what it would move and
changes nothing. Drop `--dry-run` from the line once the log looks right.

## What the audit checks

`tools/docker_packaging.py` reads the four files that make the container what it
is and checks them against this page. It needs no Docker daemon, so it runs in
the normal test suite and on any contributor's machine:

```bash
python tools/docker_packaging.py            # the table
python tools/docker_packaging.py --json     # one machine-readable document
python tools/docker_packaging.py --build    # also build; exits 2 without a daemon
```

| Area | Codes |
| --- | --- |
| File and shape | `docker.file.missing`, `docker.file.empty`, `docker.build.no_stage`, `docker.build.single_stage`, `docker.build.no_runtime_stage` |
| Base image | `docker.base.untagged`, `docker.base.floating` |
| Runtime | `docker.env.missing`, `docker.env.not_in_runtime`, `docker.user.missing`, `docker.user.root`, `docker.label.missing`, `docker.entrypoint.missing`, `docker.entrypoint.shell_form`, `docker.entrypoint.not_console_script` |
| Layers | `docker.pip.no_cache`, `docker.pip.network_in_runtime`, `docker.runtime.build_tools`, `docker.remote.add` |
| Credentials | `docker.secret.env`, and the `.dockerignore` patterns below |
| Build context | `docker.copy.missing`, `docker.copy.ignored`, `docker.copy.unknown_stage`, `dockerignore.missing`, `dockerignore.pattern_missing` |
| Schedule | `cron.missing`, `cron.no_job`, `cron.interactive`, `cron.no_rm`, `cron.no_lock`, `cron.no_log`, `cron.relative_mount`, `cron.latest` |
| CI and docs | `ci.missing`, `ci.no_build`, `ci.no_smoke`, `ci.no_nonroot`, `doc.missing`, `doc.unlisted`, `build.failed` |

Exit codes follow the rest of the repository: **0** clean, **1** at least one
finding, **2** the audit could not measure — there is no `Dockerfile`, or
`--build` was asked for on a host without Docker. A gate that cannot measure
never reports success.

### The one check that is not obvious

`docker.copy.ignored` is why the audit reads `.dockerignore` at all. A pattern
that excludes a directory the Dockerfile copies does not fail the build — Docker
copies nothing, the layer is empty, and the image builds cleanly and then fails
at run time with a `ModuleNotFoundError`. The audit resolves every `COPY`
source against the tree *and* against the ignore patterns, so that image cannot
be built here.

## What CI proves, and what it cannot

`.github/workflows/docker.yml` runs on every change to the Dockerfile, the
ignore file, the organizer or the packaging metadata. It builds the image and
then asks the questions a successful build does not answer:

1. `--version` and `--help` run inside the container;
2. `id -u` inside the container prints `10001`;
3. a real directory, mounted at `/data`, is organized — with `--read-only` and
   no network — and the moved files land in `Documents/` and `Images/`;
4. a bad flag exits `2` and a missing directory exits `1`, so the tool's exit
   codes survive `docker run`.

What it cannot prove is the schedule itself: a GitHub runner has no cron daemon,
so the crontab example is checked as text (lock, `--rm`, absolute mounts,
redirects, no TTY) rather than executed. That is stated here rather than
implied, and it is the reason every line in the example carries a comment
saying which failure it prevents.

## Not what this image is for

The container runs **one** tool, the organizer — the only one of the five whose
job is unattended. Hangman, the terminal assistant and the inventory demo are
interactive, and a portfolio tracker that runs once a night on a mounted
directory is a spreadsheet with extra steps. The other four are still installed
in the image (they ship in the same wheel) and reachable with
`docker run --entrypoint hangman …`, but nothing here schedules them.
