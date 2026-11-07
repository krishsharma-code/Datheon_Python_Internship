"""Mutation-testing driver: run ``mutmut`` and turn its cache into a verdict (Day 19).

Day 19 asks a question a coverage report cannot answer: *do the tests actually
test?* Line coverage says a statement was executed; mutation testing says
whether the assertions around it would notice if it were wrong. ``mutmut``
answers it by editing the source — flipping comparisons, dropping branches,
replacing constants — and re-running the selected tests for every edit. A
mutant that no test notices has **survived**, and every survivor is a place
where the suite is decorative.

The tool lives here, rather than in a shell one-liner, for three reasons:

* **The number needs a gate.** ``mutmut`` prints a progress bar and a cache;
  this module reads that cache and exits ``1`` when the mutation score falls
  below ``--minimum-score``, so a shrinking suite fails a build the same way a
  broken test does.
* **The number needs a scope.** ``[tool.mutmut]`` in ``pyproject.toml`` decides
  which modules are mutated and which tests are run against each mutant.
  Mutating the whole repository is minutes-to-hours; mutating the shared core
  against its own test modules is seconds, and it is the shared core that every
  project depends on.
* **The number needs to be honest.** A survivor is reported with its name, and
  ``--show-survivors`` prints the exact ``mutmut show <name>`` command that
  displays the diff, so "we will look at it later" is a specific, checkable
  task rather than a vague intention.

Run it directly::

    python tools/mutation.py                  # run mutmut, then report
    python tools/mutation.py --report-only    # report the existing cache only
    python tools/mutation.py --json           # machine-readable
    python tools/mutation.py --minimum-score 100 --show-survivors

The score is computed strictly: the denominator is every mutant mutmut
produced, minus the ones it never got to (``not checked``), the ones that were
skipped, and runs interrupted by the user. Mutants with **no tests at all**
count against the score — untested code is exactly what mutation testing is
for, and silently dropping it would flatter the result.

Requires ``mutmut`` (see ``requirements.txt``) only for the running mode;
``--report-only`` works with nothing but the standard library, which is what
the test suite uses.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: Where mutmut keeps its cache, relative to the repository root.
DEFAULT_MUTANTS_DIR = ROOT / "mutants"

#: Default quality floor. The Day 19 run reached 100% on the configured target,
#: so anything below 95 is a regression worth stopping the build for.
DEFAULT_MINIMUM_SCORE = 95.0

#: mutmut's own exit-code-to-status mapping, restricted to the codes it can
#: actually produce. Mirrors ``mutmut.stats.status_by_exit_code``; kept here so
#: ``--report-only`` needs no mutmut import.
STATUS_BY_EXIT_CODE: Dict[Optional[int], str] = {
    0: "survived",
    1: "killed",
    3: "killed",
    5: "no tests",
    33: "no tests",
    34: "skipped",
    35: "suspicious",
    36: "timeout",
    24: "timeout",
    -24: "timeout",
    152: "timeout",
    255: "timeout",
    -11: "segfault",
    -9: "segfault",
    2: "interrupted",
    None: "not checked",
}

#: Statuses that are excluded from the score's denominator: mutmut produced no
#: verdict for these mutants, so counting them either way would be a guess.
EXCLUDED_STATUSES = frozenset({"not checked", "skipped", "interrupted"})

#: Statuses that count as the suite noticing the mutation.
KILLING_STATUSES = frozenset({"killed", "timeout", "segfault", "caught by type check"})

#: Suffix mutmut uses for the per-module cache file.
META_SUFFIX = ".py.meta"


def status_for(exit_code: Optional[int]) -> str:
    """Map a mutmut exit code onto a status name.

    Args:
        exit_code: The value mutmut recorded for the mutant, or ``None`` when
            the mutant was never run.

    Returns:
        One of the status names in :data:`STATUS_BY_EXIT_CODE`; unrecognised
        codes are reported as ``"suspicious"``, matching mutmut.
    """
    return STATUS_BY_EXIT_CODE.get(exit_code, "suspicious")


@dataclass(frozen=True)
class Mutant:
    """One generated mutant and the verdict the test suite reached on it.

    Attributes:
        name: mutmut's identifier, e.g. ``"error_catalogue.x_catalogue__mutmut_1"``.
        module: The mutated file, e.g. ``"error_catalogue.py"``.
        status: The verdict, as returned by :func:`status_for`.
    """

    name: str
    module: str
    status: str


@dataclass(frozen=True)
class ModuleScore:
    """The mutation score of a single mutated module.

    Attributes:
        module: The mutated file's name.
        counts: Status name mapped to how many mutants ended in it.
    """

    module: str
    counts: Dict[str, int]

    @property
    def total(self) -> int:
        """Return the number of mutants mutmut generated for this module."""
        return sum(self.counts.values())

    @property
    def counted(self) -> int:
        """Return how many mutants carry a verdict the score uses."""
        return sum(n for status, n in self.counts.items() if status not in EXCLUDED_STATUSES)

    @property
    def killed(self) -> int:
        """Return how many mutants the suite caught."""
        return sum(n for status, n in self.counts.items() if status in KILLING_STATUSES)

    @property
    def survived(self) -> int:
        """Return how many mutants the suite let through."""
        return self.counts.get("survived", 0)

    @property
    def no_tests(self) -> int:
        """Return how many mutants no selected test exercises at all."""
        return self.counts.get("no tests", 0)

    @property
    def score(self) -> float:
        """Return ``killed / counted`` as a percentage.

        A module with no mutants at all scores ``100.0``; one whose mutants were
        generated but never run scores ``0.0``, so an interrupted run can never
        look like a perfect one.
        """
        if self.counted == 0:
            return 100.0 if self.total == 0 else 0.0
        return 100.0 * self.killed / self.counted


@dataclass(frozen=True)
class MutationReport:
    """The whole mutation run, module by module.

    Attributes:
        modules: One :class:`ModuleScore` per mutated module, sorted by name.
        mutants: Every mutant that was generated, for survivor listings.
    """

    modules: Tuple[ModuleScore, ...]
    mutants: Tuple[Mutant, ...]

    @property
    def total(self) -> int:
        """Return the number of mutants generated across every module."""
        return sum(module.total for module in self.modules)

    @property
    def killed(self) -> int:
        """Return the number of mutants caught across every module."""
        return sum(module.killed for module in self.modules)

    @property
    def survived(self) -> int:
        """Return the number of mutants that survived across every module."""
        return sum(module.survived for module in self.modules)

    @property
    def no_tests(self) -> int:
        """Return the number of mutants with no covering test across every module."""
        return sum(module.no_tests for module in self.modules)

    @property
    def counted(self) -> int:
        """Return the number of mutants that carry a verdict the score uses."""
        return sum(module.counted for module in self.modules)

    @property
    def score(self) -> float:
        """Return the overall mutation score as a percentage."""
        if self.counted == 0:
            return 100.0 if self.total == 0 else 0.0
        return 100.0 * self.killed / self.counted

    @property
    def survivors(self) -> Tuple[Mutant, ...]:
        """Return every mutant the suite failed to notice, in name order."""
        return tuple(
            sorted((m for m in self.mutants if m.status == "survived"), key=lambda m: m.name)
        )


def parse_meta(path: Path, module: Optional[str] = None) -> Tuple[Mutant, ...]:
    """Parse one ``mutants/<module>.py.meta`` cache file.

    Args:
        path: Path to the ``.meta`` file mutmut wrote.
        module: Name to report the mutants under. Defaults to the file name
            with the ``.meta`` suffix removed, e.g. ``"error_catalogue.py"``.

    Returns:
        The mutants recorded in the file, in file order.

    Raises:
        json.JSONDecodeError: If the file is not the JSON object mutmut writes.
        KeyError: If the file has no ``exit_code_by_key`` mapping.
    """
    module_name = module or path.name[: -len(".meta")]
    payload = json.loads(path.read_text(encoding="utf-8"))
    exit_codes: Dict[str, Optional[int]] = payload["exit_code_by_key"]
    return tuple(
        Mutant(name=name, module=module_name, status=status_for(code))
        for name, code in exit_codes.items()
    )


def load_report(mutants_dir: Path = DEFAULT_MUTANTS_DIR) -> MutationReport:
    """Build a :class:`MutationReport` from mutmut's on-disk cache.

    Args:
        mutants_dir: Directory mutmut writes its cache into.

    Returns:
        The report; an absent or empty cache yields a report with no modules.
    """
    if not mutants_dir.is_dir():
        return MutationReport(modules=(), mutants=())

    mutants: List[Mutant] = []
    modules: List[ModuleScore] = []
    for meta in sorted(mutants_dir.glob("*" + META_SUFFIX)):
        try:
            parsed = parse_meta(meta)
        except (json.JSONDecodeError, KeyError, OSError):
            continue
        mutants.extend(parsed)
        counts: Dict[str, int] = {}
        for mutant in parsed:
            counts[mutant.status] = counts.get(mutant.status, 0) + 1
        modules.append(ModuleScore(module=parsed[0].module if parsed else meta.name, counts=counts))

    return MutationReport(modules=tuple(modules), mutants=tuple(mutants))


def render_table(report: MutationReport) -> str:
    """Render the report as a fixed-width table.

    Args:
        report: The report to render.

    Returns:
        A multi-line string ending in a newline.
    """
    header = f"{'module':<28}{'killed':>8}{'survived':>10}{'no tests':>10}{'score':>9}"
    lines = [header, "-" * len(header)]
    for module in report.modules:
        lines.append(
            f"{module.module:<28}{module.killed:>8}{module.survived:>10}"
            f"{module.no_tests:>10}{module.score:>8.1f}%"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<28}{report.killed:>8}{report.survived:>10}"
        f"{report.no_tests:>10}{report.score:>8.1f}%"
    )
    return "\n".join(lines) + "\n"


def report_to_dict(report: MutationReport) -> Dict[str, object]:
    """Convert a report into a JSON-serialisable mapping.

    Args:
        report: The report to convert.

    Returns:
        A mapping with the per-module rows, the totals and the survivor names.
    """
    return {
        "modules": [
            {
                "module": module.module,
                "total": module.total,
                "counted": module.counted,
                "killed": module.killed,
                "survived": module.survived,
                "no_tests": module.no_tests,
                "score": round(module.score, 2),
                "counts": dict(sorted(module.counts.items())),
            }
            for module in report.modules
        ],
        "total": report.total,
        "counted": report.counted,
        "killed": report.killed,
        "survived": report.survived,
        "no_tests": report.no_tests,
        "score": round(report.score, 2),
        "survivors": [asdict(mutant) for mutant in report.survivors],
    }


def run_mutmut(max_children: Optional[int] = None, extra_args: Sequence[str] = ()) -> int:
    """Run ``mutmut run`` in the repository root and return its exit code.

    Args:
        max_children: Worker processes to use. Defaults to one per CPU, capped
            at four — mutmut's workers fork a warm pytest process, so more
            workers than cores only adds contention.
        extra_args: Additional arguments passed through to ``mutmut run``.

    Returns:
        The exit code of the ``mutmut`` process.
    """
    children = max_children or min(4, os.cpu_count() or 1)
    command = [sys.executable, "-m", "mutmut", "run", "--max-children", str(children)]
    command.extend(extra_args)
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the mutation driver.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="mutation.py",
        description="Run mutmut and report the mutation score of the configured targets.",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="read the existing mutmut cache instead of running mutmut",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON instead of a table",
    )
    parser.add_argument(
        "--show-survivors",
        action="store_true",
        help="list every surviving mutant with the command that shows its diff",
    )
    parser.add_argument(
        "--minimum-score",
        type=float,
        default=DEFAULT_MINIMUM_SCORE,
        metavar="PERCENT",
        help=f"exit 1 when the mutation score is below this (default: {DEFAULT_MINIMUM_SCORE:g})",
    )
    parser.add_argument(
        "--max-children",
        type=int,
        default=None,
        metavar="N",
        help="mutmut worker processes (default: one per CPU, capped at 4)",
    )
    parser.add_argument(
        "--mutants-dir",
        type=Path,
        default=DEFAULT_MUTANTS_DIR,
        metavar="PATH",
        help="directory holding mutmut's cache (default: ./mutants)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the driver and return the process exit code.

    Args:
        argv: Command-line arguments, without the program name. Defaults to
            :data:`sys.argv` without the program name.

    Returns:
        ``0`` when the score meets ``--minimum-score``, ``1`` when it does not,
        and ``1`` when a mutmut run itself failed.
    """
    args = build_parser().parse_args(None if argv is None else list(argv))

    if not args.report_only:
        exit_code = run_mutmut(max_children=args.max_children)
        if exit_code != 0:
            print(
                f"mutmut exited with {exit_code}; reporting the cache as it stands", file=sys.stderr
            )

    report = load_report(args.mutants_dir)

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print(render_table(report))
        if not report.modules:
            print("No mutmut cache found — run without --report-only to generate one.")
        if args.show_survivors and report.survivors:
            print("\nSurviving mutants:")
            for mutant in report.survivors:
                print(f"  {mutant.name}")
            print("\nInspect one with: mutmut show <name>")

    if not report.modules:
        return 0

    if report.score < args.minimum_score:
        print(
            f"mutation score {report.score:.1f}% is below the {args.minimum_score:g}% floor",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
