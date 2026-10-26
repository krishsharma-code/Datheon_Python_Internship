"""Automated file organizer and cleanup utility.

The module scans a directory, classifies each file by extension, and moves it
into a category subfolder. Moves run concurrently on a thread pool, collisions
are resolved by renaming, duplicates are detected by SHA-256 content hash, and
every action is recorded so it can be serialised to a JSON activity log.

Typical use::

    from organizer import FileOrganizer, write_json_log

    report = FileOrganizer().organize("~/Downloads", dry_run=True)
    print(report.moved, "files would move")
    write_json_log(report, "organizer.log")
"""

from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

from edge_cases import iter_files
from errors import ValidationError

#: Default extension → category mapping.
CATEGORY_MAP: Dict[str, str] = {
    # Documents
    ".pdf": "Documents",
    ".doc": "Documents",
    ".docx": "Documents",
    ".txt": "Documents",
    ".md": "Documents",
    ".rtf": "Documents",
    ".odt": "Documents",
    ".xls": "Documents",
    ".xlsx": "Documents",
    ".csv": "Documents",
    ".ppt": "Documents",
    ".pptx": "Documents",
    # Images
    ".jpg": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".gif": "Images",
    ".bmp": "Images",
    ".svg": "Images",
    ".webp": "Images",
    ".tiff": "Images",
    # Videos
    ".mp4": "Videos",
    ".mkv": "Videos",
    ".mov": "Videos",
    ".avi": "Videos",
    ".webm": "Videos",
    # Audio
    ".mp3": "Audio",
    ".wav": "Audio",
    ".flac": "Audio",
    ".aac": "Audio",
    ".ogg": "Audio",
    # Archives
    ".zip": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".bz2": "Archives",
    # Code
    ".py": "Code",
    ".js": "Code",
    ".ts": "Code",
    ".java": "Code",
    ".c": "Code",
    ".cpp": "Code",
    ".go": "Code",
    ".rs": "Code",
    ".sh": "Code",
    ".html": "Code",
    ".css": "Code",
    ".json": "Code",
}

#: Fallback category for unrecognised extensions.
DEFAULT_CATEGORY: str = "Other"

#: All category folder names, including the fallback.
CATEGORIES: List[str] = sorted(set(CATEGORY_MAP.values()) | {DEFAULT_CATEGORY})


def category_for(path: Union[str, Path], mapping: Optional[Dict[str, str]] = None) -> str:
    """Return the category folder a file belongs to.

    Args:
        path: The file (or filename) to classify.
        mapping: Optional custom extension → category mapping. Defaults to
            :data:`CATEGORY_MAP`.

    Returns:
        The category name, or :data:`DEFAULT_CATEGORY` when the extension is
        unknown.
    """
    table = mapping if mapping is not None else CATEGORY_MAP
    suffix = Path(path).suffix.lower()
    return table.get(suffix, DEFAULT_CATEGORY)


def sha256_of_file(path: Union[str, Path], chunk_size: int = 65536) -> str:
    """Compute the SHA-256 digest of a file's contents.

    Args:
        path: Path to the file.
        chunk_size: Read buffer size in bytes.

    Returns:
        The lowercase hexadecimal digest.

    Raises:
        FileNotFoundError: If ``path`` is not an existing file.
        OSError: If the file cannot be read.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Not a file: {file_path}")
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def find_duplicates(directory: Union[str, Path]) -> Dict[str, List[Path]]:
    """Group files in ``directory`` by content hash.

    Args:
        directory: Directory to scan (non-recursive).

    Returns:
        A mapping of SHA-256 digest → list of paths sharing that content, for
        every hash with **two or more** files. An empty dict means no
        duplicates.

    Raises:
        NotADirectoryError: If ``directory`` is not a directory.
        PermissionError: If ``directory`` cannot be listed.
    """
    root = Path(directory).expanduser()
    by_hash: Dict[str, List[Path]] = {}
    for entry in iter_files(root):
        try:
            digest = sha256_of_file(entry)
        except OSError:
            continue
        by_hash.setdefault(digest, []).append(entry)
    return {digest: paths for digest, paths in by_hash.items() if len(paths) > 1}


def unique_destination(
    source: Union[str, Path],
    dest_dir: Union[str, Path],
    *,
    max_attempts: int = 1000,
) -> Path:
    """Return a collision-free destination path inside ``dest_dir``.

    If ``dest_dir/<name>`` already exists, ``" (1)"``, ``" (2)"`` … is appended
    to the stem until a free name is found. The search is bounded, so a
    pathological directory cannot trap the tool in an infinite loop.

    Args:
        source: The file being moved (its name is preserved).
        dest_dir: Target directory (need not exist yet).
        max_attempts: Maximum number of ``" (n)"`` suffixes to try before
            giving up.

    Returns:
        A :class:`~pathlib.Path` that does not yet exist.

    Raises:
        FileExistsError: If no free name is found within ``max_attempts``.
    """
    source_path = Path(source)
    target_dir = Path(dest_dir)
    candidate = target_dir / source_path.name
    if not candidate.exists():
        return candidate

    stem, suffix = source_path.stem, source_path.suffix
    counter = 1
    while counter <= max_attempts:
        candidate = target_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
    raise FileExistsError(
        f"could not find a free name for {source_path.name!r} in {target_dir} "
        f"after {max_attempts} attempts"
    )


@dataclass
class MoveRecord:
    """The outcome of processing a single file.

    Attributes:
        source: Original path.
        destination: Destination path (empty when only planned or errored).
        category: Category folder the file was assigned to.
        status: One of ``"moved"``, ``"planned"``, ``"skipped"``, ``"error"``.
        detail: Optional human-readable explanation.
    """

    source: str
    destination: str
    category: str
    status: str
    detail: str = ""


@dataclass
class OrganizeReport:
    """Aggregated result of an :meth:`FileOrganizer.organize` run.

    Attributes:
        directory: The directory that was organised.
        dry_run: Whether this was a preview run.
        records: Per-file outcomes.
        duplicates: Content-hash → list of duplicate paths (if detected).
    """

    directory: str
    dry_run: bool = False
    records: List[MoveRecord] = field(default_factory=list)
    duplicates: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def moved(self) -> int:
        """Number of files actually moved."""
        return sum(1 for r in self.records if r.status == "moved")

    @property
    def planned(self) -> int:
        """Number of files that would move in a dry run."""
        return sum(1 for r in self.records if r.status == "planned")

    @property
    def skipped(self) -> int:
        """Number of files left in place."""
        return sum(1 for r in self.records if r.status == "skipped")

    @property
    def errors(self) -> List[MoveRecord]:
        """Records that ended in an error."""
        return [r for r in self.records if r.status == "error"]

    @property
    def total_processed(self) -> int:
        """Total number of files considered."""
        return len(self.records)

    def to_dict(self) -> Dict[str, object]:
        """Return the report as a JSON-serialisable dictionary."""
        return {
            "directory": self.directory,
            "dry_run": self.dry_run,
            "summary": {
                "total_processed": self.total_processed,
                "moved": self.moved,
                "planned": self.planned,
                "skipped": self.skipped,
                "errors": len(self.errors),
            },
            "duplicates": self.duplicates,
            "records": [asdict(r) for r in self.records],
        }


class FileOrganizer:
    """Organises a directory into category subfolders.

    Args:
        mapping: Extension → category mapping. Defaults to :data:`CATEGORY_MAP`.
        max_workers: Thread-pool size for concurrent moves.
    """

    def __init__(
        self,
        mapping: Optional[Dict[str, str]] = None,
        max_workers: int = 4,
    ) -> None:
        if max_workers < 1:
            raise ValidationError(
                f"max_workers must be at least 1, got {max_workers}.",
                hint="use a pool size of 1 or more",
                context={"field": "max_workers", "value": max_workers},
            )
        self.mapping: Dict[str, str] = dict(mapping) if mapping else dict(CATEGORY_MAP)
        self.max_workers = max_workers

    def _target_dir(self, root: Path, category: str) -> Path:
        """Return (and, on a real run, create) the category subfolder."""
        return root / category

    def _process(
        self,
        source: Path,
        root: Path,
        dry_run: bool,
    ) -> MoveRecord:
        """Classify and (optionally) move a single file."""
        category = category_for(source, self.mapping)
        try:
            dest_dir = self._target_dir(root, category)
            destination = unique_destination(source, dest_dir)
            if dry_run:
                return MoveRecord(
                    str(source),
                    str(destination),
                    category,
                    "planned",
                    "dry run — no change made",
                )
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            return MoveRecord(str(source), str(destination), category, "moved")
        except PermissionError as exc:
            return MoveRecord(str(source), "", category, "error", f"permission denied: {exc}")
        except FileExistsError as exc:
            return MoveRecord(str(source), "", category, "error", f"exists: {exc}")
        except OSError as exc:
            return MoveRecord(str(source), "", category, "error", f"os error: {exc}")

    def organize(
        self,
        directory: Union[str, Path],
        dry_run: bool = False,
        detect_duplicates: bool = False,
    ) -> OrganizeReport:
        """Organise every file directly inside ``directory``.

        Category subfolders are never themselves moved. Moves are dispatched to
        a thread pool and the results are collected in deterministic source
        order.

        Args:
            directory: Directory to organise.
            dry_run: When ``True``, only report what *would* happen.
            detect_duplicates: When ``True``, compute SHA-256 hashes and record
                groups of identical files.

        Returns:
            An :class:`OrganizeReport`.

        Raises:
            NotADirectoryError: If ``directory`` is not a directory.
            PermissionError: If ``directory`` cannot be listed.
        """
        root = Path(directory).expanduser()
        files = iter_files(root)

        report = OrganizeReport(directory=str(root), dry_run=dry_run)

        if detect_duplicates:
            report.duplicates = {
                digest: [str(p) for p in paths] for digest, paths in find_duplicates(root).items()
            }

        if not files:
            return report

        results: Dict[str, MoveRecord] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._process, source, root, dry_run): source for source in files
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    results[str(source)] = future.result()
                except Exception as exc:  # defensive: never lose a file silently
                    results[str(source)] = MoveRecord(
                        str(source),
                        "",
                        category_for(source, self.mapping),
                        "error",
                        f"unexpected: {exc}",
                    )

        report.records = [results[str(source)] for source in files if str(source) in results]
        return report


def write_json_log(report: OrganizeReport, path: Union[str, Path]) -> Path:
    """Write an organisation report to a JSON activity log.

    Args:
        report: The report to serialise.
        path: Destination ``.json`` path.

    Returns:
        The resolved destination :class:`~pathlib.Path`.
    """
    out_path = Path(path)
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(report.to_dict(), handle, indent=2)
    return out_path
