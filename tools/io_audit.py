"""Static audit that keeps real I/O out of the unit-test suite (Day 16).

Day 16's definition of done is a negative one — *no unit test touches the real
network or the user filesystem* — and a negative is exactly the kind of claim
that rots the moment nobody checks it. One ``import requests`` added six months
from now would silently turn the suite into an integration test.

This module turns the claim into a measurement. It parses every test module
with :mod:`ast` and flags the three smells that let real I/O back in:

===================  ==========================================================
Code                 Why it breaks isolation
===================  ==========================================================
``import socket``    (or ``requests``/``httpx``/``urllib3``/``aiohttp``) opens a
                     path to the network that bypasses the guard fixture.
``datetime.now()``   reads the wall clock, so the test's verdict depends on the
                     day it runs. ``date.today()`` and ``time.time()`` count too.
``Path.home()``      resolves outside the pytest sandbox, so the test reads the
                     machine it happens to run on.
===================  ==========================================================

``tests/io_fakes.py`` is exempt from the import rule because it *implements*
the guard; that exemption is a one-line constant, not a per-file pragma, so it
cannot quietly grow.

Run it directly::

    python tools/io_audit.py               # human-readable report
    python tools/io_audit.py --json        # machine-readable
    python tools/io_audit.py --strict      # exit 1 on any violation

The exit code is the verdict, which is what lets CI (or the Day 20 gate) fail
loudly instead of trusting a reviewer's memory.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: Directory scanned by default.
DEFAULT_TESTS_DIR = ROOT / "tests"

#: Test-support modules allowed to import the network stack: they build the guard.
GUARD_MODULES = frozenset({"io_fakes.py"})

#: Modules that would put a real socket within reach of a test.
NETWORK_MODULES = frozenset(
    {"socket", "requests", "httpx", "urllib3", "aiohttp", "ftplib", "smtplib", "telnetlib"}
)

#: ``(receiver, method)`` pairs that read the real wall clock.
CLOCK_CALLS = frozenset(
    {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("datetime", "today"),
        ("date", "today"),
        ("time", "time"),
    }
)

#: ``(receiver, method)`` pairs that resolve outside the pytest sandbox.
HOME_CALLS = frozenset({("Path", "home"), ("pathlib.Path", "home")})


@dataclass(frozen=True)
class Violation:
    """One real-I/O smell found in a test module.

    Attributes:
        file: Path relative to the repository root, POSIX-style.
        line: 1-based line number of the offending node.
        code: Stable identifier (``network-import``, ``real-clock``,
            ``home-lookup``) so CI output can be grepped.
        message: Human-readable explanation.
    """

    file: str
    line: int
    code: str
    message: str


def _call_name(node: ast.AST) -> Optional[tuple[str, str]]:
    """Return ``(receiver, attribute)`` for an attribute call such as ``x.y()``.

    Args:
        node: Candidate node.

    Returns:
        The pair when ``node`` is a call on a named attribute, else ``None``.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    return func.value.id, func.attr


def audit_source(source: str, filename: str = "<string>") -> List[Violation]:
    """Audit one module's source text for real-I/O smells.

    Args:
        source: Python source to parse.
        filename: Name used in the reported violations; only the basename
            matters for the guard-module exemption.

    Returns:
        Violations in source order. A syntax error is reported as a single
        ``syntax-error`` violation rather than raised, so one broken module
        cannot hide the rest of the report.
    """
    relative = filename.replace("\\", "/").split("/")[-1]
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:  # pragma: no cover - defensive
        return [Violation(filename, exc.lineno or 0, "syntax-error", f"cannot parse: {exc}")]

    violations: List[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and relative not in GUARD_MODULES:
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in NETWORK_MODULES:
                    violations.append(
                        Violation(
                            filename,
                            node.lineno,
                            "network-import",
                            f"imports {root!r}; network access belongs behind a fake",
                        )
                    )
        elif isinstance(node, ast.ImportFrom) and relative not in GUARD_MODULES:
            root = (node.module or "").split(".")[0]
            if root in NETWORK_MODULES:
                violations.append(
                    Violation(
                        filename,
                        node.lineno,
                        "network-import",
                        f"imports from {root!r}; network access belongs behind a fake",
                    )
                )

        pair = _call_name(node)
        if pair is None:
            continue
        if pair in CLOCK_CALLS:
            violations.append(
                Violation(
                    filename,
                    node.lineno,
                    "real-clock",
                    f"{pair[0]}.{pair[1]}() reads the wall clock; freeze it with FakeClock",
                )
            )
        elif pair in HOME_CALLS:
            violations.append(
                Violation(
                    filename,
                    node.lineno,
                    "home-lookup",
                    "Path.home() escapes the pytest sandbox; use tmp_path",
                )
            )
    return violations


def audit_paths(paths: Iterable[Path]) -> List[Violation]:
    """Audit a collection of test modules.

    Args:
        paths: Files to scan; non-Python files are ignored.

    Returns:
        Every violation found, ordered by file then line.
    """
    violations: List[Violation] = []
    for path in sorted(paths):
        if path.suffix != ".py":
            continue
        try:
            display = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:  # pragma: no cover - paths outside the repo
            display = path.as_posix()
        violations.extend(audit_source(path.read_text(encoding="utf-8"), display))
    return sorted(violations, key=lambda v: (v.file, v.line, v.code))


def _render_table(violations: Sequence[Violation]) -> str:
    """Render the violations as a fixed-width table."""
    if not violations:
        return "io-audit: no real I/O found in the test suite."
    lines = [f"{'file':<34} {'line':>5}  {'code':<15} message"]
    lines.append("-" * 96)
    for violation in violations:
        lines.append(
            f"{violation.file:<34} {violation.line:>5}  {violation.code:<15} {violation.message}"
        )
    lines.append("")
    lines.append(f"{len(violations)} violation(s) found.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        A parser exposing ``--tests-dir``, ``--json`` and ``--strict``.
    """
    parser = argparse.ArgumentParser(
        prog="io-audit",
        description="Check that no unit test touches the network, the wall clock or the home dir.",
    )
    parser.add_argument(
        "--tests-dir",
        default=str(DEFAULT_TESTS_DIR),
        help="directory holding the test modules (default: tests/)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any violation is found (CI mode)",
    )
    parser.add_argument("--version", action="version", version="io-audit 1.0")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the audit.

    Args:
        argv: Argument list; ``None`` means :data:`sys.argv`.

    Returns:
        ``0`` when the suite is clean (or ``--strict`` is off), ``1`` when
        violations were found under ``--strict``.
    """
    args = build_parser().parse_args(argv)
    directory = Path(args.tests_dir)
    violations = audit_paths(directory.glob("*.py")) if directory.is_dir() else []

    if args.json:
        print(
            json.dumps(
                {
                    "tests_dir": str(directory),
                    "violations": [asdict(v) for v in violations],
                    "count": len(violations),
                },
                indent=2,
            )
        )
    else:
        print(_render_table(violations))

    if violations and args.strict:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
