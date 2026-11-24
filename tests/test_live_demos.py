"""Tests for the Day 32 live-demo system (``tools/live_demos.py``).

Two claims need proving, and they are different claims:

* **The pages match the sessions.** That is the audit's job, and it is tested
  against synthetic pages that each break one rule — a transcript that drifted,
  a missing marker, a leaked path, an external asset.
* **The sessions match the tools.** That is ``--verify``'s job. Four of the five
  captures are byte-reproducible (the hangman word is random), so the suite
  re-runs them and compares the whole transcript with the committed one: a
  change to a tool's output then fails here instead of quietly rotting the site.

The re-captures cost a couple of seconds each and run the real ``main.py`` files
through a real subprocess, which is the only way to test a capture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:  # pragma: no cover - tools import each other
    sys.path.insert(0, str(ROOT / "tools"))

from tools import live_demos  # noqa: E402  (path set up above)


# --- helpers --------------------------------------------------------------
def make_demo(
    tool: str = "hangman",
    *,
    command: str = "hangman --categories",
    markers: tuple = ("Welcome",),
    output: str = "Welcome to Hangman\nAttempts left: 6",
) -> live_demos.Demo:
    """Build a small session for the tests.

    Args:
        tool: The console-script name.
        command: The command line to show.
        markers: Substrings the output must contain.
        output: The captured output.

    Returns:
        A session with one command step and one output step.
    """
    return live_demos.Demo(
        tool=tool,
        title="A test session",
        command=command,
        cwd="~/datheon-demo",
        exit_code=0,
        markers=markers,
        steps=(
            live_demos.Step("command", command),
            live_demos.Step("output", output),
        ),
    )


def make_page(demo: live_demos.Demo, *, extra: str = "") -> str:
    """Build a page carrying a rendered block for ``demo``.

    Args:
        demo: The session to render.
        extra: Text appended after the block.

    Returns:
        The page source.
    """
    return "# Title\n\nProse.\n\n" + live_demos.render_block(demo) + "\n" + extra


def recipe(tool: str) -> live_demos.Recipe:
    """Return the real recipe for ``tool``.

    Args:
        tool: The console-script name.

    Returns:
        The recipe from :data:`live_demos.RECIPES`.
    """
    return next(item for item in live_demos.RECIPES if item.tool == tool)


# --- scrubbing ------------------------------------------------------------
def test_scrub_replaces_the_scratch_directory_everywhere(tmp_path: Path):
    text = f"Organising: {tmp_path}   [DRY RUN]\n     - {tmp_path}/report.pdf\n"

    lines = live_demos.scrub_lines(text, home=tmp_path)

    assert lines == [
        "Organising: ~/datheon-demo   [DRY RUN (no changes made)]",
        "     - ~/datheon-demo/report.pdf",
    ]


def test_scrub_normalises_the_separator_after_the_prefix(tmp_path: Path):
    """Windows prints the scratch directory with backslashes.

    Replacing the prefix alone left ``~/datheon-demo\\report.pdf`` in the
    transcript, which the four Windows cells of the CI matrix caught the first
    time this shipped: the same session hashed and read differently per runner.
    """
    home = tmp_path / "scratch"
    windows_spelling = str(home).replace("/", "\\")

    lines = live_demos.scrub_lines(f"     - {windows_spelling}\\report.pdf\n", home=home)

    assert lines == ["     - ~/datheon-demo/report.pdf"]


def test_scrub_keeps_the_ascii_art_backslashes(tmp_path: Path):
    """Only the scratch prefix is rewritten; the gallows is not a path."""
    art = " /|\\  |\n / \\  |\n"

    lines = live_demos.scrub_lines(art, home=tmp_path)

    assert lines == [" /|\\  |", " / \\  |"]


def test_demo_files_are_byte_identical_on_every_platform(tmp_path: Path):
    """No CRLF translation: the duplicate detector prints a SHA-256 of them."""
    live_demos._prepare_scratch(tmp_path)

    assert b"\r\n" not in (tmp_path / "report.pdf").read_bytes()
    assert (tmp_path / "report.pdf").read_bytes() == (tmp_path / "report-copy.pdf").read_bytes()


def test_scrub_pins_a_duration(tmp_path: Path):
    lines = live_demos.scrub_lines("Duration  : 918.44 ms (asyncio engine)\n")

    assert lines == ["Duration  : 2.4 ms (asyncio engine)"]


def test_scrub_pins_an_iso_date():
    lines = live_demos.scrub_lines("Due on 2026-12-08\n")

    assert lines == ["Due on 2026-11-24"]


def test_scrub_collapses_blank_runs_and_drops_trailing_blanks():
    lines = live_demos.scrub_lines("a\n\n\n\nb\n\n\n")

    assert lines == ["a", "", "b"]


def test_scrub_drops_the_prefixes_that_belong_in_the_docs():
    lines = live_demos.scrub_lines("Categories: A, B\nkept\n")

    assert lines == ["kept"]


def test_scrub_strips_carriage_returns():
    lines = live_demos.scrub_lines("one\r\ntwo\r\n")

    assert lines == ["one", "two"]


# --- rendering ------------------------------------------------------------
def test_render_transcript_shows_the_command_and_output():
    demo = make_demo()

    transcript = live_demos.render_transcript(demo)

    assert transcript.split("\n")[0] == "$ hangman --categories"
    assert "Welcome to Hangman" in transcript


def test_render_transcript_uses_the_captured_prompt_for_input():
    demo = live_demos.Demo(
        tool="hangman",
        title="t",
        command="hangman",
        cwd="~",
        exit_code=0,
        markers=(),
        steps=(
            live_demos.Step("command", "hangman"),
            live_demos.Step("input", "e", prompt="Guess a letter: "),
            live_demos.Step("output", "Nice!"),
        ),
    )

    assert live_demos.render_transcript(demo).split("\n")[1] == "Guess a letter: e"


def test_render_transcript_falls_back_to_a_generic_prompt():
    demo = live_demos.Demo(
        tool="x",
        title="t",
        command="x",
        cwd="~",
        exit_code=0,
        markers=(),
        steps=(live_demos.Step("input", "hello"),),
    )

    assert live_demos.render_transcript(demo) == "> hello"


def test_escape_html_protects_arrows_and_ampersands():
    assert live_demos.escape_html("a -> b & c < d") == "a -&gt; b &amp; c &lt; d"


def test_render_block_carries_the_session_hook_and_the_transcript():
    block = live_demos.render_block(make_demo())

    assert live_demos.BEGIN_MARKER in block
    assert live_demos.END_MARKER in block
    assert 'data-session="assets/demos/hangman.json"' in block
    assert "Welcome to Hangman" in block


def test_render_block_ends_with_a_newline():
    assert live_demos.render_block(make_demo()).endswith("\n")


def test_extract_block_returns_the_generated_region():
    demo = make_demo()
    page = make_page(demo)

    block = live_demos.extract_block(page)

    assert block is not None
    assert block.strip() == live_demos.render_block(demo).strip()


def test_extract_block_is_none_without_markers():
    assert live_demos.extract_block("# no block here\n") is None


def test_extract_block_is_none_when_the_markers_are_out_of_order():
    page = f"{live_demos.END_MARKER}\ntext\n{live_demos.BEGIN_MARKER}\n"

    assert live_demos.extract_block(page) is None


def test_replace_block_rewrites_only_the_generated_region():
    demo = make_demo()
    page = make_page(demo, extra="after the block\n")
    fresh = make_demo(output="Welcome to Hangman\nA different board")

    updated = live_demos.replace_block(page, fresh)

    assert "A different board" in updated
    assert "after the block" in updated
    assert updated.count(live_demos.BEGIN_MARKER) == 1


def test_replace_block_refuses_a_page_without_markers():
    with pytest.raises(ValueError):
        live_demos.replace_block("# nothing\n", make_demo())


# --- sessions -------------------------------------------------------------
def test_demo_round_trips_through_json():
    demo = make_demo()

    decoded = live_demos.demo_from_dict(json.loads(json.dumps(demo.to_dict())))

    assert decoded == demo


def test_demo_to_dict_carries_the_schema():
    assert make_demo().to_dict()["schema"] == live_demos.SCHEMA_VERSION


def test_demo_from_dict_rejects_a_wrong_schema():
    payload = make_demo().to_dict()
    payload["schema"] = 99

    with pytest.raises(ValueError, match="schema"):
        live_demos.demo_from_dict(payload)


def test_demo_from_dict_rejects_empty_steps():
    payload = make_demo().to_dict()
    payload["steps"] = []

    with pytest.raises(ValueError, match="steps"):
        live_demos.demo_from_dict(payload)


def test_demo_from_dict_rejects_an_unknown_step_kind():
    payload = make_demo().to_dict()
    payload["steps"][1]["kind"] = "telepathy"

    with pytest.raises(ValueError, match="kind"):
        live_demos.demo_from_dict(payload)


def test_demo_from_dict_rejects_a_non_object_step():
    payload = make_demo().to_dict()
    payload["steps"][1] = "output"

    with pytest.raises(ValueError, match="not an object"):
        live_demos.demo_from_dict(payload)


def test_save_and_load_demo_round_trip(tmp_path: Path):
    demo = make_demo()

    saved = live_demos.save_demo(demo, tmp_path)

    assert saved == live_demos.session_path("hangman", tmp_path)
    assert live_demos.load_demo(saved) == demo


def test_session_and_page_paths_are_where_the_site_expects_them(tmp_path: Path):
    assert live_demos.session_path("hangman", tmp_path) == (
        tmp_path / "docs" / "assets" / "demos" / "hangman.json"
    )
    assert live_demos.page_path("hangman", tmp_path) == tmp_path / "docs" / "demos" / "hangman.md"
    assert live_demos.demo_index_page(tmp_path) == tmp_path / "docs" / "demos" / "index.md"


# --- building the steps ---------------------------------------------------
def test_build_steps_interleaves_input_with_the_reply_it_produced():
    recipe_ = live_demos.Recipe(
        tool="t",
        title="t",
        command="t",
        argv=("x",),
        stdin="one\ntwo\n",
        prompts=("ask: ",),
    )

    steps = live_demos.build_steps(recipe_, "hello\nask: first reply\nask: second reply\n")

    assert [step.kind for step in steps] == [
        "command",
        "output",
        "input",
        "output",
        "input",
        "output",
    ]
    assert steps[2].text == "one"
    assert steps[2].prompt == "ask: "
    assert steps[3].text == "first reply"


def test_build_steps_drops_stdin_lines_the_program_never_read():
    recipe_ = live_demos.Recipe(
        tool="t",
        title="t",
        command="t",
        argv=("x",),
        stdin="one\ntwo\nthree\n",
        prompts=("ask: ",),
    )

    steps = live_demos.build_steps(recipe_, "ask: reply\n")

    inputs = [step for step in steps if step.kind == "input"]
    assert [step.text for step in inputs] == ["one"]


def test_build_steps_lists_inputs_first_when_no_prompts_are_declared():
    recipe_ = live_demos.Recipe(tool="t", title="t", command="t", argv=("x",), stdin="one\ntwo\n")

    steps = live_demos.build_steps(recipe_, "output only\n")

    assert [step.kind for step in steps] == ["command", "input", "input", "output"]


def test_build_steps_falls_back_when_the_prompts_do_not_line_up():
    recipe_ = live_demos.Recipe(
        tool="t", title="t", command="t", argv=("x",), stdin="one\n", prompts=("ask: ",)
    )

    steps = live_demos.build_steps(recipe_, "no prompt at all\n")

    assert [step.kind for step in steps] == ["command", "input", "output"]


# --- auditing a session ---------------------------------------------------
def test_check_demo_accepts_a_clean_session():
    assert live_demos.check_demo(make_demo()) == []


def test_check_demo_flags_an_empty_command():
    demo = make_demo(command="")

    codes = [finding.code for finding in live_demos.check_demo(demo)]

    assert "live.session" in codes


def test_check_demo_flags_an_empty_capture():
    demo = make_demo(output="", markers=())

    codes = [finding.code for finding in live_demos.check_demo(demo)]

    assert "live.session" in codes


def test_check_demo_flags_a_marker_the_transcript_lost():
    demo = make_demo(markers=("Welcome", "A line that is not there"))

    findings = live_demos.check_demo(demo)

    assert [finding.code for finding in findings] == ["live.markers"]


def test_check_demo_flags_a_leaked_host_path():
    demo = make_demo(markers=(), output="/tmp/datheon-live-abc/report.pdf")

    findings = live_demos.check_demo(demo)

    assert [finding.code for finding in findings] == ["live.path"]


# --- auditing a page ------------------------------------------------------
def test_check_page_accepts_a_matching_page():
    demo = make_demo()

    assert live_demos.check_page("hangman", make_page(demo), demo) == []


def test_check_page_flags_a_missing_block():
    findings = live_demos.check_page("hangman", "# nothing\n", make_demo())

    assert [finding.code for finding in findings] == ["live.page"]


def test_check_page_flags_a_drifted_transcript():
    demo = make_demo()
    page = make_page(demo).replace("Welcome to Hangman", "Welcome to Hangman (edited)")

    findings = live_demos.check_page("hangman", page, demo)

    assert [finding.code for finding in findings] == ["live.transcript"]


def test_check_page_flags_a_block_pointing_at_another_session():
    demo = make_demo()
    page = make_page(demo).replace("assets/demos/hangman.json", "assets/demos/other.json")

    codes = [finding.code for finding in live_demos.check_page("hangman", page, demo)]

    assert "live.player" in codes


def test_check_page_flags_an_external_asset():
    demo = make_demo()

    codes = [
        finding.code
        for finding in live_demos.check_page(
            "hangman", make_page(demo, extra="![x](https://cdn.example.com/a.png)\n"), demo
        )
    ]

    assert "live.offline" in codes


# --- auditing the player and the site -------------------------------------
def test_check_player_accepts_the_real_player():
    js = live_demos.PLAYER_JS.read_text(encoding="utf-8")
    css = live_demos.PLAYER_CSS.read_text(encoding="utf-8")

    assert live_demos.check_player(js, css) == []


@pytest.mark.parametrize(
    "missing",
    ["data-session", "demo-terminal__fallback", "prefers-reduced-motion", "aria-hidden"],
)
def test_check_player_flags_a_missing_behaviour(missing: str):
    js = live_demos.PLAYER_JS.read_text(encoding="utf-8").replace(missing, "removed")
    css = live_demos.PLAYER_CSS.read_text(encoding="utf-8")

    findings = live_demos.check_player(js, css)

    assert [finding.code for finding in findings] == ["live.player"]


def test_check_player_flags_a_network_asset():
    js = live_demos.PLAYER_JS.read_text(encoding="utf-8") + '\nfetch("https://cdn.example.com/x")\n'
    css = live_demos.PLAYER_CSS.read_text(encoding="utf-8")

    findings = live_demos.check_player(js, css)

    assert [finding.code for finding in findings] == ["live.player"]


def test_check_player_flags_a_stylesheet_that_styles_nothing():
    js = live_demos.PLAYER_JS.read_text(encoding="utf-8")

    findings = live_demos.check_player(js, "")

    assert [finding.code for finding in findings] == ["live.player"]


def test_check_site_accepts_the_real_declaration():
    assert live_demos.check_site() == []


def test_check_site_flags_a_missing_workflow(tmp_path: Path):
    findings = live_demos.check_site(tmp_path)

    assert [finding.code for finding in findings] == ["live.nav"]


def test_check_site_flags_a_page_outside_the_nav(tmp_path: Path):
    (tmp_path / "mkdocs.yml").write_text(
        "nav:\n  - Home: index.md\nextra_css:\n  - stylesheets/live-demos.css\n"
        "extra_javascript:\n  - javascripts/terminal-player.js\n",
        encoding="utf-8",
    )

    findings = live_demos.check_site(tmp_path)

    assert {finding.code for finding in findings} == {"live.nav"}
    assert len(findings) == 6  # the five tool pages plus the hub


# --- the audit as a whole -------------------------------------------------
def test_audit_passes_on_the_committed_tree():
    assert live_demos.audit() == ()


def test_audit_reports_a_missing_session(tmp_path: Path):
    for path in (tmp_path / "docs" / "assets" / "demos").parent.parent.rglob("*"):
        pass  # build nothing: an empty root is the point

    findings = live_demos.audit(tmp_path)

    assert any(finding.code == "live.session" for finding in findings)


def test_audit_reports_a_missing_page(tmp_path: Path):
    demo = make_demo()
    live_demos.save_demo(demo, tmp_path)

    findings = live_demos.audit(tmp_path)

    assert any(finding.code == "live.page" for finding in findings)


def test_render_report_announces_a_pass():
    assert "PASS" in live_demos.render_report([])


def test_render_report_lists_each_finding():
    finding = live_demos.Finding("live.nav", "mkdocs.yml", "not in the nav")

    report = live_demos.render_report([finding])

    assert "FAIL" in report
    assert "live.nav" in report
    assert "not in the nav" in report


def test_findings_to_dict_is_json_serialisable():
    finding = live_demos.Finding("live.nav", "mkdocs.yml", "not in the nav")

    payload = live_demos.findings_to_dict([finding])

    assert json.loads(json.dumps(payload))["findings"] == 1
    assert payload["details"][0]["code"] == "live.nav"


# --- the command line -----------------------------------------------------
def test_main_returns_zero_when_the_audit_passes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(live_demos, "audit", lambda root=live_demos.ROOT: ())

    assert live_demos.main(["--check"]) == live_demos.EXIT_CLEAN


def test_main_returns_one_when_a_finding_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    finding = live_demos.Finding("live.nav", "mkdocs.yml", "not in the nav")
    monkeypatch.setattr(live_demos, "audit", lambda root=live_demos.ROOT: (finding,))

    assert live_demos.main(["--check"]) == live_demos.EXIT_FINDINGS
    assert "live.nav" in capsys.readouterr().err


def test_main_returns_two_when_a_session_cannot_be_read(monkeypatch: pytest.MonkeyPatch):
    def broken(root: Path = live_demos.ROOT) -> tuple:
        raise ValueError("unreadable")

    monkeypatch.setattr(live_demos, "audit", broken)

    assert live_demos.main(["--check"]) == live_demos.EXIT_UNMEASURABLE


def test_main_returns_two_for_an_unknown_tool(capsys: pytest.CaptureFixture):
    assert live_demos.main(["--check", "--tool", "nope"]) == live_demos.EXIT_UNMEASURABLE
    assert "no recipe matches" in capsys.readouterr().err


def test_main_returns_two_when_a_capture_fails(monkeypatch: pytest.MonkeyPatch):
    def broken(*args, **kwargs) -> List:
        raise RuntimeError("the tool exited 1")

    monkeypatch.setattr(live_demos, "capture_and_write", broken)

    assert live_demos.main(["--capture", "--tool", "hangman"]) == live_demos.EXIT_UNMEASURABLE


def test_main_emits_json_when_asked(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    monkeypatch.setattr(live_demos, "audit", lambda root=live_demos.ROOT: ())

    assert live_demos.main(["--check", "--json"]) == live_demos.EXIT_CLEAN
    assert json.loads(capsys.readouterr().out)["passed"] is True


# --- the sessions still describe the tools --------------------------------
@pytest.mark.parametrize("tool", ["portfolio", "organizer", "assistant", "inventory"])
def test_the_committed_session_is_reproducible(tool: str):
    """Re-run the tool and compare the whole transcript.

    These four are deterministic — the sample data is committed, the duration is
    scrubbed and dates are pinned — so a change in any tool's output fails here
    rather than silently ageing the published page. Hangman is excluded because
    the secret word is chosen at random; ``--verify`` checks its markers instead.
    """
    fresh = live_demos.capture_demo(recipe(tool))
    committed = live_demos.load_demo(live_demos.session_path(tool))

    assert live_demos.render_transcript(fresh) == live_demos.render_transcript(committed)


def test_verify_passes_on_the_committed_sessions():
    assert live_demos.verify() == ()


def test_hangman_capture_is_played_until_it_is_won():
    demo = live_demos.capture_demo(recipe("hangman"))

    assert "You guessed it!" in live_demos.render_transcript(demo)


def test_every_recipe_names_a_tool_a_page_and_a_session():
    for item in live_demos.RECIPES:
        assert item.tool in {"hangman", "portfolio", "organizer", "assistant", "inventory"}
        assert live_demos.page_path(item.tool).is_file()
        assert live_demos.session_path(item.tool).is_file()


def test_the_hub_page_links_all_five_demos():
    hub = live_demos.demo_index_page().read_text(encoding="utf-8")

    for item in live_demos.RECIPES:
        assert f"({item.tool}.md)" in hub
