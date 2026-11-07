"""Tests for the Day 19 mutation-testing driver (``tools/mutation.py``).

The driver is the half of mutation testing that survives the run: ``mutmut``
writes a cache, and this module turns that cache into a number a build can
gate on. Its tests therefore cover the four things that can go wrong quietly:

* the **exit-code mapping** — a killed mutant misread as a survivor would
  flatter the score, and a survivor misread as killed would hide a hole;
* the **scoring rule** — mutants with no covering test must count against the
  score, and mutants mutmut never ran must not be counted either way;
* the **parsing** — a truncated or missing cache file must be skipped rather
  than crash the report; and
* the **gate** — ``--minimum-score`` must actually return a non-zero exit code.

Everything here runs against synthetic ``.meta`` files in ``tmp_path``, so the
suite never needs ``mutmut`` installed and never takes minutes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import mutation  # noqa: E402  (path set up above)


def write_meta(directory: Path, module: str, exit_codes: dict) -> Path:
    """Write a synthetic mutmut cache file for ``module`` into ``directory``."""
    path = directory / f"{module}.meta"
    path.write_text(json.dumps({"exit_code_by_key": exit_codes}), encoding="utf-8")
    return path


# --- exit-code mapping -------------------------------------------------
@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [
        (0, "survived"),
        (1, "killed"),
        (3, "killed"),
        (33, "no tests"),
        (5, "no tests"),
        (34, "skipped"),
        (36, "timeout"),
        (-24, "timeout"),
        (None, "not checked"),
        (999, "suspicious"),
    ],
)
def test_status_for_maps_every_known_exit_code(exit_code, expected):
    assert mutation.status_for(exit_code) == expected


# --- parsing -----------------------------------------------------------
def test_parse_meta_names_the_module_from_the_file(tmp_path):
    meta = write_meta(tmp_path, "core.py", {"core.x_fn__mutmut_1": 0, "core.x_fn__mutmut_2": 1})

    mutants = mutation.parse_meta(meta)

    assert [mutant.name for mutant in mutants] == ["core.x_fn__mutmut_1", "core.x_fn__mutmut_2"]
    assert {mutant.module for mutant in mutants} == {"core.py"}
    assert [mutant.status for mutant in mutants] == ["survived", "killed"]


def test_parse_meta_rejects_a_file_without_exit_codes(tmp_path):
    path = tmp_path / "core.py.meta"
    path.write_text(json.dumps({"something_else": {}}), encoding="utf-8")

    with pytest.raises(KeyError):
        mutation.parse_meta(path)


def test_load_report_of_a_missing_directory_is_empty(tmp_path):
    report = mutation.load_report(tmp_path / "absent")

    assert report.modules == ()
    assert report.mutants == ()
    assert report.total == 0
    assert report.score == 100.0


def test_load_report_skips_unreadable_cache_files(tmp_path):
    write_meta(tmp_path, "good.py", {"good.x_fn__mutmut_1": 1})
    (tmp_path / "broken.py.meta").write_text("{not json", encoding="utf-8")
    (tmp_path / "nocodes.py.meta").write_text(json.dumps({"other": 1}), encoding="utf-8")

    report = mutation.load_report(tmp_path)

    assert [module.module for module in report.modules] == ["good.py"]
    assert report.total == 1


def test_load_report_aggregates_modules_in_name_order(tmp_path):
    write_meta(tmp_path, "b.py", {"b.x__mutmut_1": 1})
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 0, "a.x__mutmut_2": 1})

    report = mutation.load_report(tmp_path)

    assert [module.module for module in report.modules] == ["a.py", "b.py"]
    assert report.total == 3
    assert report.killed == 2
    assert report.survived == 1


# --- scoring -----------------------------------------------------------
def test_mutation_score_is_killed_over_counted(tmp_path):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1, "a.x__mutmut_2": 1, "a.x__mutmut_3": 0})

    report = mutation.load_report(tmp_path)

    assert report.score == pytest.approx(200.0 / 3.0)


def test_mutants_with_no_tests_count_against_the_score(tmp_path):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1, "a.x__mutmut_2": 33})

    report = mutation.load_report(tmp_path)

    assert report.killed == 1
    assert report.no_tests == 1
    assert report.score == pytest.approx(50.0)


def test_unrun_mutants_are_excluded_from_the_denominator(tmp_path):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1, "a.x__mutmut_2": None})

    report = mutation.load_report(tmp_path)

    assert report.counted == 1
    assert report.score == pytest.approx(100.0)


def test_a_cache_that_was_never_run_scores_zero(tmp_path):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": None, "a.x__mutmut_2": None})

    report = mutation.load_report(tmp_path)

    assert report.counted == 0
    assert report.score == 0.0


def test_timeouts_and_segfaults_count_as_kills(tmp_path):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 36, "a.x__mutmut_2": -11, "a.x__mutmut_3": 1})

    report = mutation.load_report(tmp_path)

    assert report.killed == 3
    assert report.survived == 0
    assert report.score == pytest.approx(100.0)


def test_survivors_are_listed_in_name_order(tmp_path):
    write_meta(tmp_path, "a.py", {"a.z__mutmut_1": 0, "a.a__mutmut_1": 0, "a.m__mutmut_1": 1})

    report = mutation.load_report(tmp_path)

    assert [mutant.name for mutant in report.survivors] == [
        "a.a__mutmut_1",
        "a.z__mutmut_1",
    ]


# --- rendering ---------------------------------------------------------
def test_render_table_has_a_row_per_module_and_a_total(tmp_path):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1, "a.x__mutmut_2": 0})

    table = mutation.render_table(mutation.load_report(tmp_path))

    rows = table.splitlines()
    assert rows[0].startswith("module")
    assert any(row.startswith("a.py") for row in rows)
    assert rows[-1].startswith("TOTAL")
    assert "50.0%" in rows[-1]


def test_report_to_dict_is_json_serialisable(tmp_path):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1, "a.x__mutmut_2": 0})

    payload = mutation.report_to_dict(mutation.load_report(tmp_path))

    assert json.loads(json.dumps(payload))["score"] == pytest.approx(50.0)
    assert payload["modules"][0]["module"] == "a.py"
    assert [entry["name"] for entry in payload["survivors"]] == ["a.x__mutmut_2"]


# --- the gate ----------------------------------------------------------
def test_main_passes_when_the_score_meets_the_floor(tmp_path, capsys):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1})

    assert mutation.main(["--report-only", "--mutants-dir", str(tmp_path)]) == 0
    assert "100.0%" in capsys.readouterr().out


def test_main_fails_when_the_score_is_below_the_floor(tmp_path, capsys):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1, "a.x__mutmut_2": 0})

    assert mutation.main(["--report-only", "--mutants-dir", str(tmp_path)]) == 1
    assert "below the 95% floor" in capsys.readouterr().err


def test_main_accepts_a_lower_floor(tmp_path):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1, "a.x__mutmut_2": 0})

    assert (
        mutation.main(["--report-only", "--minimum-score", "50", "--mutants-dir", str(tmp_path)])
        == 0
    )


def test_main_emits_json(tmp_path, capsys):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 1})

    assert mutation.main(["--report-only", "--json", "--mutants-dir", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["killed"] == 1
    assert payload["total"] == 1


def test_main_lists_survivors_on_request(tmp_path, capsys):
    write_meta(tmp_path, "a.py", {"a.x__mutmut_1": 0})

    assert (
        mutation.main(
            [
                "--report-only",
                "--show-survivors",
                "--minimum-score",
                "0",
                "--mutants-dir",
                str(tmp_path),
            ]
        )
        == 0
    )

    assert "a.x__mutmut_1" in capsys.readouterr().out


def test_main_succeeds_with_no_cache_at_all(tmp_path, capsys):
    assert mutation.main(["--report-only", "--mutants-dir", str(tmp_path)]) == 0
    assert "No mutmut cache found" in capsys.readouterr().out


# --- the configured target --------------------------------------------
def test_the_configured_target_scores_one_hundred_percent():
    """The committed configuration must keep the shared core fully covered.

    This is the regression guard for the Day 19 deliverable: if a future change
    deletes the assertions that kill a mutant, this test fails on the machine
    that has run ``python tools/mutation.py`` at least once.
    """
    cache = mutation.DEFAULT_MUTANTS_DIR
    if not (cache / ("error_catalogue" + mutation.META_SUFFIX)).is_file():
        pytest.skip("mutmut cache absent; run python tools/mutation.py first")

    report = mutation.load_report(cache)

    assert report.survivors == ()
    assert report.no_tests == 0
    assert report.score == pytest.approx(100.0)
