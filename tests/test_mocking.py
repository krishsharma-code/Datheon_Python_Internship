"""Tests for the Day 16 external-I/O isolation policy.

Everything the suite relies on to stay hermetic is tested here — the doubles in
``tests/io_fakes.py`` *and* the autouse fixtures in ``conftest.py``. Testing the
guard matters as much as testing the code: a guard nobody verified is a guard
that silently stops working the day a library changes its call path.

The contract:

* :class:`~tests.io_fakes.FakeClock` is deterministic, monotonic and refuses to
  run backwards;
* a frozen clock installed on a module drives the timestamps that module
  produces — proven against ``structured_logging`` (run IDs, durations) and the
  assistant's conversation log;
* :class:`~tests.io_fakes.NetworkGuard` blocks the socket layer *and* the
  higher-level clients built on it, while still allowing local computation;
* :class:`~tests.io_fakes.FakeFileSystem` feeds the dataset loaders from memory,
  including BOM and CRLF fixtures, with no file on disk;
* the autouse fixtures keep ``HOME``, the working directory and the log
  directory inside the pytest sandbox.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import structured_logging
from chatbot import ConversationLogger
from dataset_loader import read_text_robust
from errors import MissingFileError
from tests.io_fakes import (
    FakeClock,
    FakeFileSystem,
    NetworkGuard,
    RealIOAttempted,
    fake_date_class,
    fake_datetime_class,
    install_fake_clock,
    install_fake_date,
    probe_loopback_connect,
    probe_network,
    probe_socketpair,
)


# --- FakeClock ---------------------------------------------------------
def test_fake_clock_starts_at_a_fixed_moment():
    clock = FakeClock()
    assert clock.now(timezone.utc) == datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    assert clock.monotonic() == 0.0
    assert clock.iso().startswith("2026-11-04T09:00")


def test_fake_clock_advance_moves_wall_and_monotonic_time():
    clock = FakeClock()
    start = clock.now(timezone.utc)
    clock.advance(90)
    assert (clock.now(timezone.utc) - start).total_seconds() == pytest.approx(90.0)
    assert clock.monotonic() == pytest.approx(90.0)
    assert clock.timestamp() == pytest.approx(start.timestamp() + 90)


def test_fake_clock_advance_is_chainable_and_cumulative():
    clock = FakeClock()
    assert clock.advance(1).advance(2).monotonic() == pytest.approx(3.0)


def test_fake_clock_refuses_to_run_backwards():
    with pytest.raises(ValueError):
        FakeClock().advance(-1)


def test_fake_clock_now_without_timezone_is_naive():
    assert FakeClock().now().tzinfo is None


def test_fake_clock_utcnow_is_naive_utc():
    stamp = FakeClock().utcnow()
    assert stamp.tzinfo is None
    assert (stamp.hour, stamp.minute) == (9, 0)


def test_fake_clock_set_accepts_an_iso_string():
    clock = FakeClock()
    clock.set("2026-12-25T00:00:00+00:00")
    assert clock.today() == date(2026, 12, 25)


def test_fake_clock_set_rejects_a_naive_moment():
    with pytest.raises(ValueError):
        FakeClock().set(datetime(2026, 12, 25, 0, 0))


# --- frozen datetime / date classes ------------------------------------
def test_frozen_datetime_is_still_a_datetime():
    frozen = fake_datetime_class(FakeClock())
    value = frozen(2026, 1, 2, 3, 4, 5)
    assert isinstance(value, datetime)
    assert value.year == 2026
    assert frozen.fromisoformat("2026-01-02T03:04:05").day == 2


def test_frozen_datetime_now_honours_a_timezone():
    frozen = fake_datetime_class(FakeClock())
    shifted = frozen.now(timezone.utc).astimezone(timezone.utc)
    assert shifted.utcoffset().total_seconds() == 0


def test_frozen_date_today_returns_the_frozen_day():
    frozen = fake_date_class(date(2026, 11, 4))
    assert frozen.today() == date(2026, 11, 4)
    assert frozen(2020, 5, 6).year == 2020


# --- clock injection into real modules ---------------------------------
def test_run_id_uses_the_injected_clock(monkeypatch):
    install_fake_clock(monkeypatch, structured_logging)
    run_id = structured_logging.new_run_id("demo")
    assert run_id.startswith("demo-20261104T090000Z-")


def test_run_context_duration_follows_the_fake_monotonic_clock(monkeypatch):
    clock = install_fake_clock(monkeypatch, structured_logging)
    # ``started_monotonic`` is a dataclass default factory, so it captured
    # ``time.monotonic`` at class-definition time and is injected explicitly;
    # ``elapsed_ms`` reads ``time.monotonic()`` at call time and therefore picks
    # up the patched module attribute on its own.
    run = structured_logging.RunContext(
        "run-fixed", "organizer", started_monotonic=clock.monotonic()
    )
    clock.advance(0.25)
    assert run.elapsed_ms() == pytest.approx(250.0)
    assert run.fields()["duration_ms"] == pytest.approx(250.0)


def test_run_context_started_stamp_is_frozen(monkeypatch):
    install_fake_clock(monkeypatch, structured_logging)
    run = structured_logging.begin_run("portfolio", run_id="run-frozen")
    assert run.started_at == datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)


def test_conversation_log_timestamp_comes_from_the_fake_clock(monkeypatch, tmp_path):
    clock = install_fake_clock(monkeypatch, __import__("chatbot"))
    log_path = tmp_path / "conversation.jsonl"
    ConversationLogger(log_path).log("user", "hello")
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["timestamp"] == clock.now(timezone.utc).isoformat()
    assert entry["text"] == "hello"


def test_install_fake_date_freezes_the_calendar(monkeypatch):
    import validators

    frozen = install_fake_date(monkeypatch, validators, date(2026, 6, 1))
    assert validators.date.today() == frozen
    assert validators.date(2020, 1, 1).year == 2020


def test_frozen_today_fixture_freezes_the_date_boundary(frozen_today):
    import validators

    assert validators.date.today() == frozen_today
    assert validators.validate_date(frozen_today.isoformat()) == frozen_today


# --- network guard -----------------------------------------------------
def test_network_guard_blocks_a_direct_socket_connection():
    with pytest.raises(RealIOAttempted) as excinfo:
        probe_network()
    assert excinfo.value.resource == "socket.create_connection"


def test_network_guard_records_blocked_attempts(forbid_network: NetworkGuard):
    with pytest.raises(RealIOAttempted):
        probe_network("pypi.org", 443)
    assert forbid_network.attempts == ["socket.create_connection"]


def test_network_guard_leaves_the_event_loop_self_pipe_alone():
    """A loop's own wake-up pipe is not network access.

    On Windows an event loop builds that pipe from a loopback socket pair, so a
    guard that blocked every ``connect`` blocked ``asyncio.run()`` itself — the
    defect that turned the organizer half of the suite red on all four Windows
    cells (Day 24).
    """
    assert probe_socketpair() == 4


def test_the_loopback_connect_is_blocked_by_default():
    """The exemption is narrow: without it, 127.0.0.1 is blocked like anywhere else."""
    with pytest.raises(RealIOAttempted) as excinfo:
        probe_loopback_connect()
    assert excinfo.value.resource == "socket.socket.connect"


def test_the_self_pipe_exemption_permits_the_loopback_connect(forbid_network: NetworkGuard):
    """Inside the exemption, the pair of sockets an event loop needs may connect."""
    with forbid_network.allow_local_plumbing():
        assert probe_loopback_connect() == 4


def test_the_self_pipe_exemption_ends_with_the_block(forbid_network: NetworkGuard):
    """Leaving the block reinstates the guard, even though the body succeeded."""
    with forbid_network.allow_local_plumbing():
        assert probe_loopback_connect() == 4
    with pytest.raises(RealIOAttempted):
        probe_loopback_connect()


def test_the_self_pipe_exemption_survives_an_exception(forbid_network: NetworkGuard):
    """A failing body must not leave the network open."""
    with pytest.raises(RuntimeError):
        with forbid_network.allow_local_plumbing():
            raise RuntimeError("boom")
    with pytest.raises(RealIOAttempted):
        probe_loopback_connect()


def test_the_self_pipe_exemption_does_not_open_the_network():
    """The socket pair is allowed; an outbound connection still is not."""
    assert probe_socketpair() == 4
    with pytest.raises(RealIOAttempted) as excinfo:
        probe_network()
    assert excinfo.value.resource == "socket.create_connection"


def test_network_guard_blocks_higher_level_clients_too():
    with pytest.raises(RealIOAttempted):
        urllib.request.urlopen("http://example.com", timeout=1)


def test_network_guard_error_reads_like_an_assertion():
    error = RealIOAttempted("socket.socket.connect", "no network in unit tests")
    assert isinstance(error, AssertionError)
    assert "socket.socket.connect" in str(error)
    assert "no network in unit tests" in str(error)


# --- filesystem doubles ------------------------------------------------
def test_fake_filesystem_serves_text_without_touching_the_disk(monkeypatch):
    fake = FakeFileSystem()
    fake.add("/virtual/words.txt", "apple\nbanana\n")
    fake.install(monkeypatch)

    source = read_text_robust("/virtual/words.txt")
    assert source.text == "apple\nbanana\n"
    assert fake.reads == ["/virtual/words.txt"]


def test_fake_filesystem_reproduces_bom_and_crlf_handling(monkeypatch):
    fake = FakeFileSystem()
    fake.add("/virtual/constituents.csv", b"\xef\xbb\xbfsymbol,price\r\nAAPL,150\r\n")
    fake.install(monkeypatch)

    source = read_text_robust("/virtual/constituents.csv")
    assert source.had_bom is True
    assert source.newline == "crlf"
    assert source.text.startswith("symbol,price")


def test_fake_filesystem_reports_a_missing_file_like_the_real_loader(monkeypatch):
    FakeFileSystem().install(monkeypatch)
    with pytest.raises(MissingFileError):
        read_text_robust("/virtual/absent.txt")


def test_fake_filesystem_refuses_write_modes(monkeypatch):
    fake = FakeFileSystem()
    fake.add("/virtual/a.txt", "x")
    fake.install(monkeypatch)
    with pytest.raises(RealIOAttempted):
        fake.open("/virtual/a.txt", "w")


def test_fake_filesystem_open_returns_a_readable_handle(monkeypatch):
    fake = FakeFileSystem()
    fake.add("/virtual/a.txt", "line\n")
    fake.install(monkeypatch)
    with fake.open("/virtual/a.txt") as handle:
        assert handle.read() == "line\n"
    with fake.open("/virtual/a.txt", "rb") as handle:
        assert handle.read() == b"line\n"


# --- autouse fixtures --------------------------------------------------
def test_home_directory_points_into_the_sandbox(isolated_home: Path):
    # ``Path.home()`` is itself banned in tests by the auditor, so the fixture's
    # effect is asserted through the environment and through ``expanduser``.
    assert Path(os.environ["HOME"]) == isolated_home
    assert Path("~").expanduser() == isolated_home


def test_working_directory_is_a_sandbox(isolated_cwd: Path):
    assert Path.cwd() == isolated_cwd
    Path("relative-output.txt").write_text("ok", encoding="utf-8")
    assert (isolated_cwd / "relative-output.txt").read_text(encoding="utf-8") == "ok"


def test_log_directory_is_redirected(isolated_home: Path):
    from logging_config import default_log_dir

    # ``DATHEON_LOG_DIR`` is set by the autouse fixture to a sandbox path, so no
    # test can append to the developer's real log directory.
    assert default_log_dir().is_relative_to(isolated_home.parent)
