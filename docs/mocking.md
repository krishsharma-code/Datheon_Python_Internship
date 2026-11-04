# External-I/O isolation in the test suite (Day 16)

> **Rule:** a unit test in this repository may not touch the network, the wall
> clock, or anything outside the pytest sandbox. If a test needs one of those,
> it uses a double from `tests/io_fakes.py`.

Day 16's definition of done is a negative claim — *no unit test touches the real
network or the user filesystem* — and negative claims rot quietly. This document
records the policy, the doubles that implement it, and the gate that keeps it
true.

---

## 1. Why the policy exists

Before Day 16 the suite was already careful, but three dependencies on the
machine leaked in:

| Leak | Symptom | Where it was |
| --- | --- | --- |
| Wall clock | A "future date is rejected" test means something different on the day it runs | `tests/test_inventory.py`, `tests/test_validation.py` |
| Home directory | `Path.home()` resolved to the developer's real `~`, so `~/.datheon/logs` could be created or read by a test | `tests/test_logging.py`, `logging_config.default_log_dir` |
| Working directory | A relative path in a test wrote into the repository checkout | any test that opened a relative path |

None of these ever failed in CI — which is exactly the problem. They fail on a
contributor's laptop, in a container with a read-only home, or the morning after
a clock change, and the failure looks like a flaky test rather than a design
bug. A test that depends on the environment is not a unit test; it is an
integration test nobody labelled as one.

---

## 2. The doubles (`tests/io_fakes.py`)

| Double | What it replaces | Installed by |
| --- | --- | --- |
| `FakeClock` | wall time and monotonic time | `install_fake_clock(monkeypatch, module)` |
| `fake_datetime_class` / `fake_date_class` | `datetime.now/utcnow/today`, `date.today` | the two `install_*` helpers |
| `NetworkGuard` | `socket.connect`, `connect_ex`, `create_connection`, `getaddrinfo`, `gethostbyname` | autouse `forbid_network` fixture |
| `FakeFileSystem` | `Path.read_bytes/read_text/is_file` and `builtins.open` | `FakeFileSystem().install(monkeypatch)` |

### 2.1 The clock

`FakeClock` keeps wall time and a monotonic counter, and `advance()` moves both,
so a run context that measures a duration sees the same elapsed time the
timestamps suggest:

```python
clock = install_fake_clock(monkeypatch, structured_logging)
run = structured_logging.begin_run("organizer", run_id="run-fixed")
clock.advance(0.25)
assert run.elapsed_ms() == 250.0
```

`install_fake_clock` patches the **module's** `datetime` and `time` names, not
the global ones, so two tests can freeze different clocks at the same time and
no test can freeze another test's clock by accident.

Two traps are worth knowing:

* **A dataclass captures its default factory early.** `RunContext.started_monotonic`
  is `field(default_factory=time.monotonic)`, bound when the class is defined —
  patching the `time` module afterwards does not reach it. Pass the value
  explicitly (`started_monotonic=clock.monotonic()`); `elapsed_ms()` itself reads
  `time.monotonic()` at call time and does pick up the patch.
* **`datetime` subclasses still work.** `FrozenDatetime` is a real `datetime`, so
  `isinstance`, arithmetic and `fromisoformat` behave normally; only the three
  class methods that read the clock are redirected.

For date-only modules (`validators`, the inventory `models`), use
`install_fake_date` or the `frozen_today` fixture, which freezes the calendar at
**2026-11-04**.

### 2.2 The network

`NetworkGuard` patches the socket layer rather than any particular client,
because every client ends there — `requests`, `urllib`, `http.client`, database
drivers, `yfinance`. The patch raises `RealIOAttempted`, which derives from
`AssertionError`, so pytest reports an accidental network call as a test bug
with a readable message:

```
AssertionError: real I/O attempted: socket.create_connection
(the network guard is installed for every unit test)
```

The guard also records `attempts`, so a test can assert that a client *was*
invoked without letting it out — the useful half of a mock that a bare
"block everything" fixture throws away.

`probe_network()` exists so a test can prove the guard fires without importing
`socket` itself (the auditor in §3 would flag that import).

### 2.3 The filesystem

Two tools, for two situations:

* **`tmp_path`** for anything that genuinely needs a file — it is a real,
  isolated directory and the right default.
* **`FakeFileSystem`** when the *loading logic* is the unit under test: register
  the bytes, install the fake, call the loader. BOM, CRLF and legacy-encoding
  fixtures become dictionary literals, and `reads` proves the loader consulted
  the fake rather than the disk.

```python
fake = FakeFileSystem()
fake.add("/virtual/constituents.csv", b"\xef\xbb\xbfsymbol,price\r\nAAPL,150\r\n")
fake.install(monkeypatch)

source = read_text_robust("/virtual/constituents.csv")
assert source.had_bom is True
```

Write modes are refused (`RealIOAttempted`): a unit test that writes through a
fake is a test whose intent is unclear, and failing loudly is cheaper than
guessing.

---

## 3. The autouse fixtures (`conftest.py`)

Every test, without opting in, gets:

| Fixture | Effect |
| --- | --- |
| `isolated_home` | `HOME`, `USERPROFILE`, `TMPDIR`/`TEMP`/`TMP`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` and `DATHEON_LOG_DIR` point into a private sandbox |
| `isolated_cwd` | the working directory is a private temp directory |
| `forbid_network` | `NetworkGuard` installed |

The sandbox is a **sibling** of `tmp_path`, not a child: tests that assert
"nothing was written" against their own `tmp_path` keep working, which is why
`tmp_path_factory.mktemp(...)` is used rather than `tmp_path / "home"`.

Clock isolation stays opt-in on purpose. A frozen clock is only meaningful
relative to a module, and an autouse freeze would hide the real dependency
instead of removing it.

---

## 4. The gate (`tools/io_audit.py`)

A guard nobody verifies is a guard that stops working silently, so the policy is
checked statically on every run:

```bash
python tools/io_audit.py            # table
python tools/io_audit.py --json     # machine-readable
python tools/io_audit.py --strict   # exit 1 on any violation (CI mode)
```

It parses each `tests/*.py` with `ast` and flags three smells:

| Code | Trigger | Fix |
| --- | --- | --- |
| `network-import` | `import socket` / `requests` / `httpx` / `urllib3` / `aiohttp` / `ftplib` / `smtplib` | use the guard, or `probe_network()` |
| `real-clock` | `datetime.now/utcnow/today`, `date.today`, `time.time` | `FakeClock` + `install_fake_clock` |
| `home-lookup` | `Path.home()` | `tmp_path`, or `os.environ["HOME"]` when asserting the fixture |

`tests/io_fakes.py` is exempt from the import rule because it implements the
guard; the exemption is one constant (`GUARD_MODULES`), not a per-file pragma,
so it cannot quietly grow.

CI runs `python tools/io_audit.py --strict` before the test suite, and
`tests/test_io_audit.py` asserts the repository is currently clean *and* that
each detector actually fires on a planted violation.

---

## 5. Scope: what is deliberately still real

* **Subprocess smoke tests** (`test_cli_contract`, `test_dataset_loader`,
  `test_edge_cases`, `test_async_organizer`) spawn a real interpreter, because
  the property under test — exit codes and stderr from a real process — cannot
  be observed in-process. They pass `cwd=ROOT` and a closed stdin, and they run
  against the repository's own fixture data.
* **Repository fixture data** (`projects/*/data`) is read from disk. It is
  version-controlled input, not user state.
* **`tmp_path` writes** are real writes to a temporary directory. The policy
  forbids touching the *user's* filesystem, not writing files at all.

Everything else — the five libraries, the shared modules, the CLI wiring — is
exercised with no external resource at all.

---

## 6. Verifying the claim

```bash
python -m compileall -q projects tests conftest.py
flake8 .
black --check .
python tools/io_audit.py --strict
pytest -q
```

`pytest` at the time of writing: **688 test functions, 1,288 assertions**, all
green, with the auditor reporting *no real I/O found in the test suite*.
