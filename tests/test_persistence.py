"""Tests for the atomic, versioned persistence layer (Day 9).

The suite is organised around the two promises the module makes:

* **Atomicity** — a write either publishes the complete new content or leaves
  the previous file byte-for-byte untouched, and it never leaves a temporary
  file behind. Simulated with a failing writer, a failing ``os.replace``, a
  target that is a directory, and an unwritable parent.
* **Versioning** — every JSON document carries ``schema_version``, and the
  reader rejects a missing, malformed, newer or unsupported version instead of
  guessing at the payload.

The last section checks that the three real call sites — the organizer's
activity log, the portfolio summary CSV and the assistant's conversation log —
actually go through the new layer.
"""

from __future__ import annotations

import csv
import json
import os
import stat

import pytest

from error_catalogue import render_catalogue_markdown, write_catalogue
from errors import DatheonError, MissingFileError, PersistenceError, SchemaVersionError
from persistence import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_FIELD,
    SUPPORTED_SCHEMA_VERSIONS,
    TEMP_SUFFIX,
    WriteReceipt,
    atomic_append_text,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    atomic_write_with,
    check_schema_version,
    read_json_document,
    stamp_schema,
)


def _temp_leftovers(directory):
    """Return every staged temporary file still sitting in ``directory``."""
    return [item.name for item in directory.iterdir() if item.name.endswith(TEMP_SUFFIX)]


# --- plain text and bytes -------------------------------------------------
def test_atomic_write_text_creates_the_file(tmp_path):
    receipt = atomic_write_text(tmp_path / "note.txt", "hello\n")

    assert receipt.path.read_text(encoding="utf-8") == "hello\n"
    assert receipt.bytes_written == 6
    assert receipt.replaced_existing is False
    assert receipt.schema_version is None


def test_atomic_write_text_replaces_existing_content(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("old", encoding="utf-8")

    receipt = atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert receipt.replaced_existing is True


def test_atomic_write_text_creates_parent_directories(tmp_path):
    receipt = atomic_write_text(tmp_path / "a" / "b" / "note.txt", "x")

    assert receipt.path.is_file()


def test_atomic_write_text_leaves_no_temporary_file(tmp_path):
    atomic_write_text(tmp_path / "note.txt", "hello")

    assert _temp_leftovers(tmp_path) == []


def test_atomic_write_text_preserves_the_existing_file_mode(tmp_path):
    target = tmp_path / "secret.txt"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, 0o600)

    atomic_write_text(target, "new")

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_write_text_without_fsync_still_publishes(tmp_path):
    receipt = atomic_write_text(tmp_path / "note.txt", "hello", fsync=False)

    assert receipt.path.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_records_the_supplied_schema_version(tmp_path):
    receipt = atomic_write_text(tmp_path / "note.txt", "hello", schema_version=SCHEMA_VERSION)

    assert receipt.schema_version == SCHEMA_VERSION


def test_atomic_write_bytes_round_trips_binary_content(tmp_path):
    payload = bytes(range(256))

    receipt = atomic_write_bytes(tmp_path / "blob.bin", payload)

    assert receipt.path.read_bytes() == payload
    assert receipt.bytes_written == 256


def test_atomic_write_with_streams_through_a_callback(tmp_path):
    def writer(handle):
        handle.write("a,b\n")
        handle.write("1,2\n")

    receipt = atomic_write_with(tmp_path / "table.csv", writer)

    assert receipt.path.read_text(encoding="utf-8") == "a,b\n1,2\n"


# --- failure paths keep the old file --------------------------------------
def test_a_failing_writer_leaves_the_previous_file_untouched(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("original", encoding="utf-8")

    def explode(handle):
        handle.write("partial")
        raise ValueError("writer failed")

    with pytest.raises(ValueError):
        atomic_write_with(target, explode)

    assert target.read_text(encoding="utf-8") == "original"
    assert _temp_leftovers(tmp_path) == []


def test_a_failing_rename_leaves_the_previous_file_untouched(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("original", encoding="utf-8")

    def broken_replace(source, destination):
        raise OSError("rename refused")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(PersistenceError) as excinfo:
        atomic_write_text(target, "new")

    assert "could not publish" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == "original"
    assert _temp_leftovers(tmp_path) == []


def test_a_directory_target_is_reported_as_a_persistence_error(tmp_path):
    target = tmp_path / "note.txt"
    target.mkdir()

    with pytest.raises(PersistenceError):
        atomic_write_text(target, "new")


def test_an_unwritable_parent_is_reported_as_a_persistence_error(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(PersistenceError) as excinfo:
        atomic_write_text(blocker / "note.txt", "x")

    assert "could not stage a write" in str(excinfo.value)


def test_a_failing_binary_rename_is_reported_as_a_persistence_error(tmp_path, monkeypatch):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"original")

    def broken_replace(source, destination):
        raise OSError("rename refused")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(PersistenceError) as excinfo:
        atomic_write_bytes(target, b"new")

    assert "could not publish" in str(excinfo.value)
    assert target.read_bytes() == b"original"
    assert _temp_leftovers(tmp_path) == []


def test_a_failing_binary_flush_is_reported_as_a_persistence_error(tmp_path, monkeypatch):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"original")

    def broken_fsync(descriptor):
        raise OSError("fsync refused")

    monkeypatch.setattr("persistence.os.fsync", broken_fsync)

    with pytest.raises(PersistenceError) as excinfo:
        atomic_write_bytes(target, b"new")

    assert "could not write" in str(excinfo.value)
    assert target.read_bytes() == b"original"
    assert _temp_leftovers(tmp_path) == []


def test_a_failing_text_flush_is_reported_as_a_persistence_error(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("original", encoding="utf-8")

    def broken_fsync(descriptor):
        raise OSError("fsync refused")

    monkeypatch.setattr("persistence.os.fsync", broken_fsync)

    with pytest.raises(PersistenceError) as excinfo:
        atomic_write_text(target, "new")

    assert "could not write" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == "original"
    assert _temp_leftovers(tmp_path) == []


def test_an_interrupted_binary_write_is_cleaned_up(tmp_path, monkeypatch):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"original")

    def interrupt(temp, destination, *, fsync):
        raise KeyboardInterrupt

    monkeypatch.setattr("persistence._publish", interrupt)

    with pytest.raises(KeyboardInterrupt):
        atomic_write_bytes(target, b"new")

    assert target.read_bytes() == b"original"
    assert _temp_leftovers(tmp_path) == []


def test_a_persistence_error_from_the_rename_is_not_double_wrapped(tmp_path, monkeypatch):
    def refuse(source, destination):
        raise PersistenceError("refused by policy")

    monkeypatch.setattr(os, "replace", refuse)

    with pytest.raises(PersistenceError) as excinfo:
        atomic_write_text(tmp_path / "note.txt", "new")

    assert str(excinfo.value) == "refused by policy"
    assert _temp_leftovers(tmp_path) == []


def test_an_interrupted_write_is_propagated_and_cleaned_up(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("original", encoding="utf-8")

    def interrupt(handle):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        atomic_write_with(target, interrupt)

    assert target.read_text(encoding="utf-8") == "original"
    assert _temp_leftovers(tmp_path) == []


def test_persistence_error_is_a_datheon_error_and_an_os_error():
    error = PersistenceError("write failed")

    assert isinstance(error, DatheonError)
    assert isinstance(error, OSError)
    assert error.exit_code == 1


def test_schema_version_error_is_a_persistence_error():
    error = SchemaVersionError("bad version")

    assert isinstance(error, PersistenceError)
    assert error.code == "DT-3006"


# --- appending ------------------------------------------------------------
def test_atomic_append_text_creates_then_appends(tmp_path):
    target = tmp_path / "log.txt"

    atomic_append_text(target, "one\n")
    receipt = atomic_append_text(target, "two\n")

    assert target.read_text(encoding="utf-8") == "one\ntwo\n"
    assert receipt.replaced_existing is True


# --- stamping and reading versioned documents -----------------------------
def test_stamp_schema_puts_the_version_first():
    document = stamp_schema({"a": 1})

    assert list(document) == [SCHEMA_VERSION_FIELD, "a"]
    assert document[SCHEMA_VERSION_FIELD] == SCHEMA_VERSION


def test_stamp_schema_overrides_an_existing_version():
    document = stamp_schema({SCHEMA_VERSION_FIELD: 99, "a": 1})

    assert document[SCHEMA_VERSION_FIELD] == SCHEMA_VERSION


@pytest.mark.parametrize("bad_version", [True, False, "1", 1.5, None])
def test_stamp_schema_rejects_a_non_integer_version(bad_version):
    with pytest.raises(SchemaVersionError):
        stamp_schema({"a": 1}, bad_version)


def test_stamp_schema_rejects_a_version_below_one():
    with pytest.raises(SchemaVersionError):
        stamp_schema({"a": 1}, 0)


def test_atomic_write_json_round_trips(tmp_path):
    receipt = atomic_write_json(tmp_path / "doc.json", {"summary": {"moved": 2}})

    assert receipt.schema_version == SCHEMA_VERSION
    assert receipt.replaced_existing is False

    document = read_json_document(tmp_path / "doc.json")
    assert document[SCHEMA_VERSION_FIELD] == SCHEMA_VERSION
    assert document["summary"] == {"moved": 2}


def test_atomic_write_json_writes_compact_output_when_indent_is_none(tmp_path):
    receipt = atomic_write_json(tmp_path / "doc.json", {"a": 1}, indent=None)

    assert receipt.path.read_text(encoding="utf-8") == '{"schema_version": 1, "a": 1}\n'


def test_read_json_document_tolerates_a_bom_and_crlf(tmp_path):
    target = tmp_path / "doc.json"
    payload = json.dumps({SCHEMA_VERSION_FIELD: 1, "a": 1}).replace("\n", "\r\n")
    target.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))

    assert read_json_document(target)["a"] == 1


def test_read_json_document_requires_the_named_keys(tmp_path):
    atomic_write_json(tmp_path / "doc.json", {"a": 1})

    with pytest.raises(PersistenceError) as excinfo:
        read_json_document(tmp_path / "doc.json", require_keys=("a", "b"))

    assert "missing required key(s) b" in str(excinfo.value)


def test_read_json_document_reports_a_missing_file(tmp_path):
    with pytest.raises(MissingFileError):
        read_json_document(tmp_path / "nope.json")


def test_read_json_document_rejects_invalid_json(tmp_path):
    target = tmp_path / "doc.json"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(PersistenceError) as excinfo:
        read_json_document(target, field="activity log")

    assert "not valid JSON" in str(excinfo.value)
    assert "activity log" in str(excinfo.value)


def test_read_json_document_rejects_a_non_object_document(tmp_path):
    target = tmp_path / "doc.json"
    target.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(PersistenceError) as excinfo:
        read_json_document(target)

    assert "must contain a JSON object" in str(excinfo.value)


# --- schema version checks ------------------------------------------------
def test_check_schema_version_rejects_a_missing_header():
    with pytest.raises(SchemaVersionError) as excinfo:
        check_schema_version({"a": 1}, field="report")

    assert "no schema_version field" in str(excinfo.value)
    assert "report" in str(excinfo.value)


@pytest.mark.parametrize("bad_version", [True, "1", None, [1]])
def test_check_schema_version_rejects_a_non_integer_header(bad_version):
    with pytest.raises(SchemaVersionError):
        check_schema_version({SCHEMA_VERSION_FIELD: bad_version})


def test_check_schema_version_rejects_a_newer_document():
    with pytest.raises(SchemaVersionError) as excinfo:
        check_schema_version({SCHEMA_VERSION_FIELD: SCHEMA_VERSION + 1})

    assert "newer tool" in str(excinfo.value)


def test_check_schema_version_rejects_an_unsupported_old_document(monkeypatch):
    import persistence

    monkeypatch.setattr(persistence, "SUPPORTED_SCHEMA_VERSIONS", (SCHEMA_VERSION + 1,))

    with pytest.raises(SchemaVersionError) as excinfo:
        check_schema_version({SCHEMA_VERSION_FIELD: SCHEMA_VERSION})

    assert "is not readable" in str(excinfo.value)


def test_check_schema_version_accepts_every_supported_version():
    for version in SUPPORTED_SCHEMA_VERSIONS:
        assert check_schema_version({SCHEMA_VERSION_FIELD: version}) == version


def test_write_receipt_describes_itself(tmp_path):
    created = WriteReceipt(tmp_path / "a.json", 12, False, SCHEMA_VERSION)
    replaced = WriteReceipt(tmp_path / "a.json", 12, True, None)

    assert created.describe() == f"created {tmp_path / 'a.json'} (12 bytes, schema v1)"
    assert replaced.describe() == f"replaced {tmp_path / 'a.json'} (12 bytes)"


# --- real call sites ------------------------------------------------------
def test_organizer_log_round_trips_with_a_schema_version(tmp_path):
    from organizer import FileOrganizer, read_json_log, write_json_log

    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "a.pdf").write_text("x", encoding="utf-8")
    report = FileOrganizer().organize(tmp_path / "work")

    log_path = write_json_log(report, tmp_path / "run.json")
    document = read_json_log(log_path)

    assert document[SCHEMA_VERSION_FIELD] == SCHEMA_VERSION
    assert document["summary"]["moved"] == 1
    assert _temp_leftovers(tmp_path) == []


def test_organizer_log_rewrite_keeps_the_previous_file_on_failure(tmp_path, monkeypatch):
    from organizer import FileOrganizer, write_json_log

    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "a.pdf").write_text("x", encoding="utf-8")
    report = FileOrganizer().organize(tmp_path / "work")
    log_path = write_json_log(report, tmp_path / "run.json")
    before = log_path.read_text(encoding="utf-8")

    monkeypatch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("no")))

    with pytest.raises(PersistenceError):
        write_json_log(report, log_path)

    assert log_path.read_text(encoding="utf-8") == before


def test_portfolio_summary_is_written_atomically(tmp_path):
    from portfolio import Portfolio, write_summary_csv

    portfolio = Portfolio.from_prices({"AAPL": 150.0})
    portfolio.add("AAPL", 10, 100.0)

    out = write_summary_csv(portfolio, tmp_path / "summary.csv")

    with out.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[-1]["symbol"] == "TOTAL"
    assert _temp_leftovers(tmp_path) == []


def test_conversation_log_entries_carry_the_schema_version(tmp_path):
    from chatbot import ConversationLogger

    logger = ConversationLogger(tmp_path / "conv.jsonl")
    logger.log("user", "hello")
    logger.log("assistant", "hi", intent="greeting", confidence=0.5)

    lines = (tmp_path / "conv.jsonl").read_text(encoding="utf-8").strip().splitlines()
    entries = [json.loads(line) for line in lines]

    assert [entry[SCHEMA_VERSION_FIELD] for entry in entries] == [SCHEMA_VERSION] * 2
    assert entries[1]["intent"] == "greeting"
    assert _temp_leftovers(tmp_path) == []


def test_error_catalogue_is_written_atomically(tmp_path):
    target = tmp_path / "catalogue.md"

    written = write_catalogue(target)

    assert written.read_text(encoding="utf-8") == render_catalogue_markdown()
    assert _temp_leftovers(tmp_path) == []
