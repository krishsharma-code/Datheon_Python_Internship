"""Shared logging architecture for the five Datheon CLIs (Day 8).

Days 1–7 gave the tools one command line, one validation layer, one edge-case
policy, one dataset loader and one exception taxonomy. What was still missing
was *observability*: once a tool misbehaved there was nothing on disk to
reconstruct what it had done. This module supplies the missing piece — a single
logging setup that every entry point under ``projects/`` shares.

The contract has three parts:

* **One flag.** :func:`add_logging_flags` attaches ``--log-level`` and
  ``--log-file`` to a parser, so all five tools accept the same switches with
  the same semantics.
* **Two handlers.** :func:`configure_logging` installs a console handler (on
  ``stderr``, so ``stdout`` stays machine-readable) and a
  :class:`~logging.handlers.RotatingFileHandler` (so an unattended cron run can
  never fill the disk). The console line is terse — ``LEVEL    prog: message``
  — while the file line carries the timestamp and the source location needed
  for a post-mortem.
* **Off unless asked.** Logging is *opt-in*: without ``--log-level`` (or an
  explicit ``--log-file``) a tool installs no handlers and writes no files, so
  a plain ``python projects/01_hangman_game/main.py`` behaves exactly as it did
  on Day 7. ``--log-level DEBUG`` turns the whole pipeline on and writes a log
  file in every project.

Since Day 10 the same module also carries the *structured* layer: passing
``--json-logs`` swaps both formatters for
:class:`structured_logging.JsonFormatter`, so every line becomes one JSON object
carrying a ``run_id``, the ``project``, the elapsed ``duration_ms`` and the
eventual ``run_outcome``. Each session opens a
:class:`~structured_logging.RunContext`, and :func:`finish_active_runs` closes
it with an outcome and a total duration, so the first and last record of a run
correlate without a single call site changing shape. The text format remains
the default: a human tailing a log file is never punished by the new mode.

Where the file goes is deliberately not hard-coded: :func:`default_log_dir`
honours the ``DATHEON_LOG_DIR`` environment variable and otherwise falls back
to ``~/.datheon/logs``. That keeps the repository clean, keeps the tests
hermetic (they point the variable at ``tmp_path``), and gives an operator one
knob to redirect every tool's logs at once.

A :class:`LoggingSession` is returned rather than a bare logger so the caller
owns the handlers it created: :meth:`LoggingSession.close` removes and closes
them, :func:`shutdown_logging` closes whatever is still open, and
:func:`cli_contract.run_cli` calls that in a ``finally`` block — the process
boundary is the one place that can guarantee cleanup.

Typical use::

    from logging_config import add_logging_flags, configure_logging_from_args

    parser = build_parser("hangman", "Console Hangman game.")
    add_logging_flags(parser)
    args = parser.parse_args(argv)

    session = configure_logging_from_args("hangman", args)
    logger = session.logger
    logger.debug("hangman starting with %s", args)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional, TextIO, Union

from errors import ConfigurationError
from structured_logging import (
    JSON_SCHEMA_VERSION,
    RUN_ID_ENV,
    JsonFormatter,
    RunContext,
    begin_run,
    current_run,
    end_run,
    log_error,
    new_run_id,
    structured_extra,
)

__all__ = [
    "CONSOLE_FORMAT",
    "DATE_FORMAT",
    "DEFAULT_BACKUP_COUNT",
    "DEFAULT_LOG_DIR_ENV",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_MAX_BYTES",
    "FILE_FORMAT",
    "JSON_SCHEMA_VERSION",
    "LOG_LEVELS",
    "RUN_ID_ENV",
    "JsonFormatter",
    "LoggingSession",
    "RunContext",
    "add_logging_flags",
    "begin_run",
    "configure_logging",
    "configure_logging_from_args",
    "current_run",
    "default_log_dir",
    "default_log_file",
    "end_run",
    "finish_active_runs",
    "installed_handlers",
    "log_error",
    "new_run_id",
    "resolve_level",
    "shutdown_logging",
    "structured_extra",
]

#: Accepted ``--log-level`` values, quietest last, in the order a user reads
#: them in ``--help``.
LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: Level used when only ``--log-file`` is supplied without ``--log-level``.
DEFAULT_LOG_LEVEL: str = "WARNING"

#: Environment variable that redirects every tool's log directory.
DEFAULT_LOG_DIR_ENV: str = "DATHEON_LOG_DIR"

#: Sub-path appended to the home directory when the variable is unset.
DEFAULT_LOG_SUBDIR: Path = Path(".datheon") / "logs"

#: Rotate once a log file exceeds one mebibyte.
DEFAULT_MAX_BYTES: int = 1_048_576

#: Keep three rotated generations per tool (``.log.1`` … ``.log.3``).
DEFAULT_BACKUP_COUNT: int = 3

#: Console line: level and logger name, nothing that would clutter a terminal.
CONSOLE_FORMAT: str = "%(levelname)-8s %(name)s: %(message)s"

#: File line: adds the timestamp and the exact source location.
FILE_FORMAT: str = "%(asctime)s %(levelname)-8s %(name)s [%(filename)s:%(lineno)d] %(message)s"

#: ISO-8601-ish timestamp used in the file handler, with the UTC offset.
DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%S%z"

#: Attribute stamped on every handler this module installs, so a later call can
#: remove its own handlers without disturbing handlers added by a test, a
#: library, or ``pytest``'s ``caplog`` fixture.
_OWNED_ATTRIBUTE: str = "_datheon_logging_handler"

#: Sessions that have not been closed yet, closed by :func:`shutdown_logging`.
_ACTIVE_SESSIONS: List["LoggingSession"] = []


def default_log_dir() -> Path:
    """Return the directory that receives every tool's log file.

    ``DATHEON_LOG_DIR`` wins when it is set to a non-empty value; otherwise the
    directory is ``~/.datheon/logs``.

    Returns:
        The log directory as a :class:`~pathlib.Path`. The directory is *not*
        created here — :func:`configure_logging` creates it only when a file
        handler is actually needed.
    """
    override = os.environ.get(DEFAULT_LOG_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    try:
        home = Path.home()
    except (RuntimeError, KeyError):  # pragma: no cover - exotic environments
        home = Path.cwd()
    return home / DEFAULT_LOG_SUBDIR


def default_log_file(prog: str) -> Path:
    """Return the default log file for a program.

    Args:
        prog: Program name as used in ``--help`` (for example ``"organizer"``).

    Returns:
        ``<log dir>/<prog>.log``.
    """
    return default_log_dir() / f"{prog}.log"


def resolve_level(level: Union[str, int, None]) -> int:
    """Translate a ``--log-level`` value into a :mod:`logging` level constant.

    Args:
        level: A level name (case-insensitive), a numeric level, a numeric
            string, or ``None`` for :data:`DEFAULT_LOG_LEVEL`.

    Returns:
        The integer level understood by :mod:`logging`.

    Raises:
        ConfigurationError: If the value is not a known level name and not an
            integer in the ``0``–``50`` range. The error carries the accepted
            names as its hint.
    """
    if level is None:
        return int(logging.getLevelName(DEFAULT_LOG_LEVEL))

    if isinstance(level, bool):  # bool is an int subclass; reject it explicitly.
        raise ConfigurationError(
            f"--log-level: expected a level name, got {level!r}",
            hint="choose one of " + ", ".join(LOG_LEVELS),
            context={"value": repr(level)},
        )

    if isinstance(level, int):
        if 0 <= level <= logging.CRITICAL:
            return level
        raise ConfigurationError(
            f"--log-level: numeric level {level} is out of range",
            hint=(
                f"use a value between 0 and {logging.CRITICAL}, or one of " + ", ".join(LOG_LEVELS)
            ),
            context={"value": str(level)},
        )

    text = str(level).strip().upper()
    if text.isdigit():
        return resolve_level(int(text))
    if text in LOG_LEVELS:
        return int(logging.getLevelName(text))

    raise ConfigurationError(
        f"--log-level: unknown level {level!r}",
        hint="choose one of " + ", ".join(LOG_LEVELS),
        context={"value": str(level)},
    )


def add_logging_flags(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add the shared ``--log-level`` and ``--log-file`` switches to a parser.

    Both switches default to ``None``, which means "do not log": the tools stay
    silent on disk unless the user asks for logs.

    Args:
        parser: The parser to extend.

    Returns:
        The same parser, for fluent use.
    """
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=list(LOG_LEVELS),
        default=None,
        metavar="LEVEL",
        help=(
            "Enable logging at LEVEL and write a rotating log file "
            f"({', '.join(LOG_LEVELS)}; default: off)."
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help=(
            "Rotating log file to write "
            f"(default: <log dir>/<prog>.log; set {DEFAULT_LOG_DIR_ENV} to move the log dir)."
        ),
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        help=(
            "Emit one JSON object per record (run ID, project, duration, outcome) "
            "instead of text lines; needs --log-level or --log-file to have any effect."
        ),
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        metavar="ID",
        help=(
            "Correlation ID stamped on every record of this run "
            f"(default: generated; {RUN_ID_ENV} overrides)."
        ),
    )
    return parser


@dataclass
class LoggingSession:
    """The handlers installed for one CLI invocation.

    Attributes:
        logger: The :class:`logging.Logger` the tool should log through. Its
            name is the program name, so file lines are attributable at a
            glance.
        level: The integer level the logger and its handlers run at.
        handlers: The handlers this module installed, in installation order.
        log_file: The file actually being written, or ``None`` when no file
            handler was installed (logging disabled, or the path was
            unwritable).
        file_error: The reason the file handler could not be installed, or
            ``None``. A non-empty value means the tool kept running with a
            console handler only.
    """

    logger: logging.Logger
    level: int = logging.WARNING
    handlers: List[logging.Handler] = field(default_factory=list)
    log_file: Optional[Path] = None
    file_error: Optional[str] = None
    run: Optional[RunContext] = None
    json_logs: bool = False
    finished: bool = False

    @property
    def enabled(self) -> bool:
        """Whether any handler was installed for this session."""
        return bool(self.handlers)

    @property
    def file_enabled(self) -> bool:
        """Whether a rotating file handler is writing to :attr:`log_file`."""
        return self.log_file is not None

    @property
    def run_id(self) -> Optional[str]:
        """The correlation ID stamped on this session's records, if any."""
        return self.run.run_id if self.run is not None else None

    def finish(
        self,
        outcome: str = "success",
        *,
        error: Optional[dict] = None,
        level: int = logging.INFO,
    ) -> None:
        """Record the outcome and total duration of this run.

        Emits exactly one ``run finished`` record — the counterpart of the
        ``run started`` record :func:`configure_logging` writes — carrying
        ``event``, ``outcome``, ``duration_ms`` and, when the run failed, the
        machine-readable ``error`` payload. The record's ``run_id`` matches
        every other record of the session, so ``start`` and ``finish``
        correlate.

        Idempotent: the first call wins and later calls are ignored, so
        :func:`finish_active_runs` and :meth:`close` can both ask without
        producing a second finish line.

        Args:
            outcome: Terminal state — ``"success"``, ``"error"``,
                ``"interrupted"`` or ``"incomplete"``.
            error: Optional ``DatheonError.to_dict()`` style payload describing
                the failure.
            level: Level for the finish record; ``INFO`` by default, so the
                line is present at the level a cron job normally runs at.
        """
        if self.finished:
            return
        self.finished = True
        if self.run is None or not self.handlers:
            return

        self.run.finish(outcome)
        fields: dict = {"event": "run_finish", "outcome": outcome}
        if error is not None:
            fields["error"] = error
        self.logger.log(level, "run finished", extra=structured_extra(**fields))

    def close(self) -> None:
        """Finish the run, then remove and close every handler it installed.

        A run that is closed without an explicit :meth:`finish` is recorded as
        ``incomplete`` rather than silently losing its end record. Safe to call
        twice: the second call is a no-op. Any records still buffered in a
        handler are flushed by :meth:`logging.Handler.close`.
        """
        self.finish("incomplete")
        for handler in self.handlers:
            self.logger.removeHandler(handler)
            handler.close()
        self.handlers.clear()
        self.log_file = None
        end_run(self.run)
        if self in _ACTIVE_SESSIONS:
            _ACTIVE_SESSIONS.remove(self)

    def __enter__(self) -> "LoggingSession":
        """Return the session so it can be used as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the session on leaving the ``with`` block."""
        self.close()


def _remove_owned_handlers(logger: logging.Logger) -> None:
    """Detach and close handlers installed by a previous call for this logger.

    Args:
        logger: The logger to clean. Handlers that this module did not install
            — ``pytest``'s ``caplog`` handler, for instance — are left alone.
    """
    for handler in list(logger.handlers):
        if getattr(handler, _OWNED_ATTRIBUTE, False):
            logger.removeHandler(handler)
            handler.close()


def _close_sessions_for(logger: logging.Logger) -> None:
    """Close any active session that already owns ``logger``.

    Reconfiguring a program must not leave the previous session holding a
    finished run: closing it first writes its ``run finished`` record while its
    handlers are still open, then detaches them, so the new session starts from
    a clean slate with a fresh run ID.

    Args:
        logger: The logger about to be reconfigured.
    """
    for session in [item for item in _ACTIVE_SESSIONS if item.logger is logger]:
        session.close()


def installed_handlers(logger: logging.Logger) -> List[logging.Handler]:
    """Return the handlers this module currently has installed on a logger.

    Handlers added by anyone else — ``pytest``'s capture handler, an embedding
    application — are ignored, which makes the helper safe to use in
    assertions.

    Args:
        logger: The logger to inspect.

    Returns:
        The subset of ``logger.handlers`` that this module installed.
    """
    return [handler for handler in logger.handlers if getattr(handler, _OWNED_ATTRIBUTE, False)]


def configure_logging(
    prog: str,
    level: Union[str, int, None] = None,
    log_file: Optional[Union[str, Path]] = None,
    *,
    stream: Optional[TextIO] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    json_logs: bool = False,
    run_id: Optional[str] = None,
) -> LoggingSession:
    """Configure console and rotating-file logging for one tool.

    Calling this again for the same program replaces the handlers the previous
    call installed, so a long-lived process (or a test module) cannot
    accumulate duplicated log lines.

    Args:
        prog: Program name; used as the logger name and the default log file
            stem.
        level: Level name, numeric level, or ``None`` for :data:`DEFAULT_LOG_LEVEL`
            when a log file was requested. ``None`` with no ``log_file``
            disables logging entirely.
        log_file: Explicit log file path; defaults to :func:`default_log_file`.
        stream: Console stream; defaults to ``sys.stderr`` so ``stdout`` stays
            parseable. Passing an explicit stream is mainly useful in tests.
        max_bytes: Rotation threshold in bytes.
        backup_count: Number of rotated generations to keep.
        json_logs: Emit one JSON object per record on both handlers
            (:class:`~structured_logging.JsonFormatter`) instead of text lines.
        run_id: Explicit run ID for the structured records; when ``None`` the
            ``DATHEON_RUN_ID`` environment variable is honoured, and otherwise
            one is generated.

    Returns:
        A :class:`LoggingSession` describing what was installed. The session is
        disabled (:attr:`LoggingSession.enabled` is ``False``) when neither
        ``level`` nor ``log_file`` was supplied; a disabled session opens no
        run context and writes nothing anywhere.

    Raises:
        ConfigurationError: If ``level`` is not a recognised level.
    """
    logger = logging.getLogger(prog)
    logger.propagate = False
    _close_sessions_for(logger)
    _remove_owned_handlers(logger)

    if level is None and log_file is None:
        logger.setLevel(logging.WARNING)
        return LoggingSession(logger=logger, level=logging.WARNING)

    level_value = resolve_level(DEFAULT_LOG_LEVEL if level is None else level)
    logger.setLevel(level_value)
    session = LoggingSession(logger=logger, level=level_value, json_logs=json_logs)

    console = logging.StreamHandler(sys.stderr if stream is None else stream)
    console.setLevel(level_value)
    console.setFormatter(JsonFormatter() if json_logs else logging.Formatter(CONSOLE_FORMAT))
    setattr(console, _OWNED_ATTRIBUTE, True)
    logger.addHandler(console)
    session.handlers.append(console)

    target = Path(log_file).expanduser() if log_file is not None else default_log_file(prog)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler: logging.Handler = RotatingFileHandler(
            target,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError as exc:
        # An unwritable log path must never abort a run: report it once, keep
        # the console handler, and carry on.
        session.file_error = str(exc)
        logger.warning("file logging disabled: %s", exc)
    else:
        file_handler.setLevel(level_value)
        file_handler.setFormatter(
            JsonFormatter() if json_logs else logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT)
        )
        setattr(file_handler, _OWNED_ATTRIBUTE, True)
        logger.addHandler(file_handler)
        session.handlers.append(file_handler)
        session.log_file = target

    # Open the run context last: the "file logging disabled" warning above
    # belongs to no run, and every record from here on carries the run ID.
    session.run = begin_run(prog, run_id)
    logger.info(
        "run started",
        extra=structured_extra(
            event="run_start",
            project=prog,
            json_logs=json_logs,
            log_file=str(session.log_file) if session.log_file else None,
        ),
    )

    _ACTIVE_SESSIONS.append(session)
    return session


def configure_logging_from_args(
    prog: str,
    args: argparse.Namespace,
    *,
    stream: Optional[TextIO] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> LoggingSession:
    """Configure logging from a parsed command line.

    Args:
        prog: Program name; used as the logger name and the default log file
            stem.
        args: A namespace produced by a parser extended with
            :func:`add_logging_flags`. Missing attributes are treated as
            ``None``, so a namespace from an older parser still works.
        stream: Console stream; defaults to ``sys.stderr``.
        max_bytes: Rotation threshold in bytes.
        backup_count: Number of rotated generations to keep.

    Returns:
        The resulting :class:`LoggingSession`.
    """
    return configure_logging(
        prog,
        getattr(args, "log_level", None),
        getattr(args, "log_file", None),
        stream=stream,
        max_bytes=max_bytes,
        backup_count=backup_count,
        json_logs=bool(getattr(args, "json_logs", False)),
        run_id=getattr(args, "run_id", None),
    )


def finish_active_runs(outcome: str = "success", *, error: Optional[dict] = None) -> None:
    """Record a terminal outcome for every run that is still open.

    :func:`cli_contract.run_cli` calls this in its ``finally`` block, before
    :func:`shutdown_logging`, so every invocation ends with exactly one
    ``run finished`` record carrying the outcome and the total duration — on
    the success path, on a domain error, and on ``Ctrl-C``.

    Args:
        outcome: Terminal state to record — ``"success"``, ``"error"``,
            ``"interrupted"`` or ``"incomplete"``.
        error: Optional machine-readable error payload for the finish record.
    """
    for session in list(_ACTIVE_SESSIONS):
        session.finish(outcome, error=error)


def shutdown_logging() -> None:
    """Close every session that is still open.

    Called by :func:`cli_contract.run_cli` in a ``finally`` block, and by tests
    that configure logging directly. Only handlers installed by this module are
    closed, so an embedding application's own logging is untouched. A session
    that was never finished is recorded as ``incomplete`` before it closes.
    """
    for session in list(_ACTIVE_SESSIONS):
        session.close()
    _ACTIVE_SESSIONS.clear()
