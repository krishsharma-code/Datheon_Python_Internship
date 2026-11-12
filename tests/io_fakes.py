"""Reusable test doubles for the Day 16 external-I/O isolation policy.

Day 16 of the internship plan asks for one property: **a unit test must never
touch a real external resource**. Three resources were still leaking into the
suite before this module existed:

* the **wall clock** — ``date.today()`` / ``datetime.now()`` made two tests
  behave differently on the day they ran (a test that asserts "a future borrow
  date is rejected" is only meaningful relative to *some* today);
* the **user filesystem** — anything resolving ``Path.home()`` or a relative
  path could read or write outside the pytest sandbox, which makes a run on a
  developer laptop behave differently from a run in CI;
* the **network** — a single stray ``socket`` call turns a hermetic unit test
  into an integration test that fails on a plane.

Rather than repeat a dozen ``monkeypatch`` incantations across the suite, the
doubles live here once and are installed by the autouse fixtures in the root
``conftest.py``. Everything is deliberately tiny and synchronous so that a test
reading it needs no prior knowledge:

* :class:`FakeClock` — a hand-cranked clock with ``now``/``utcnow``/``monotonic``;
* :func:`fake_datetime_class` / :func:`install_fake_clock` — drop the frozen
  clock into a module that imported ``datetime`` (and ``time``) directly;
* :func:`install_fake_date` — the same idea for modules that only need ``date``;
* :class:`NetworkGuard` — every socket entry point raises :class:`RealIOAttempted`;
* :class:`FakeFileSystem` — a dict-backed stand-in for the ``pathlib`` calls the
  dataset loaders make, so a loader test needs no disk at all.

The guard is the important half. A mock that is merely *available* is a mock
nobody uses; a guard that *fails the test* is what keeps the suite honest.
"""

from __future__ import annotations

import builtins
import contextlib
import socket
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

#: The genuine ``socket.socket.connect``, captured at import time. The network
#: guard replaces the class attribute, so anything that needs to *suspend* the
#: guard has to hold a reference taken before the first patch — capturing it
#: inside ``install()`` would capture the guard itself.
_REAL_SOCKET_CONNECT = socket.socket.connect

__all__ = [
    "FakeClock",
    "FakeFileSystem",
    "NetworkGuard",
    "RealIOAttempted",
    "fake_date_class",
    "fake_datetime_class",
    "install_fake_clock",
    "install_fake_date",
    "probe_loopback_connect",
    "probe_network",
]

#: Type alias for the path-like arguments the loaders accept.
PathLike = Union[str, Path]


class RealIOAttempted(AssertionError):
    """Raised when a unit test reaches for a real external resource.

    The class derives from :class:`AssertionError` on purpose: a test that
    accidentally performs real I/O is a *test bug*, not a production failure,
    and pytest reports it as a plain assertion failure with a readable message.

    Attributes:
        resource: The entry point that was called, e.g.
            ``"socket.create_connection"``.
        detail: Optional extra context for the failure message.
    """

    def __init__(self, resource: str, detail: str = "") -> None:
        message = f"real I/O attempted: {resource}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.resource = resource
        self.detail = detail


@dataclass
class FakeClock:
    """A deterministic clock with no relationship to the wall clock.

    The clock keeps two notions of time, exactly like the standard library
    does: a *wall* time used for timestamps, and a *monotonic* counter used for
    durations. Advancing the clock moves both, so a run context that measures
    ``time.monotonic()`` sees the same elapsed time as the timestamps suggest.

    Args:
        current: Starting wall time; defaults to 09:00 UTC on 2026-11-04.

    Example:
        >>> clock = FakeClock()
        >>> clock.now(timezone.utc).hour
        9
        >>> clock.advance(90).monotonic()
        90.0
    """

    current: datetime = field(
        default_factory=lambda: datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    )
    _monotonic: float = 0.0

    def now(self, tz: Optional[timezone] = None) -> datetime:
        """Return the clock's wall time, optionally converted to ``tz``.

        Args:
            tz: Target timezone. When ``None`` the naive local time is returned,
                mirroring :meth:`datetime.datetime.now`.

        Returns:
            The frozen wall time.
        """
        if tz is None:
            return self.current.replace(tzinfo=None)
        return self.current.astimezone(tz)

    def utcnow(self) -> datetime:
        """Return the naive UTC wall time, mirroring ``datetime.utcnow()``."""
        return self.current.astimezone(timezone.utc).replace(tzinfo=None)

    def today(self) -> date:
        """Return the clock's calendar date in UTC."""
        return self.current.astimezone(timezone.utc).date()

    def monotonic(self) -> float:
        """Return the accumulated monotonic seconds since the clock was made."""
        return self._monotonic

    def timestamp(self) -> float:
        """Return the wall time as a POSIX timestamp, mirroring ``time.time()``."""
        return self.current.timestamp()

    def advance(self, seconds: float = 1.0) -> "FakeClock":
        """Move the clock forward, updating wall and monotonic time together.

        Args:
            seconds: Seconds to advance; must not be negative.

        Returns:
            This clock, so calls can be chained.

        Raises:
            ValueError: If ``seconds`` is negative — a clock that runs backwards
                would hide ordering bugs rather than expose them.
        """
        if seconds < 0:
            raise ValueError("a fake clock can only move forward")
        self.current = self.current + timedelta(seconds=seconds)
        self._monotonic += seconds
        return self

    def set(self, moment: Union[str, datetime]) -> "FakeClock":
        """Jump to an explicit wall time without touching the monotonic counter.

        Args:
            moment: An aware :class:`datetime`, or an ISO-8601 string.

        Returns:
            This clock, so calls can be chained.

        Raises:
            ValueError: If the resulting moment is naive — durations and
                timestamps are only comparable when the timezone is explicit.
        """
        value = datetime.fromisoformat(moment) if isinstance(moment, str) else moment
        if value.tzinfo is None:
            raise ValueError("the fake clock needs a timezone-aware moment")
        self.current = value
        return self

    def iso(self) -> str:
        """Return the wall time as an ISO-8601 string."""
        return self.current.isoformat()


def fake_datetime_class(clock: FakeClock) -> type:
    """Build a :class:`datetime` subclass whose ``now``/``utcnow``/``today`` are frozen.

    The subclass is a real ``datetime``, so ``isinstance`` checks, arithmetic
    and :meth:`datetime.datetime.fromisoformat` keep working; only the three
    class methods that read the wall clock are redirected.

    Args:
        clock: The clock the frozen methods delegate to.

    Returns:
        A class suitable for ``monkeypatch.setattr(module, "datetime", ...)``.
    """

    class FrozenDatetime(datetime):
        """A ``datetime`` whose clock-reading class methods are frozen."""

        @classmethod
        def now(cls, tz: Optional[timezone] = None) -> "FrozenDatetime":
            """Return the frozen wall time instead of the current time."""
            return clock.now(tz)  # type: ignore[return-value]

        @classmethod
        def utcnow(cls) -> "FrozenDatetime":
            """Return the frozen naive UTC time instead of the current time."""
            return clock.utcnow()  # type: ignore[return-value]

        @classmethod
        def today(cls) -> "FrozenDatetime":
            """Return the frozen wall time instead of the current time."""
            return clock.now()  # type: ignore[return-value]

    FrozenDatetime.__name__ = "FrozenDatetime"
    FrozenDatetime.__qualname__ = "FrozenDatetime"
    return FrozenDatetime


def fake_date_class(today: date) -> type:
    """Build a :class:`date` subclass whose ``today`` is frozen.

    Args:
        today: The date every ``today()`` call should return.

    Returns:
        A class suitable for ``monkeypatch.setattr(module, "date", ...)``.
    """

    class FrozenDate(date):
        """A ``date`` whose ``today`` class method is frozen."""

        @classmethod
        def today(cls) -> "FrozenDate":
            """Return the frozen date instead of the current date."""
            return today  # type: ignore[return-value]

    FrozenDate.__name__ = "FrozenDate"
    FrozenDate.__qualname__ = "FrozenDate"
    return FrozenDate


class _FakeTimeModule:
    """A stand-in for :mod:`time` that reads ``monotonic``/``time`` from a clock.

    Every other attribute (``sleep``, ``strftime``, ``perf_counter``) is
    forwarded to the real module, so a patched module keeps working.
    """

    def __init__(self, clock: FakeClock, real: Any = _time) -> None:
        self._clock = clock
        self._real = real

    def monotonic(self) -> float:
        """Return the fake clock's monotonic seconds."""
        return self._clock.monotonic()

    def time(self) -> float:
        """Return the fake clock's POSIX timestamp."""
        return self._clock.timestamp()

    def __getattr__(self, name: str) -> Any:
        """Forward any other attribute to the real :mod:`time` module."""
        return getattr(self._real, name)


def install_fake_clock(
    monkeypatch: Any,
    module: Any,
    clock: Optional[FakeClock] = None,
) -> FakeClock:
    """Freeze the clock a module sees, without touching the global one.

    The module's ``datetime`` name is replaced with a frozen subclass, and its
    ``time`` name (when present) with a proxy that reports the same clock. Both
    patches are undone automatically by ``monkeypatch``.

    Args:
        monkeypatch: The pytest ``monkeypatch`` fixture.
        module: The module under test, e.g. ``structured_logging``.
        clock: Clock to install; a fresh :class:`FakeClock` is created when
            omitted.

    Returns:
        The clock now in force, so the test can advance it.
    """
    clock = clock or FakeClock()
    monkeypatch.setattr(module, "datetime", fake_datetime_class(clock), raising=False)
    if hasattr(module, "time"):
        monkeypatch.setattr(module, "time", _FakeTimeModule(clock), raising=False)
    return clock


def install_fake_date(monkeypatch: Any, module: Any, today: Optional[date] = None) -> date:
    """Freeze the calendar date a module sees.

    Args:
        monkeypatch: The pytest ``monkeypatch`` fixture.
        module: The module under test, e.g. ``validators`` or ``models``.
        today: Date every ``date.today()`` should return; defaults to
            2026-11-04.

    Returns:
        The frozen date now in force.
    """
    frozen = today or date(2026, 11, 4)
    monkeypatch.setattr(module, "date", fake_date_class(frozen), raising=False)
    return frozen


@dataclass
class NetworkGuard:
    """Fail the test the moment anything tries to open a socket.

    ``socket`` is the choke point every higher-level client eventually reaches
    — ``requests``, ``urllib``, ``http.client``, database drivers and ``yfinance``
    all end up in :meth:`socket.socket.connect` or :func:`socket.create_connection`.
    Patching those four entry points therefore covers the network without
    having to know which library the code under test prefers.

    Attributes:
        attempts: Names of the blocked entry points, in the order they were
            called — useful when a test wants to assert *that* a client was
            invoked without letting it out.
    """

    attempts: List[str] = field(default_factory=list)

    def _deny(self, resource: str) -> Any:
        """Build a replacement that records the attempt and raises."""

        def _blocked(*args: Any, **kwargs: Any) -> Any:
            self.attempts.append(resource)
            raise RealIOAttempted(resource, "the network guard is installed for every unit test")

        return _blocked

    def install(self, monkeypatch: Any) -> "NetworkGuard":
        """Patch the socket entry points for the lifetime of the test.

        Args:
            monkeypatch: The pytest ``monkeypatch`` fixture.

        Returns:
            This guard, so a test can inspect :attr:`attempts`.
        """
        monkeypatch.setattr(socket.socket, "connect", self._deny("socket.socket.connect"))
        monkeypatch.setattr(socket.socket, "connect_ex", self._deny("socket.socket.connect_ex"))
        monkeypatch.setattr(socket, "create_connection", self._deny("socket.create_connection"))
        monkeypatch.setattr(socket, "getaddrinfo", self._deny("socket.getaddrinfo"))
        monkeypatch.setattr(socket, "gethostbyname", self._deny("socket.gethostbyname"))
        monkeypatch.setattr(socket, "socketpair", self._socketpair())
        return self

    def _socketpair(self) -> Any:
        """Wrap :func:`socket.socketpair` so an event loop can build its self-pipe.

        An event loop needs a pair of connected file descriptors for its own
        wake-up plumbing. POSIX gets them from :func:`os.pipe`; Windows has no
        such call, so :func:`socket.socketpair` falls back to connecting two
        loopback sockets to each other — which runs straight into the ``connect``
        patch above. On Windows that meant *every* ``asyncio.run()`` in the suite
        raised :class:`RealIOAttempted` before the first line of the test
        executed, and the whole organizer half of the suite went red on a
        platform it had never been run on (Day 24's matrix).

        Suspending the guard for the duration of the call keeps the exemption
        exactly as wide as the loop's own plumbing: a pair of sockets talking to
        each other inside one process is not network access, and every other
        connect still raises.

        Returns:
            A replacement for :func:`socket.socketpair`.
        """
        real_pair = socket.socketpair
        guard = self

        def _pair(*args: Any, **kwargs: Any) -> Any:
            with guard.allow_local_plumbing():
                return real_pair(*args, **kwargs)

        return _pair

    @contextlib.contextmanager
    def allow_local_plumbing(self) -> Iterator[None]:
        """Suspend the ``connect`` patch for one in-process socket operation.

        This is the exemption an event loop needs on Windows, and it is exactly
        as wide as the loop's own plumbing: a pair of sockets talking to each
        other inside one process is not network access. Everything else still
        raises, and the patch is reinstated on the way out — including when the
        body raises.

        Yields:
            ``None``, so the caller can use it as a plain ``with`` block.
        """
        socket.socket.connect = _REAL_SOCKET_CONNECT  # type: ignore[method-assign]
        try:
            yield
        finally:
            denied = self._deny("socket.socket.connect")
            socket.socket.connect = denied  # type: ignore[method-assign]


def probe_socketpair() -> int:
    """Open a socket pair the way an event loop does, and return the bytes sent.

    The only legitimate caller is a test proving that the guard leaves the
    loop's own self-pipe alone. Keeping it here keeps ``import socket`` out of
    the test modules, where the static auditor would flag it.

    Returns:
        The number of bytes the other end received.
    """
    left, right = socket.socketpair()
    try:
        left.sendall(b"ping")
        return len(right.recv(4))
    finally:
        left.close()
        right.close()


def probe_loopback_connect() -> int:
    """Connect two sockets over 127.0.0.1, the way Windows' socket pair does.

    Python's ``socket.socketpair`` on Windows is a fallback that binds a
    listener on ``127.0.0.1`` and connects a second socket to it. That path
    cannot be requested on POSIX — the C implementation refuses ``AF_INET`` —
    so this probe reproduces it instead: same two sockets, same ``connect``
    call, the call the network guard patches.

    Returns:
        The number of bytes the accepting socket received.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(listener.getsockname())
            client.sendall(b"ping")
            connection, _ = listener.accept()
            try:
                return len(connection.recv(4))
            finally:
                connection.close()
        finally:
            client.close()
    finally:
        listener.close()


def probe_network(host: str = "example.com", port: int = 80) -> None:
    """Attempt a real outbound connection.

    The only legitimate caller is a test that proves the guard works: the call
    must raise :class:`RealIOAttempted` while the guard is installed. Having the
    probe here keeps ``import socket`` out of the test modules, where the static
    auditor in ``tools/io_audit.py`` would flag it.

    Args:
        host: Host to connect to.
        port: Port to connect to.

    Raises:
        RealIOAttempted: Whenever the network guard is installed, which is
            every test run.
    """
    socket.create_connection((host, port), timeout=1)


@dataclass
class FakeFileSystem:
    """A dict-backed stand-in for the filesystem calls the loaders make.

    Installing it patches :meth:`pathlib.Path.read_bytes`,
    :meth:`pathlib.Path.read_text`, :meth:`pathlib.Path.is_file` and
    :func:`builtins.open`, so a loader can be exercised end to end — BOM
    handling, CRLF detection, encoding fallbacks — with no file on disk and no
    ``tmp_path`` fixture in sight.

    Attributes:
        files: Mapping of absolute-path string to raw bytes.
        reads: Every path read through the fake, in call order. A test asserts
            on this to prove the unit under test consulted *the fake* rather
            than the disk.
    """

    files: Dict[str, bytes] = field(default_factory=dict)
    reads: List[str] = field(default_factory=list)

    @staticmethod
    def key(path: PathLike) -> str:
        """Return the platform-neutral key for ``path``.

        The fake is addressed with POSIX-style virtual paths such as
        ``"/virtual/words.txt"``, but the code under test passes a
        :class:`pathlib.Path`, whose ``str()`` is ``"\\virtual\\words.txt"`` on
        Windows. Normalising both sides through ``as_posix()`` means a test can
        name a path one way and the loader can resolve it another, on any
        platform — Day 24's Windows cells found this, and it is why two
        ``test_mocking`` cases failed there.

        Args:
            path: A string or path-like object.

        Returns:
            The normalised POSIX-style key.
        """
        return Path(path).as_posix()

    def add(self, path: PathLike, content: Union[str, bytes], encoding: str = "utf-8") -> str:
        """Register a file in the fake filesystem.

        Args:
            path: Path the code under test will ask for.
            content: File body, as text (encoded with ``encoding``) or bytes.
            encoding: Encoding used when ``content`` is text.

        Returns:
            The normalised key the file was stored under.
        """
        key = self.key(path)
        self.files[key] = content.encode(encoding) if isinstance(content, str) else content
        return key

    def _lookup(self, path: PathLike, action: str) -> bytes:
        """Return the bytes registered for ``path``, recording the read."""
        key = self.key(path)
        self.reads.append(key)
        if key not in self.files:
            raise FileNotFoundError(f"{action}: no such file in the fake filesystem: {key}")
        return self.files[key]

    def read_bytes(self, path: PathLike) -> bytes:
        """Serve a file's bytes from memory."""
        return self._lookup(path, "read_bytes")

    def read_text(self, path: PathLike, encoding: str = "utf-8", errors: str = "strict") -> str:
        """Serve a file's text from memory, decoded with ``encoding``."""
        return self._lookup(path, "read_text").decode(encoding, errors)

    def is_file(self, path: PathLike) -> bool:
        """Report whether ``path`` is registered as a file."""
        return self.key(path) in self.files

    def open(self, path: PathLike, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        """Serve :func:`open` for read modes and refuse everything else.

        Args:
            path: File to open.
            mode: Open mode; only ``"r"`` and ``"rb"`` are supported.

        Returns:
            A file-like object over the in-memory bytes.

        Raises:
            RealIOAttempted: If a write mode is requested — a unit test that
                writes is exactly what the isolation policy forbids.
        """
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            raise RealIOAttempted("builtins.open", f"write mode {mode!r} is not faked")
        encoding = kwargs.get("encoding") or "utf-8"
        raw = self._lookup(path, "open")
        if "b" in mode:
            import io as _io

            return _io.BytesIO(raw)
        import io as _io

        return _io.StringIO(raw.decode(encoding))

    def install(self, monkeypatch: Any) -> "FakeFileSystem":
        """Patch the filesystem entry points for the lifetime of the test.

        Args:
            monkeypatch: The pytest ``monkeypatch`` fixture.

        Returns:
            This filesystem, so a test can inspect :attr:`reads`.
        """
        fs = self
        monkeypatch.setattr(Path, "read_bytes", lambda self: fs.read_bytes(self))
        monkeypatch.setattr(
            Path,
            "read_text",
            lambda self, encoding="utf-8", errors="strict": fs.read_text(self, encoding, errors),
        )
        monkeypatch.setattr(Path, "is_file", lambda self: fs.is_file(self))
        monkeypatch.setattr(
            builtins, "open", lambda path, mode="r", *a, **kw: fs.open(path, mode, *a, **kw)
        )
        return self
