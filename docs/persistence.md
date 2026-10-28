# Persistence contract — atomic, versioned writes

Every file the Datheon tools produce goes through
[`persistence.py`](../persistence.py). This document is the contract: what is
guaranteed, what is not, and how a stored document is versioned.

Before Day 9 every write was a plain `open(path, "w")`. That has two failure
modes no upstream validation can fix:

| Failure | What a plain write does | What the contract does |
| --- | --- | --- |
| Process killed, disk full, writer raises mid-way | Truncates the target *first*, so the previous good file is already gone and the destination is left empty or half-written | Writes a temporary file, then publishes it with one `os.replace`; the target is either the complete old file or the complete new one |
| Reader opens a file from an older tool | Nothing marks the format, so the payload is misread | Every JSON document carries `schema_version`; the reader rejects an unknown version |

---

## 1. Atomic write

```python
from persistence import atomic_write_text, atomic_write_json, atomic_write_with

atomic_write_text("out/report.txt", "hello\n")
atomic_write_json("out/run.json", {"summary": {...}, "records": [...]})

def render(handle):                       # any stream-based format
    writer = csv.DictWriter(handle, fieldnames=[...])
    ...

atomic_write_with("out/summary.csv", render, newline="")
```

The sequence, in order:

1. Create the parent directory if it is missing.
2. Create a temporary file **in the destination directory** with
   `tempfile.mkstemp` — a rename across filesystems is not atomic, so the
   staging file can never live in `/tmp`.
3. Write the content, `flush()`, and `fsync()` the file descriptor.
4. Copy the existing target's permission bits onto the replacement, so a write
   never silently changes a file's mode.
5. `os.replace(temp, target)` — one atomic rename.
6. `fsync()` the directory entry (best effort; skipped where the platform does
   not support it, e.g. Windows).
7. On any failure: delete the temporary file and leave the target untouched.

Every writer returns a `WriteReceipt`:

```python
receipt = atomic_write_json("out/run.json", payload)
receipt.path              # Path('out/run.json')
receipt.bytes_written     # 812
receipt.replaced_existing # True when a file was overwritten
receipt.schema_version    # 1
receipt.describe()        # 'replaced out/run.json (812 bytes, schema v1)'
```

Passing `fsync=False` skips steps 3 and 6. That is a durability trade for
speed and belongs only in tests and scratch writes: the rename is still atomic,
so the file is never torn, but the content may not survive a power loss.

### What atomicity does *not* cover

- **Concurrent writers.** Two processes writing the same path both stage
  successfully; the last `os.replace` wins and the loser's content is dropped —
  no file is ever corrupt, but no merge happens either. Use one writer per path.
- **Appends.** `O_APPEND` guarantees a shared offset, not that a multi-byte
  write lands in one piece, so a crash can leave a half-written trailing line.
  `atomic_append_text` therefore reads the current content and publishes
  old-plus-new in a single rename: atomic, but O(size) per call. That is the
  right trade for the conversation log (thousands of short lines); a
  high-volume log should rotate instead.

---

## 2. Versioned documents

```python
from persistence import SCHEMA_VERSION, SCHEMA_VERSION_FIELD, read_json_document

atomic_write_json("out/run.json", payload)
# -> {"schema_version": 1, "summary": {...}, "records": [...]}

document = read_json_document("out/run.json", field="activity log", require_keys=("summary",))
document[SCHEMA_VERSION_FIELD]  # 1
```

| Constant | Meaning |
| --- | --- |
| `SCHEMA_VERSION` | The version this code **writes** (currently `1`) |
| `SUPPORTED_SCHEMA_VERSIONS` | The versions it can **read** (currently `(1,)`) |
| `SCHEMA_VERSION_FIELD` | The header key, `"schema_version"` |

The header is stamped first, so it is the first key in the rendered JSON and
the version is obvious to anyone who opens the file.

`read_json_document` decodes through `dataset_loader.read_text_robust`, so a
UTF-8 BOM or a CRLF-authored file still loads, then enforces:

| Condition | Result |
| --- | --- |
| Not valid JSON | `PersistenceError` (DT-3005) naming the line and column |
| Not a JSON object | `PersistenceError` |
| No `schema_version` | `SchemaVersionError` (DT-3006) |
| `schema_version` not a positive integer (`true` and `"1"` are rejected) | `SchemaVersionError` |
| Version **newer** than `SCHEMA_VERSION` | `SchemaVersionError` — "written by a newer tool" |
| Version older than every supported version | `SchemaVersionError` — "not readable", naming the supported set |
| A key in `require_keys` is absent | `PersistenceError` |

`SchemaVersionError` subclasses `PersistenceError`, which subclasses both
`OperationError` and `OSError`, so `except OSError` in an entry point still
catches a failed write and maps it onto exit code `1`.

### Changing a format

1. Bump `SCHEMA_VERSION`.
2. Add the new number to `SUPPORTED_SCHEMA_VERSIONS` (keep the old one if the
   reader must understand both).
3. If old documents must be upgraded in place, do it in a migration step, not
   in the reader — the reader's job is to refuse, not to guess.

---

## 3. Who writes through it

| Call site | Format | Change on Day 9 |
| --- | --- | --- |
| `organizer.write_json_log` | JSON | Atomic publish, `schema_version` stamped; new `read_json_log` reads it back |
| `portfolio.write_summary_csv` | CSV | Atomic publish through `atomic_write_with` |
| `chatbot.ConversationLogger.log` | JSONL | Each turn stamped with `schema_version`; appended through `atomic_append_text` |
| `error_catalogue.write_catalogue` | Markdown | Atomic publish, so a regeneration cannot truncate `docs/error_catalogue.md` |

---

## 4. Cost

| Operation | Time | Space | Notes |
| --- | --- | --- | --- |
| `atomic_write_text` / `_bytes` | O(n) | O(n) | One write, one `fsync`, one rename; n = payload bytes |
| `atomic_write_json` | O(n) | O(n) | `json.dumps` in memory, then O(n) |
| `atomic_append_text` | O(n) | O(n) | Reads n, writes n + m — quadratic over k appends |
| `read_json_document` | O(n) | O(n) | Decode + parse, one pass each |
| `check_schema_version` | O(1) | O(1) | Dictionary lookup + tuple membership |

The `fsync` is the dominant cost — typically 1–5 ms on a spinning disk, tens of
microseconds on an SSD — and it is what makes a completed write survive a power
loss. Batch a run's output into one document rather than writing per record.

---

## 5. Verified by

`tests/test_persistence.py` covers the round trip for every writer, the
`WriteReceipt` contents, mode preservation, BOM/CRLF tolerance, every
`SchemaVersionError` branch, and — the point of the exercise — that a writer
that raises, a rename that fails, an interrupted write and an unwritable parent
all leave the previous file byte-for-byte intact with no temporary file left
behind. The module runs at 100% line coverage.
