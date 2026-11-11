"""Distribution gate: build the wheel and sdist, then prove they actually ship (Day 23).

Day 22 made the tools installable with ``pip install -e .``. That is the easy
half: an editable install reads the source tree, so it cannot tell you whether a
file was left out of the package. A wheel is the first artefact that answers the
question a user's machine asks — *is everything here?* — and the answer is
almost always discovered by a stranger after publication, not by the author.

This module builds both artefacts and then checks them the way an installer
would:

* **The build.** ``python -m build`` produces ``.whl`` and ``.tar.gz`` into a
  clean ``dist/`` directory, so a stale artefact from an earlier run can never
  be mistaken for today's.
* **The contents.** Both archives are opened with :mod:`zipfile` and
  :mod:`tarfile` and compared against the declaration in ``pyproject.toml``:
  every ``console_scripts`` entry, every ``py-modules`` module, every package
  directory and every bundled dataset must be present, and the name, version
  and ``Requires-Python`` must agree with the metadata.
* **The metadata.** ``twine check`` renders the long description and validates
  the core metadata of each artefact — the same check PyPI applies on upload.
* **The install.** The wheel is installed, with ``--no-index --no-deps``, into a
  throwaway virtualenv and each of the five console scripts is run with
  ``--version`` from a temporary working directory. This is the only check that
  exercises the generated stubs, the loader's ``sys.path`` repair and the
  bundled datasets in the layout a user actually gets.

Nothing in the unit-test suite shells out; :func:`inspect_wheel`,
:func:`inspect_sdist` and the ``verify_*`` functions are pure, so
``tests/test_distribution.py`` can hand them synthetic archives and cover the
failure modes (a missing dataset, a script pointing at the wrong module, a
version that disagrees with ``pyproject.toml``) without building anything.

Run it directly::

    python tools/distribution.py                      # build, inspect, twine, install
    python tools/distribution.py --no-isolation       # offline machine, no build env
    python tools/distribution.py --report-only        # inspect an existing dist/
    python tools/distribution.py --skip-install       # skip the clean-venv smoke test
    python tools/distribution.py --json               # machine-readable verdict

Exit codes: ``0`` every artefact check passed, ``1`` an artefact or install
check failed, ``2`` the gate could not reach a verdict (the build failed, or
``build``/``twine`` is not installed). A gate that cannot measure never reports
success.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:  # pragma: no cover - the interpreter on 3.11+ takes this branch
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 needs `tomli`
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover - no TOML reader at all
        tomllib = None  # type: ignore[assignment]

#: Repository root, resolved from this file so the tool runs from any directory.
ROOT = Path(__file__).resolve().parents[1]

#: Where the artefacts are written. ``dist/`` is git-ignored build output.
DEFAULT_DIST_DIR: Path = ROOT / "dist"

#: The declaration the artefacts are checked against.
PYPROJECT: Path = ROOT / "pyproject.toml"

#: Exit code for "the artefact is wrong".
EXIT_FAILED: int = 1

#: Exit code for "no verdict" — a failed build or missing tooling.
EXIT_UNVERIFIABLE: int = 2

#: The five console scripts, in task-pack order. Mirrors
#: :data:`datheon.cli._registry.TOOL_NAMES`; ``tests/test_distribution.py``
#: asserts the two agree, so a rename cannot land in only one place.
EXPECTED_TOOLS: Tuple[str, ...] = (
    "hangman",
    "portfolio",
    "organizer",
    "assistant",
    "inventory",
)


@dataclass(frozen=True)
class Declaration:
    """What ``pyproject.toml`` promises the artefacts will contain.

    Attributes:
        name: Distribution name, exactly as declared.
        version: Distribution version.
        requires_python: The ``Requires-Python`` specifier.
        py_modules: Top-level modules shipped from the repository root.
        scripts: ``console_scripts`` table, name to ``module:attribute``.
        package_data: Every ``[tool.setuptools.package-data]`` glob, flattened.
        package_dirs: Package directories on disk, POSIX paths relative to the
            root (``datheon``, ``projects/01_hangman_game`` …).
        dataset_files: Every bundled data file on disk, POSIX paths relative to
            the root. Each must appear in the wheel, not merely "some file
            matching the glob".
        typed_marker: Path of the PEP 561 marker, when the package ships one.
    """

    name: str
    version: str
    requires_python: str
    py_modules: Tuple[str, ...]
    scripts: Mapping[str, str]
    package_data: Tuple[str, ...]
    package_dirs: Tuple[str, ...]
    dataset_files: Tuple[str, ...]
    typed_marker: Optional[str]

    @property
    def normalised_name(self) -> str:
        """Return the PEP 503-normalised distribution name."""
        return normalise_name(self.name)


@dataclass(frozen=True)
class Artifact:
    """One built distribution file.

    Attributes:
        kind: ``"wheel"`` or ``"sdist"``.
        path: Location on disk.
        size: Size in bytes, for the report.
    """

    kind: str
    path: Path
    size: int


@dataclass(frozen=True)
class WheelContents:
    """What a wheel archive contains, read without installing it.

    Attributes:
        path: The wheel file.
        name: ``Name`` from the wheel's ``METADATA``.
        version: ``Version`` from the wheel's ``METADATA``.
        requires_python: ``Requires-Python`` from the wheel's ``METADATA``.
        modules: Top-level ``.py`` files (the ``py-modules`` entries).
        packages: Top-level package names.
        package_dirs: Every directory holding an ``__init__.py``, as a POSIX
            path relative to the archive root.
        data_files: Packaged non-Python files, excluding ``.dist-info``.
        console_scripts: ``entry_points.txt`` ``[console_scripts]`` table.
        license_files: Licence files carried inside ``.dist-info``.
        has_typed_marker: Whether ``datheon/py.typed`` is present.
    """

    path: Path
    name: str
    version: str
    requires_python: str
    modules: Tuple[str, ...]
    packages: Tuple[str, ...]
    package_dirs: Tuple[str, ...]
    data_files: Tuple[str, ...]
    console_scripts: Mapping[str, str]
    license_files: Tuple[str, ...]
    has_typed_marker: bool


@dataclass(frozen=True)
class SdistContents:
    """What a source distribution contains, read without unpacking it.

    Attributes:
        path: The ``.tar.gz`` file.
        root: The single top-level directory inside the archive.
        name: ``Name`` from ``PKG-INFO``.
        version: ``Version`` from ``PKG-INFO``.
        requires_python: ``Requires-Python`` from ``PKG-INFO``.
        files: Member paths relative to ``root``, sorted.
    """

    path: Path
    root: str
    name: str
    version: str
    requires_python: str
    files: Tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    """One check the gate performed.

    Attributes:
        name: Short label, unique within a run.
        ok: Whether the check passed.
        detail: One line explaining the result, suitable for a build log.
        kind: ``"build"``, ``"artifact"``, ``"twine"`` or ``"install"``.
    """

    name: str
    ok: bool
    detail: str
    kind: str = "artifact"


@dataclass(frozen=True)
class DistVerdict:
    """The gate's decision and the checks behind it.

    Attributes:
        passed: True when every check passed and at least one ran.
        checks: Every check, in execution order.
        artifacts: The artefacts that were examined.
    """

    passed: bool
    checks: Tuple[CheckResult, ...] = ()
    artifacts: Tuple[Artifact, ...] = ()

    @property
    def failures(self) -> Tuple[CheckResult, ...]:
        """Return the failed checks."""
        return tuple(check for check in self.checks if not check.ok)


def normalise_name(name: str) -> str:
    """Normalise a distribution name per PEP 503.

    Args:
        name: A project or distribution name, in any of its spellings.

    Returns:
        The name lowercased with runs of ``-``, ``_`` and ``.`` collapsed to a
        single ``-`` — the form both setuptools and pip compare with.

    Examples:
        >>> normalise_name("Datheon_Python.Internship")
        'datheon-python-internship'
    """
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def parse_core_metadata(text: str) -> Dict[str, str]:
    """Parse the RFC 822 header block of a ``METADATA``/``PKG-INFO`` file.

    Only the headers matter to this gate, so the body (the long description) is
    ignored, and multi-use fields are last-wins — which is what we want for
    ``Name``, ``Version`` and ``Requires-Python``, all of which are single-use.

    Args:
        text: The decoded metadata file.

    Returns:
        A mapping of lowercased header name to value, without folded newlines.
    """
    headers: Dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            break
        if line[:1] in (" ", "\t") and headers:
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        headers[key.strip().lower()] = value.strip()
    return headers


def parse_entry_points(text: str) -> Dict[str, str]:
    """Parse ``entry_points.txt`` and return the ``console_scripts`` table.

    Args:
        text: The decoded ``entry_points.txt``.

    Returns:
        Script name to ``module:attribute`` target. Other groups are ignored.
    """
    scripts: Dict[str, str] = {}
    group = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1].strip()
            continue
        if group != "console_scripts":
            continue
        name, separator, target = line.partition("=")
        if separator:
            scripts[name.strip()] = target.strip()
    return scripts


def _relative_files(directory: Path) -> List[str]:
    """List every file under a directory as sorted POSIX relative paths.

    Args:
        directory: The directory to walk.

    Returns:
        Paths relative to ``directory``, using ``/`` separators so the same
        strings compare on Windows and POSIX.
    """
    if not directory.is_dir():
        return []
    return sorted(
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    )


def load_declaration(root: Path = ROOT, pyproject: Optional[Path] = None) -> Declaration:
    """Read the promises the artefacts are checked against from ``pyproject.toml``.

    The on-disk facts (package directories, bundled datasets, the PEP 561
    marker) are read from the tree as well, because the interesting failure is
    a file that exists here but never made it into the wheel — a comparison of
    the declaration with itself could not catch that.

    Args:
        root: Repository root holding ``projects/`` and ``datheon/``.
        pyproject: Declaration file; defaults to ``pyproject.toml`` in ``root``.

    Returns:
        The parsed declaration.

    Raises:
        RuntimeError: If no TOML reader is available (Python 3.10 without
            ``tomli``).
        FileNotFoundError: If the declaration file does not exist.
        KeyError: If ``[project]`` has no ``name`` or ``version``.
    """
    if tomllib is None:  # pragma: no cover - only on a bare Python 3.10
        raise RuntimeError("no TOML reader available; install 'tomli' on Python 3.10")

    path = Path(pyproject) if pyproject is not None else root / "pyproject.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data["project"]
    setuptools_table = data.get("tool", {}).get("setuptools", {})

    package_data: List[str] = []
    for patterns in setuptools_table.get("package-data", {}).values():
        package_data.extend(str(pattern) for pattern in patterns)

    package_dirs: List[str] = []
    for candidate in sorted((root / "datheon").glob("**/__init__.py")):
        package_dirs.append(candidate.parent.relative_to(root).as_posix())
    for candidate in sorted((root / "projects").glob("**/__init__.py")):
        package_dirs.append(candidate.parent.relative_to(root).as_posix())

    dataset_files: List[str] = []
    for data_dir in sorted((root / "projects").glob("*/data")):
        for name in _relative_files(data_dir):
            dataset_files.append((data_dir / name).relative_to(root).as_posix())

    marker = root / "datheon" / "py.typed"
    return Declaration(
        name=str(project["name"]),
        version=str(project["version"]),
        requires_python=str(project.get("requires-python", "")),
        py_modules=tuple(str(module) for module in setuptools_table.get("py-modules", [])),
        scripts={str(key): str(value) for key, value in project.get("scripts", {}).items()},
        package_data=tuple(package_data),
        package_dirs=tuple(package_dirs),
        dataset_files=tuple(dataset_files),
        typed_marker="datheon/py.typed" if marker.is_file() else None,
    )


def inspect_wheel(path: Path) -> WheelContents:
    """Read a wheel's contents without installing or unpacking it.

    Args:
        path: The ``.whl`` file.

    Returns:
        The parsed contents.

    Raises:
        ValueError: If the archive has no ``METADATA`` or no top-level content,
            which means it is not a wheel at all.
        zipfile.BadZipFile: If the file is not a valid zip archive.
    """
    wheel = Path(path)
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
        if metadata_name is None:
            raise ValueError(f"{wheel.name} has no .dist-info/METADATA")
        metadata = parse_core_metadata(archive.read(metadata_name).decode("utf-8", "replace"))

        entry_point_name = next(
            (name for name in names if name.endswith(".dist-info/entry_points.txt")), None
        )
        console_scripts = (
            parse_entry_points(archive.read(entry_point_name).decode("utf-8", "replace"))
            if entry_point_name
            else {}
        )

    modules = tuple(sorted(name for name in names if "/" not in name and name.endswith(".py")))
    packages = tuple(
        sorted(
            {
                name.split("/", 1)[0]
                for name in names
                if "/" in name and not name.split("/", 1)[0].endswith(".dist-info")
            }
        )
    )
    package_dirs = tuple(
        sorted({name.rsplit("/", 1)[0] for name in names if name.endswith("/__init__.py")})
    )
    data_files = tuple(
        sorted(
            name
            for name in names
            if not name.endswith(".py") and ".dist-info/" not in name and "/" in name
        )
    )
    license_files = tuple(sorted(name for name in names if "/licenses/" in name))
    if not modules and not packages:
        raise ValueError(f"{wheel.name} contains no importable content")

    return WheelContents(
        path=wheel,
        name=metadata.get("name", ""),
        version=metadata.get("version", ""),
        requires_python=metadata.get("requires-python", ""),
        modules=modules,
        packages=packages,
        package_dirs=package_dirs,
        data_files=data_files,
        console_scripts=console_scripts,
        license_files=license_files,
        has_typed_marker="datheon/py.typed" in names,
    )


def inspect_sdist(path: Path) -> SdistContents:
    """Read a source distribution's contents without unpacking it.

    Args:
        path: The ``.tar.gz`` file.

    Returns:
        The parsed contents, with member paths relative to the single root
        directory.

    Raises:
        ValueError: If the archive has no single root directory or no
            ``PKG-INFO``.
        tarfile.TarError: If the file is not a readable tar archive.
    """
    sdist = Path(path)
    with tarfile.open(sdist, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        if not members:
            raise ValueError(f"{sdist.name} is empty")
        roots = {member.name.split("/", 1)[0] for member in members}
        if len(roots) != 1:
            raise ValueError(f"{sdist.name} has {len(roots)} top-level directories, expected 1")
        root = roots.pop()
        relative = sorted(member.name.split("/", 1)[1] for member in members if "/" in member.name)
        try:
            pkg_info = archive.extractfile(f"{root}/PKG-INFO")
        except KeyError:
            pkg_info = None
        if pkg_info is None:
            raise ValueError(f"{sdist.name} has no PKG-INFO")
        metadata = parse_core_metadata(pkg_info.read().decode("utf-8", "replace"))

    return SdistContents(
        path=sdist,
        root=root,
        name=metadata.get("name", ""),
        version=metadata.get("version", ""),
        requires_python=metadata.get("requires-python", ""),
        files=tuple(relative),
    )


def _check(name: str, ok: bool, detail: str, kind: str = "artifact") -> CheckResult:
    """Build a :class:`CheckResult` (a tiny helper to keep the checks readable).

    Args:
        name: Short label for the check.
        ok: Whether it passed.
        detail: One-line explanation.
        kind: The check category.

    Returns:
        The result.
    """
    return CheckResult(name=name, ok=ok, detail=detail, kind=kind)


def verify_wheel(wheel: WheelContents, declaration: Declaration) -> List[CheckResult]:
    """Check a wheel against the declaration and the source tree.

    Args:
        wheel: The inspected wheel.
        declaration: The promises from ``pyproject.toml`` plus the on-disk facts.

    Returns:
        One result per check, in reporting order. A missing console script is
        reported per script, because "which command vanished" is the question
        the build log has to answer.
    """
    results: List[CheckResult] = []

    results.append(
        _check(
            "wheel:name",
            normalise_name(wheel.name) == declaration.normalised_name,
            f"Name is {wheel.name!r}, declared {declaration.name!r}",
        )
    )
    results.append(
        _check(
            "wheel:version",
            wheel.version == declaration.version,
            f"Version is {wheel.version!r}, declared {declaration.version!r}",
        )
    )
    results.append(
        _check(
            "wheel:requires-python",
            wheel.requires_python == declaration.requires_python,
            f"Requires-Python is {wheel.requires_python!r}, "
            f"declared {declaration.requires_python!r}",
        )
    )

    for script, target in declaration.scripts.items():
        actual = wheel.console_scripts.get(script)
        results.append(
            _check(
                f"wheel:script:{script}",
                actual == target,
                f"{script} -> {actual or 'MISSING'} (declared {target})",
            )
        )
    extra = sorted(set(wheel.console_scripts) - set(declaration.scripts))
    results.append(
        _check(
            "wheel:no-extra-scripts",
            not extra,
            "no undeclared console scripts" if not extra else f"undeclared: {', '.join(extra)}",
        )
    )

    missing_modules = [
        module for module in declaration.py_modules if f"{module}.py" not in wheel.modules
    ]
    results.append(
        _check(
            "wheel:py-modules",
            not missing_modules,
            (
                f"{len(declaration.py_modules)} core modules present"
                if not missing_modules
                else f"missing from wheel: {', '.join(missing_modules)}"
            ),
        )
    )

    missing_packages = [
        package for package in declaration.package_dirs if package not in wheel.package_dirs
    ]
    results.append(
        _check(
            "wheel:packages",
            not missing_packages,
            (
                f"{len(declaration.package_dirs)} package directories present"
                if not missing_packages
                else f"missing from wheel: {', '.join(missing_packages)}"
            ),
        )
    )

    missing_datasets = [
        dataset for dataset in declaration.dataset_files if dataset not in wheel.data_files
    ]
    results.append(
        _check(
            "wheel:datasets",
            not missing_datasets,
            (
                f"{len(declaration.dataset_files)} bundled data files present"
                if not missing_datasets
                else f"missing from wheel: {', '.join(missing_datasets)}"
            ),
        )
    )

    unmatched_patterns = [
        pattern
        for pattern in declaration.package_data
        if not any(fnmatch.fnmatch(name, f"*/{pattern}") for name in wheel.data_files)
    ]
    results.append(
        _check(
            "wheel:package-data",
            not unmatched_patterns,
            (
                f"every package-data glob matches: {', '.join(declaration.package_data)}"
                if not unmatched_patterns
                else f"globs matching nothing: {', '.join(unmatched_patterns)}"
            ),
        )
    )

    if declaration.typed_marker is not None:
        results.append(
            _check(
                "wheel:py.typed",
                wheel.has_typed_marker,
                (
                    "PEP 561 marker shipped"
                    if wheel.has_typed_marker
                    else f"{declaration.typed_marker} is on disk but missing from the wheel"
                ),
            )
        )

    results.append(
        _check(
            "wheel:license",
            bool(wheel.license_files),
            (
                "LICENSE carried in .dist-info"
                if wheel.license_files
                else "no licence file inside .dist-info"
            ),
        )
    )
    return results


def verify_sdist(sdist: SdistContents, declaration: Declaration) -> List[CheckResult]:
    """Check a source distribution against the declaration.

    Args:
        sdist: The inspected sdist.
        declaration: The promises from ``pyproject.toml``.

    Returns:
        One result per check, in reporting order.
    """
    files = set(sdist.files)
    results: List[CheckResult] = [
        _check(
            "sdist:name",
            normalise_name(sdist.name) == declaration.normalised_name,
            f"Name is {sdist.name!r}, declared {declaration.name!r}",
        ),
        _check(
            "sdist:version",
            sdist.version == declaration.version,
            f"Version is {sdist.version!r}, declared {declaration.version!r}",
        ),
        _check(
            "sdist:pyproject",
            "pyproject.toml" in files,
            (
                "pyproject.toml included"
                if "pyproject.toml" in files
                else "pyproject.toml missing — the sdist could not be rebuilt"
            ),
        ),
        _check(
            "sdist:license",
            any(name == "LICENSE" or name.endswith("/LICENSE") for name in files),
            "LICENSE included",
        ),
    ]

    missing_modules = [module for module in declaration.py_modules if f"{module}.py" not in files]
    results.append(
        _check(
            "sdist:py-modules",
            not missing_modules,
            (
                f"{len(declaration.py_modules)} core modules present"
                if not missing_modules
                else f"missing from sdist: {', '.join(missing_modules)}"
            ),
        )
    )

    missing_datasets = [dataset for dataset in declaration.dataset_files if dataset not in files]
    results.append(
        _check(
            "sdist:datasets",
            not missing_datasets,
            (
                f"{len(declaration.dataset_files)} bundled data files present"
                if not missing_datasets
                else f"missing from sdist: {', '.join(missing_datasets)}"
            ),
        )
    )

    build_noise = [
        name for name in files if "__pycache__" in name or name.endswith((".pyc", ".whl", ".egg"))
    ]
    results.append(
        _check(
            "sdist:no-build-noise",
            not build_noise,
            (
                "no bytecode or nested artefacts"
                if not build_noise
                else f"unwanted files: {', '.join(build_noise[:5])}"
            ),
        )
    )
    return results


def verify_pair(
    wheel: WheelContents, sdist: SdistContents, declaration: Declaration
) -> List[CheckResult]:
    """Check that the wheel and the sdist describe the same release.

    Args:
        wheel: The inspected wheel.
        sdist: The inspected sdist.
        declaration: The promises from ``pyproject.toml``.

    Returns:
        One result per check.
    """
    return [
        _check(
            "pair:same-version",
            wheel.version == sdist.version,
            f"wheel {wheel.version} vs sdist {sdist.version}",
        ),
        _check(
            "pair:same-name",
            normalise_name(wheel.name) == normalise_name(sdist.name),
            f"wheel {wheel.name} vs sdist {sdist.name}",
        ),
        _check(
            "pair:release-version",
            wheel.version == declaration.version == sdist.version,
            f"artefacts {wheel.version}/{sdist.version} vs declared {declaration.version}",
        ),
    ]


def collect_artifacts(dist_dir: Path = DEFAULT_DIST_DIR) -> Tuple[Artifact, ...]:
    """Find the wheel and the sdist in a directory.

    Args:
        dist_dir: Directory to search.

    Returns:
        The artefacts found, wheel first.

    Raises:
        FileNotFoundError: If the directory has no wheel or no sdist, or holds
            more than one of either — an ambiguous dist directory is a stale
            build, not a release.
    """
    directory = Path(dist_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} does not exist; build first")

    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    for label, found in (("wheel", wheels), ("sdist", sdists)):
        if not found:
            raise FileNotFoundError(f"no {label} in {directory}")
        if len(found) > 1:
            names = ", ".join(path.name for path in found)
            raise FileNotFoundError(f"{len(found)} {label}s in {directory}: {names}")

    return (
        Artifact(kind="wheel", path=wheels[0], size=wheels[0].stat().st_size),
        Artifact(kind="sdist", path=sdists[0], size=sdists[0].stat().st_size),
    )


def build_artifacts(
    dist_dir: Path = DEFAULT_DIST_DIR,
    no_isolation: bool = False,
    timeout: int = 240,
) -> CheckResult:
    """Build the wheel and the sdist into a clean directory.

    Args:
        dist_dir: Output directory. Removed first so a stale artefact can never
            stand in for a fresh build.
        no_isolation: Build against the current environment instead of a fresh
            build environment. Needed on a machine with no index access.
        timeout: Seconds before the build is abandoned.

    Returns:
        A ``"build"`` result. A missing ``build`` package is reported as a
        failure here and turns into exit code 2 in :func:`main`.
    """
    directory = Path(dist_dir)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)

    command = [
        sys.executable,
        "-m",
        "build",
        "--wheel",
        "--sdist",
        "--outdir",
        str(directory),
    ]
    if no_isolation:
        command.append("--no-isolation")

    try:
        completed = subprocess.run(
            command, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError:  # pragma: no cover - the module always exists
        return _check("build", False, "cannot run python -m build", kind="build")
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        return _check("build", False, f"build exceeded {timeout}s", kind="build")

    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip().splitlines()
        message = tail[-1] if tail else f"exit {completed.returncode}"
        return _check("build", False, f"python -m build failed: {message}", kind="build")

    built = sorted(path.name for path in directory.iterdir() if path.is_file())
    return _check(
        "build",
        bool(built),
        f"built {', '.join(built)}" if built else "build produced no artefacts",
        kind="build",
    )


def twine_check(artifacts: Sequence[Artifact], timeout: int = 120) -> CheckResult:
    """Run ``twine check`` over the artefacts.

    Args:
        artifacts: The files to check.
        timeout: Seconds before the check is abandoned.

    Returns:
        A ``"twine"`` result. A missing ``twine`` install is a failure, because
        the gate cannot claim the metadata renders without it.
    """
    command = [sys.executable, "-m", "twine", "check"]
    command.extend(str(artifact.path) for artifact in artifacts)
    try:
        completed = subprocess.run(
            command, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        return _check("twine-check", False, f"twine check exceeded {timeout}s", kind="twine")

    if completed.returncode != 0:
        output = (completed.stdout + completed.stderr).strip().splitlines()
        message = output[-1] if output else f"exit {completed.returncode}"
        return _check("twine-check", False, f"twine check failed: {message}", kind="twine")

    passed = sum(1 for line in completed.stdout.splitlines() if "PASSED" in line)
    return _check(
        "twine-check",
        True,
        f"twine check passed for {passed or len(artifacts)} artefact(s)",
        kind="twine",
    )


def install_environment(base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Return a subprocess environment that cannot redirect the install.

    ``pip`` honours ``PIP_USER``, ``PIP_TARGET`` and ``PIP_PREFIX`` from the
    environment. Any of them turns a wheel install into something else — with
    ``PIP_USER=1`` pip refuses outright inside a virtualenv — so the clean-venv
    check strips them and forces ``PYTHONNOUSERSITE``. Without this the check
    measures the developer's shell rather than the wheel.

    Args:
        base: Environment to start from; defaults to :data:`os.environ`.

    Returns:
        A copy of ``base`` without the ``pip`` location overrides.
    """
    env = dict(os.environ if base is None else base)
    for key in ("PIP_USER", "PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_NO_INDEX"):
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _venv_python(venv_dir: Path) -> Path:
    """Return the interpreter path inside a virtual environment.

    Args:
        venv_dir: The environment root.

    Returns:
        ``bin/python`` on POSIX, ``Scripts/python.exe`` on Windows.
    """
    for candidate in (venv_dir / "bin" / "python", venv_dir / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return venv_dir / "bin" / "python"


def _venv_script(venv_dir: Path, name: str) -> Path:
    """Return the path of an installed console script.

    Args:
        venv_dir: The environment root.
        name: The script name, without an extension.

    Returns:
        The script path for this platform.
    """
    for suffix in ("", ".exe"):
        for directory in ("bin", "Scripts"):
            candidate = venv_dir / directory / f"{name}{suffix}"
            if candidate.exists():
                return candidate
    return venv_dir / "bin" / name


def install_and_smoke(
    wheel: Path,
    declaration: Declaration,
    timeout: int = 180,
) -> List[CheckResult]:
    """Install the wheel into a throwaway virtualenv and run every console script.

    The install uses ``--no-index --no-deps``: the package has no runtime
    dependencies, so a clean environment that needs nothing from the network is
    exactly the environment a user gets.

    Args:
        wheel: The wheel to install.
        declaration: Supplies the script names and the expected version.
        timeout: Seconds allowed per subprocess.

    Returns:
        One result for the install, one for the installed metadata, one for the
        bundled datasets and one per console script.
    """
    results: List[CheckResult] = []
    with tempfile.TemporaryDirectory(prefix="datheon-dist-") as tmp:
        venv_dir = Path(tmp) / "venv"
        workdir = Path(tmp) / "elsewhere"
        workdir.mkdir()
        try:
            venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        except OSError as exc:  # pragma: no cover - defensive
            return [_check("install:venv", False, f"cannot create venv: {exc}", kind="install")]

        python = _venv_python(venv_dir)
        install = subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--disable-pip-version-check",
                str(wheel),
            ],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=install_environment(),
        )
        results.append(
            _check(
                "install:wheel",
                install.returncode == 0,
                (
                    f"installed {Path(wheel).name} into a clean venv"
                    if install.returncode == 0
                    else f"pip install failed: {(install.stderr or install.stdout).strip()[-200:]}"
                ),
                kind="install",
            )
        )
        if install.returncode != 0:
            return results

        probe = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import importlib.metadata as m;"
                    "d=m.distribution('datheon-python-internship');"
                    "print(len([e for e in d.entry_points if e.group=='console_scripts']))"
                ),
            ],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        declared_count = len(declaration.scripts)
        results.append(
            _check(
                "install:metadata",
                probe.returncode == 0 and probe.stdout.strip() == str(declared_count),
                f"{probe.stdout.strip() or '?'} console scripts registered "
                f"(expected {declared_count})",
                kind="install",
            )
        )

        dataset_probe = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "from pathlib import Path;"
                    "from datheon.cli._registry import projects_root;"
                    "root=projects_root();"
                    "files=sorted(p.relative_to(root).as_posix() "
                    "for p in root.glob('**/data/*') if p.is_file());"
                    "print(','.join(files))"
                ),
            ],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        shipped = {name for name in dataset_probe.stdout.strip().split(",") if name}
        # `projects_root()` is `site-packages/projects`, so the paths it reports
        # are relative to `projects/`, not to the repository root.
        expected = [
            dataset[len("projects/") :] if dataset.startswith("projects/") else dataset
            for dataset in declaration.dataset_files
        ]
        missing = [dataset for dataset in expected if dataset not in shipped]
        results.append(
            _check(
                "install:datasets",
                dataset_probe.returncode == 0 and not missing,
                (
                    f"{len(shipped)} data files readable from the installed package"
                    if dataset_probe.returncode == 0 and not missing
                    else "datasets unreadable after install: "
                    f"{', '.join(missing) or 'probe failed'}"
                ),
                kind="install",
            )
        )

        for script in declaration.scripts:
            script_path = _venv_script(venv_dir, script)
            if not script_path.exists():
                results.append(
                    _check(
                        f"install:script:{script}",
                        False,
                        f"{script} was not installed into the venv",
                        kind="install",
                    )
                )
                continue
            run = subprocess.run(
                [str(script_path), "--version"],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            banner = (run.stdout or "").strip().splitlines()
            line = banner[0] if banner else (run.stderr or "").strip().splitlines()[:1]
            ok = run.returncode == 0 and declaration.version in " ".join(banner)
            results.append(
                _check(
                    f"install:script:{script}",
                    ok,
                    f"{script} --version -> {line!r} exit {run.returncode}",
                    kind="install",
                )
            )
    return results


def run_pipeline(
    dist_dir: Path = DEFAULT_DIST_DIR,
    *,
    build: bool = True,
    twine: bool = True,
    install: bool = True,
    no_isolation: bool = False,
    root: Path = ROOT,
) -> DistVerdict:
    """Run the whole gate: build, inspect, verify, check metadata, install.

    Args:
        dist_dir: Where the artefacts live (or should be written).
        build: Whether to run ``python -m build`` first.
        twine: Whether to run ``twine check``.
        install: Whether to install the wheel into a clean virtualenv.
        no_isolation: Build without a fresh build environment.
        root: Repository root used to read the declaration.

    Returns:
        The verdict. When the build fails, the verdict carries only that result
        and :func:`main` maps it to exit code 2 — no artefacts, no verdict.
    """
    checks: List[CheckResult] = []

    if build:
        build_result = build_artifacts(dist_dir, no_isolation=no_isolation)
        checks.append(build_result)
        if not build_result.ok:
            return DistVerdict(passed=False, checks=tuple(checks), artifacts=())

    try:
        artifacts = collect_artifacts(dist_dir)
    except FileNotFoundError as exc:
        checks.append(_check("artifacts", False, str(exc)))
        return DistVerdict(passed=False, checks=tuple(checks), artifacts=())

    try:
        declaration = load_declaration(root)
    except (OSError, RuntimeError, KeyError) as exc:
        checks.append(_check("declaration", False, f"cannot read pyproject.toml: {exc}"))
        return DistVerdict(passed=False, checks=tuple(checks), artifacts=artifacts)

    wheel_path = next(artifact.path for artifact in artifacts if artifact.kind == "wheel")
    sdist_path = next(artifact.path for artifact in artifacts if artifact.kind == "sdist")
    try:
        wheel = inspect_wheel(wheel_path)
        sdist = inspect_sdist(sdist_path)
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        checks.append(_check("artifacts", False, f"cannot read the artefacts: {exc}"))
        return DistVerdict(passed=False, checks=tuple(checks), artifacts=artifacts)

    checks.extend(verify_wheel(wheel, declaration))
    checks.extend(verify_sdist(sdist, declaration))
    checks.extend(verify_pair(wheel, sdist, declaration))

    if twine:
        checks.append(twine_check(artifacts))
    if install:
        checks.extend(install_and_smoke(wheel_path, declaration))

    passed = all(check.ok for check in checks) and bool(checks)
    return DistVerdict(passed=passed, checks=tuple(checks), artifacts=artifacts)


def render_report(verdict: DistVerdict) -> str:
    """Render a verdict as a human-readable report.

    Args:
        verdict: The gate's decision.

    Returns:
        The report, ending in a newline.
    """
    lines = ["Distribution gate"]
    for artifact in verdict.artifacts:
        lines.append(f"  {artifact.kind:<6}: {artifact.path.name} ({artifact.size:,} bytes)")

    if verdict.artifacts:
        lines.append("")
    for check in verdict.checks:
        status = "PASS" if check.ok else "FAIL"
        lines.append(f"  [{status}] {check.name:<32} {check.detail}")

    passed = sum(1 for check in verdict.checks if check.ok)
    lines.append("")
    lines.append(f"{passed}/{len(verdict.checks)} checks passed")
    if verdict.passed:
        lines.append("PASS — the artefacts build, check clean and install.")
    elif verdict.failures:
        lines.append("FAIL — the artefacts do not match the declaration:")
        for failure in verdict.failures:
            lines.append(f"  - {failure.name}: {failure.detail}")
    return "\n".join(lines) + "\n"


def verdict_to_dict(verdict: DistVerdict) -> Dict[str, Any]:
    """Convert a verdict into a JSON-serialisable mapping.

    Args:
        verdict: The gate's decision.

    Returns:
        A mapping with the verdict, the artefacts and every check.
    """
    return {
        "passed": verdict.passed,
        "artifacts": [
            {
                "kind": artifact.kind,
                "name": artifact.path.name,
                "size": artifact.size,
            }
            for artifact in verdict.artifacts
        ],
        "checks": [
            {"name": check.name, "kind": check.kind, "ok": check.ok, "detail": check.detail}
            for check in verdict.checks
        ],
        "passed_count": sum(1 for check in verdict.checks if check.ok),
        "failed_count": len(verdict.failures),
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the distribution gate.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="distribution.py",
        description="Build the wheel and sdist, then verify and install them.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        metavar="DIR",
        help=f"artefact directory (default: {DEFAULT_DIST_DIR.name}/)",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="inspect the existing artefacts instead of rebuilding them",
    )
    parser.add_argument(
        "--no-isolation",
        action="store_true",
        help="build in the current environment (offline machines)",
    )
    parser.add_argument(
        "--skip-twine",
        action="store_true",
        help="do not run twine check",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="do not install the wheel into a clean virtualenv",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the verdict as JSON instead of a report",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the gate and return the process exit code.

    Args:
        argv: Command-line arguments without the program name. Defaults to
            :data:`sys.argv` without the program name.

    Returns:
        ``0`` when every check passed, ``1`` when a check failed, and ``2``
        when no verdict could be reached — the build failed, or the artefacts
        or the declaration could not be read.
    """
    args = build_parser().parse_args(None if argv is None else list(argv))

    verdict = run_pipeline(
        args.dist_dir,
        build=not args.report_only,
        twine=not args.skip_twine,
        install=not args.skip_install,
        no_isolation=args.no_isolation,
    )

    if args.json:
        print(json.dumps(verdict_to_dict(verdict), indent=2))
    else:
        print(render_report(verdict), end="")

    if verdict.passed:
        return 0
    if not verdict.checks or (len(verdict.checks) == 1 and verdict.checks[0].kind == "build"):
        return EXIT_UNVERIFIABLE
    if any(check.name in {"artifacts", "declaration"} for check in verdict.failures):
        return EXIT_UNVERIFIABLE
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
