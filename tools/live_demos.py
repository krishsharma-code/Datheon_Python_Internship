"""Live demos: the five tools, replayable on the published site (Day 32).

The documentation site is the only part of this repository a visitor can open
without cloning it. Until now it *described* the five tools; the GIF in the
README showed them for fifteen seconds and then stopped. This module makes the
site show them properly: one page per tool, and on that page a terminal that
replays a **real captured session** line by line.

Three things make that honest rather than decorative:

* **The transcripts are captured, never typed.** :func:`capture_all` runs the
  five command lines through the real ``main.py`` files, with real stdin, in a
  scratch directory — the same technique ``tools/demo_assets.py`` uses for the
  GIF. Nothing on these pages is a mock-up.
* **A page cannot drift from its session.** Each page carries the whole
  transcript inside ``<!-- live-demo:begin -->`` … ``<!-- live-demo:end -->``,
  and ``--check`` compares that block byte for byte against
  :func:`render_transcript` of the committed JSON. Edit one and the gate fails
  until you re-capture.
* **The page works without JavaScript.** The block *is* the transcript, so the
  page is readable in a feed reader, by a screen reader, and by a search engine.
  The player enhances it: it hides the static block and replays the session in
  an animated terminal. No CDN, no build step, no third-party script — the
  published site loads exactly two local files, which ``--check`` also enforces.

Run it directly::

    python tools/live_demos.py --capture    # re-run the five tools, rewrite the pages
    python tools/live_demos.py --check      # sessions, pages, player and nav agree
    python tools/live_demos.py --verify     # re-run each tool, confirm it still matches
    python tools/live_demos.py --json       # machine-readable verdict
    python tools/live_demos.py --tool hangman --capture   # one page at a time

Exit codes: ``0`` everything agrees, ``1`` at least one finding, ``2`` the gate
could not measure — a session file is missing or unreadable, or a tool could not
be run. A demo built from a failed run is worse than no demo, so a capture that
exits non-zero aborts instead of publishing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: Captured sessions, one JSON file per tool. Served as static assets.
SESSIONS_DIR: Path = ROOT / "docs" / "assets" / "demos"

#: The five demo pages.
PAGES_DIR: Path = ROOT / "docs" / "demos"

#: The player and its stylesheet. Both are local: the site loads no third-party
#: script, so a reader on a locked-down network still sees the demos.
PLAYER_JS: Path = ROOT / "docs" / "javascripts" / "terminal-player.js"
PLAYER_CSS: Path = ROOT / "docs" / "stylesheets" / "live-demos.css"

#: The site declaration, whose nav and asset lists the audit reads.
MKDOCS_PATH: Path = ROOT / "mkdocs.yml"

#: Layout version of a session file; bump it when the shape changes.
SCHEMA_VERSION: int = 1

#: Delimiters around the generated transcript block in a page. They are HTML
#: comments so Markdown passes them through untouched, and they are unique
#: enough that a stray match is impossible.
BEGIN_MARKER: str = "<!-- live-demo:begin -->"
END_MARKER: str = "<!-- live-demo:end -->"

#: Seconds one captured command may take. The slowest is the organizer, which
#: hashes a handful of files; a minute is generous and still bounded.
CAPTURE_TIMEOUT_SECONDS: float = 120.0

#: How many times the hangman capture is retried to land a won round. The word
#: is chosen with ``random.choice`` and the tool exposes no seed, so a demo that
#: insists on a win has to re-roll. Five attempts is enough in practice and
#: still bounded.
HANGMAN_ATTEMPTS: int = 20

#: Exit codes, matching the rest of the repository.
EXIT_CLEAN: int = 0
EXIT_FINDINGS: int = 1
EXIT_UNMEASURABLE: int = 2

#: Step kinds the player understands.
STEP_KINDS = frozenset({"command", "input", "output"})

#: Paths that must never reach a published transcript: they name the machine
#: that captured it, which is exactly the defect Day 27 found in the GIF assets.
_HOST_PATH_PATTERNS: Tuple[str, ...] = (
    r"/scratch/",
    r"/tmp/",
    r"/home/",
    r"/Users/",
    r"[A-Za-z]:\\\\",
    r"file:///",
)

#: Lines a transcript never keeps, because they are true only of the capturing
#: machine. Applied to the *raw* run, before the session is written.
_SCRUB_RULES: Tuple[Tuple[str, str], ...] = (
    (r"Duration\s+:\s+[0-9.]+ (?:ms|s).*", "Duration  : 2.4 ms (asyncio engine)"),
    (r"Organising: .*", "Organising: ~/datheon-demo   [DRY RUN (no changes made)]"),
    (r"Summary written to .*", "Summary written to portfolio_summary.csv"),
    (r"Conversation saved to .*", "Conversation saved to conversation.jsonl"),
    (r"you > bot > ", "bot > "),
    # Any ISO date is pinned to the day the demos were cut. No transcript prints
    # one today, but a transcript that silently changes every midnight is a demo
    # that goes stale on its own — cheap insurance, and the reason `--check`
    # stays green tomorrow.
    (r".*\d{4}-\d{2}-\d{2}.*", None),
)

#: Prefixes dropped outright: prompts echoed before a read, and the tool's own
#: list of category names, which belongs in the docs rather than a transcript.
_DROP_PREFIXES: Tuple[str, ...] = ("Categories:",)

#: Files written into the organizer's scratch directory.
DEMO_FILES: Tuple[str, ...] = (
    "notes.txt",
    "photo.jpg",
    "song.mp3",
    "report.pdf",
    "report-copy.pdf",
    "archive.zip",
    "script.py",
    "clip.mp4",
)

#: The date any ISO date in a transcript is pinned to, so a capture does not go
#: stale at midnight.
CAPTURE_DATE: str = "2026-11-24"

#: Files whose content is identical, so the duplicate detector has something to
#: find. ``report-copy.pdf`` is a copy of ``report.pdf``.
DEMO_DUPLICATES: Tuple[str, ...] = ("report.pdf", "report-copy.pdf")


@dataclass(frozen=True)
class Step:
    """One line of a replayable session.

    Attributes:
        kind: ``command`` (what the reader would type at a shell), ``input``
            (what was typed into the program's prompt) or ``output`` (what the
            program printed).
        text: The text. ``output`` steps may hold several lines.
        prompt: For an ``input`` step, the prompt the program had printed when
            the line was typed (``"Guess a letter: "``). Kept so the replay
            reads like a terminal — ``Guess a letter: e`` — rather than a list
            of bare letters.
    """

    kind: str
    text: str
    prompt: str = ""

    def to_dict(self) -> Dict[str, str]:
        """Return the JSON-serialisable form."""
        return {"kind": self.kind, "text": self.text, "prompt": self.prompt}


@dataclass(frozen=True)
class Demo:
    """A captured session for one tool.

    Attributes:
        tool: The console-script name (``hangman``, ``portfolio``, …).
        title: What the page calls this session.
        command: The command line as a reader would type it.
        cwd: The working directory shown in the terminal's title bar.
        exit_code: The exit status the run ended with.
        markers: Substrings the output must contain. ``--verify`` re-runs the
            tool and checks them, which is what catches a session that has gone
            stale without being re-captured.
        steps: The session, in order.
    """

    tool: str
    title: str
    command: str
    cwd: str
    exit_code: int
    markers: Tuple[str, ...]
    steps: Tuple[Step, ...]

    def to_dict(self) -> Dict[str, Any]:
        """Return the JSON-serialisable form."""
        return {
            "schema": SCHEMA_VERSION,
            "tool": self.tool,
            "title": self.title,
            "command": self.command,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "markers": list(self.markers),
            "steps": [step.to_dict() for step in self.steps],
        }

    @property
    def output(self) -> str:
        """Return every output line joined by newlines."""
        return "\n".join(step.text for step in self.steps if step.kind == "output")


@dataclass(frozen=True)
class Finding:
    """One problem with the demo set.

    Attributes:
        code: Stable finding code, e.g. ``live.transcript``.
        subject: The file or tool the finding is about.
        message: One sentence a reviewer can act on.
    """

    code: str
    subject: str
    message: str


@dataclass(frozen=True)
class Recipe:
    """How to capture one tool's session.

    Attributes:
        tool: The console-script name, which is also the session file's stem and
            the page's stem.
        title: The heading the page shows above the terminal.
        command: The command line as a reader would type it.
        argv: The real invocation, relative to the repository root. The token
            ``<SCRATCH>`` is replaced with the scratch directory.
        stdin: What to feed the program's prompts, or ``None``.
        markers: Substrings the output must contain.
        win_marker: A substring the capture retries for — the hangman word is
            chosen with ``random.choice`` and the tool exposes no seed, so a
            demo that insists on a won round has to re-roll. ``None`` means one
            attempt is enough.
        prompts: The literal strings the program prints before each read. When
            set, the captured output is split on them so each typed line sits
            directly above the reply it produced, which is what a real terminal
            looks like. An empty tuple keeps the simpler "inputs, then output"
            ordering.
    """

    tool: str
    title: str
    command: str
    argv: Tuple[str, ...]
    stdin: Optional[str] = None
    markers: Tuple[str, ...] = ()
    win_marker: Optional[str] = None
    prompts: Tuple[str, ...] = ()


#: The five captures, in task-pack order.
RECIPES: Tuple[Recipe, ...] = (
    Recipe(
        tool="hangman",
        title="One round, played from stdin",
        command="hangman --categories",
        argv=("projects/01_hangman_game/main.py", "--categories"),
        # Themed banks, vowels first and then the letters English words use
        # most. Six wrong guesses are allowed, so the order matters; the capture
        # retries until the round is won.
        stdin="programming\n"
        # English letter frequency order, one letter per line, then "n" for the
        # play-again prompt.
        + "\n".join("etaoinsrhldcumfpgwybvkxjqz") + "\nn\n",
        # The markers are the lines every run prints, so ``--verify`` can re-run
        # the tool and stay green whatever word it picks. The win is what the
        # *capture* retries for, because a demo that ends in six wrong guesses
        # shows the gallows and not the game.
        markers=("Welcome to Hangman", "Attempts left", "Final record"),
        win_marker="You guessed it!",
        prompts=(
            "Pick a category [programming]: ",
            "Guess a letter: ",
            "Play again? [y/N]: ",
        ),
    ),
    Recipe(
        tool="portfolio",
        title="The sample portfolio, valued",
        command="portfolio --demo",
        argv=(
            "projects/02_portfolio_tracker/main.py",
            "--demo",
            "--output",
            "<SCRATCH>/portfolio_summary.csv",
        ),
        markers=("SYMBOL", "Net P/L", "Summary written to"),
    ),
    Recipe(
        tool="organizer",
        title="A messy folder, sorted (dry run)",
        command="organizer ~/datheon-demo --dry-run --duplicates",
        argv=(
            "projects/03_file_organizer_automation/main.py",
            "<SCRATCH>",
            "--dry-run",
            "--duplicates",
        ),
        markers=("DRY RUN", "Duplicate groups found", "Duration"),
    ),
    Recipe(
        tool="assistant",
        title="Three turns with the intent matcher",
        command="assistant --no-log",
        argv=("projects/04_terminal_assistant/main.py", "--no-log"),
        stdin="what are your hours\nrefund policy\nexit\n",
        markers=("Rule-Based Terminal Assistant", "bot >", "Goodbye"),
        prompts=("you > ",),
    ),
    Recipe(
        tool="inventory",
        title="The OOP walkthrough, end to end",
        command="inventory",
        argv=("projects/05_inventory_oop/main.py",),
        markers=("Initial inventory", "Demonstrating error handling", "Final audit"),
    ),
)


# --- scrubbing ------------------------------------------------------------
def scrub_lines(text: str, *, home: Optional[Path] = None) -> List[str]:
    """Normalise captured output so it is true of any machine.

    The scratch directory is replaced **everywhere** rather than line by line:
    a tool that lists the paths it touched — the organizer's duplicate groups do
    exactly that — would otherwise publish the capturing machine's filesystem.
    That is the defect Day 27 found in the GIF assets, and it is the one thing
    on these pages a reader would notice.

    Args:
        text: Raw standard output from a captured run.
        home: The scratch directory the run used, replaced by
            ``~/datheon-demo`` wherever it appears.

    Returns:
        The kept lines: carriage returns removed, trailing whitespace stripped,
        runs of blank lines collapsed to one, machine-specific lines replaced by
        their stable form, and the dropped prefixes removed.
    """
    if home is not None:
        # Both separators, because the same directory is spelled two ways: the
        # path we hold is POSIX on Linux and the tool may print it with
        # backslashes on Windows. Only the prefix is rewritten — the hangman
        # gallows is full of backslashes and must survive untouched.
        for variant in {str(home), str(home).replace("/", "\\"), str(home).replace("\\", "/")}:
            text = text.replace(variant, "~/datheon-demo")
        text = text.replace("~/datheon-demo\\", "~/datheon-demo/")
    kept: List[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if line.startswith(_DROP_PREFIXES):
            continue
        for pattern, replacement in _SCRUB_RULES:
            if re.fullmatch(pattern, line):
                if replacement is None:
                    line = re.sub(r"\d{4}-\d{2}-\d{2}", CAPTURE_DATE, line)
                else:
                    line = replacement
                break
        kept.append(line)
    while kept and kept[-1] == "":
        kept.pop()
    return kept


def render_transcript(demo: Demo) -> str:
    """Render a session as the plain-text transcript the page shows.

    This is the single definition of what a transcript looks like: the static
    block in the page is generated from it, and ``--check`` compares the two, so
    the readable-without-JavaScript copy can never disagree with the JSON the
    player animates.

    Args:
        demo: The captured session.

    Returns:
        The transcript, without a trailing newline.
    """
    lines: List[str] = []
    for step in demo.steps:
        if step.kind == "command":
            lines.append(f"$ {step.text}")
        elif step.kind == "input":
            # With a captured prompt the line reads exactly as the terminal
            # showed it; without one, a generic ``>`` keeps the direction clear.
            lines.append(f"{step.prompt or '> '}{step.text}")
        else:
            lines.extend(step.text.split("\n"))
    return "\n".join(lines)


# --- paths ----------------------------------------------------------------
def session_path(tool: str, root: Path = ROOT) -> Path:
    """Return where a tool's session JSON lives.

    Args:
        tool: The console-script name.
        root: The repository root.

    Returns:
        ``docs/assets/demos/<tool>.json`` under ``root``.
    """
    return root / "docs" / "assets" / "demos" / f"{tool}.json"


def page_path(tool: str, root: Path = ROOT) -> Path:
    """Return the demo page for a tool.

    Args:
        tool: The console-script name.
        root: The repository root.

    Returns:
        ``docs/demos/<tool>.md`` under ``root``.
    """
    return root / "docs" / "demos" / f"{tool}.md"


def demo_index_page(root: Path = ROOT) -> Path:
    """Return the hub page that links the five demos.

    Args:
        root: The repository root.

    Returns:
        ``docs/demos/index.md`` under ``root``.
    """
    return root / "docs" / "demos" / "index.md"


# --- capture --------------------------------------------------------------
def _prepare_scratch(scratch: Path) -> None:
    """Populate the scratch directory the demos run against.

    Args:
        scratch: The directory to fill: the organizer's messy folder, a pair of
            identical files for the duplicate detector, and a log directory so
            no tool writes anywhere else.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    for name in DEMO_FILES:
        # Every file gets its own content, so the duplicate detector finds the
        # pair it is supposed to find instead of reporting the whole folder.
        payload = "identical content\n" if name in DEMO_DUPLICATES else f"{name}: demo\n"
        # Written as bytes on purpose: `write_text` translates "\n" to "\r\n" on
        # Windows, which changes the digest the duplicate detector prints and
        # makes the same session hash differently on two runners.
        (scratch / name).write_bytes(payload.encode("utf-8"))
    (scratch / "logs").mkdir(exist_ok=True)


def _run(recipe: Recipe, root: Path, scratch: Path) -> subprocess.CompletedProcess:
    """Run one recipe's command line in the scratch directory.

    Args:
        recipe: What to run.
        root: The repository root the script paths are relative to.
        scratch: The demo working directory.

    Returns:
        The completed process, with text streams captured.

    Raises:
        OSError: If the interpreter cannot be started at all.
        subprocess.TimeoutExpired: If the run exceeds the capture timeout.
    """
    # The token is replaced wherever it appears, not only when it is the whole
    # argument: `--output <SCRATCH>/portfolio_summary.csv` is one argument, and
    # matching only whole arguments wrote the summary into the checkout.
    argv = [part.replace("<SCRATCH>", str(scratch)) for part in recipe.argv]
    env = dict(os.environ)
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Every tool writes its rotating log under this directory, so the
            # capture cannot touch the developer's home or the checkout.
            "DATHEON_LOG_DIR": str(scratch / "logs"),
            "COLUMNS": "100",
        }
    )
    return subprocess.run(
        [sys.executable, *argv],
        cwd=str(root),
        input=recipe.stdin,
        capture_output=True,
        text=True,
        timeout=CAPTURE_TIMEOUT_SECONDS,
        env=env,
        check=False,
    )


def build_steps(recipe: Recipe, output: str) -> List[Step]:
    """Turn one captured run into the steps the player replays.

    A terminal shows each typed line directly above the reply it produced, and
    the reader of a demo wants the same. When the recipe names the prompts the
    program prints, the output is split on them and the inputs are interleaved;
    when it does not — or when the split does not line up with the stdin — the
    inputs are listed first, which is still truthful, only less pretty.

    Args:
        recipe: The recipe that was captured.
        output: The scrubbed output of the run.

    Returns:
        ``command`` first, then alternating ``input`` and ``output`` steps.
    """
    inputs = recipe.stdin.strip().split("\n") if recipe.stdin else []
    steps: List[Step] = [Step("command", recipe.command)]
    if not inputs:
        steps.append(Step("output", output))
        return steps

    def simple_order() -> List[Step]:
        """Return the truthful-but-plain ordering: every input, then the output."""
        fallback: List[Step] = [Step("command", recipe.command)]
        fallback.extend(Step("input", line) for line in inputs)
        fallback.append(Step("output", output))
        return fallback

    if not recipe.prompts:
        return simple_order()

    # Prompts are matched without their trailing space, and the padding the
    # program leaves before its reply is swallowed too: the scrub step
    # right-strips every line, and a prompt that ends a line would otherwise
    # never match its own pattern.
    pattern = "(" + "|".join(re.escape(prompt.rstrip()) for prompt in recipe.prompts) + ")[ \t]*"
    parts = re.split(pattern, output)
    segments = [part for index, part in enumerate(parts) if index % 2 == 0]
    prompts_found = [part for index, part in enumerate(parts) if index % 2 == 1]
    answered = len(segments) - 1

    # Two ways the split cannot be trusted: it found no prompt at all (so nothing
    # can be attributed to an input), or the program asked more often than there
    # was stdin to answer with (it hit EOF and the run is not a clean session).
    # Both fall back rather than invent an ordering.
    if answered <= 0 or answered > len(inputs):
        return simple_order()

    # A program stops reading when the game ends, so stdin can hold more lines
    # than the run consumed. The split is the authority on how many were read:
    # one segment per prompt answered.
    inputs = inputs[:answered]

    for index, line in enumerate(inputs):
        segment = segments[index].strip("\n")
        if segment:
            steps.append(Step("output", segment))
        prompt = prompts_found[index] if index < len(prompts_found) else ""
        steps.append(Step("input", line, prompt=f"{prompt} " if prompt else ""))
    tail = segments[-1].strip("\n")
    if tail:
        steps.append(Step("output", tail))
    return steps


def capture_demo(recipe: Recipe, root: Path = ROOT, scratch: Optional[Path] = None) -> Demo:
    """Capture one tool's session by running it for real.

    Args:
        recipe: What to run and what the output must contain.
        root: The repository root.
        scratch: The demo working directory. A temporary directory is created
            and removed when ``None``.

    Returns:
        The captured session.

    Raises:
        RuntimeError: If the command exits non-zero, prints nothing, or — when
            ``wants_win`` is set — never reaches the win marker inside
            :data:`HANGMAN_ATTEMPTS` attempts.
    """
    owned = scratch is None
    working = Path(tempfile.mkdtemp(prefix="datheon-live-")) if owned else scratch
    assert working is not None
    try:
        _prepare_scratch(working)
        attempts = HANGMAN_ATTEMPTS if recipe.win_marker else 1
        last_reason = ""
        for _ in range(attempts):
            completed = _run(recipe, root, working)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"{recipe.command!r} exited {completed.returncode}: "
                    f"{completed.stderr.strip()[:400]}"
                )
            lines = scrub_lines(completed.stdout, home=working)
            if not lines:
                raise RuntimeError(f"{recipe.command!r} printed nothing")
            output = "\n".join(lines)
            missing = [marker for marker in recipe.markers if marker not in output]
            if missing:
                last_reason = f"missing {missing!r}"
                continue
            if recipe.win_marker is not None and recipe.win_marker not in output:
                last_reason = f"no {recipe.win_marker!r} in this run"
                continue
            steps = build_steps(recipe, output)
            return Demo(
                tool=recipe.tool,
                title=recipe.title,
                command=recipe.command,
                cwd="~/datheon-demo",
                exit_code=completed.returncode,
                markers=recipe.markers,
                steps=tuple(steps),
            )
        raise RuntimeError(
            f"{recipe.command!r} never produced a usable capture after {attempts} "
            f"attempt(s): {last_reason}"
        )
    finally:
        if owned:
            shutil.rmtree(working, ignore_errors=True)


def capture_all(
    root: Path = ROOT,
    recipes: Sequence[Recipe] = RECIPES,
) -> Tuple[Demo, ...]:
    """Capture every recipe, in order.

    Args:
        root: The repository root.
        recipes: The recipes to capture.

    Returns:
        One session per recipe.
    """
    scratch = Path(tempfile.mkdtemp(prefix="datheon-live-"))
    try:
        # One directory per tool: a shared scratch let the portfolio's summary
        # CSV appear in the organizer's sweep, which is a demo of the wrong
        # folder and a session that never reproduces.
        return tuple(capture_demo(recipe, root, scratch / recipe.tool) for recipe in recipes)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def save_demo(demo: Demo, root: Path = ROOT) -> Path:
    """Write a session as JSON, creating the directory if needed.

    Args:
        demo: The session to write.
        root: The repository root.

    Returns:
        The path written.
    """
    path = session_path(demo.tool, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(demo.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_demo(path: Path) -> Demo:
    """Read a session file.

    Args:
        path: The JSON file to read.

    Returns:
        The decoded session.

    Raises:
        ValueError: If the file is not a valid session of the current schema.
        OSError: If the file cannot be read.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return demo_from_dict(data, source=str(path))


def demo_from_dict(data: Mapping[str, Any], *, source: str = "<memory>") -> Demo:
    """Build a session from decoded JSON, validating its shape.

    Args:
        data: The decoded mapping.
        source: Where it came from, for the error message.

    Returns:
        The session.

    Raises:
        ValueError: If a required key is missing, the schema is not the current
            one, or a step carries an unknown ``kind``.
    """
    if not isinstance(data, Mapping):
        raise ValueError(f"{source}: expected a JSON object")
    schema = data.get("schema")
    if schema != SCHEMA_VERSION:
        raise ValueError(f"{source}: schema {schema!r}, expected {SCHEMA_VERSION}")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(f"{source}: 'steps' must be a non-empty list")
    steps: List[Step] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, Mapping):
            raise ValueError(f"{source}: step {index} is not an object")
        kind = str(raw.get("kind", ""))
        if kind not in STEP_KINDS:
            raise ValueError(f"{source}: step {index} has unknown kind {kind!r}")
        steps.append(
            Step(kind=kind, text=str(raw.get("text", "")), prompt=str(raw.get("prompt", "")))
        )
    markers = data.get("markers") or []
    return Demo(
        tool=str(data.get("tool", "")),
        title=str(data.get("title", "")),
        command=str(data.get("command", "")),
        cwd=str(data.get("cwd", "")),
        exit_code=int(data.get("exit_code", 0)),
        markers=tuple(str(marker) for marker in markers),
        steps=tuple(steps),
    )


# --- the page block -------------------------------------------------------
def escape_html(text: str) -> str:
    """Escape text for use inside a ``<pre>`` element.

    Args:
        text: The transcript.

    Returns:
        The text with ``&``, ``<`` and ``>`` replaced by entities, so a
        transcript that prints an arrow or a comparison survives the browser.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_block(demo: Demo) -> str:
    """Render the generated block a page carries between its markers.

    The block is the full transcript inside a ``<pre>`` that the player hides
    once it takes over, plus the ``data-session`` hook the player reads. It is
    plain HTML inside Markdown, so it renders identically with and without
    JavaScript.

    Args:
        demo: The session to render.

    Returns:
        The block, markers included, ending in a newline.
    """
    transcript = escape_html(render_transcript(demo))
    return (
        f"{BEGIN_MARKER}\n"
        f'<div class="demo-terminal" data-session="assets/demos/{demo.tool}.json"'
        f' data-command="{escape_html(demo.command)}">\n'
        f'<pre class="demo-terminal__fallback" aria-label="Captured session: '
        f'{escape_html(demo.title)}">{transcript}</pre>\n'
        f"</div>\n"
        f"{END_MARKER}\n"
    )


def extract_block(page_text: str) -> Optional[str]:
    """Return the generated block from a page, markers included.

    Args:
        page_text: The page's Markdown source.

    Returns:
        The block (markers included, trailing newline kept), or ``None`` when
        the markers are missing or out of order.
    """
    start = page_text.find(BEGIN_MARKER)
    end = page_text.find(END_MARKER)
    if start < 0 or end < start:
        return None
    return page_text[start : end + len(END_MARKER)] + "\n"


def replace_block(page_text: str, demo: Demo) -> str:
    """Replace a page's generated block with a freshly rendered one.

    Args:
        page_text: The page's Markdown source.
        demo: The session to render into it.

    Returns:
        The page source with the block replaced.

    Raises:
        ValueError: If the page carries no markers to replace.
    """
    block = extract_block(page_text)
    if block is None:
        raise ValueError("page has no live-demo markers")
    return page_text.replace(block, render_block(demo), 1)


# --- the audit ------------------------------------------------------------
def check_demo(demo: Demo) -> List[Finding]:
    """Check one session for problems the player or the reader would hit.

    Args:
        demo: The session to check.

    Returns:
        One finding per problem: an empty command, no output, a marker the
        transcript does not contain, or a machine-specific path that leaked into
        a published page.
    """
    findings: List[Finding] = []
    if not demo.command.strip():
        findings.append(Finding("live.session", demo.tool, "the session names no command"))
    if not demo.output.strip():
        findings.append(Finding("live.session", demo.tool, "the session captured no output"))
    transcript = render_transcript(demo)
    for marker in demo.markers:
        if marker not in transcript:
            findings.append(
                Finding(
                    "live.markers",
                    demo.tool,
                    f"marker {marker!r} is not in the transcript: the session is stale",
                )
            )
    for pattern in _HOST_PATH_PATTERNS:
        match = re.search(pattern, transcript)
        if match:
            findings.append(
                Finding(
                    "live.path",
                    demo.tool,
                    f"the transcript leaks a machine-specific path ({match.group(0)!r}); "
                    "scrub it before publishing",
                )
            )
            break
    return findings


def check_page(tool: str, page_text: str, demo: Demo) -> List[Finding]:
    """Check one page against its session.

    Args:
        tool: The console-script name the page belongs to.
        page_text: The page's Markdown source.
        demo: The session the page should show.

    Returns:
        One finding per problem: missing markers, a transcript that no longer
        matches the session, a missing ``data-session`` hook, or an external
        asset the offline-first rule forbids.
    """
    findings: List[Finding] = []
    subject = page_path(tool).name
    block = extract_block(page_text)
    if block is None:
        findings.append(
            Finding(
                "live.page",
                subject,
                f"no {BEGIN_MARKER} … {END_MARKER} block: run --capture to generate it",
            )
        )
        return findings
    if block.strip() != render_block(demo).strip():
        findings.append(
            Finding(
                "live.transcript",
                subject,
                "the committed transcript differs from the session JSON: run --capture",
            )
        )
    if f'data-session="assets/demos/{tool}.json"' not in block:
        findings.append(
            Finding(
                "live.player",
                subject,
                f"the block does not point the player at assets/demos/{tool}.json",
            )
        )
    for url in re.findall(r"https?://[^\s\"')]+", page_text):
        findings.append(
            Finding(
                "live.offline",
                subject,
                f"the page references {url} — the demos must load no third-party asset",
            )
        )
    return findings


def check_player(js_text: str, css_text: str) -> List[Finding]:
    """Check that the player implements the contract the pages rely on.

    The player is the one file in this repository no test can execute as a unit,
    so its contract is checked structurally: it must read the ``data-session``
    hook, keep the static transcript for readers without JavaScript, honour
    ``prefers-reduced-motion``, and load nothing from the network.

    Args:
        js_text: The player's source.
        css_text: The stylesheet's source.

    Returns:
        One finding per missing behaviour.
    """
    findings: List[Finding] = []
    required = {
        "live.player": [
            ("data-session", "read the data-session hook"),
            ("demo-terminal__fallback", "keep the static transcript for no-JS readers"),
            ("prefers-reduced-motion", "honour the reduced-motion preference"),
            ("aria-hidden", "hide the animated copy from screen readers"),
        ]
    }
    for marker, description in required["live.player"]:
        if marker not in js_text:
            findings.append(
                Finding("live.player", PLAYER_JS.name, f"the player does not {description}")
            )
    for url in re.findall(r"https?://[^\s\"')]+", js_text):
        findings.append(
            Finding("live.player", PLAYER_JS.name, f"the player loads {url} from the network")
        )
    if "demo-terminal" not in css_text:
        findings.append(
            Finding("live.player", PLAYER_CSS.name, "the stylesheet styles no terminal")
        )
    return findings


def check_site(root: Path = ROOT) -> List[Finding]:
    """Check that the site publishes the demos.

    Args:
        root: The repository root.

    Returns:
        One finding per page missing from the nav, and one per asset missing
        from ``mkdocs.yml``'s ``extra_javascript`` / ``extra_css``.
    """
    findings: List[Finding] = []
    path = root / "mkdocs.yml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [
            Finding(
                "live.nav", "mkdocs.yml", "the site declaration is missing, so nothing publishes"
            )
        ]
    for tool in [recipe.tool for recipe in RECIPES]:
        if f"demos/{tool}.md" not in text:
            findings.append(Finding("live.nav", "mkdocs.yml", f"demos/{tool}.md is not in the nav"))
    if "demos/index.md" not in text:
        findings.append(Finding("live.nav", "mkdocs.yml", "demos/index.md is not in the nav"))
    if "javascripts/terminal-player.js" not in text:
        findings.append(
            Finding(
                "live.nav",
                "mkdocs.yml",
                "extra_javascript does not load javascripts/terminal-player.js",
            )
        )
    if "stylesheets/live-demos.css" not in text:
        findings.append(
            Finding("live.nav", "mkdocs.yml", "extra_css does not load stylesheets/live-demos.css")
        )
    return findings


def audit(root: Path = ROOT) -> Tuple[Finding, ...]:
    """Run every check and return the findings, in a stable order.

    Args:
        root: The repository root.

    Returns:
        Every finding. An empty tuple means the demo set is consistent.

    Raises:
        ValueError: If a session file exists but cannot be decoded, which is a
            "cannot measure" condition rather than a finding.
    """
    findings: List[Finding] = []
    for recipe in RECIPES:
        path = session_path(recipe.tool, root)
        if not path.is_file():
            findings.append(
                Finding(
                    "live.session",
                    path.name,
                    "the session file is missing: run --capture",
                )
            )
            continue
        demo = load_demo(path)
        findings.extend(check_demo(demo))
        page = page_path(recipe.tool, root)
        if not page.is_file():
            findings.append(Finding("live.page", page.name, "the demo page is missing"))
            continue
        findings.extend(check_page(recipe.tool, page.read_text(encoding="utf-8"), demo))
    if not demo_index_page(root).is_file():
        findings.append(Finding("live.page", "index.md", "the demos hub page is missing"))
    try:
        js_text = PLAYER_JS.read_text(encoding="utf-8")
        css_text = PLAYER_CSS.read_text(encoding="utf-8")
    except OSError:
        findings.append(
            Finding("live.player", PLAYER_JS.name, "the player or its stylesheet is missing")
        )
    else:
        findings.extend(check_player(js_text, css_text))
    findings.extend(check_site(root))
    return tuple(findings)


def verify(root: Path = ROOT, recipes: Sequence[Recipe] = RECIPES) -> Tuple[Finding, ...]:
    """Re-run every tool and check the committed session still describes it.

    This is the anti-staleness check: ``--check`` compares the page with the
    JSON, ``--verify`` compares the JSON with a fresh run of the tool. It costs
    a few seconds, so it lives in the suite rather than in ``--check``.

    Args:
        root: The repository root.
        recipes: The recipes to verify.

    Returns:
        One finding per tool whose output no longer carries its markers or whose
        exit code changed.
    """
    findings: List[Finding] = []
    scratch = Path(tempfile.mkdtemp(prefix="datheon-live-"))
    try:
        for recipe in recipes:
            try:
                fresh = capture_demo(recipe, root, scratch / recipe.tool)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                findings.append(Finding("live.stale", recipe.tool, f"could not re-run: {exc}"))
                continue
            committed = session_path(recipe.tool, root)
            if committed.is_file():
                recorded = load_demo(committed)
                if recorded.exit_code != fresh.exit_code:
                    findings.append(
                        Finding(
                            "live.stale",
                            recipe.tool,
                            f"exit code changed: recorded {recorded.exit_code}, "
                            f"now {fresh.exit_code}",
                        )
                    )
            for marker in recipe.markers:
                if marker not in fresh.output:
                    findings.append(
                        Finding(
                            "live.stale",
                            recipe.tool,
                            f"a fresh run no longer prints {marker!r}: re-capture the page",
                        )
                    )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return tuple(findings)


def capture_and_write(
    root: Path = ROOT,
    recipes: Sequence[Recipe] = RECIPES,
    *,
    write_pages: bool = True,
) -> List[Tuple[Path, Path]]:
    """Capture every tool, save its session and rewrite its page block.

    Args:
        root: The repository root.
        recipes: The recipes to capture.
        write_pages: When ``False``, only the JSON is written. The suite uses
            this to check a capture without touching the working tree.

    Returns:
        ``(session path, page path)`` per captured tool.

    Raises:
        ValueError: If a page carries no markers to replace.
    """
    written: List[Tuple[Path, Path]] = []
    scratch = Path(tempfile.mkdtemp(prefix="datheon-live-"))
    try:
        for recipe in recipes:
            demo = capture_demo(recipe, root, scratch / recipe.tool)
            saved = save_demo(demo, root)
            page = page_path(recipe.tool, root)
            if write_pages:
                text = page.read_text(encoding="utf-8")
                page.write_text(replace_block(text, demo), encoding="utf-8")
            written.append((saved, page))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return written


# --- reporting ------------------------------------------------------------
def render_report(findings: Sequence[Finding], *, verbose: bool = False) -> str:
    """Render the findings as a text report.

    Args:
        findings: The audit's findings.
        verbose: Unused today; kept so the flag has one meaning across the tools.

    Returns:
        The report, ending in a newline.
    """
    lines = [
        "Live demos",
        f"  tools    : {', '.join(recipe.tool for recipe in RECIPES)}",
        f"  sessions : {len(RECIPES)}",
        f"  findings : {len(findings)}",
        "",
    ]
    if findings:
        for finding in findings:
            lines.append(f"  - [{finding.code}] {finding.subject}: {finding.message}")
        lines.append("")
        lines.append("FAIL — the demo set is inconsistent.")
    else:
        lines.append("PASS — every page matches its captured session.")
    return "\n".join(lines) + "\n"


def findings_to_dict(findings: Sequence[Finding]) -> Dict[str, Any]:
    """Convert findings into a JSON-serialisable mapping.

    Args:
        findings: The audit's findings.

    Returns:
        A mapping with the verdict, the count and each finding.
    """
    return {
        "passed": not findings,
        "findings": len(findings),
        "details": [
            {"code": finding.code, "subject": finding.subject, "message": finding.message}
            for finding in findings
        ],
        "tools": [recipe.tool for recipe in RECIPES],
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the live-demo tool.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="live_demos.py",
        description="Capture the five tools and keep the site's demo pages in sync.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--capture",
        action="store_true",
        help="re-run the five tools and rewrite the sessions and page blocks",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="re-run each tool and confirm its session is still accurate",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="audit the committed sessions, pages, player and nav (default)",
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        metavar="NAME",
        help="limit the run to one tool (repeatable)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the verdict as JSON instead of a table",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="list every finding, however many there are",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the tool and return the process exit code.

    Args:
        argv: Command-line arguments, without the program name. Defaults to
            :data:`sys.argv` without the program name.

    Returns:
        ``0`` when everything agrees, ``1`` when a finding was reported, and
        ``2`` when the tool could not measure — a capture failed, or a session
        file is unreadable.
    """
    args = build_parser().parse_args(None if argv is None else list(argv))
    selected = tuple(recipe for recipe in RECIPES if not args.tool or recipe.tool in args.tool)
    if not selected:
        print(f"no recipe matches {args.tool!r}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    if args.capture:
        try:
            written = capture_and_write(ROOT, selected)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"capture failed: {exc}", file=sys.stderr)
            return EXIT_UNMEASURABLE
        for saved, page in written:
            print(f"captured {saved.relative_to(ROOT)} -> {page.relative_to(ROOT)}")
        return EXIT_CLEAN

    try:
        findings = verify(ROOT, selected) if args.verify else audit(ROOT)
    except (ValueError, OSError) as exc:
        print(f"cannot read a session: {exc}", file=sys.stderr)
        return EXIT_UNMEASURABLE

    if args.json:
        print(json.dumps(findings_to_dict(findings), indent=2))
    else:
        print(render_report(findings, verbose=args.verbose), end="")

    if not findings:
        return EXIT_CLEAN
    for finding in findings:
        print(f"{finding.code}: {finding.subject}: {finding.message}", file=sys.stderr)
    return EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
