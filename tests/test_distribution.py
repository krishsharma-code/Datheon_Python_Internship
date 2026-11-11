"""Tests for the Day 23 distribution gate (``tools/distribution.py``).

The gate's whole value is that it says *no* to an artefact that would install
badly, so the interesting tests are the negative ones: a wheel missing a
dataset, an ``entry_points.txt`` pointing at a module that no longer exists, an
sdist whose version disagrees with ``pyproject.toml``. Each of those is a real
release defect and each must produce a named failure rather than a traceback.

Nothing here builds anything. The archives are synthesised in ``tmp_path`` with
:mod:`zipfile` and :mod:`tarfile` from the *real* declaration in
``pyproject.toml``, so a new dataset or a renamed module flows into the fixtures
automatically instead of leaving the tests testing last week's layout. The
subprocess-backed steps (``build``, ``twine``) are exercised with
:func:`unittest.mock.patch` on the return value, which is why the suite stays
hermetic and fast.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Sequence

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - conftest already does this
    sys.path.insert(0, str(ROOT))

from datheon.cli._registry import TOOL_NAMES  # noqa: E402  (path set up above)
from tools import distribution  # noqa: E402

#: The declaration read from the repository's own ``pyproject.toml``. Fixtures
#: are generated from it, so they cannot drift away from what ships.
DECLARATION = distribution.load_declaration(ROOT)

#: The ``console_scripts`` targets the declaration must carry.
EXPECTED_SCRIPTS = {name: f"datheon.cli.{name}:main" for name in distribution.EXPECTED_TOOLS}


def _dist_info(name: str, version: str) -> str:
    """Return the ``.dist-info`` directory name for a distribution.

    Args:
        name: Distribution name, in any spelling.
        version: Distribution version.

    Returns:
        The ``.dist-info`` directory name a wheel would carry.
    """
    return f"{name.replace('-', '_')}-{version}.dist-info"


def make_wheel(
    directory: Path,
    *,
    declaration: distribution.Declaration = DECLARATION,
    name: Optional[str] = None,
    version: Optional[str] = None,
    requires_python: Optional[str] = None,
    scripts: Optional[Dict[str, str]] = None,
    drop: Iterable[str] = (),
    py_typed: Optional[bool] = None,
    license_file: bool = True,
) -> Path:
    """Write a synthetic wheel that satisfies the declaration by default.

    Args:
        directory: Where to write the archive.
        declaration: Layout to reproduce.
        name: Override the ``Name`` metadata field.
        version: Override the ``Version`` metadata field.
        requires_python: Override ``Requires-Python``.
        scripts: Override the ``console_scripts`` table.
        drop: Member paths to leave out of the archive.
        py_typed: Whether to ship ``datheon/py.typed``; defaults to the
            declaration.
        license_file: Whether to carry ``LICENSE`` inside ``.dist-info``.

    Returns:
        The path of the written wheel.
    """
    dist_name = declaration.name if name is None else name
    dist_version = declaration.version if version is None else version
    python_specifier = declaration.requires_python if requires_python is None else requires_python
    table = dict(declaration.scripts if scripts is None else scripts)
    typed = (declaration.typed_marker is not None) if py_typed is None else py_typed

    dist_info = _dist_info(dist_name, dist_version)
    members: Dict[str, bytes] = {}
    for module in declaration.py_modules:
        members[f"{module}.py"] = b"# module\n"
    for package in declaration.package_dirs:
        members[f"{package}/__init__.py"] = b""
    for dataset in declaration.dataset_files:
        members[dataset] = b"data\n"
    if typed:
        members["datheon/py.typed"] = b""
    members[f"{dist_info}/METADATA"] = (
        f"Metadata-Version: 2.4\nName: {dist_name}\nVersion: {dist_version}\n"
        f"Requires-Python: {python_specifier}\n\nLong description.\n"
    ).encode()
    members[f"{dist_info}/entry_points.txt"] = (
        "[console_scripts]\n"
        + "".join(f"{key} = {value}\n" for key, value in sorted(table.items()))
    ).encode()
    if license_file:
        members[f"{dist_info}/licenses/LICENSE"] = b"MIT\n"

    for member in drop:
        members.pop(member, None)

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{dist_name.replace('-', '_')}-{dist_version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for member, payload in members.items():
            archive.writestr(member, payload)
    return path


def make_sdist(
    directory: Path,
    *,
    declaration: distribution.Declaration = DECLARATION,
    name: Optional[str] = None,
    version: Optional[str] = None,
    drop: Iterable[str] = (),
    extra: Iterable[str] = (),
    pkg_info: bool = True,
    second_root: bool = False,
    empty: bool = False,
) -> Path:
    """Write a synthetic source distribution.

    Args:
        directory: Where to write the archive.
        declaration: Layout to reproduce.
        name: Override the ``Name`` metadata field.
        version: Override the ``Version`` metadata field.
        drop: Member paths (relative to the root) to leave out.
        extra: Extra member paths to add, such as build noise.
        pkg_info: Whether to include ``PKG-INFO``.
        second_root: Add a member under a second top-level directory.
        empty: Write an archive with no files at all.

    Returns:
        The path of the written sdist.
    """
    dist_name = declaration.name if name is None else name
    dist_version = declaration.version if version is None else version
    root = f"{dist_name.replace('-', '_')}-{dist_version}"

    files: Dict[str, bytes] = {
        "pyproject.toml": b"[project]\n",
        "LICENSE": b"MIT\n",
    }
    for module in declaration.py_modules:
        files[f"{module}.py"] = b"# module\n"
    for package in declaration.package_dirs:
        files[f"{package}/__init__.py"] = b""
    for dataset in declaration.dataset_files:
        files[dataset] = b"data\n"
    if pkg_info:
        files["PKG-INFO"] = (
            f"Metadata-Version: 2.4\nName: {dist_name}\nVersion: {dist_version}\n"
            f"Requires-Python: {declaration.requires_python}\n\nLong description.\n"
        ).encode()
    for member in drop:
        files.pop(member, None)
    for member in extra:
        files[member] = b"noise\n"

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{root}.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        if not empty:
            for member, payload in sorted(files.items()):
                info = tarfile.TarInfo(f"{root}/{member}")
                info.size = len(payload)
                archive.addfile(info, _bytes_reader(payload))
            if second_root:
                payload = b"stray\n"
                info = tarfile.TarInfo("stray-1.0.0/stray.txt")
                info.size = len(payload)
                archive.addfile(info, _bytes_reader(payload))
    return path


def _bytes_reader(payload: bytes):
    """Wrap bytes in a file-like object :mod:`tarfile` can read.

    Args:
        payload: The file content.

    Returns:
        A readable in-memory stream positioned at the start.
    """
    return io.BytesIO(payload)


def failed(results: Sequence[distribution.CheckResult]) -> List[str]:
    """Return the names of the failed checks.

    Args:
        results: Check results to filter.

    Returns:
        The names of the checks that did not pass, in order.
    """
    return [result.name for result in results if not result.ok]


def test_expected_tools_match_the_console_scripts():
    """The gate's idea of the five commands is the registry's idea."""
    assert distribution.EXPECTED_TOOLS == TOOL_NAMES
    assert set(EXPECTED_SCRIPTS) == set(TOOL_NAMES)


class TestNormaliseName:
    """PEP 503 name normalisation."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("datheon-python-internship", "datheon-python-internship"),
            ("Datheon_Python.Internship", "datheon-python-internship"),
            ("datheon__python--internship", "datheon-python-internship"),
            ("  Datheon  ", "datheon"),
        ],
    )
    def test_variants_collapse_to_one_spelling(self, raw: str, expected: str):
        """Every spelling of a name normalises to the same string."""
        assert distribution.normalise_name(raw) == expected


class TestParseCoreMetadata:
    """Reading ``METADATA``/``PKG-INFO`` headers."""

    def test_headers_are_lowercased_and_trimmed(self):
        """Header names lose their case and values lose their padding."""
        headers = distribution.parse_core_metadata(
            "Name: datheon\nVersion:  1.0.0  \nRequires-Python: >=3.10\n\nbody: ignored\n"
        )
        assert headers == {
            "name": "datheon",
            "version": "1.0.0",
            "requires-python": ">=3.10",
        }

    def test_the_body_is_not_parsed_as_headers(self):
        """The long description stops the header block."""
        headers = distribution.parse_core_metadata("Name: x\n\nName: not-a-header\n")
        assert headers["name"] == "x"

    def test_a_line_without_a_colon_is_skipped(self):
        """Malformed lines are ignored rather than raising."""
        headers = distribution.parse_core_metadata("Name: x\ngarbage line\nVersion: 2\n")
        assert headers == {"name": "x", "version": "2"}

    def test_an_empty_document_yields_no_headers(self):
        """An empty metadata file is not an error."""
        assert distribution.parse_core_metadata("") == {}


class TestParseEntryPoints:
    """Reading ``entry_points.txt``."""

    def test_only_console_scripts_are_returned(self):
        """The GUI and plugin groups are ignored."""
        text = (
            "[console_scripts]\nhangman = datheon.cli.hangman:main\n\n"
            "[gui_scripts]\ngui = datheon.gui:main\n"
        )
        assert distribution.parse_entry_points(text) == {"hangman": "datheon.cli.hangman:main"}

    def test_blank_lines_and_comments_are_tolerated(self):
        """A hand-written file with noise still parses."""
        text = "# comment\n[console_scripts]\n\n  inventory  =  datheon.cli.inventory:main  \n"
        assert distribution.parse_entry_points(text) == {"inventory": "datheon.cli.inventory:main"}

    def test_a_file_without_the_group_returns_nothing(self):
        """No ``[console_scripts]`` section means no commands."""
        assert distribution.parse_entry_points("[gui_scripts]\nx = y:z\n") == {}


class TestLoadDeclaration:
    """Reading the promises out of ``pyproject.toml`` and the tree."""

    def test_identity_fields_come_from_the_project_table(self):
        """Name, version and the Python floor are read verbatim."""
        assert DECLARATION.name == "datheon-python-internship"
        assert DECLARATION.version == "1.0.0"
        assert DECLARATION.requires_python == ">=3.10"
        assert DECLARATION.normalised_name == "datheon-python-internship"

    def test_every_console_script_is_declared(self):
        """The five commands point at their shims."""
        assert dict(DECLARATION.scripts) == EXPECTED_SCRIPTS

    def test_the_core_modules_are_declared(self):
        """``py-modules`` covers the shared modules at the root."""
        assert "cli_contract" in DECLARATION.py_modules
        assert "validators" in DECLARATION.py_modules
        assert all(not module.endswith(".py") for module in DECLARATION.py_modules)

    def test_package_directories_are_discovered_on_disk(self):
        """The datheon package and all five project packages are expected."""
        assert "datheon" in DECLARATION.package_dirs
        assert "datheon/cli" in DECLARATION.package_dirs
        assert sum(1 for path in DECLARATION.package_dirs if path.startswith("projects/")) == 5

    def test_the_bundled_datasets_are_discovered_on_disk(self):
        """Every data file under ``projects/*/data`` must ship."""
        assert DECLARATION.dataset_files
        assert any(name.endswith("words.txt") for name in DECLARATION.dataset_files)
        assert all("/data/" in name for name in DECLARATION.dataset_files)

    def test_package_data_patterns_cover_datasets_and_the_typed_marker(self):
        """The globs in ``package-data`` include both kinds of payload."""
        assert "data/*.txt" in DECLARATION.package_data
        assert "data/*.csv" in DECLARATION.package_data
        assert "py.typed" in DECLARATION.package_data

    def test_the_typed_marker_is_found(self):
        """Day 23 ships the PEP 561 marker the ``Typed`` classifier promises."""
        assert DECLARATION.typed_marker == "datheon/py.typed"

    def test_a_missing_declaration_is_an_error(self, tmp_path: Path):
        """Pointing at a directory without ``pyproject.toml`` raises."""
        with pytest.raises(FileNotFoundError):
            distribution.load_declaration(tmp_path)


class TestInspectWheel:
    """Reading a wheel without installing it."""

    def test_the_metadata_is_read(self, tmp_path: Path):
        """Name, version and the Python floor come from ``METADATA``."""
        wheel = distribution.inspect_wheel(make_wheel(tmp_path))
        assert wheel.name == "datheon-python-internship"
        assert wheel.version == "1.0.0"
        assert wheel.requires_python == ">=3.10"

    def test_the_console_scripts_are_read(self, tmp_path: Path):
        """The ``entry_points.txt`` table is exposed as a mapping."""
        wheel = distribution.inspect_wheel(make_wheel(tmp_path))
        assert dict(wheel.console_scripts) == EXPECTED_SCRIPTS

    def test_modules_packages_and_data_are_separated(self, tmp_path: Path):
        """Top-level modules, packages and payload files are distinguished."""
        wheel = distribution.inspect_wheel(make_wheel(tmp_path))
        assert "cli_contract.py" in wheel.modules
        assert "datheon" in wheel.packages
        assert "projects" in wheel.packages
        assert "datheon/cli" in wheel.package_dirs
        assert any(name.endswith("words.txt") for name in wheel.data_files)
        assert wheel.has_typed_marker is True

    def test_the_licence_is_inside_dist_info(self, tmp_path: Path):
        """A licence file is carried with the metadata."""
        wheel = distribution.inspect_wheel(make_wheel(tmp_path))
        assert wheel.license_files
        assert all("/licenses/" in name for name in wheel.license_files)

    def test_a_zip_without_metadata_is_rejected(self, tmp_path: Path):
        """A zip that is not a wheel is an error, not an empty result."""
        path = tmp_path / "not-a-wheel.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("something.py", b"")
        with pytest.raises(ValueError, match="no .dist-info/METADATA"):
            distribution.inspect_wheel(path)

    def test_a_wheel_without_content_is_rejected(self, tmp_path: Path):
        """Metadata alone is not an importable distribution."""
        path = tmp_path / "empty.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("empty-1.0.dist-info/METADATA", b"Name: empty\nVersion: 1.0\n")
        with pytest.raises(ValueError, match="no importable content"):
            distribution.inspect_wheel(path)


class TestVerifyWheel:
    """Checking a wheel against the declaration."""

    def test_a_faithful_wheel_passes_every_check(self, tmp_path: Path):
        """The synthetic baseline is clean, so every failure below is the mutation."""
        results = distribution.verify_wheel(
            distribution.inspect_wheel(make_wheel(tmp_path)), DECLARATION
        )
        assert failed(results) == []
        assert len(results) >= 15

    def test_a_wrong_version_is_caught(self, tmp_path: Path):
        """A stale version in the metadata is a release defect."""
        wheel = make_wheel(tmp_path, version="0.9.0")
        assert "wheel:version" in failed(
            distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        )

    def test_a_wrong_name_is_caught(self, tmp_path: Path):
        """A renamed distribution does not install as the documented one."""
        wheel = make_wheel(tmp_path, name="datheon-internship")
        assert "wheel:name" in failed(
            distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        )

    def test_a_wrong_python_floor_is_caught(self, tmp_path: Path):
        """``Requires-Python`` must survive into the artefact."""
        wheel = make_wheel(tmp_path, requires_python=">=3.8")
        results = distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        assert "wheel:requires-python" in failed(results)

    def test_a_missing_console_script_is_named(self, tmp_path: Path):
        """The report says *which* command vanished."""
        scripts = dict(EXPECTED_SCRIPTS)
        scripts.pop("organizer")
        wheel = make_wheel(tmp_path, scripts=scripts)
        assert "wheel:script:organizer" in failed(
            distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        )

    def test_a_script_pointing_at_the_wrong_module_is_caught(self, tmp_path: Path):
        """A stale target is a stub that raises ``ImportError`` on the user's PATH."""
        scripts = dict(EXPECTED_SCRIPTS)
        scripts["hangman"] = "datheon.cli.hangman:run"
        wheel = make_wheel(tmp_path, scripts=scripts)
        assert "wheel:script:hangman" in failed(
            distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        )

    def test_an_undeclared_console_script_is_caught(self, tmp_path: Path):
        """A command nobody documented is a command nobody tests."""
        scripts = dict(EXPECTED_SCRIPTS)
        scripts["surprise"] = "datheon.cli.hangman:main"
        wheel = make_wheel(tmp_path, scripts=scripts)
        assert "wheel:no-extra-scripts" in failed(
            distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        )

    def test_a_missing_core_module_is_caught(self, tmp_path: Path):
        """A ``py-modules`` entry left out breaks every import of it."""
        wheel = make_wheel(tmp_path, drop=["validators.py"])
        results = distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        assert "wheel:py-modules" in failed(results)
        assert "validators" in next(
            check.detail for check in results if check.name == "wheel:py-modules"
        )

    def test_a_missing_dataset_is_caught(self, tmp_path: Path):
        """The word list is the one file whose absence only shows at runtime."""
        dataset = next(name for name in DECLARATION.dataset_files if name.endswith("words.txt"))
        wheel = make_wheel(tmp_path, drop=[dataset])
        results = distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        assert "wheel:datasets" in failed(results)
        assert "words.txt" in next(
            check.detail for check in results if check.name == "wheel:datasets"
        )

    def test_a_missing_typed_marker_is_caught(self, tmp_path: Path):
        """The ``Typing :: Typed`` classifier is only true if the marker ships."""
        wheel = make_wheel(tmp_path, py_typed=False)
        assert "wheel:py.typed" in failed(
            distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        )

    def test_a_missing_licence_is_caught(self, tmp_path: Path):
        """The MIT licence travels with the code."""
        wheel = make_wheel(tmp_path, license_file=False)
        assert "wheel:license" in failed(
            distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        )

    def test_a_missing_package_is_caught(self, tmp_path: Path):
        """A package directory without its ``__init__.py`` is not importable."""
        wheel = make_wheel(tmp_path, drop=["datheon/cli/__init__.py"])
        assert "wheel:packages" in failed(
            distribution.verify_wheel(distribution.inspect_wheel(wheel), DECLARATION)
        )

    def test_a_package_data_glob_matching_nothing_is_caught(self, tmp_path: Path):
        """A glob in the declaration that matches no file is a dead promise."""
        declaration = distribution.Declaration(
            **{**DECLARATION.__dict__, "package_data": ("data/*.json",)}
        )
        results = distribution.verify_wheel(
            distribution.inspect_wheel(make_wheel(tmp_path)), declaration
        )
        assert "wheel:package-data" in failed(results)

    def test_a_declaration_without_a_typed_marker_skips_that_check(self, tmp_path: Path):
        """Repositories that ship no marker are not asked to."""
        declaration = distribution.Declaration(**{**DECLARATION.__dict__, "typed_marker": None})
        results = distribution.verify_wheel(
            distribution.inspect_wheel(make_wheel(tmp_path)), declaration
        )
        assert "wheel:py.typed" not in [check.name for check in results]
        assert failed(results) == []


class TestInspectSdist:
    """Reading a source distribution without unpacking it."""

    def test_the_root_and_files_are_read(self, tmp_path: Path):
        """Members are reported relative to the single root directory."""
        sdist = distribution.inspect_sdist(make_sdist(tmp_path))
        assert sdist.root == "datheon_python_internship-1.0.0"
        assert sdist.name == "datheon-python-internship"
        assert sdist.version == "1.0.0"
        assert "pyproject.toml" in sdist.files
        assert "validators.py" in sdist.files

    def test_a_missing_pkg_info_is_rejected(self, tmp_path: Path):
        """Without ``PKG-INFO`` the archive is not a distribution."""
        path = make_sdist(tmp_path, pkg_info=False)
        with pytest.raises(ValueError, match="no PKG-INFO"):
            distribution.inspect_sdist(path)

    def test_two_top_level_directories_are_rejected(self, tmp_path: Path):
        """A stray directory means the archive cannot be rebuilt predictably."""
        path = make_sdist(tmp_path, second_root=True)
        with pytest.raises(ValueError, match="top-level directories"):
            distribution.inspect_sdist(path)

    def test_an_empty_archive_is_rejected(self, tmp_path: Path):
        """An sdist with no files is not a distribution either."""
        path = make_sdist(tmp_path, empty=True)
        with pytest.raises(ValueError, match="is empty"):
            distribution.inspect_sdist(path)


class TestVerifySdist:
    """Checking an sdist against the declaration."""

    def test_a_faithful_sdist_passes_every_check(self, tmp_path: Path):
        """The baseline is clean."""
        results = distribution.verify_sdist(
            distribution.inspect_sdist(make_sdist(tmp_path)), DECLARATION
        )
        assert failed(results) == []

    def test_a_wrong_version_is_caught(self, tmp_path: Path):
        """The sdist must agree with the declaration."""
        path = make_sdist(tmp_path, version="0.0.1")
        assert "sdist:version" in failed(
            distribution.verify_sdist(distribution.inspect_sdist(path), DECLARATION)
        )

    def test_a_missing_pyproject_is_caught(self, tmp_path: Path):
        """Without ``pyproject.toml`` the sdist cannot be rebuilt."""
        path = make_sdist(tmp_path, drop=["pyproject.toml"])
        assert "sdist:pyproject" in failed(
            distribution.verify_sdist(distribution.inspect_sdist(path), DECLARATION)
        )

    def test_a_missing_module_is_caught(self, tmp_path: Path):
        """A core module left out of the sdist breaks a rebuild."""
        path = make_sdist(tmp_path, drop=["errors.py"])
        results = distribution.verify_sdist(distribution.inspect_sdist(path), DECLARATION)
        assert "sdist:py-modules" in failed(results)
        assert "errors" in next(
            check.detail for check in results if check.name == "sdist:py-modules"
        )

    def test_a_missing_dataset_is_caught(self, tmp_path: Path):
        """Datasets ship in the sdist too, so a rebuild reproduces the wheel."""
        dataset = next(name for name in DECLARATION.dataset_files if name.endswith(".csv"))
        path = make_sdist(tmp_path, drop=[dataset])
        assert "sdist:datasets" in failed(
            distribution.verify_sdist(distribution.inspect_sdist(path), DECLARATION)
        )

    def test_build_noise_is_caught(self, tmp_path: Path):
        """Bytecode in an sdist is a build that ran in the wrong directory."""
        path = make_sdist(tmp_path, extra=["datheon/__pycache__/x.pyc"])
        results = distribution.verify_sdist(distribution.inspect_sdist(path), DECLARATION)
        assert "sdist:no-build-noise" in failed(results)
        assert "x.pyc" in next(
            check.detail for check in results if check.name == "sdist:no-build-noise"
        )


class TestVerifyPair:
    """Checking that the two artefacts describe one release."""

    def test_matching_artefacts_pass(self, tmp_path: Path):
        """Same name, same version, same as the declaration."""
        wheel = distribution.inspect_wheel(make_wheel(tmp_path))
        sdist = distribution.inspect_sdist(make_sdist(tmp_path))
        assert failed(distribution.verify_pair(wheel, sdist, DECLARATION)) == []

    def test_a_version_skew_between_artefacts_is_caught(self, tmp_path: Path):
        """A wheel and an sdist from different builds are two releases."""
        wheel = distribution.inspect_wheel(make_wheel(tmp_path))
        sdist = distribution.inspect_sdist(make_sdist(tmp_path / "other", version="1.0.1"))
        names = failed(distribution.verify_pair(wheel, sdist, DECLARATION))
        assert "pair:same-version" in names
        assert "pair:release-version" in names


class TestCollectArtifacts:
    """Finding the artefacts in a directory."""

    def test_one_wheel_and_one_sdist_are_returned(self, tmp_path: Path):
        """The pair is returned wheel first."""
        make_wheel(tmp_path)
        make_sdist(tmp_path)
        artifacts = distribution.collect_artifacts(tmp_path)
        assert [artifact.kind for artifact in artifacts] == ["wheel", "sdist"]
        assert all(artifact.size > 0 for artifact in artifacts)

    def test_a_missing_directory_is_reported(self, tmp_path: Path):
        """Pointing at a directory that does not exist is a clear error."""
        with pytest.raises(FileNotFoundError, match="does not exist"):
            distribution.collect_artifacts(tmp_path / "absent")

    def test_a_missing_wheel_is_reported(self, tmp_path: Path):
        """An sdist alone cannot be installed."""
        make_sdist(tmp_path)
        with pytest.raises(FileNotFoundError, match="no wheel"):
            distribution.collect_artifacts(tmp_path)

    def test_two_wheels_are_rejected(self, tmp_path: Path):
        """A stale artefact beside a fresh one is an ambiguous dist directory."""
        make_wheel(tmp_path)
        make_wheel(tmp_path, version="0.9.0")
        make_sdist(tmp_path)
        with pytest.raises(FileNotFoundError, match="2 wheels"):
            distribution.collect_artifacts(tmp_path)


class TestInstallEnvironment:
    """The environment handed to ``pip`` inside the clean venv."""

    def test_pip_location_overrides_are_removed(self):
        """``PIP_USER``/``PIP_TARGET``/``PIP_PREFIX`` would redirect the install."""
        env = distribution.install_environment(
            {"PIP_USER": "1", "PIP_TARGET": "/elsewhere", "PIP_PREFIX": "/opt", "PATH": "/bin"}
        )
        assert "PIP_USER" not in env
        assert "PIP_TARGET" not in env
        assert "PIP_PREFIX" not in env
        assert env["PATH"] == "/bin"

    def test_user_site_is_disabled(self):
        """The probe must see the venv, never the developer's site-packages."""
        assert distribution.install_environment({})["PYTHONNOUSERSITE"] == "1"


class TestSubprocessSteps:
    """``build`` and ``twine`` handling, with the subprocess mocked."""

    def test_a_successful_build_reports_the_artefacts(self, tmp_path: Path, monkeypatch):
        """The build result names what was produced."""

        def fake_build(*args, **kwargs):
            (tmp_path / "x.whl").write_bytes(b"")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(distribution.subprocess, "run", fake_build)
        result = distribution.build_artifacts(tmp_path)
        assert result.ok
        assert result.kind == "build"
        assert "x.whl" in result.detail

    def test_a_failed_build_reports_the_last_line_of_stderr(self, tmp_path: Path, monkeypatch):
        """A build failure is summarised, not dumped."""
        monkeypatch.setattr(
            distribution.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=1, stdout="", stderr="traceback\nERROR boom"
            ),
        )
        result = distribution.build_artifacts(tmp_path)
        assert not result.ok
        assert "ERROR boom" in result.detail

    def test_a_hung_build_is_reported_as_a_timeout(self, tmp_path: Path, monkeypatch):
        """A build that never returns must not hang the gate."""

        def explode(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="build", timeout=1)

        monkeypatch.setattr(distribution.subprocess, "run", explode)
        result = distribution.build_artifacts(tmp_path, timeout=1)
        assert not result.ok
        assert "exceeded" in result.detail

    def test_an_empty_build_is_a_failure(self, tmp_path: Path, monkeypatch):
        """A build that exits 0 without writing anything has not shipped."""
        monkeypatch.setattr(
            distribution.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        result = distribution.build_artifacts(tmp_path)
        assert not result.ok
        assert "no artefacts" in result.detail

    def test_the_clean_build_removes_stale_artefacts(self, tmp_path: Path, monkeypatch):
        """Yesterday's wheel must not be mistaken for today's."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "stale-0.1.whl").write_bytes(b"")
        monkeypatch.setattr(
            distribution.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        distribution.build_artifacts(dist_dir)
        assert not (dist_dir / "stale-0.1.whl").exists()

    def test_twine_success_counts_the_artefacts(self, tmp_path: Path, monkeypatch):
        """``twine check`` passing is a pass with a count."""
        make_wheel(tmp_path)
        make_sdist(tmp_path)
        monkeypatch.setattr(
            distribution.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="PASSED\nPASSED\n", stderr=""),
        )
        result = distribution.twine_check(distribution.collect_artifacts(tmp_path))
        assert result.ok
        assert "2 artefact(s)" in result.detail

    def test_twine_failure_is_reported(self, tmp_path: Path, monkeypatch):
        """A metadata error must fail the gate."""
        make_wheel(tmp_path)
        make_sdist(tmp_path)
        monkeypatch.setattr(
            distribution.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=1, stdout="", stderr="ERROR InvalidDistribution: bad metadata"
            ),
        )
        result = distribution.twine_check(distribution.collect_artifacts(tmp_path))
        assert not result.ok
        assert "bad metadata" in result.detail


class TestReporting:
    """The text and JSON renderings of a verdict."""

    def test_the_report_lists_every_check_and_the_artefacts(self, tmp_path: Path):
        """A build log reader can see what ran and what it weighed."""
        make_wheel(tmp_path)
        make_sdist(tmp_path)
        verdict = distribution.run_pipeline(tmp_path, build=False, twine=False, install=False)
        report = distribution.render_report(verdict)
        assert "Distribution gate" in report
        assert "[PASS]" in report
        assert ".whl" in report
        assert f"{len(verdict.checks)} checks passed" in report
        assert report.endswith("\n")

    def test_a_failing_report_names_each_failure(self, tmp_path: Path):
        """The failure list is the part a reader actually needs."""
        make_wheel(tmp_path, version="0.0.1")
        make_sdist(tmp_path, version="0.0.1")
        verdict = distribution.run_pipeline(tmp_path, build=False, twine=False, install=False)
        report = distribution.render_report(verdict)
        assert "[FAIL]" in report
        assert "do not match the declaration" in report
        assert "wheel:version" in report

    def test_the_json_verdict_is_serialisable_and_counted(self, tmp_path: Path):
        """CI consumes the JSON, so it must round-trip."""
        make_wheel(tmp_path)
        make_sdist(tmp_path)
        verdict = distribution.run_pipeline(tmp_path, build=False, twine=False, install=False)
        payload = distribution.verdict_to_dict(verdict)
        assert json.loads(json.dumps(payload))["passed"] is True
        assert payload["failed_count"] == 0
        assert payload["passed_count"] == len(payload["checks"])
        assert {artifact["kind"] for artifact in payload["artifacts"]} == {"wheel", "sdist"}


class TestMain:
    """The CLI contract: 0 pass, 1 artefact wrong, 2 no verdict."""

    def test_a_clean_dist_directory_exits_zero(self, tmp_path: Path, capsys):
        """``--report-only`` inspects without building."""
        make_wheel(tmp_path)
        make_sdist(tmp_path)
        code = distribution.main(
            ["--report-only", "--dist-dir", str(tmp_path), "--skip-twine", "--skip-install"]
        )
        assert code == 0
        assert "PASS" in capsys.readouterr().out

    def test_a_wrong_artefact_exits_one(self, tmp_path: Path):
        """An artefact that does not match the declaration fails the build."""
        make_wheel(tmp_path, drop=[DECLARATION.dataset_files[0]])
        make_sdist(tmp_path)
        code = distribution.main(
            ["--report-only", "--dist-dir", str(tmp_path), "--skip-twine", "--skip-install"]
        )
        assert code == 1

    def test_an_empty_dist_directory_cannot_reach_a_verdict(self, tmp_path: Path):
        """No artefacts means no verdict, never a silent success."""
        code = distribution.main(
            ["--report-only", "--dist-dir", str(tmp_path), "--skip-twine", "--skip-install"]
        )
        assert code == 2

    def test_a_failed_build_cannot_reach_a_verdict(self, tmp_path: Path, monkeypatch):
        """A build failure is exit 2, not a pass on yesterday's artefacts."""
        monkeypatch.setattr(
            distribution,
            "build_artifacts",
            lambda *a, **k: distribution.CheckResult(
                name="build", ok=False, detail="python -m build failed", kind="build"
            ),
        )
        assert distribution.main(["--dist-dir", str(tmp_path)]) == 2

    def test_the_json_flag_emits_machine_readable_output(self, tmp_path: Path, capsys):
        """CI reads the verdict, not the table."""
        make_wheel(tmp_path)
        make_sdist(tmp_path)
        code = distribution.main(
            [
                "--report-only",
                "--dist-dir",
                str(tmp_path),
                "--skip-twine",
                "--skip-install",
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["passed"] is True

    def test_the_parser_defaults_are_the_repository_conventions(self):
        """The default dist directory is ``dist/`` at the repository root."""
        args = distribution.build_parser().parse_args([])
        assert args.dist_dir == distribution.DEFAULT_DIST_DIR
        assert args.dist_dir.name == "dist"
        assert args.report_only is False
        assert args.no_isolation is False
        assert args.skip_twine is False
        assert args.skip_install is False
        assert args.json is False
