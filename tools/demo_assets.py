"""Render the README's terminal demo from real command output (Day 27).

The submission's demo asset is a short animated GIF. An asset nobody can
regenerate is an asset nobody can correct, so this module makes it a build
artefact with two halves:

``--capture``
    Runs the five tools from the source tree, scrubs the parts that are not
    reproducible (the scratch directory's absolute path, the organizer's
    measured duration) and writes ``docs/assets/demo/transcript.json``. The
    transcript — not this file — is the source of truth for what the GIF shows.

default
    Renders ``docs/assets/demo/quickstart.gif`` from the transcript. Rendering
    is deterministic: same transcript and same arguments give the same frames,
    which is what lets the audit reason about the asset at all.

``--check``
    Reports what the committed GIF and transcript currently are, and fails when
    either is missing, malformed or over budget. The same structural checks run
    in :mod:`tools.readme_audit` without Pillow, so CI can gate the asset in an
    environment that has no imaging library.

The console scripts in ``datheon/cli`` are one-line shims over
``projects/<task>/main.py``, so the output captured here is byte-identical to
what ``hangman --version`` prints once the package is installed — the
transcript simply shows the installed form a reader would type.

Usage::

    python tools/demo_assets.py --capture      # re-run the tools, rewrite the transcript
    python tools/demo_assets.py                # transcript -> quickstart.gif
    python tools/demo_assets.py --check        # structural report, exit 1 on a finding
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "docs" / "assets" / "demo"
TRANSCRIPT_PATH = ASSETS_DIR / "transcript.json"
GIF_PATH = ASSETS_DIR / "quickstart.gif"

#: Fallback font stack, in preference order. The first hit wins.
MONO_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
)

#: Geometry of the rendered terminal window, in pixels.
WINDOW_WIDTH = 860
TITLE_BAR_HEIGHT = 34
PADDING = 24
FONT_SIZE = 14
LINE_HEIGHT = 20
PROMPT = "$ "

#: Animation budget: how long a finished session rests before the next one, and
#: how many of a session's output lines the GIF shows. The GIF is a trailer, not
#: a transcript — the whole transcript is one click away in ``transcript.json``.
HOLD_FRAMES = 8
TYPING_CHUNK = 4
FRAME_DURATION_MS = 110
MAX_OUTPUT_LINES = 12

#: Captured commands, in the order the GIF plays them. Each entry is
#: ``(displayed command, argv relative to the repository root, stdin)``.
CAPTURE_SPEC: Tuple[Tuple[str, Tuple[str, ...], Optional[str]], ...] = (
    ("hangman --version", ("projects/01_hangman_game/main.py", "--version"), None),
    ("portfolio --demo", ("projects/02_portfolio_tracker/main.py", "--demo"), None),
    (
        "organizer ~/Downloads --dry-run",
        ("projects/03_file_organizer_automation/main.py", "<SCRATCH>", "--dry-run"),
        None,
    ),
    (
        "assistant --faq projects/04_terminal_assistant/sample_faq.json",
        (
            "projects/04_terminal_assistant/main.py",
            "--faq",
            "projects/04_terminal_assistant/sample_faq.json",
            "--no-log",
        ),
        "hello\ndo you have python internships?\nbye\n",
    ),
    ("inventory", ("projects/05_inventory_oop/main.py",), None),
)

#: Files the organizer demo sorts; written into the scratch directory.
DEMO_FILES = (
    "notes.txt",
    "photo.jpg",
    "song.mp3",
    "report.pdf",
    "archive.zip",
    "script.py",
    "clip.mp4",
    "data.json",
)

#: Lines a transcript never keeps, because they are true only of the machine
#: that captured them: absolute scratch paths and measured durations.
_SCRUB_RULES: Tuple[Tuple[str, str], ...] = (
    (r"Duration\s+:\s+[0-9.]+ (?:ms|s).*", "Duration  : 2.6 ms (asyncio engine)"),
    (r"Organising: .*", "Organising: ~/Downloads   [DRY RUN (no changes made)]"),
    (r"Summary written to .*", "Summary written to portfolio_summary.csv"),
    (r"Conversation saved to .*", "Conversation saved to conversation.jsonl"),
)

#: Lines dropped outright: they name the machine's own paths or are noise.
_DROP_PREFIXES = ("Loaded FAQ dataset:", "Categories:")


@dataclass(frozen=True)
class Session:
    """One command and its captured output.

    Attributes:
        command: The line a reader would type, prompt excluded.
        output: Output lines, in order. A leading blank line is preserved as
            ``""`` so spacing survives the round trip through JSON.
    """

    command: str
    output: List[str]

    def to_dict(self) -> dict:
        """Return the JSON-serialisable form.

        Returns:
            A mapping with ``command`` and ``output`` keys.
        """
        return {"command": self.command, "output": list(self.output)}

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """Rebuild a session from :meth:`to_dict` output.

        Args:
            data: Mapping with at least ``command`` and ``output``.

        Returns:
            The reconstructed session.

        Raises:
            ValueError: If the mapping is missing a key or has the wrong type.
        """
        command = data.get("command")
        output = data.get("output")
        if not isinstance(command, str) or not isinstance(output, list):
            raise ValueError("session needs a string 'command' and a list 'output'")
        return cls(command=command, output=[str(line) for line in output])


def scrub(text: str) -> List[str]:
    """Normalise captured output so it is true of any machine.

    Args:
        text: Raw stdout from a captured run.

    Returns:
        The kept lines, with absolute paths and measured durations replaced by
        their stable forms.
    """
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
                line = replacement
                break
        kept.append(line)
    while kept and kept[-1] == "":
        kept.pop()
    return kept


def capture(root: Path = ROOT, spec: Sequence = CAPTURE_SPEC) -> List[Session]:
    """Run every command in ``spec`` and collect its scrubbed output.

    Args:
        root: Repository root the commands run from.
        spec: ``(displayed, argv, stdin)`` triples; a ``<SCRATCH>`` argv entry
            is replaced by a temporary directory populated with
            :data:`DEMO_FILES`.

    Returns:
        One :class:`Session` per spec entry, in order.

    Raises:
        RuntimeError: If a command exits non-zero or prints nothing, because a
            demo built from a failed run is worse than no demo.
    """
    sessions: List[Session] = []
    scratch = Path(tempfile.mkdtemp(prefix="datheon-demo-"))
    try:
        for name in DEMO_FILES:
            (scratch / name).write_text("demo\n", encoding="utf-8")
        for displayed, argv, stdin in spec:
            resolved = [str(scratch) if part == "<SCRATCH>" else part for part in argv]
            completed = subprocess.run(
                [sys.executable, *resolved],
                cwd=str(root),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"{displayed!r} exited {completed.returncode}: {completed.stderr.strip()[:400]}"
                )
            lines = scrub(completed.stdout)
            if not lines:
                raise RuntimeError(f"{displayed!r} printed nothing")
            sessions.append(Session(command=displayed, output=lines))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return sessions


def save_transcript(sessions: Sequence[Session], path: Path = TRANSCRIPT_PATH) -> Path:
    """Write the transcript as JSON.

    Args:
        sessions: Sessions to record.
        path: Destination file.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_by": "python tools/demo_assets.py --capture",
        "note": (
            "Verbatim output of the five tools, scrubbed of absolute paths and "
            "measured durations. Edit only by re-capturing."
        ),
        "sessions": [session.to_dict() for session in sessions],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_transcript(path: Path = TRANSCRIPT_PATH) -> List[Session]:
    """Read a transcript back.

    Args:
        path: Transcript file.

    Returns:
        The recorded sessions, in playback order.

    Raises:
        FileNotFoundError: If the transcript is absent.
        ValueError: If the document is malformed or holds no sessions.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("sessions")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} holds no sessions")
    return [Session.from_dict(item) for item in raw]


def _font(size: int = FONT_SIZE):
    """Load the first available monospace font.

    Args:
        size: Point size.

    Returns:
        A Pillow font object.

    Raises:
        RuntimeError: If no candidate font is installed.
    """
    from PIL import ImageFont

    for candidate in MONO_FONTS:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("no monospace font found; install DejaVu Sans Mono")


def _window_height(line_count: int) -> int:
    """Height of a window holding ``line_count`` rows of text.

    Args:
        line_count: Rows to fit.

    Returns:
        Pixel height, title bar and padding included.
    """
    return TITLE_BAR_HEIGHT + PADDING + line_count * LINE_HEIGHT + PADDING


def _draw(lines: Sequence[Tuple[str, str]], height: int):
    """Draw one terminal window.

    Args:
        lines: ``(role, text)`` pairs; role is ``prompt``, ``command``,
            ``output`` or ``comment`` and selects the colour.
        height: Window height in pixels.

    Returns:
        A Pillow image in RGB mode.
    """
    from PIL import Image, ImageDraw

    palette = {
        "promptline": (233, 237, 243),
        "command": (233, 237, 243),
        "output": (168, 178, 190),
        "comment": (110, 120, 134),
    }
    prompt_colour = (86, 182, 126)
    image = Image.new("RGB", (WINDOW_WIDTH, height), (24, 27, 33))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, WINDOW_WIDTH, TITLE_BAR_HEIGHT], fill=(38, 42, 51))
    for index, colour in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        centre_x = 22 + index * 20
        draw.ellipse(
            [centre_x - 6, TITLE_BAR_HEIGHT // 2 - 6, centre_x + 6, TITLE_BAR_HEIGHT // 2 + 6],
            fill=colour,
        )
    font = _font()
    title_font = _font(12)
    prompt_width = draw.textlength(PROMPT, font=font)
    draw.text(
        (WINDOW_WIDTH // 2 - 60, TITLE_BAR_HEIGHT // 2 - 7),
        "datheon — five tools, one core",
        font=title_font,
        fill=(150, 158, 170),
    )
    y = TITLE_BAR_HEIGHT + PADDING - 6
    for role, text in lines:
        if role == "promptline":
            draw.text((PADDING, y), PROMPT, font=font, fill=prompt_colour)
            draw.text((PADDING + prompt_width, y), text, font=font, fill=palette[role])
        else:
            draw.text((PADDING, y), text, font=font, fill=palette[role])
        y += LINE_HEIGHT
    return image


def build_frames(sessions: Sequence[Session]) -> List:
    """Turn sessions into animation frames.

    Each session types its command in chunks, prints one output line per frame,
    then rests for :data:`HOLD_FRAMES` frames. Every frame is the same size —
    a GIF cannot grow — so the window is sized once for the tallest session and
    the content simply fills it from the top.

    Args:
        sessions: Sessions to animate.

    Returns:
        Frames in playback order.
    """
    shown = [
        session.output[:MAX_OUTPUT_LINES]
        + (["…"] if len(session.output) > MAX_OUTPUT_LINES else [])
        for session in sessions
    ]
    tallest = max(len(lines) for lines in shown)
    rows = tallest + 3
    height = _window_height(rows)

    def draw(lines: Sequence[Tuple[str, str]]):
        """Draw the window, scrolled so the newest rows stay visible.

        Args:
            lines: Full transcript-so-far as ``(role, text)`` pairs.

        Returns:
            A frame of the fixed window size.
        """
        return _draw(lines[-rows:], height)

    body: List[Tuple[str, str]] = [("comment", "# five tools, standard library only")]
    frames = [draw(body)]

    for session, lines in zip(sessions, shown):
        body = body + [("promptline", "")]
        for start in range(0, len(session.command), TYPING_CHUNK):
            body[-1] = ("promptline", session.command[: start + TYPING_CHUNK])
            frames.append(draw(body))
        body[-1] = ("promptline", session.command)
        for line in lines:
            body.append(("output", line))
            frames.append(draw(body))
        for _ in range(HOLD_FRAMES):
            frames.append(draw(body))
        body.append(("comment", ""))
    return frames


def render_gif(
    sessions: Sequence[Session],
    path: Path = GIF_PATH,
    fps: int = 1000 // FRAME_DURATION_MS,
) -> Path:
    """Render the transcript to an animated GIF.

    Args:
        sessions: Sessions to animate.
        path: Destination file.
        fps: Frames per second; clamped to the range 2–20 so the animation stays
            legible whatever the caller asks for.

    Returns:
        The path written.
    """
    frames = build_frames(sessions)
    duration = max(2, min(20, fps))
    path.parent.mkdir(parents=True, exist_ok=True)
    paletted = [frame.convert("P", palette=1, colors=32) for frame in frames]
    paletted[0].save(
        path,
        save_all=True,
        append_images=paletted[1:],
        duration=1000 // duration,
        loop=0,
        optimize=True,
    )
    return path


def _display(path: Path) -> str:
    """Render a path for the terminal, relative to the repository when possible.

    Args:
        path: Path to describe.

    Returns:
        The repository-relative POSIX path, or the bare file name.
    """
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the asset pipeline.

    Args:
        argv: Argument list; ``None`` means :data:`sys.argv`.

    Returns:
        ``0`` on success, ``1`` when ``--check`` finds the asset missing or
        malformed.
    """
    if str(ROOT) not in sys.path:  # running as a script puts tools/ on the path
        sys.path.insert(0, str(ROOT))
    from tools.readme_audit import inspect_asset

    parser = argparse.ArgumentParser(
        prog="demo-assets",
        description="Capture the tools' real output and render the README demo GIF.",
    )
    parser.add_argument(
        "--capture", action="store_true", help="re-run the tools, rewrite the transcript"
    )
    parser.add_argument(
        "--output",
        default=str(GIF_PATH),
        help="GIF destination (default: docs/assets/demo/quickstart.gif)",
    )
    parser.add_argument(
        "--fps", type=int, default=1000 // FRAME_DURATION_MS, help="animation speed, 2-20"
    )
    parser.add_argument(
        "--check", action="store_true", help="report on the committed assets; exit 1 on a finding"
    )
    args = parser.parse_args(argv)

    if args.check:
        findings = 0
        try:
            sessions = load_transcript(TRANSCRIPT_PATH)
            print(f"{_display(TRANSCRIPT_PATH):<34} transcript, {len(sessions)} sessions — ok")
        except (OSError, ValueError) as exc:
            print(f"{_display(TRANSCRIPT_PATH):<34} unreadable — {exc}")
            findings += 1
        report = inspect_asset(GIF_PATH)
        print(f"{_display(GIF_PATH):<34} {report.summary()}")
        findings += 0 if report.ok else 1
        return 1 if findings else 0

    if args.capture:
        sessions = capture()
        written = save_transcript(sessions)
        print(f"captured {len(sessions)} sessions -> {_display(written)}")

    sessions = load_transcript()
    gif = render_gif(sessions, path=Path(args.output), fps=args.fps)
    size_kb = gif.stat().st_size / 1024
    frames = len(build_frames(sessions))
    print(f"rendered {len(sessions)} sessions, {frames} frames -> {gif.name} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
