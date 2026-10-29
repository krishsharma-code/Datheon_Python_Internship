"""Structured, machine-readable log records for the five Datheon CLIs (Day 10).

Day 8 gave every tool one logging setup: ``--log-level``, a console handler on
``stderr`` and a rotating file handler. That answered *"is there a trace?"* but
not *"can a program read the trace?"*. A text line like::

    processed=12 moved=12 planned=0 errors=0 duplicates=0

is pleasant to read and painful to aggregate: a log shipper has to parse prose,
nothing ties the lines of one invocation together, and no line says how long the
run took or whether it succeeded.

This module supplies the missing layer:

* **JSON records.** :class:`JsonFormatter` renders each
  :class:`logging.LogRecord` as a single-line JSON object with a stable schema
  (``schema_version``, ``ts``, ``level``, ``logger``, ``message``, ``source``)
  plus whatever structured fields the caller attached.
* **Run IDs.** :func:`begin_run` opens a :class:`RunContext` — a run ID, a
  project name, a start timestamp and a monotonic start — and stores it in a
  :class:`~contextvars.ContextVar`. Every record emitted while that context is
  current carries the same ``run_id``, so ``start`` and ``finish`` correlate
  without the caller threading an identifier through five call sites.
* **Durations.** Each record carries ``duration_ms``: the elapsed time since
  the run started. The ``run_finish`` record therefore reports the total
  wall-clock cost of the invocation, and any record in between reports its
  offset — enough to find the slow phase without a profiler (Day 12's job).
* **Outcomes.** :meth:`RunContext.finish` records ``success``, ``error``,
  ``interrupted`` or ``incomplete``. :func:`~cli_contract.run_cli` decides which
  one applies, so a failing cron run is one ``jq`` filter away from a pager.

The JSON format is **opt-in** — ``--json-logs`` — and replaces only the
*formatter*; the handlers, the level, the file path, the rotation policy and the
opt-in default all stay exactly as Day 8 defined them, and the text format stays
the default so a human tailing a log is never punished.

Typical use::

    from logging_config import add_logging_flags, configure_logging_from_args

    parser = add_logging_flags(build_parser("organizer", "..."))
    args = parser.parse_args(argv)
    session = configure_logging_from_args("organizer", args)  # --json-logs honoured
    session.logger.info("processed=%d", 12, extra=structured_extra(processed=12))
    session.finish("success")

and a consumer reads it with::

    jq -c 'select(.run_outcome == "error") | {run_id, message, fields}' organizer.log
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Mapping, Optional

from errors import DatheonError

__all__ = [
    "FIELDS_ATTRIBUTE",
    "JSON_SCHEMA_VERSION",
    "OUTCOMES",
    "RUN_ID_ENV",
    "JsonFormatter",
    "RunContext",
    "begin_run",
    "current_run",
    "end_run",
    "log_error",
    "new_run_id",
    "structured_extra",
]

#: Version of the JSON record schema. Bumped when a consumer-visible key is
#: renamed or removed, so a log pipeline can branch on it instead of guessing.
JSON_SCHEMA_VERSION: int = 1

#: Environment variable that pins the run ID for every tool in a process tree —
#: a cron wrapper, a CI job or a shell script can set it once.
RUN_ID_ENV: str = "DATHEON_RUN_ID"

#: ``logging`` attribute that carries caller-supplied structured fields. Using
#: ``extra={FIELDS_ATTRIBUTE: {...}}`` (or :func:`structured_extra`) keeps the
#: caller's keys out of the reserved top-level namespace.
FIELDS_ATTRIBUTE: str = "datheon_fields"

#: The terminal states a run can reach, in the order they are reported.
OUTCOMES: tuple[str, ...] = ("success", "error", "interrupted", "incomplete")

#: The run context currently in force for this thread or asyncio task.
_CURRENT_RUN: ContextVar[Optional["RunContext"]] = ContextVar("datheon_current_run", default=None)


def new_run_id(prefix: str = "run") -> str:
    """Mint a sortable, collision-resistant run identifier.

    The shape is ``<prefix>-<UTC stamp>-<random>``, for example
    ``run-20261029T033104Z-9f3c1a2b``. Sorting the IDs sorts the runs, and the
    random suffix keeps two runs that start in the same second distinct.

    Args:
        prefix: Leading label; defaults to ``"run"``.

    Returns:
        The new run ID.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass
class RunContext:
    """The identity and timing of one tool invocation.

    A context is created by :func:`begin_run` and closed by
    :meth:`RunContext.finish`. While it is current, every JSON record emitted
    through :class:`JsonFormatter` carries its ``run_id``, ``project``,
    ``duration_ms`` and ``run_outcome``.

    Attributes:
        run_id: Correlation ID shared by every record of this run.
        project: Program name the run belongs to (``"organizer"``, …).
        started_at: Timezone-aware wall-clock start, used for the record's
            ``started`` field.
        started_monotonic: :func:`time.monotonic` start, used for durations so
            a system clock adjustment cannot make them negative.
        outcome: One of :data:`OUTCOMES`; ``"running"`` until
            :meth:`finish` is called.
        sequence: Monotonic record counter, exposed as ``seq`` so a consumer
            can order records that share a timestamp.
    """

    run_id: str
    project: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_monotonic: float = field(default_factory=time.monotonic)
    outcome: str = "running"
    sequence: Iterator[int] = field(default_factory=lambda: itertools.count(1), repr=False)

    def elapsed_ms(self) -> float:
        """Return the milliseconds elapsed since the run started.

        Returns:
            A non-negative float, rounded to three decimals by the formatter.
        """
        return max(0.0, (time.monotonic() - self.started_monotonic) * 1000.0)

    def started_iso(self) -> str:
        """Return the start time as an ISO-8601 string with the UTC offset.

        Returns:
            For example ``"2026-10-29T09:01:04.512+05:30"``.
        """
        return self.started_at.astimezone().isoformat(timespec="milliseconds")

    def finish(self, outcome: str = "success") -> "RunContext":
        """Record the terminal state of the run.

        Args:
            outcome: One of :data:`OUTCOMES`. An unknown value is recorded
                verbatim rather than rejected — losing the finish record over a
                typo in a status string would be worse than an odd value.

        Returns:
            This same context, for chaining.
        """
        self.outcome = outcome
        return self

    def fields(self) -> Dict[str, Any]:
        """Return the context fields a JSON record should carry.

        Returns:
            A mapping with ``run_id``, ``project``, ``started``,
            ``duration_ms`` and ``run_outcome``.
        """
        return {
            "run_id": self.run_id,
            "project": self.project,
            "started": self.started_iso(),
            "duration_ms": round(self.elapsed_ms(), 3),
            "run_outcome": self.outcome,
        }


def begin_run(project: str, run_id: Optional[str] = None) -> RunContext:
    """Open a run context and make it current.

    Args:
        project: Program name the run belongs to.
        run_id: Explicit correlation ID; when ``None`` the ``DATHEON_RUN_ID``
            environment variable is used if set, otherwise
            :func:`new_run_id` mints one.

    Returns:
        The newly current :class:`RunContext`.
    """
    resolved = (run_id or os.environ.get(RUN_ID_ENV, "")).strip() or new_run_id()
    context = RunContext(run_id=resolved, project=project)
    _CURRENT_RUN.set(context)
    return context


def current_run() -> Optional[RunContext]:
    """Return the run context in force, if any.

    Returns:
        The current :class:`RunContext`, or ``None`` when no run is open — for
        example in a library that logs without going through a CLI.
    """
    return _CURRENT_RUN.get()


def end_run(run: Optional[RunContext] = None) -> None:
    """Clear the run context, optionally only if ``run`` is the current one.

    Args:
        run: The context expected to be current. ``None`` clears whatever is
            current; passing a specific context makes the call safe from a
            session that was already superseded by a newer run.
    """
    active = _CURRENT_RUN.get()
    if run is None or active is run:
        _CURRENT_RUN.set(None)


def structured_extra(**fields: Any) -> Dict[str, Any]:
    """Build the ``extra=`` mapping that attaches structured fields to a record.

    Args:
        **fields: Arbitrary JSON-serialisable key/value pairs. Values that are
            not serialisable are rendered with :func:`str` by the formatter
            rather than dropping the record.

    Returns:
        A mapping suitable for ``logger.info(msg, extra=structured_extra(...))``.

    Example:
        >>> structured_extra(processed=12)["datheon_fields"]
        {'processed': 12}
    """
    return {FIELDS_ATTRIBUTE: dict(fields)}


def log_error(
    logger: logging.Logger,
    exc: BaseException,
    *,
    message: Optional[str] = None,
    level: int = logging.ERROR,
    **fields: Any,
) -> None:
    """Log an exception as a structured record.

    A :class:`~errors.DatheonError` contributes its full
    :meth:`~errors.DatheonError.to_dict` payload — code, type, message, hint,
    context and exit code — so the log line a consumer sees is the same
    machine-readable description the CLI prints. Any other exception is
    reduced to its type and message.

    Args:
        logger: The logger to write through.
        exc: The exception to describe.
        message: Record message; defaults to ``"<type>: <message>"``.
        level: Logging level; defaults to :data:`logging.ERROR`.
        **fields: Extra structured fields, merged beside the ``error`` payload.
    """
    if isinstance(exc, DatheonError):
        payload: Dict[str, Any] = exc.to_dict()
    else:
        payload = {"type": type(exc).__name__, "message": str(exc)}
    logger.log(
        level,
        message or f"{payload.get('type', 'Error')}: {payload.get('message', '')}",
        extra=structured_extra(error=payload, **fields),
    )


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as one line of JSON.

    The schema is stable and versioned (:data:`JSON_SCHEMA_VERSION`)::

        {
          "schema_version": 1,
          "ts": "2026-10-29T09:03:12.418+05:30",
          "level": "INFO",
          "levelno": 20,
          "logger": "organizer",
          "message": "processed=12",
          "source": "main.py:154",
          "function": "main",
          "run_id": "run-20261029T033104Z-9f3c1a2b",
          "project": "organizer",
          "started": "2026-10-29T09:03:12.377+05:30",
          "duration_ms": 41.207,
          "run_outcome": "running",
          "seq": 3,
          "fields": {"processed": 12}
        }

    ``run_id``, ``project``, ``duration_ms`` and ``run_outcome`` appear only
    when a :class:`RunContext` is current (or attached to the record as
    ``datheon_run``). ``exception`` appears only for a record carrying
    ``exc_info``. Caller fields are always nested under ``fields`` so they can
    never shadow a reserved key.
    """

    def __init__(
        self,
        *,
        include_source: bool = True,
        schema_version: int = JSON_SCHEMA_VERSION,
    ) -> None:
        """Initialise the formatter.

        Args:
            include_source: Whether to emit ``source`` and ``function`` keys.
                They cost a few bytes per record and save minutes when reading
                a stack trace back to its origin, so the default is ``True``.
            schema_version: Schema version stamped on every record; overridable
                so a downstream test can assert consumer behaviour.
        """
        super().__init__()
        self.include_source = include_source
        self.schema_version = schema_version

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a single JSON object.

        Args:
            record: The record to render.

        Returns:
            A JSON string with no embedded newlines, safe for a line-oriented
            log pipeline.
        """
        return json.dumps(self.build_payload(record), default=str, ensure_ascii=False)

    def build_payload(self, record: logging.LogRecord) -> Dict[str, Any]:
        """Build the mapping that :meth:`format` serialises.

        Exposed separately so a test (or an embedding application that wants to
        ship records to a different sink) can inspect the structure without
        parsing JSON back.

        Args:
            record: The record to render.

        Returns:
            An ordered mapping of the record's fields.
        """
        payload: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "levelno": record.levelno,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if self.include_source:
            payload["source"] = f"{record.filename}:{record.lineno}"
            payload["function"] = record.funcName

        run: Optional[RunContext] = getattr(record, "datheon_run", None) or current_run()
        if run is not None:
            payload.update(run.fields())
            # Both handlers format the same record; number it once so the
            # console copy and the file copy agree on ``seq``.
            seq = getattr(record, "datheon_seq", None)
            if seq is None:
                seq = next(run.sequence)
                setattr(record, "datheon_seq", seq)
            payload["seq"] = seq

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        extra = getattr(record, FIELDS_ATTRIBUTE, None)
        if isinstance(extra, Mapping):
            payload["fields"] = {key: value for key, value in extra.items()}

        return payload
