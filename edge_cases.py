"""Shared edge-case handling for the five Datheon internship CLIs.

Day 5 of the internship progression closes the failure modes a shell user can
actually hit — the ones that used to surface as an unhandled traceback:

* an **empty directory** (the organizer has nothing to do),
* a **permission error** reading a directory or an input file,
* a **name collision** when a destination file already exists,
* a **missing or oversized** input file,
* an **interrupted input stream** (``Ctrl-D``/EOF or ``Ctrl-C``), and
* a **broken output pipe** (``organizer . | head``).

Each helper turns one low-level failure into either a single, actionable
message or a graceful stop. They hold no project state, so they can be unit
tested directly and reused by every tool.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional, Union

__all__ = [
    "MAX_INPUT_FILE_BYTES",
    "InputClosed",
    "check_file_size",
    "guard_broken_pipe",
    "iter_files",
    "safe_input",
]

#: Largest input file the tools will read from disk (64 MiB). A larger file is
#: almost certainly a mistake — a log, a database dump, the wrong path — and is
#: rejected up front rather than being pulled into memory line by line.
MAX_INPUT_FILE_BYTES: int = 64 * 1024 * 1024


class InputClosed(Exception):
    """Raised by :func:`safe_input` when standard input ends or is interrupted.

    The interactive shells catch this to finish the session cleanly — the
    terminal equivalent of ``Ctrl-D`` (EOF) or ``Ctrl-C`` — instead of letting
    :class:`EOFError` or :class:`KeyboardInterrupt` escape as a traceback.
    """


def safe_input(prompt: str = "", *, default: Optional[str] = None) -> str:
    """Read one line from standard input, converting EOF/``Ctrl-C`` into a stop.

    Args:
        prompt: Text shown to the user before the read.
        default: Value returned when the stream is closed (EOF) instead of
            raising. When ``None`` (the default), a closed stream raises
            :class:`InputClosed`.

    Returns:
        The raw line typed by the user, without the trailing newline.

    Raises:
        InputClosed: If the stream is closed and no ``default`` was supplied, or
            if the read is interrupted with ``Ctrl-C``.
    """
    try:
        return input(prompt)
    except EOFError as exc:
        if default is not None:
            return default
        raise InputClosed("input stream closed (EOF)") from exc
    except KeyboardInterrupt as exc:
        raise InputClosed("input interrupted (Ctrl-C)") from exc


def check_file_size(
    path: Union[str, Path],
    *,
    max_bytes: int = MAX_INPUT_FILE_BYTES,
    field: str = "file",
) -> int:
    """Return a file's size in bytes, rejecting an unreasonably large input.

    Args:
        path: The file to inspect.
        max_bytes: Maximum accepted size in bytes. Defaults to
            :data:`MAX_INPUT_FILE_BYTES`.
        field: Field name used in the error message.

    Returns:
        The file size in bytes.

    Raises:
        FileNotFoundError: If ``path`` is not an existing file.
        ValueError: If the file is larger than ``max_bytes``.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"{field} not found: {file_path}")
    size = file_path.stat().st_size
    if size > max_bytes:
        raise ValueError(
            f"{field}: {file_path} is too large " f"({size:,} bytes > limit of {max_bytes:,} bytes)"
        )
    return size


def iter_files(directory: Union[str, Path]) -> List[Path]:
    """List the regular files directly inside ``directory``, sorted by name.

    Unlike a bare :meth:`pathlib.Path.iterdir`, an unreadable directory (a
    missing ``r`` bit, a ``chmod 000`` fixture) raises a
    :class:`PermissionError` that names the directory, so the CLI can report it
    as one actionable line instead of a traceback.

    Args:
        directory: The directory to scan.

    Returns:
        The regular files directly inside ``directory``, sorted by lower-case
        name. An empty list means the directory holds no files.

    Raises:
        NotADirectoryError: If ``directory`` is not a directory.
        PermissionError: If the directory's contents cannot be listed.
    """
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")
    try:
        entries = list(root.iterdir())
    except PermissionError as exc:
        raise PermissionError(f"cannot read directory (permission denied): {root}") from exc
    return sorted((entry for entry in entries if entry.is_file()), key=lambda p: p.name.lower())


def guard_broken_pipe() -> None:
    """Point ``stdout`` at the null device after a broken pipe.

    When a tool's output is piped into a consumer that exits early (for example
    ``organizer . | head``), the next write raises :class:`BrokenPipeError`.
    The CLI maps that onto a clean exit; this helper additionally redirects
    ``stdout`` to :data:`os.devnull` so the interpreter does not emit a second
    "Exception ignored" message while flushing at shutdown.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):  # pragma: no cover - platform dependent
        pass
