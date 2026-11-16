"""Tests for the Day 27 demo-asset pipeline (``tools/demo_assets.py``).

Two things are being protected here. The scrubber, because it is the only
reason the transcript is reproducible on a machine that is not this one — a
leaked scratch path or a measured duration makes every re-capture a spurious
diff. And the renderer's geometry, because a GIF cannot grow: the first version
of this file sized each frame from its own content and Pillow cropped every
frame after the first to the initial height, producing a perfectly valid
animation of one command.

Rendering needs Pillow, so those tests skip when it is absent; the transcript
tests run everywhere, since the transcript is plain JSON.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from tools import demo_assets  # noqa: E402  (path set up above)
from tools import readme_audit  # noqa: E402


def _pillow():
    """Import Pillow, skipping the test when it is not installed.

    Returns:
        The ``PIL.Image`` module.
    """
    pytest.importorskip("PIL", reason="the renderer needs Pillow (dev extra)")
    from PIL import Image

    return Image


# --- scrubbing ---------------------------------------------------------
def test_measured_duration_is_replaced_by_a_stable_one():
    lines = demo_assets.scrub("Processed : 8\nDuration  : 2.61 ms (asyncio engine)\n")
    assert lines[-1] == "Duration  : 2.6 ms (asyncio engine)"


def test_scratch_paths_never_reach_the_transcript():
    lines = demo_assets.scrub("Organising: /tmp/datheon-demo-abc123   [DRY RUN (no changes made)]")
    assert lines == ["Organising: ~/Downloads   [DRY RUN (no changes made)]"]


def test_output_paths_are_reduced_to_file_names():
    text = "Summary written to /home/krish/repo/portfolio_summary.csv\n"
    assert demo_assets.scrub(text) == ["Summary written to portfolio_summary.csv"]


def test_machine_specific_banner_lines_are_dropped():
    text = "Loaded FAQ dataset: projects/04/sample_faq.json\nCategories: Audio\nhello\n"
    assert demo_assets.scrub(text) == ["hello"]


def test_blank_lines_are_collapsed_but_kept_as_spacing():
    assert demo_assets.scrub("a\n\n\n\nb\n\n") == ["a", "", "b"]


def test_crlf_is_normalised():
    assert demo_assets.scrub("a\r\nb\r\n") == ["a", "b"]


# --- transcript round trip --------------------------------------------
def test_session_round_trips_through_json(tmp_path: Path):
    sessions = [demo_assets.Session(command="hangman --version", output=["hangman 1.1.0"])]
    path = demo_assets.save_transcript(sessions, tmp_path / "t.json")
    assert demo_assets.load_transcript(path) == sessions


def test_transcript_is_written_with_a_trailing_newline(tmp_path: Path):
    path = demo_assets.save_transcript([demo_assets.Session("x", ["y"])], tmp_path / "t.json")
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_transcript_without_sessions_is_rejected(tmp_path: Path):
    path = tmp_path / "t.json"
    path.write_text('{"sessions": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        demo_assets.load_transcript(path)


def test_session_dict_with_the_wrong_shape_is_rejected():
    with pytest.raises(ValueError):
        demo_assets.Session.from_dict({"command": 3, "output": "nope"})


# --- capture -----------------------------------------------------------
def test_capture_runs_a_command_and_scrubs_its_output():
    sessions = demo_assets.capture(spec=(("python -c", ("-c", "print('hi')"), None),))
    assert [session.command for session in sessions] == ["python -c"]
    assert sessions[0].output == ["hi"]


def test_capture_reads_standard_input():
    spec = (("cat", ("-c", "import sys; print(sys.stdin.read().strip())"), "piped\n"),)
    assert demo_assets.capture(spec=spec)[0].output == ["piped"]


def test_capture_refuses_a_failed_command():
    with pytest.raises(RuntimeError, match="exited 3"):
        demo_assets.capture(spec=(("boom", ("-c", "raise SystemExit(3)"), None),))


def test_capture_refuses_a_silent_command():
    with pytest.raises(RuntimeError, match="printed nothing"):
        demo_assets.capture(spec=(("quiet", ("-c", "pass"), None),))


# --- rendering ---------------------------------------------------------
def test_every_frame_is_the_same_size():
    _pillow()
    frames = demo_assets.build_frames(demo_assets.load_transcript())
    assert len({frame.size for frame in frames}) == 1


def test_frame_count_follows_the_typing_and_hold_budget():
    _pillow()
    sessions = demo_assets.load_transcript()
    frames = demo_assets.build_frames(sessions)
    expected = 1
    for session in sessions:
        typing = -(-len(session.command) // demo_assets.TYPING_CHUNK)
        shown = min(len(session.output), demo_assets.MAX_OUTPUT_LINES)
        if len(session.output) > demo_assets.MAX_OUTPUT_LINES:
            shown += 1
        expected += typing + shown + demo_assets.HOLD_FRAMES
    assert len(frames) == expected


def test_long_output_is_truncated_with_an_ellipsis():
    _pillow()
    session = demo_assets.Session("cmd", [f"line {i}" for i in range(40)])
    frames = demo_assets.build_frames([session])
    assert len(frames) == 1 + 1 + demo_assets.MAX_OUTPUT_LINES + 1 + demo_assets.HOLD_FRAMES


def test_rendered_gif_is_animated_and_within_budget(tmp_path: Path):
    _pillow()
    path = demo_assets.render_gif(demo_assets.load_transcript(), tmp_path / "demo.gif")
    report = readme_audit.inspect_asset(path)
    assert report.ok
    assert report.frames >= 60
    assert report.size <= readme_audit.ASSET_BUDGET_BYTES


def test_rendering_is_deterministic(tmp_path: Path):
    _pillow()
    sessions = demo_assets.load_transcript()
    first = demo_assets.render_gif(sessions, tmp_path / "a.gif").read_bytes()
    second = demo_assets.render_gif(sessions, tmp_path / "b.gif").read_bytes()
    assert first == second


def test_fps_is_clamped_to_a_legible_range(tmp_path: Path):
    _pillow()
    image = _pillow()
    path = demo_assets.render_gif(demo_assets.load_transcript(), tmp_path / "fast.gif", fps=999)
    with image.open(path) as gif:
        assert gif.info["duration"] >= 50


# --- command line ------------------------------------------------------
def test_check_reports_missing_assets_and_exits_one(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(demo_assets, "TRANSCRIPT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(demo_assets, "GIF_PATH", tmp_path / "missing.gif")
    assert demo_assets.main(["--check"]) == 1
    assert "missing" in capsys.readouterr().out


def test_check_passes_on_the_committed_assets(capsys):
    assert demo_assets.main(["--check"]) == 0
    assert "ok" in capsys.readouterr().out


# --- the committed assets ---------------------------------------------
def test_the_committed_transcript_covers_all_five_tools():
    sessions = demo_assets.load_transcript()
    assert len(sessions) == 5
    commands = [session.command.split()[0] for session in sessions]
    assert commands == ["hangman", "portfolio", "organizer", "assistant", "inventory"]
    assert all(session.output for session in sessions)


def test_the_committed_transcript_is_free_of_absolute_paths():
    text = demo_assets.TRANSCRIPT_PATH.read_text(encoding="utf-8")
    assert "/tmp/" not in text and "/home/" not in text and "/scratch/" not in text
