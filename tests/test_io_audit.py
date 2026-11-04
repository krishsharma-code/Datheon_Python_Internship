"""Tests for the Day 16 static I/O auditor (``tools/io_audit.py``).

The auditor is the part of the isolation policy that cannot rot: it re-checks
every test module on each run instead of relying on a reviewer remembering the
rule. Its own tests therefore cover both halves — that it *detects* each smell
(a guard that never fires is indistinguishable from a guard that is not there)
and that the real suite is currently clean.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import io_audit  # noqa: E402  (path set up above)


# --- detection ---------------------------------------------------------
@pytest.mark.parametrize("module", ["socket", "requests", "httpx", "aiohttp"])
def test_network_imports_are_flagged(module: str):
    violations = io_audit.audit_source(f"import {module}\n", "test_x.py")
    assert [v.code for v in violations] == ["network-import"]
    assert module in violations[0].message


def test_network_import_from_is_flagged():
    violations = io_audit.audit_source("from urllib3 import PoolManager\n", "test_x.py")
    assert violations[0].code == "network-import"


def test_guard_module_is_exempt_from_the_import_rule():
    assert io_audit.audit_source("import socket\n", "io_fakes.py") == []


@pytest.mark.parametrize(
    "call",
    ["datetime.now()", "datetime.utcnow()", "datetime.today()", "date.today()", "time.time()"],
)
def test_real_clock_reads_are_flagged(call: str):
    violations = io_audit.audit_source(f"stamp = {call}\n", "test_x.py")
    assert [v.code for v in violations] == ["real-clock"]


def test_home_lookup_is_flagged():
    violations = io_audit.audit_source("root = Path.home()\n", "test_x.py")
    assert [v.code for v in violations] == ["home-lookup"]


def test_clean_source_produces_no_violations():
    source = (
        "from pathlib import Path\n"
        "def test_ok(tmp_path):\n"
        "    target = tmp_path / 'file.txt'\n"
        "    target.write_text('x', encoding='utf-8')\n"
        "    assert target.read_text(encoding='utf-8') == 'x'\n"
    )
    assert io_audit.audit_source(source, "test_x.py") == []


def test_unparseable_source_is_reported_not_raised():
    violations = io_audit.audit_source("def broken(:\n", "test_x.py")
    assert violations[0].code == "syntax-error"


def test_violations_carry_file_and_line():
    violations = io_audit.audit_source("\nimport socket\n", "tests/test_x.py")
    assert violations[0].file == "tests/test_x.py"
    assert violations[0].line == 2


# --- the gate ----------------------------------------------------------
def test_the_real_suite_is_free_of_real_io():
    violations = io_audit.audit_paths((ROOT / "tests").glob("*.py"))
    assert violations == [], io_audit._render_table(violations)


def test_audit_paths_ignores_non_python_files(tmp_path):
    (tmp_path / "notes.txt").write_text("import socket\n", encoding="utf-8")
    assert io_audit.audit_paths([tmp_path / "notes.txt"]) == []


# --- CLI ---------------------------------------------------------------
def test_cli_exits_zero_on_a_clean_directory(tmp_path, capsys):
    (tmp_path / "test_clean.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    assert io_audit.main(["--tests-dir", str(tmp_path)]) == 0
    assert "no real I/O" in capsys.readouterr().out


def test_cli_strict_exits_one_on_a_violation(tmp_path, capsys):
    (tmp_path / "test_dirty.py").write_text("import socket\n", encoding="utf-8")
    assert io_audit.main(["--tests-dir", str(tmp_path), "--strict"]) == 1
    assert "violation(s) found" in capsys.readouterr().out


def test_cli_without_strict_reports_but_exits_zero(tmp_path, capsys):
    (tmp_path / "test_dirty.py").write_text("import socket\n", encoding="utf-8")
    assert io_audit.main(["--tests-dir", str(tmp_path)]) == 0
    assert "network-import" in capsys.readouterr().out


def test_cli_json_output_is_machine_readable(tmp_path, capsys):
    (tmp_path / "test_dirty.py").write_text("x = Path.home()\n", encoding="utf-8")
    assert io_audit.main(["--tests-dir", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["violations"][0]["code"] == "home-lookup"


def test_cli_handles_a_missing_directory(tmp_path, capsys):
    assert io_audit.main(["--tests-dir", str(tmp_path / "absent")]) == 0
    assert "no real I/O" in capsys.readouterr().out
