# Task 3 — Automated File Organizer & Cleanup Utility

An autonomous directory-cleanup utility that sorts files into category
subfolders using the Python standard library (`os`, `shutil`, `pathlib`,
`hashlib`, `concurrent.futures`).

## Features

- **Extension-based sorting** into `Documents`, `Images`, `Videos`, `Audio`,
  `Archives`, `Code`, and a fallback `Other` folder.
- **Multi-threaded moves** via `ThreadPoolExecutor`.
- **Collision-safe renaming** — an existing name becomes `name (1).ext`.
- **SHA-256 duplicate detection** — groups byte-identical files by content hash.
- **Robust error handling** — `PermissionError`, `FileExistsError`, and general
  `OSError` are caught per file and reported, never crashing the run.
- **Dry-run preview** with `--dry-run`.
- **JSON activity logging** with `--log`.

## Run

```bash
# Preview what would happen
python projects/03_file_organizer_automation/main.py ~/Downloads --dry-run

# Actually organize
python projects/03_file_organizer_automation/main.py ~/Downloads

# With duplicate detection and a JSON log
python projects/03_file_organizer_automation/main.py ~/Downloads \
    --duplicates --log organizer_run.json
```

## Architecture

| File | Responsibility |
| --- | --- |
| `organizer.py` | `FileOrganizer`, `category_for`, `find_duplicates`, `unique_destination`, `write_json_log`. |
| `main.py` | Argument parsing and formatted summary output. |

## Safety

This tool **never deletes** anything — it only relocates files into subfolders.
Run with `--dry-run` first; it prints every intended move without touching the
filesystem.
