"""Atomic, versioned persistence for the Datheon tools (Day 9).

Days 1–8 hardened what the tools *read*: one command line, one validation
layer, one edge-case policy, one dataset loader, one exception taxonomy and one
logging setup. Everything the tools *write* — the portfolio summary CSV, the
organizer's JSON activity log, the assistant's JSONL conversation log, the
generated error catalogue — still went through a plain ``open(path, "w")``.

A plain truncating write has two failure modes that no amount of validation
upstream can fix:

* **A torn file.** ``open(..., "w")`` empties the target *before* writing. If
  the process is killed, the disk fills, or the writer raises halfway through,
  the destination is left empty or half-written — the old, good copy is gone.
* **An unreadable schema.** Nothing in the payload said which version of the
  format produced it, so a file written by last week's tool was
  indistinguishable from one written by today's.

This module closes both gaps:

* :func:`atomic_write_text`, :func:`atomic_write_bytes`,
  :func:`atomic_write_json` and :func:`atomic_write_with` write to a temporary
  file **in the destination directory**, ``flush`` and ``fsync`` it, then
  publish it with a single :func:`os.replace`. The rename is atomic on POSIX and
  on Windows (``ReplaceFile`` semantics), so a reader sees either the complete
  old file or the complete new one — never a mixture. Any failure removes the
  temporary file and leaves the target untouched.
* :func:`stamp_schema` adds the :data:`SCHEMA_VERSION_FIELD` header
  (``"schema_version": 1``) to a JSON payload, and :func:`read_json_document`
  validates it on the way back in. A document with no version, a version that
  is not an integer, or a version written by a *newer* tool is rejected with a
  :class:`~errors.SchemaVersionError` instead of being misread.

Versioning policy — deliberately simple and honest about its limits:

* :data:`SCHEMA_VERSION` is the version this code *writes*.
* :data:`SUPPORTED_SCHEMA_VERSIONS` is the set it can *read*. It starts as just
  ``(1,)``; when a format changes, the writer bumps :data:`SCHEMA_VERSION`, the
  new number is added here, and a reader that must understand both keeps the
  old number too. A document older than every supported version is rejected
  with a pointer to the tool that can migrate it, rather than guessed at.

Typical use::

    from persistence import atomic_write_json, read_json_document

    atomic_write_json("run.json", {"summary": {...}, "records": [...]})
    document = read_json_document("run.json", field="activity log")
    document["schema_version"]  # -> 1
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, TextIO, Tuple, Union

from dataset_loader import read_text_robust
from errors import DatheonError, PersistenceError, SchemaVersionError

__all__ = [
    "DEFAULT_ENCODING",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_FIELD",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TEMP_SUFFIX",
    "WriteReceipt",
    "atomic_append_text",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "atomic_write_with",
    "check_schema_version",
    "read_json_document",
    "stamp_schema",
]

#: The schema version this module writes into every document it produces.
SCHEMA_VERSION: int = 1

#: Schema versions this module can read back. Newest last.
SUPPORTED_SCHEMA_VERSIONS: Tuple[int, ...] = (1,)

#: Key that carries the schema version inside a JSON document.
SCHEMA_VERSION_FIELD: str = "schema_version"

#: Suffix of the temporary file a write is staged in before it is published.
TEMP_SUFFIX: str = ".tmp"

#: Encoding used for every file this module writes.
DEFAULT_ENCODING: str = "utf-8"


@dataclass(frozen=True)
class WriteReceipt:
    """What a completed atomic write did.

    Attributes:
        path: The destination that now holds the new content.
        bytes_written: Size of the published file, in bytes.
        replaced_existing: ``True`` when a file already existed at ``path`` and
            was replaced, ``False`` when the file was created.
        schema_version: The schema version stamped into the document, or
            ``None`` for the plain-text and byte writers.
    """

    path: Path
    bytes_written: int
    replaced_existing: bool
    schema_version: Optional[int] = None

    def describe(self) -> str:
        """Return a one-line human summary of the write."""
        action = "replaced" if self.replaced_existing else "created"
        version = f", schema v{self.schema_version}" if self.schema_version is not None else ""
        return f"{action} {self.path} ({self.bytes_written} bytes{version})"


# -- internals -------------------------------------------------------------
def _unlink_quietly(path: Path) -> None:
    """Remove ``path`` if it exists, swallowing any error.

    Used on the failure path: a temporary file that cannot be deleted must not
    mask the exception that is already propagating.

    Args:
        path: The temporary file to remove.
    """
    try:
        path.unlink()
    except OSError:  # pragma: no cover - the original error matters more
        pass


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry to disk so a completed rename survives a crash.

    Best effort: platforms that cannot open or ``fsync`` a directory (Windows)
    simply skip the step, because the data file itself was already synced.

    Args:
        directory: The directory that received the renamed file.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(directory), flags)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - platform dependent
        pass
    finally:
        os.close(descriptor)


def _create_temp(target: Path) -> Path:
    """Create an empty temporary file in ``target``'s directory.

    The temporary file must live in the same directory as the destination: a
    rename across filesystems is not atomic, and :func:`os.replace` would fall
    back to a copy.

    Args:
        target: The destination the temporary file will be published to.

    Returns:
        The path of the created temporary file.

    Raises:
        PersistenceError: If the parent directory cannot be created or is not
            writable.
    """
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=TEMP_SUFFIX, dir=str(parent)
        )
    except OSError as exc:
        raise PersistenceError(
            f"could not stage a write for {target}: {exc}",
            hint="check that the parent directory exists, is writable, and the disk has free space",
            context={"path": str(target), "parent": str(parent)},
        ) from exc
    os.close(descriptor)
    return Path(name)


def _publish(temp: Path, target: Path, *, fsync: bool) -> Tuple[int, bool]:
    """Publish a fully written temporary file as ``target``.

    The existing file's permission bits are copied onto the replacement first,
    so an atomic write never silently widens or narrows a file's mode.

    Args:
        temp: The fully written and synced temporary file.
        target: The destination path.
        fsync: Whether to flush the directory entry after the rename.

    Returns:
        A ``(bytes_written, replaced_existing)`` pair.

    Raises:
        PersistenceError: If the rename fails — for example when the target is
            a directory, or the file is held open on Windows.
    """
    size = temp.stat().st_size
    replaced = target.exists()
    if replaced:
        try:
            os.chmod(temp, stat.S_IMODE(target.stat().st_mode))
        except OSError:  # pragma: no cover - exotic filesystems
            pass
    try:
        os.replace(temp, target)
    except PersistenceError:
        raise
    except OSError as exc:
        raise PersistenceError(
            f"could not publish {target}: {exc}",
            hint="close the file if another program has it open, then retry",
            context={"path": str(target), "temp": str(temp)},
        ) from exc
    if fsync:
        _fsync_directory(target.parent)
    return size, replaced


# -- writers ---------------------------------------------------------------
def atomic_write_bytes(
    path: Union[str, Path],
    data: bytes,
    *,
    fsync: bool = True,
) -> WriteReceipt:
    """Write ``data`` to ``path`` atomically.

    Args:
        path: Destination file. Parent directories are created as needed.
        data: The bytes to publish.
        fsync: When ``True`` (the default), flush the file and its directory
            entry to disk before returning. Passing ``False`` trades durability
            for speed and is only appropriate in tests and scratch writes.

    Returns:
        A :class:`WriteReceipt` describing the write.

    Raises:
        PersistenceError: If the write cannot be staged or published. The
            destination is left exactly as it was.
    """
    target = Path(path).expanduser()
    temp = _create_temp(target)
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        size, replaced = _publish(temp, target, fsync=fsync)
    except DatheonError:
        _unlink_quietly(temp)
        raise
    except OSError as exc:
        _unlink_quietly(temp)
        raise PersistenceError(
            f"could not write {target}: {exc}",
            hint="check the free space and the permissions on the destination directory",
            context={"path": str(target)},
        ) from exc
    except BaseException:
        _unlink_quietly(temp)
        raise
    return WriteReceipt(
        path=target, bytes_written=size, replaced_existing=replaced, schema_version=None
    )


def atomic_write_with(
    path: Union[str, Path],
    writer: Callable[[TextIO], None],
    *,
    encoding: str = DEFAULT_ENCODING,
    newline: str = "",
    fsync: bool = True,
) -> WriteReceipt:
    """Write a text file atomically, letting a callback produce the content.

    This is the escape hatch for formats that need a stream rather than a
    string — :class:`csv.DictWriter`, for instance. The callback receives an
    open handle on the *temporary* file; it is called exactly once, and
    whatever it raises aborts the write with the destination untouched.

    Args:
        path: Destination file. Parent directories are created as needed.
        writer: Callback that writes the content to the supplied handle.
        encoding: Text encoding for the file.
        newline: Newline translation mode; ``""`` (the default) writes ``\\n``
            verbatim, which is what :mod:`csv` expects.
        fsync: Whether to flush the file and its directory entry to disk.

    Returns:
        A :class:`WriteReceipt` describing the write.

    Raises:
        PersistenceError: If the write cannot be staged or published.
    """
    target = Path(path).expanduser()
    temp = _create_temp(target)
    try:
        with temp.open("w", encoding=encoding, newline=newline) as handle:
            writer(handle)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        size, replaced = _publish(temp, target, fsync=fsync)
    except DatheonError:
        _unlink_quietly(temp)
        raise
    except OSError as exc:
        _unlink_quietly(temp)
        raise PersistenceError(
            f"could not write {target}: {exc}",
            hint="check the free space and the permissions on the destination directory",
            context={"path": str(target)},
        ) from exc
    except BaseException:
        _unlink_quietly(temp)
        raise
    return WriteReceipt(
        path=target, bytes_written=size, replaced_existing=replaced, schema_version=None
    )


def atomic_write_text(
    path: Union[str, Path],
    text: str,
    *,
    encoding: str = DEFAULT_ENCODING,
    newline: str = "",
    fsync: bool = True,
    schema_version: Optional[int] = None,
) -> WriteReceipt:
    """Write ``text`` to ``path`` atomically.

    Args:
        path: Destination file. Parent directories are created as needed.
        text: The text to publish.
        encoding: Text encoding for the file.
        newline: Newline translation mode; ``""`` writes ``\\n`` verbatim.
        fsync: Whether to flush the file and its directory entry to disk.
        schema_version: Recorded on the returned receipt when the text is a
            versioned document; it does not alter the content.

    Returns:
        A :class:`WriteReceipt` describing the write.

    Raises:
        PersistenceError: If the write cannot be staged or published.
    """
    receipt = atomic_write_with(
        path,
        lambda handle: handle.write(text),
        encoding=encoding,
        newline=newline,
        fsync=fsync,
    )
    return WriteReceipt(
        path=receipt.path,
        bytes_written=receipt.bytes_written,
        replaced_existing=receipt.replaced_existing,
        schema_version=schema_version,
    )


def atomic_append_text(
    path: Union[str, Path],
    text: str,
    *,
    encoding: str = DEFAULT_ENCODING,
    fsync: bool = True,
) -> WriteReceipt:
    """Append ``text`` to a file by rewriting it atomically.

    A true append cannot be atomic: ``O_APPEND`` guarantees the *offset* is
    shared, not that a multi-byte write lands in one piece, so a crash can leave
    a half-written line at the end of the file. This helper instead reads the
    current content and publishes old-plus-new in a single
    :func:`os.replace`, which is atomic. The cost is O(size) per append, which
    is the right trade for the small logs this repository writes (a
    conversation log of a few thousand short lines).

    Args:
        path: Destination file; created when absent.
        text: The text to append.
        encoding: Text encoding for the file.
        fsync: Whether to flush the file and its directory entry to disk.

    Returns:
        A :class:`WriteReceipt` describing the write.

    Raises:
        MissingFileError: If ``path`` exists but cannot be read.
        PersistenceError: If the write cannot be staged or published.
    """
    target = Path(path).expanduser()
    existing = ""
    if target.is_file():
        existing = read_text_robust(target, field="append target").text
    return atomic_write_text(target, existing + text, encoding=encoding, fsync=fsync)


# -- versioned JSON --------------------------------------------------------
def stamp_schema(
    payload: Mapping[str, Any],
    schema_version: int = SCHEMA_VERSION,
) -> Dict[str, Any]:
    """Return a copy of ``payload`` carrying the schema-version header.

    The header is placed first so it is the first key in the rendered JSON,
    which makes the version obvious to anyone who opens the file.

    Args:
        payload: The document body. Its ``schema_version`` key, if any, is
            replaced.
        schema_version: The version to record; defaults to
            :data:`SCHEMA_VERSION`.

    Returns:
        A new dictionary: ``{"schema_version": <n>, **payload}``.

    Raises:
        SchemaVersionError: If ``schema_version`` is not a positive integer
            (``bool`` is rejected even though it is an ``int`` subclass).
    """
    _require_version_value(schema_version, field="schema_version", path=None)
    document: Dict[str, Any] = {SCHEMA_VERSION_FIELD: schema_version}
    for key, value in payload.items():
        if key != SCHEMA_VERSION_FIELD:
            document[key] = value
    return document


def _require_version_value(value: Any, *, field: str, path: Optional[Path]) -> int:
    """Validate a schema-version value and return it as an ``int``.

    Args:
        value: The candidate version.
        field: Human name of the document, used in the error message.
        path: Optional document path, used in the error message.

    Returns:
        The version as a plain :class:`int`.

    Raises:
        SchemaVersionError: If ``value`` is not a positive integer.
    """
    where = f" in {path}" if path is not None else ""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaVersionError(
            f"{field}{where}: {SCHEMA_VERSION_FIELD} must be an integer, found "
            f"{type(value).__name__} ({value!r})",
            hint=f"write the document through persistence.atomic_write_json, which stamps "
            f"{SCHEMA_VERSION_FIELD}={SCHEMA_VERSION}",
            context={"field": field, "path": str(path) if path is not None else None},
        )
    if value < 1:
        raise SchemaVersionError(
            f"{field}{where}: {SCHEMA_VERSION_FIELD}={value} is not a positive version",
            hint="schema versions start at 1; re-create the document with the current tool",
            context={"field": field, "path": str(path) if path is not None else None},
        )
    return value


def check_schema_version(
    document: Mapping[str, Any],
    *,
    field: str = "document",
    path: Optional[Union[str, Path]] = None,
) -> int:
    """Validate the schema-version header of a loaded document.

    Three conditions are rejected: a missing header, a header that is not a
    positive integer, and a version outside
    :data:`SUPPORTED_SCHEMA_VERSIONS` — with a distinct message for "written by
    a newer tool" and "too old to read".

    Args:
        document: The decoded JSON object.
        field: Human name of the document, used in the error message.
        path: Optional document path, used in the error message.

    Returns:
        The validated schema version.

    Raises:
        SchemaVersionError: If the header is absent or unusable.
    """
    location = Path(path) if path is not None else None
    where = f" in {location}" if location is not None else ""
    if SCHEMA_VERSION_FIELD not in document:
        raise SchemaVersionError(
            f"{field}{where}: no {SCHEMA_VERSION_FIELD} field — the document predates "
            f"versioned persistence",
            hint="re-create the document with the current tool, or add "
            f'"{SCHEMA_VERSION_FIELD}": {SCHEMA_VERSION} to its top level',
            context={"field": field, "path": str(location) if location else None},
        )
    version = _require_version_value(document[SCHEMA_VERSION_FIELD], field=field, path=location)
    if version > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"{field}{where}: schema version {version} was written by a newer tool "
            f"(this one reads up to {SCHEMA_VERSION})",
            hint="upgrade the Datheon tools, or re-create the document with this version",
            context={
                "field": field,
                "version": version,
                "supported": list(SUPPORTED_SCHEMA_VERSIONS),
            },
        )
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(str(item) for item in SUPPORTED_SCHEMA_VERSIONS)
        raise SchemaVersionError(
            f"{field}{where}: schema version {version} is not readable "
            f"(supported: {supported})",
            hint="migrate the document with the tool that produced it, then re-run",
            context={
                "field": field,
                "version": version,
                "supported": list(SUPPORTED_SCHEMA_VERSIONS),
            },
        )
    return version


def atomic_write_json(
    path: Union[str, Path],
    payload: Mapping[str, Any],
    *,
    schema_version: int = SCHEMA_VERSION,
    indent: int = 2,
    sort_keys: bool = False,
    fsync: bool = True,
) -> WriteReceipt:
    """Write a JSON document atomically, with its schema version stamped in.

    Args:
        path: Destination file. Parent directories are created as needed.
        payload: The document body. A ``schema_version`` key, if present, is
            overwritten by ``schema_version``.
        schema_version: Version to record; defaults to :data:`SCHEMA_VERSION`.
        indent: Indentation width; ``None`` writes a single compact line.
        sort_keys: Whether to sort object keys in the output.
        fsync: Whether to flush the file and its directory entry to disk.

    Returns:
        A :class:`WriteReceipt` carrying the stamped ``schema_version``.

    Raises:
        SchemaVersionError: If ``schema_version`` is not a positive integer.
        PersistenceError: If the write cannot be staged or published.
    """
    document = stamp_schema(payload, schema_version)
    text = json.dumps(document, indent=indent, sort_keys=sort_keys, ensure_ascii=False) + "\n"
    return atomic_write_text(path, text, fsync=fsync, schema_version=schema_version)


def read_json_document(
    path: Union[str, Path],
    *,
    field: str = "document",
    require_keys: Sequence[str] = (),
) -> Dict[str, Any]:
    """Read a versioned JSON document and validate its envelope.

    The bytes are decoded through :func:`dataset_loader.read_text_robust`, so a
    UTF-8 BOM or a CRLF-authored file still loads. The result must be a JSON
    object carrying a supported :data:`SCHEMA_VERSION_FIELD`.

    Args:
        path: The document to read.
        field: Human name of the document, used in error messages.
        require_keys: Top-level keys the document must contain — a cheap
            structural check that catches a truncated or hand-edited file.

    Returns:
        The decoded document, including its ``schema_version`` key.

    Raises:
        MissingFileError: If ``path`` is not an existing file.
        AccessDeniedError: If ``path`` cannot be read.
        PersistenceError: If the content is not valid JSON, or is not a JSON
            object, or lacks a key named in ``require_keys``.
        SchemaVersionError: If the schema version is missing, malformed, newer
            than this code, or older than every supported version.
    """
    document_path = Path(path)
    source = read_text_robust(document_path, field=field)
    try:
        document = json.loads(source.text)
    except json.JSONDecodeError as exc:
        raise PersistenceError(
            f"{field} is not valid JSON: {document_path} (line {exc.lineno}: {exc.msg})",
            hint="the file is truncated or hand-edited; re-create it with the tool that writes it",
            context={"field": field, "path": str(document_path), "line": exc.lineno},
        ) from exc
    if not isinstance(document, dict):
        raise PersistenceError(
            f"{field} must contain a JSON object, found {type(document).__name__}: "
            f"{document_path}",
            hint=f'wrap the payload in an object with a "{SCHEMA_VERSION_FIELD}" field',
            context={"field": field, "path": str(document_path)},
        )
    check_schema_version(document, field=field, path=document_path)
    missing = [key for key in require_keys if key not in document]
    if missing:
        raise PersistenceError(
            f"{field} is missing required key(s) {', '.join(missing)}: {document_path}",
            hint="the document was written by a different tool, or truncated; re-create it",
            context={"field": field, "path": str(document_path), "missing": missing},
        )
    return document
