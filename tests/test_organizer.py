"""Tests for the file organizer and cleanup utility."""

from __future__ import annotations

import json

import pytest

from organizer import (
    CATEGORY_MAP,
    DEFAULT_CATEGORY,
    FileOrganizer,
    category_for,
    find_duplicates,
    sha256_of_file,
    unique_destination,
    write_json_log,
)


def _make_file(directory, name, content="data"):
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


# --- category_for ------------------------------------------------------
@pytest.mark.parametrize(
    "name,expected",
    [
        ("report.pdf", "Documents"),
        ("photo.JPG", "Images"),
        ("clip.mp4", "Videos"),
        ("song.mp3", "Audio"),
        ("backup.zip", "Archives"),
        ("script.py", "Code"),
        ("mystery.xyz", DEFAULT_CATEGORY),
        ("noextension", DEFAULT_CATEGORY),
    ],
)
def test_category_for(name, expected):
    assert category_for(name) == expected


def test_category_for_custom_mapping():
    assert category_for("a.foo", {".foo": "Custom"}) == "Custom"


# --- unique_destination ------------------------------------------------
def test_unique_destination_no_collision(tmp_path):
    dest = unique_destination(tmp_path / "a.txt", tmp_path / "out")
    assert dest == tmp_path / "out" / "a.txt"


def test_unique_destination_renames_on_collision(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _make_file(out, "a.txt")
    dest = unique_destination(tmp_path / "a.txt", out)
    assert dest.name == "a (1).txt"
    _make_file(out, "a (1).txt")
    dest2 = unique_destination(tmp_path / "a.txt", out)
    assert dest2.name == "a (2).txt"


# --- hashing / duplicates ---------------------------------------------
def test_sha256_of_file(tmp_path):
    path = _make_file(tmp_path, "a.txt", "hello")
    digest = sha256_of_file(path)
    assert len(digest) == 64
    assert digest == sha256_of_file(path)


def test_sha256_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        sha256_of_file(tmp_path / "nope.txt")


def test_find_duplicates_groups_identical_files(tmp_path):
    _make_file(tmp_path, "a.txt", "same")
    _make_file(tmp_path, "b.txt", "same")
    _make_file(tmp_path, "c.txt", "different")
    dupes = find_duplicates(tmp_path)
    assert len(dupes) == 1
    group = next(iter(dupes.values()))
    assert {p.name for p in group} == {"a.txt", "b.txt"}


def test_find_duplicates_none(tmp_path):
    _make_file(tmp_path, "a.txt", "one")
    _make_file(tmp_path, "b.txt", "two")
    assert find_duplicates(tmp_path) == {}


def test_find_duplicates_not_a_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        find_duplicates(tmp_path / "missing")


# --- organize ----------------------------------------------------------
def test_organize_moves_files_into_categories(tmp_path):
    _make_file(tmp_path, "report.pdf")
    _make_file(tmp_path, "photo.jpg")
    _make_file(tmp_path, "notes.txt")
    _make_file(tmp_path, "script.py")

    report = FileOrganizer(max_workers=2).organize(tmp_path)

    assert report.moved == 4
    assert (tmp_path / "Documents" / "report.pdf").exists()
    assert (tmp_path / "Images" / "photo.jpg").exists()
    assert (tmp_path / "Documents" / "notes.txt").exists()
    assert (tmp_path / "Code" / "script.py").exists()


def test_organize_dry_run_moves_nothing(tmp_path):
    _make_file(tmp_path, "report.pdf")
    report = FileOrganizer().organize(tmp_path, dry_run=True)
    assert report.planned == 1
    assert report.moved == 0
    assert (tmp_path / "report.pdf").exists()
    assert not (tmp_path / "Documents").exists()


def test_organize_handles_collision(tmp_path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    _make_file(docs, "report.pdf", "existing")
    _make_file(tmp_path, "report.pdf", "incoming")

    report = FileOrganizer().organize(tmp_path)
    assert report.moved == 1
    assert (docs / "report (1).pdf").exists()


def test_organize_ignores_subdirectories(tmp_path):
    (tmp_path / "Images").mkdir()
    _make_file(tmp_path, "a.png")
    report = FileOrganizer().organize(tmp_path)
    # Only the png is moved; the existing Images folder is not touched.
    assert report.total_processed == 1
    assert (tmp_path / "Images" / "a.png").exists()


def test_organize_empty_directory(tmp_path):
    report = FileOrganizer().organize(tmp_path)
    assert report.total_processed == 0
    assert report.moved == 0


def test_organize_not_a_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        FileOrganizer().organize(tmp_path / "nope")


def test_organize_with_duplicate_detection(tmp_path):
    _make_file(tmp_path, "a.txt", "same")
    _make_file(tmp_path, "b.txt", "same")
    report = FileOrganizer().organize(tmp_path, dry_run=True, detect_duplicates=True)
    assert len(report.duplicates) == 1


def test_organizer_rejects_bad_workers():
    with pytest.raises(ValueError):
        FileOrganizer(max_workers=0)


# --- logging -----------------------------------------------------------
def test_write_json_log(tmp_path):
    _make_file(tmp_path, "a.pdf")
    report = FileOrganizer().organize(tmp_path)
    log_path = write_json_log(report, tmp_path / "run.json")

    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert data["summary"]["moved"] == 1
    assert data["records"][0]["status"] == "moved"


def test_category_map_covers_common_types():
    assert ".pdf" in CATEGORY_MAP
    assert ".py" in CATEGORY_MAP


def test_unique_destination_gives_up_after_max_attempts(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _make_file(out, "a.txt")
    _make_file(out, "a (1).txt")
    with pytest.raises(FileExistsError):
        unique_destination(tmp_path / "a.txt", out, max_attempts=1)
