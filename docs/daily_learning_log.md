# Daily Learning Log — Datheon Python Internship

**Intern:** Krish Sharma · **Credential ID:** DT-2026-O6Q0X
**Program:** Python Programming Internship (Batch 01 · 2026 Cohort)

This log records daily engineering progress: architectural decisions,
complexity analysis, and milestones. Each entry corresponds to one branch /
pull request in the Branch → PR → Review → Merge lifecycle.

---

## Day 1 — Repository scaffold & Task 1–5 foundations

**Branch:** `feat/datheon-day-1-scaffold`
**Scope:** Initialise the repository and implement the five task projects with
tests and CI.

### Milestones

- Created the public repository with MIT licence, contributing guide, and a
  strict `.gitignore`.
- Implemented all five Datheon task-pack projects as modular packages.
- Added a `pytest` suite covering happy paths and edge cases.
- Wired a GitHub Actions CI pipeline (flake8 + black + pytest) across Python
  3.10–3.12.

### Architectural decisions

1. **Pure core + thin shell.** Every project separates a UI-independent core
   module (`hangman.py`, `portfolio.py`, `organizer.py`, `chatbot.py`,
   `models.py`/`inventory.py`) from an interactive `main.py`. This keeps the
   logic importable and unit-testable without mocking `stdin`/`stdout`.
2. **Standard library only at runtime.** No third-party runtime dependencies —
   `random`, `csv`, `json`, `re`, `pathlib`, `shutil`, `hashlib`, `abc`,
   `concurrent.futures` cover every requirement. This keeps the projects
   portable and the CI fast.
3. **Numeric-prefixed project folders.** Folders mirror the task-pack ordering
   (`01_…`–`05_…`). Because such names are not valid Python identifiers, a
   root `conftest.py` injects each project directory into `sys.path` so modules
   import by their plain names in tests.
4. **Immutable results, mutable state.** Outcome objects (`GuessResult`,
   `Response`, `LoanRecord`, `MoveRecord`) are frozen dataclasses, while the
   engines that produce them hold mutable state. This makes the data flowing
   between layers safe to share.
5. **Fail loudly at the boundary.** Constructors and setters validate inputs
   (`ValueError`), while domain operations raise a dedicated
   `InventoryError`. Filesystem failures are caught per file so one bad file
   never aborts a whole organizer run.
6. **Concurrency only where it pays.** The organizer moves files on a
   `ThreadPoolExecutor` (I/O-bound), while results are re-ordered to the sorted
   source order for deterministic, reproducible reports.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `HangmanGame.guess` | hangman | O(L) | O(1) | Set membership + `masked_word` scan over word length L |
| `HangmanGame.masked_word` | hangman | O(L) | O(L) | Builds the masked string |
| `Portfolio.add` (merge) | portfolio | O(1) | O(1) | Constant-time blend; `get` is O(H) worst case |
| `Portfolio.total_value` | portfolio | O(H) | O(1) | Single pass over H holdings |
| `load_prices_from_csv` | portfolio | O(R) | O(U) | R rows → U unique symbols |
| `category_for` | organizer | O(1) | O(1) | Dict lookup on the extension |
| `sha256_of_file` | organizer | O(B) | O(1) | Streams B bytes in 64 KiB chunks |
| `find_duplicates` | organizer | O(N·B) | O(N) | Hash N files of B bytes each |
| `unique_destination` | organizer | O(K) | O(1) | K = number of name collisions |
| `FileOrganizer.organize` | organizer | O(N) wall-clock ≈ O(N/workers) | O(N) | Thread-pooled moves, N files |
| `IntentEngine.respond` | chatbot | O(I·P) | O(1) | I intents × P patterns each |
| `InventoryManager.checkout` | inventory | O(1) | O(1) | Dict lookup + append |
| `InventoryManager.audit_rows` | inventory | O(N log N) | O(N) | Sort N items by id |

### Edge cases covered by tests

- Hangman: non-alphabetic word, empty guess, multi-character guess, duplicate
  guesses (no penalty), guessing after the round ends, ASCII stage clamping.
- Portfolio: negative/zero quantities, empty portfolio, missing price file,
  malformed columns, non-numeric prices, `TOTAL` row integrity, collision-free
  parent-directory creation.
- Organizer: name collisions (`a (1).txt`), dry-run makes no changes,
  subdirectories are skipped, duplicate grouping by content hash, missing
  directory, JSON log structure.
- Assistant: unknown input fallback, empty input, case-insensitive exit,
  malformed/missing intent JSON, confidence scaling, priority tie-breaks,
  response rotation, JSONL log format.
- Inventory: abstract class cannot be instantiated, invalid price/borrow date,
  future borrow dates, polymorphic fees and lengths, equality/hash contracts,
  duplicate ids, double checkout, returning an item that is not on loan.

### Next up

- Task-pack polish: live-price integration for the portfolio tracker.
- Expand assistant intents and add a confidence threshold configuration.
- Add a coverage badge and enforce a minimum coverage gate in CI.

---

## Day 2 — Official dataset integration

**Branch:** `feat/datheon-day-2-datasets`
**Scope:** Wire the official Datheon task-pack datasets into the projects.

### Milestones

- Bundled the official English word list (`google-10000-english`, 9,894 lines)
  at `projects/01_hangman_game/data/words.txt`.
- Bundled the S&P 500 constituents dataset at
  `projects/02_portfolio_tracker/data/constituents.csv` (503 companies).
- Bundled the Datheon sample FAQ dataset at
  `projects/04_terminal_assistant/sample_faq.json`.
- Added three loaders plus a CLI flag for each project.

### Architectural decisions

1. **Data is bundled, not fetched at runtime.** Every dataset is committed to
   the repository so the projects stay offline-capable and the CI job needs no
   network access. Downloads happen once, at development time.
2. **Sanitise at the boundary, not in the algorithm.** `load_words_from_file`
   normalises case, rejects non-alphabetic tokens, applies length bounds, and
   de-duplicates while preserving first-seen order. The game engine then
   receives a clean, predictable list and needs no defensive checks.
3. **Backwards compatibility.** The original themed category banks and the
   hand-written `intents.json` remain the fallback paths, so nothing that worked
   on Day 1 regresses. New behaviour is opt-in via `--words-file` /
   `--categories`, `--constituents`, and `--faq`.
4. **Derive intents, don't hard-code them.** `IntentEngine.from_faq_json`
   converts each FAQ question into a regex of its significant keywords
   (stop-words and short tokens removed), so a new FAQ entry needs no code
   change. This keeps the dataset the single source of truth.
5. **Reuse the existing detection helper.** `load_symbols_from_csv` shares the
   case-insensitive column-detection logic (`_detect_key`) already used for
   price CSVs, so both loaders accept flexible header spellings.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `load_words_from_file` | words | O(L·W) | O(W) | L lines, W words kept; set-based de-dup |
| `get_random_word_from_list` | words | O(W) | O(W) | `random.choice` on a materialised list |
| `load_symbols_from_csv` | portfolio | O(R) | O(U) | R rows → U unique tickers |
| `Portfolio.unknown_symbols` | portfolio | O(H + S) | O(S) | H holdings, S universe symbols |
| `_keywords` | chatbot | O(T) | O(T) | T tokens in the question |
| `IntentEngine.from_faq_json` | chatbot | O(F·K) | O(F·K) | F FAQs × K keywords each |
| `IntentEngine.respond` (FAQ engine) | chatbot | O(F) | O(1) | One compiled regex per FAQ |

### Edge cases covered by tests

- Word list: short words, non-alphabetic tokens, duplicates, capitalisation,
  `min_length`/`max_length` bounds, invalid bounds, missing file, and a live
  assertion over the bundled list (>1,000 words, all alphabetic and lower-case).
- Constituents: S&P 500 header layout, missing file, missing ticker column,
  unknown-symbol detection, and a live assertion over the bundled dataset
  (>400 symbols, `AAPL` present).
- FAQ dataset: greeting match, keyword-derived FAQ matches, fallback for
  unknown input, missing file, malformed JSON, and a dataset with no FAQs.

### Next up

- Add a coverage gate to CI.
- Optional live-price path for the portfolio tracker behind a feature flag.
- Expand the FAQ dataset and expose a configurable confidence threshold.

---

## Day 3 — CLI ergonomics & exit-code contract

**Branch:** `feat/datheon-day-3-cli-contract`
**PR:** #4
**Scope:** Give all five CLIs one consistent command-line surface.

### Milestones

- Added `cli_contract.py`, the single source of truth for the CLI contract.
- Every entry point now builds its parser with `build_parser(...)`, exposing
  `-h/--help` and `--version` uniformly; `--version` prints `<prog> 1.0.0`.
- Standardised exit codes — `0` success, `1` user/data error, `2` usage error —
  exposed as the `ExitCode` `IntEnum`.
- Added `run_cli()`, which maps an uncaught `ValueError`/`OSError` to exit code
  `1` instead of leaking a traceback.
- Documented the contract in `docs/cli_contract.md` and the README.
- Added `tests/test_cli_contract.py`, which drives every CLI in a subprocess
  and asserts each exit code.

### Architectural decisions

1. **One contract, five consumers.** Rather than duplicating argparse setup in
   each `main.py`, the shared module owns parser construction, the version
   string, and the exit-code mapping. A new tool opts in with a single import.
2. **`IntEnum`, not magic numbers.** `ExitCode.OK` / `DATA_ERROR` /
   `USAGE_ERROR` documents intent at every `return` site and keeps the three
   codes in lock step across projects.
3. **`argparse`'s native exit code is the contract.** `argparse` already exits
   `2` on a malformed command line, so the contract adopts that value rather
   than fighting it — matching POSIX `sysexits` `EX_USAGE`.
4. **Errors are classified at the boundary.** `run_cli` treats `ValueError`
   and `OSError` as user/data errors (exit `1`), so an unhandled input problem
   becomes a clean message instead of a stack trace.
5. **Exit codes are tested end-to-end.** Because coverage cannot see a
   subprocess, the tests assert `returncode` directly — the observable contract
   a shell or CI script actually depends on.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `build_parser` | cli_contract | O(1) | O(1) | One `ArgumentParser` per invocation |
| `version_string` | cli_contract | O(1) | O(1) | Single f-string format |
| `run_cli` | cli_contract | O(main) | O(1) | Adds only one try/except frame |

### Edge cases covered by tests

- `--version` and `--help` exit `0` in all five tools.
- An unknown flag exits `2` in all five tools.
- A missing word file (hangman), a missing directory (organizer), a missing
  intent file (assistant), and a malformed price CSV (portfolio) each exit `1`.
- `run_cli` maps a raised `ValueError` to exit code `1` and writes to stderr.

### Next up

- Day 4: strict input validators and a `--strict` mode with actionable errors.

---

## Day 4 — Input validation layer

**Branch:** `feat/datheon-day-4-validation`
**PR:** #5
**Scope:** Reject bad input early, in every CLI, with a single actionable message.

### Milestones

- Added `validators.py`, a shared, pure validation layer: `validate_symbol`,
  `validate_quantity`, `validate_price`, `validate_int`, `validate_path`,
  `validate_letter`, `validate_date`, `validate_name`, `validate_message`, and
  the `add_strict_flag` helper.
- Every one of the five CLIs now exposes `--strict` and validates its boundary
  inputs before doing any work.
- Added `tests/test_validation.py` (64 tests) covering both modes, the error
  messages, and the end-to-end `--strict` behaviour of all five tools.
- Documented the layer in `docs/cli_contract.md`.

### Architectural decisions

1. **Lenient by default, strict on request.** The tools keep their friendly
   behaviour (trim whitespace, upper-case tickers, parse `str` numbers) unless
   `--strict` is passed, at which point they additionally enforce the *shape*
   of each value. This keeps Day 1-3 behaviour intact while offering a hard
   mode for scripting.
2. **One error type, mapped once.** Every failure raises `ValidationError`, a
   `ValueError` subclass. Because `cli_contract.run_cli` already maps
   `ValueError` onto exit code `1`, the new layer needs no wiring in the exit
   contract — a bad value becomes a clean message instead of a traceback.
3. **Messages are actionable.** Each message names the field, quotes the
   offending value, and shows a concrete example of valid input
   (`"--prices: file not found: X — e.g. data/sample_prices.csv"`), so the
   user can fix the problem without reading the source.
4. **Pure functions, no I/O, no printing.** The validators never print and
   never mutate global state, so they are trivially unit-testable and reusable
   from both the interactive shells and the pure cores.
5. **Validate at the boundary.** Paths, counts, and bounds are checked up
   front, before any filesystem access, so failures are cheap and consistent
   across tools.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `validate_symbol` | validators | O(S) | O(S) | S = symbol length; regex full-match |
| `validate_quantity` / `validate_price` | validators | O(1) | O(1) | `float()` + finiteness check |
| `validate_int` | validators | O(D) | O(1) | D = digit count |
| `validate_path` | validators | O(1) | O(1) | Existence checks are `stat` calls |
| `validate_date` | validators | O(1) | O(1) | `date.fromisoformat` |
| `_prompt_positions` (portfolio) | main | O(L) | O(1) | L input lines, one validation pass each |

### Edge cases covered by tests

- Symbols: empty/whitespace, over-long, non-ticker shapes (`9LIVES`,
  `AAPL US`); strict accepts `RELIANCE.NS` and `BRK-B`.
- Numbers: zero/negative quantities, `NaN`/`inf`, strict rejects `1,000` and
  `₹150`; prices allow zero by default and reject values above the strict cap.
- Integers: non-integers (`4.0`), bounds, strict rejects `1_000`.
- Paths: missing files/directories, wrong kind, strict requires an existing
  parent.
- Letters: multi-character, digits, empty; strict rejects non-ASCII letters.
- Dates: bad formats, future dates, `allow_future` override.
- Names/messages: empty, over-long, control characters; strict message limits.
- CLI: `--strict` appears in every `--help`; `--min-length 0`, `--workers 0`,
  a missing price file under `--strict`, an unknown ticker, an empty borrower,
  `--days 0`, and a future `--borrow-date` each exit `1` with the field named
  in stderr — while the same invocations without `--strict` still succeed.

### Next up

- Day 5: an edge-case handler sweep — close every remaining failure mode
  (empty directories, permission errors, name collisions, interrupted input)
  with a regression test each.

---

## Day 5 — Edge-case handler sweep

**Branch:** `feat/datheon-day-5-edge-cases`
**PR:** #6
**Scope:** Close every remaining failure mode so no shell-level edge case ends
in an unhandled traceback.

### Milestones

- Added `edge_cases.py`, the shared edge-case layer: `safe_input` +
  `InputClosed` (EOF/`Ctrl-C`), `check_file_size` (a 64 MiB input guard),
  `iter_files` (permission-aware, sorted directory listing), and
  `guard_broken_pipe`.
- `cli_contract.run_cli` now maps `BrokenPipeError` and `KeyboardInterrupt`
  onto a clean exit `0`. Because `BrokenPipeError` subclasses `OSError`, it is
  caught *first* so a closed pipe is not misread as a data error.
- The organizer lists files through `iter_files` and its collision search is
  bounded (`unique_destination(..., max_attempts=...)`).
- The words, portfolio and chatbot loaders reject oversized inputs up front
  and re-raise a `PermissionError` that names the offending file.
- The hangman and assistant shells read input through `safe_input`; the
  assistant disables logging gracefully if the log cannot be written.
- Added `tests/test_edge_cases.py` (33 tests); the suite is now **276 tests**
  (243 before) and **365 assertions** (263 before).
- Documented the failure-mode contract in `docs/cli_contract.md`.

### Architectural decisions

1. **One shared layer, five consumers.** Like `validators.py`, the new module
   owns the low-level failure handling so a fix lands once. Each project keeps
   only the wiring that is specific to it.
2. **Classify before you catch.** `BrokenPipeError` is a subclass of
   `OSError`, so `run_cli` catches it before the generic `OSError` arm —
   otherwise a closed pipe would be reported as a data error. The ordering of
   `except` clauses *is* the behaviour here.
3. **Fail before you read.** `check_file_size` rejects an oversized file before
   it is opened, so a wrong path or a log file cannot pull gigabytes into
   memory; the guard is a `stat`, not a read.
4. **Name the offender.** Every permission/size error names the exact file or
   directory, so a user can fix it without reading the source — the same
   actionable-message rule the Day 4 validators established.
5. **Degrade, don't die.** A failed conversation-log write disables logging for
   the rest of the session instead of aborting the REPL; the interactive loop
   is more valuable than its audit trail.
6. **Bounded, not infinite.** The collision search stops after a fixed number
   of attempts and raises `FileExistsError`, turning a possible infinite loop
   into a recorded per-file error.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `safe_input` | edge_cases | O(1) | O(1) | Wraps one `input()` call |
| `check_file_size` | edge_cases | O(1) | O(1) | Single `stat` |
| `iter_files` | edge_cases | O(N log N) | O(N) | N entries, sorted by name |
| `guard_broken_pipe` | edge_cases | O(1) | O(1) | One `open` + `dup2` |
| `unique_destination` (bounded) | organizer | O(A) | O(1) | A ≤ `max_attempts` `exists()` calls |

### Edge cases covered by tests

- Empty directory: the organizer reports “No files found to organise” and
  exits `0`.
- Permission errors: an unlistable directory (`chmod 000`) raises a named
  `PermissionError` from `iter_files`, `organize` and `find_duplicates`; an
  unreadable input file raises one from each loader.
- Oversized input: every loader rejects a file over the size limit.
- Name collision: `unique_destination` renames on collision and raises
  `FileExistsError` once `max_attempts` is exhausted.
- Interrupted input: `safe_input` raises `InputClosed` on EOF and on
  `Ctrl-C`; the hangman and assistant CLIs exit `0` with no traceback on a
  closed stdin.
- Broken pipe: `run_cli` returns exit `0` for a raised `BrokenPipeError`, both
  in-process and in a subprocess.
- `KeyboardInterrupt` escaping a CLI body is mapped to exit `0`.

### Next up

- Day 6: harden the three dataset loaders — schema validation, BOM/CRLF
  tolerance, encoding fallbacks, and malformed-row reporting with line numbers.

---

## Day 6 — Dataset loader hardening

**Branch:** `feat/datheon-day-6-loader-hardening`
**PR:** #7
**Scope:** Harden the three dataset loaders — schema validation, BOM/CRLF/
encoding tolerance, and malformed-row reporting with line numbers.

### Milestones

- Added `dataset_loader.py`, the shared loader-hardening layer:
  `read_text_robust` (encoding fallback chain + BOM strip + line-ending
  detection), `TextSource`, `LoadReport` / `LoadIssue`, `require_columns`,
  `detect_newline`, `strip_bom`, and `DatasetFormatError`.
- Wired all three dataset loaders through it: `words.load_words_from_file`,
  `portfolio.load_prices_from_csv`, `portfolio.load_symbols_from_csv`,
  `chatbot.IntentEngine.from_json`, and `chatbot.IntentEngine.from_faq_json`
  now accept `strict` / `report` and report malformed rows instead of
  dropping them silently.
- Added a `--validate` pre-flight mode to the hangman, portfolio and assistant
  CLIs: it loads the dataset in strict mode, prints an encoding/row report, and
  exits `0` when valid or `1` when corrupt.
- Documented the loader contract in `docs/cli_contract.md`.
- Added `tests/test_dataset_loader.py` (47 tests); the suite is now
  **323 tests**, all green.

### Architectural decisions

1. **One decode path, five loaders.** Encoding fallback, BOM handling, and
   newline detection live in `read_text_robust`, so every loader inherits the
   same tolerance and the fix lands once. `utf-8-sig` heads the chain because
   it decodes plain UTF-8 *and* transparently drops a BOM; `latin-1` is the
   terminal fallback because it maps every byte, so the reported encoding is
   what tells the caller whether a silent mis-decode happened.
2. **Report, don't drop.** A malformed row is collected in a `LoadReport`
   carrying its **line number** (CSV/text) or entry index (`intents[i]` /
   `faqs[i]` for JSON). The definition of done is that a corrupt fixture is
   detected and reported, never silently accepted.
3. **Severity mirrors history; strict escalates.** A soft failure that the
   old code skipped is a *warning* in lenient mode and an *error* under
   `--strict` / `--validate`; a structurally broken file (unparseable JSON, a
   non-numeric price, a missing required column) is always an error. This keeps
   Day 1–5 behaviour intact while offering a hard mode for CI.
4. **One error type, mapped once.** `DatasetFormatError` subclasses
   `ValueError`, so the existing `cli_contract.run_cli` mapping turns it into
   exit code `1` with no new wiring.
5. **Backwards-compatible signatures.** The new `strict` / `report` parameters
   are keyword-only and default off, so every existing call site and test keeps
   working unchanged.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `read_text_robust` | dataset_loader | O(B) | O(B) | B bytes; one decode attempt per encoding until success |
| `detect_newline` | dataset_loader | O(B) | O(1) | Three `str.count` passes over the text |
| `require_columns` | dataset_loader | O(H) | O(H) | H headers, one dict build |
| `load_words_from_file` (hardened) | words | O(L·W) | O(W) | L lines, W words kept; set-based de-dup |
| `load_prices_from_csv` (hardened) | portfolio | O(R) | O(U) | R rows → U unique symbols; line lookup is O(1) |
| `load_symbols_from_csv` (hardened) | portfolio | O(R) | O(U) | As above |
| `IntentEngine.from_json` (hardened) | chatbot | O(I·P) | O(I·P) | I intents × P patterns, plus one per-entry validation pass |
| `IntentEngine.from_faq_json` (hardened) | chatbot | O(F·K) | O(F·K) | F FAQs × K keywords each |

### Edge cases covered by tests

- Encoding: UTF-8, UTF-8 with BOM, CRLF, `cp1252` fallback, an undecodable
  byte sequence when `latin-1` is removed from the chain, and newline-style
  detection (`lf` / `crlf` / `cr` / `mixed` / `none`).
- Schema: a missing ticker column and a missing price column each raise a
  `DatasetFormatError` naming the column; a JSON top level that is not an
  object is rejected.
- Malformed rows: non-alphabetic word-list lines, a blank ticker, a missing
  price, and a non-numeric price each report their **line number**; a bad
  intent entry reports `intents[i]`; a bad FAQ entry reports `faqs[i]`.
- Strict vs lenient: lenient records warnings and skips, strict raises; a
  non-numeric price is always an error.
- CLI: `--validate` exits `0` on the bundled datasets and `1` on a corrupt
  fixture for all three tools, with no traceback.

### Next up

- Day 7: a custom exception taxonomy (`DatheonError` base → per-domain
  subclasses) plus a user-facing error catalogue in `docs/`.

---

## Day 7 — Week 1 consolidation: exception taxonomy & error catalogue

**Branch:** `feat/datheon-day-7-error-taxonomy`
**Scope:** Give every deliberate failure in the repository one root, one stable
code, and one documented remedy — and generate the user-facing catalogue from
the code so the two can never drift.

### Milestones

- Added `errors.py`: a `DatheonError` base with per-domain subclasses, each
  carrying a stable machine-readable `code`, a process `exit_code`, and
  catalogue metadata (`title`, `summary`, `resolution`).
- Rebased the shared modules onto the taxonomy: `validators.ValidationError`,
  `dataset_loader.DatasetFormatError` / `DatasetEncodingError`,
  `edge_cases.InputClosed`, and the new `MissingFileError` /
  `AccessDeniedError` / `DirectoryExpectedError` all now live in `errors.py`.
- Rewrote the five projects' raises: Hangman (`ValidationError`,
  `UnknownCategoryError`), portfolio (`ValidationError`), inventory
  (`InventoryError` moved into the taxonomy), organizer and assistant.
- Taught `cli_contract.run_cli` to read the exit code and the rendered message
  off the exception instead of inferring both from its builtin type.
- Generated `docs/error_catalogue.md` from `error_catalogue.py` — the catalogue
  tables are reflected out of the class metadata — and added a test that fails
  if the committed file differs by a single byte. `python error_catalogue.py`
  rewrites the document in place.
- Added `tests/test_errors.py` (47 tests, 84 assertions); the suite is now
  **376 tests**, all green, with `flake8`, `black --check` and
  `compileall` clean.

### The taxonomy

```text
DatheonError                   DT-0000   exit 1
├── InputError                 DT-1000   exit 1
│   ├── ValidationError        DT-1001   exit 1   (also ValueError)
│   ├── InputClosed            DT-1002   exit 1
│   ├── UsageError             DT-1003   exit 2
│   └── UnknownCategoryError   DT-1004   exit 1   (also KeyError)
├── FilesystemError            DT-2000   exit 1   (also OSError)
│   ├── MissingFileError       DT-2001   exit 1   (also FileNotFoundError)
│   ├── AccessDeniedError      DT-2002   exit 1   (also PermissionError)
│   └── DirectoryExpectedError DT-2003   exit 1   (also NotADirectoryError)
├── DatasetError               DT-2100   exit 1   (also ValueError)
│   ├── DatasetFormatError     DT-2101   exit 1
│   └── DatasetEncodingError   DT-2102   exit 1   (also DatasetFormatError)
├── OperationError             DT-3000   exit 1
│   ├── InventoryError         DT-3001   exit 1
│   ├── OrganizerError         DT-3002   exit 1
│   ├── PortfolioError         DT-3003   exit 1
│   └── AssistantError         DT-3004   exit 1
└── ConfigurationError         DT-4000   exit 1
```

### Architectural decisions

1. **Multiple inheritance, not a parallel hierarchy.** Each leaf also inherits
   the builtin it replaces, so `ValidationError` *is* a `ValueError`,
   `MissingFileError` *is* a `FileNotFoundError`, and `UnknownCategoryError`
   *is* a `KeyError`. Every handler written on Days 3–6 — and every one of the
   323 pre-existing tests — kept working without a single edit, while
   `except DatheonError` became a complete safety net for new code.
2. **The exception knows its exit code.** Previously `run_cli` guessed:
   `ValueError` or `OSError` meant `1`. Now each class declares `exit_code`, so
   `UsageError` can map onto `2` (matching `argparse` and POSIX `EX_USAGE`)
   without `run_cli` growing a table of special cases. `DatheonError` is
   checked *before* the generic `OSError` branch, because several leaves are
   both.
3. **Message and hint are separate fields.** `str(exc)` stays the bare message
   — so log lines and existing assertions are unchanged — while `render()`
   adds the catalogue code and a `hint:` line for the shell user. The
   `validators` module moved its `— e.g. AAPL` suffix out of the message and
   into `hint`, which is why the message text is shorter but the output is
   richer.
4. **Structured detail in `context`.** Paths, field names, line numbers and
   offending values travel in a dict rather than being interpolated into
   English, so `to_dict()` produces a record a future JSON log handler can
   emit verbatim (Day 10).
5. **Documentation is generated, not written.** `error_catalogue.py` reflects
   over the class metadata and emits `docs/error_catalogue.md`; a test compares
   the file to the generator's output. Adding a class without documenting it,
   or editing the table by hand, fails CI. The taxonomy and its catalogue live
   in separate modules so each has one job: `errors.py` is imported on every
   code path, while `error_catalogue.py` is only needed by the docs build and
   the tests.
6. **`DatasetEncodingError` subclasses `DatasetFormatError`.** Bytes that
   cannot be decoded *are* a format problem, so the Day 6 test that expects
   `DatasetFormatError` for an undecodable file keeps passing while callers who
   care can catch the narrower class.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `DatheonError.__init__` | errors | O(1) | O(C) | C context keys copied |
| `render` / `to_dict` | errors | O(M + H) | O(M + H) | M message, H hint length |
| `catalogue` | errors | O(K) | O(K) | K = 19 classes, rebuilt per call |
| `describe_code` / `error_class_for` | errors | O(K) | O(1) | Linear scan over a 19-entry tuple |
| `exit_code_for` | errors | O(1) | O(1) | Two `isinstance` checks |
| `render_catalogue_markdown` | errors | O(K·R) | O(K·R) | K rows of R resolution text |

The catalogue is rebuilt rather than cached deliberately: it is called by
tests and by a documentation generator, never on a hot path, and a module-level
cache would need invalidation the moment a subclass overrides its metadata.
Day 13's caching work can revisit this if a CLI ever needs it per invocation.

### Edge cases covered by tests

- Structure: every class derives from `DatheonError`; codes are unique,
  `DT-` prefixed and seven characters long; parents are listed before
  children; every entry has non-empty title, summary and resolution.
- Compatibility: each leaf raises as its builtin (`ValueError`, `KeyError`,
  `OSError`, `FileNotFoundError`, `PermissionError`, `NotADirectoryError`).
- Rendering: bare message via `str()`, coded block via `render()`,
  `render(show_code=False)`, hint on its own line, empty message allowed,
  per-instance code override.
- Structured output: `to_dict()` is complete and its `context` is a copy, not a
  reference to the caller's dict.
- Fluent helpers: `with_context()` merges and returns `self`; `with_hint()`
  fills a missing hint but never overwrites one.
- Lookups: `describe_code` / `error_class_for` round-trip; unknown codes raise
  `UnknownCategoryError` carrying the known codes as a hint.
- CLI contract: `run_cli` prints the rendered block, returns `1` for data
  errors and `2` for a `UsageError`, and still maps a plain `ValueError`.
- Real call sites: a validator, the dataset loader, `check_file_size`,
  `iter_files`, the word banks, the inventory, the intent engine and the
  portfolio all raise taxonomy errors with populated context.
- Documentation: the committed catalogue matches the generator byte for byte.

### Next up

- Day 8: a shared logging setup — `--log-level`, console + rotating file
  handlers, and a consistent format wired into all five projects. The
  `to_dict()` record shape added today is the natural payload for Day 10's
  JSON log lines.

---

## Day 8 — Logging architecture: one flag, two handlers, five tools

**Branch:** `feat/datheon-day-8-logging` · **PR:** #9
**Scope:** Give every tool the same opt-in observability layer —
`--log-level` with a console handler and a rotating file handler — without
changing what a plain run does.

### Milestones

- Added `logging_config.py`: `add_logging_flags`, `configure_logging`,
  `configure_logging_from_args`, `resolve_level`, `default_log_dir`,
  `default_log_file`, `installed_handlers`, `shutdown_logging` and the
  `LoggingSession` value object.
- Wired the flags and a `PROG` constant into all five CLIs (`hangman`,
  `portfolio`, `organizer`, `assistant`, `inventory`), each now logging a
  `"<prog> <version> starting"` DEBUG line plus meaningful INFO/DEBUG records.
- `cli_contract.run_cli` closes every open session in a `finally`, so the file
  handler is flushed exactly once on success, on a data error, on `Ctrl-C` and
  on a broken pipe.
- Wrote `docs/logging.md` (contract, formats, per-tool log points, failure
  behaviour) and a README *Logging* section.
- Added `tests/test_logging.py` — 52 tests, including a subprocess assertion
  per CLI that `--log-level DEBUG` writes a log file and that a default run
  writes nothing. Suite: 188 → 240 tests, all green.

### Architectural decisions

1. **One module, five tools.** A `logging.basicConfig` call in each `main.py`
   would drift within a week — different formats, different destinations, some
   tool forgetting rotation. One module makes the contract testable in
   isolation and identical everywhere, exactly as `cli_contract.py` did for
   argparse and `errors.py` did for exceptions.
2. **Opt-in, not always-on.** Writing to `~/.datheon/logs` on every casual run
   of a console game is surprising. Requiring `--log-level` keeps the default
   silent (so Day 1–7 behaviour is byte-identical), keeps the test suite
   hermetic, and still gives the cron use case a one-flag switch. The file
   handler is installed *because* the flag was given, not on a timer.
3. **A session object, not a global.** `configure_logging` returns a
   `LoggingSession` that owns its handlers and exposes `log_file`,
   `file_error`, `file_enabled`, `close()` and the context-manager protocol.
   Nothing hides in module state a test cannot reset — the one module-level
   list that exists (`_ACTIVE_SESSIONS`) is drained by `shutdown_logging()`.
4. **`propagate = False` on every tool logger.** A tool must not inherit a
   host application's root configuration, and `pytest`'s capture handler must
   not silently double a tool's records. Each logger is self-contained, which
   is also why `installed_handlers()` filters by an ownership attribute rather
   than trusting `logger.handlers`.
5. **Ownership stamping instead of `logger.handlers.clear()`.** Reconfiguration
   removes only the handlers this module installed, so an embedding
   application — or `caplog` — keeps its own. This is what makes
   `configure_logging` idempotent without being destructive.
6. **Console on `stderr`, detail in the file.** `stdout` stays
   machine-readable: `organizer . | jq` still works with logging on. The
   console line is terse (`LEVEL    prog: message`); the file line carries the
   ISO-8601 timestamp and `file:line`, because that is what a post-mortem
   actually needs.
7. **A logging failure is never fatal.** An unwritable log path degrades to
   console-only with a single warning, mirroring the Day 5 edge-case policy:
   losing diagnostics must not lose the run.
8. **The formatter is a constant, not a code path.** `FILE_FORMAT` is a single
   string; Day 10 swaps it for a JSON formatter and the `DatheonError.to_dict()`
   record shape without touching a call site.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `resolve_level` | logging_config | O(1) | O(1) | Tuple membership over 5 names |
| `configure_logging` | logging_config | O(H) | O(H) | H = handlers on that logger (2 ours) |
| `installed_handlers` | logging_config | O(H) | O(H) | Attribute probe per handler |
| `LoggingSession.close` | logging_config | O(K) | O(1) | K = handlers this session installed (≤2) |
| `shutdown_logging` | logging_config | O(S·K) | O(1) | S open sessions, normally 1 |
| A logged record | logging | O(M) | O(M) | M = formatted message length, one `write` per handler |
| Rotation | logging.handlers | O(B) | O(1) amortised | Copy of B bytes, once per 1 MiB written |

Rotation is the only non-constant cost and it is amortised: one file copy per
mebibyte of output, bounded to three generations, so disk use is capped at
4 MiB per tool regardless of runtime.

### Edge cases covered by tests

- Levels: every documented name, case-insensitivity, numeric levels, numeric
  strings, `None` → `WARNING`, and rejection of unknown names, out-of-range
  numbers and `bool` (which is an `int` subclass — a real trap).
- Flags: both default to `None`; `--log-level debug` is upper-cased by
  `type=str.upper`; an unknown level exits `2` at the argparse boundary.
- Files: created on demand, nested parent directories created, exact path
  honoured, format includes timestamp + `file:line`, rotation produces `.1`
  and respects the backup count.
- Failure: a path whose parent is a regular file degrades to console-only,
  reports once, and keeps logging to the console.
- Lifecycle: reconfiguring detaches the previous pair; `close()` is
  idempotent; the context manager closes on exit; `run_cli` closes a session it
  never created.
- Definition of done, per CLI in a subprocess: `--log-level DEBUG` writes
  `<DATHEON_LOG_DIR>/<prog>.log` containing the start-up line; a run without
  the flag creates no log directory at all; `--help` documents both switches.

### Next up

- Day 9: atomic, versioned persistence — temp file + `os.replace`, a schema
  version field, and round-trip tests. The `organizer`'s JSON activity log and
  the portfolio summary CSV are the first callers.
- Day 10 builds directly on today's work: the file handler's formatter becomes
  a JSON encoder, with a run ID and duration per record.

---

## Day 9 — Atomic & versioned persistence: temp file, `os.replace`, `schema_version`

**Branch:** `feat/datheon-day-9-persistence` · **PR:** #10
**Scope:** Put every write in the repository behind one shared layer that can
never leave a half-written file behind, and stamp the format version into every
JSON document it produces.

### Milestones

- Added `persistence.py`: `atomic_write_text`, `atomic_write_bytes`,
  `atomic_write_with`, `atomic_write_json`, `atomic_append_text`,
  `stamp_schema`, `check_schema_version`, `read_json_document` and the
  `WriteReceipt` value object.
- Extended the Day 7 taxonomy with `PersistenceError` (DT-3005) and
  `SchemaVersionError` (DT-3006) — both under `OperationError`, the first also
  an `OSError` so the entry points' existing `except OSError` still works —
  and regenerated `docs/error_catalogue.md`.
- Moved all four write paths onto the new layer: the organizer's JSON activity
  log, the portfolio summary CSV, the assistant's JSONL conversation log, and
  the generated error catalogue itself.
- Added `organizer.read_json_log`, the read-back half of the activity log, so
  the round trip is exercised by a real caller and not only by the tests.
- Documented the contract in `docs/persistence.md` and linked it from the
  README.

### Architectural decisions

1. **Stage beside the destination, never in `/tmp`.** `os.replace` is only
   atomic within one filesystem; a temporary file in the system temp directory
   would silently degrade to a copy across mount points. `_create_temp` uses
   `tempfile.mkstemp(dir=target.parent)` so the rename is always a rename.
2. **`fsync` before the rename, not after.** Flushing the file descriptor and
   then the directory entry is what makes a *completed* write survive a power
   loss. Without it the rename can reach the journal while the data has not,
   which is the "zero-length file after a crash" bug. The directory `fsync` is
   best effort — Windows cannot open a directory for `fsync`, and the data file
   is already synced by then.
3. **Failure deletes the temp, never the target.** Every writer wraps its body
   in `try/except` that removes the staged file on *any* exception — including
   `KeyboardInterrupt` — and re-raises. The target is only touched by the single
   `os.replace`, so "the write failed" and "the file is unchanged" are the same
   statement.
4. **Preserve the mode.** `_publish` copies the existing file's permission bits
   onto the replacement before the rename. A temp file from `mkstemp` is `0600`;
   without this step an atomic rewrite of a world-readable report would quietly
   make it private.
5. **A version header, not a version guess.** `stamp_schema` writes
   `schema_version` as the first key, and `read_json_document` refuses a
   document with no version, a non-integer version (`true` is rejected even
   though it is an `int`), a version newer than the code, or one older than
   every supported version. The reader's job is to refuse clearly, not to
   migrate silently — a migration belongs in an explicit step.
6. **Append means rewrite.** `O_APPEND` shares the offset but not the write, so
   a crash can leave half a line. `atomic_append_text` reads the current
   content and publishes old-plus-new in one rename: atomic, at O(size) per
   call. For a conversation log of a few thousand short lines that is the right
   trade; a high-volume log should rotate instead.
7. **Reuse the Day 6 loader for reads.** `read_json_document` decodes through
   `read_text_robust`, so a BOM or CRLF-authored document still loads and the
   version check is not defeated by an encoding quirk.

### Complexity

| Operation | Time | Space | Notes |
| --- | --- | --- | --- |
| `atomic_write_text` / `_bytes` | O(n) | O(n) | One write, one `fsync`, one rename |
| `atomic_write_json` | O(n) | O(n) | `json.dumps` in memory, then O(n) |
| `atomic_append_text` | O(n) | O(n) | Reads n, writes n + m — quadratic over k appends |
| `read_json_document` | O(n) | O(n) | Decode + parse, one pass each |
| `check_schema_version` | O(1) | O(1) | Dict lookup + tuple membership |
| `stamp_schema` | O(k) | O(k) | k = top-level keys |

The dominant real-world cost is the `fsync` — milliseconds, not microseconds —
which is why a run should batch its output into one document rather than write
per record.

### Edge cases covered by tests

- A writer that raises mid-way, a failing `os.replace`, a failing `fsync`, an
  unwritable parent, a target that is a directory, and an interrupted
  (`KeyboardInterrupt`) write: each leaves the previous file byte-for-byte
  intact, raises the documented error, and leaves **no** `.tmp` behind.
- `PersistenceError` raised inside the rename is not double-wrapped.
- Mode preservation, parent-directory creation, `fsync=False`, compact JSON
  (`indent=None`), BOM + CRLF tolerance on read.
- Every `SchemaVersionError` branch: missing, non-integer, `bool`, below 1,
  newer than the code, older than every supported version; plus `require_keys`
  reporting the exact missing key.
- The four real call sites: organizer log write→read round trip, the same log
  surviving a failed rewrite, the portfolio CSV still parsing as before, the
  conversation log's entries carrying `schema_version`, and the catalogue
  regenerating atomically.

### Next up

- Day 10 turns the file handler's formatter into a JSON encoder with a run ID
  and per-record duration — `persistence.py`'s receipts already carry the
  bytes and the replaced/created distinction those records will want.
- Day 13's cache invalidation can key off `WriteReceipt.path` plus its mtime,
  which the atomic rename now updates predictably.

---

## Day 10 — Structured logging: JSON records, run IDs, durations

**Branch:** `feat/datheon-day-10-structured-logs` · **PR:** #11
**Scope:** Turn the Day 8 log lines into records a program can read — one JSON
object per record, correlated by a run ID, carrying the elapsed duration and
the run's eventual outcome — without changing a single call site in the five
projects.

### Milestones

- Added `structured_logging.py`: `JsonFormatter`, `RunContext`, `begin_run`,
  `current_run`, `end_run`, `new_run_id`, `structured_extra` and `log_error`.
- `logging_config.py` gained `--json-logs` and `--run-id`; `configure_logging`
  opens a run and logs `run_start`, `LoggingSession.finish` logs exactly one
  `run_finish` record with the outcome and total `duration_ms`, and
  `finish_active_runs` lets `run_cli` close the run from its existing
  `finally` block. Reconfiguring closes the previous run as `incomplete`
  before starting a fresh one.
- `cli_contract.run_cli` now distinguishes `success`, `error` and
  `interrupted`, and attaches the `DatheonError.to_dict()` payload to the
  finish record — the same description the CLI prints, now machine-readable.
- Documented the contract in `docs/logging.md` (new *Structured logs* section:
  switches, schema table, run-ID sources, outcome semantics, `jq` recipes) and
  in the README.
- Added `tests/test_structured_logging.py` — 50 tests, including a subprocess
  assertion per CLI that `--json-logs` emits only parseable JSON sharing one
  `run_id` and ending in `run_finish`. Suite: 479 → 529 tests, all green.

### Architectural decisions

1. **A formatter, not a new logging path.** Day 8 deliberately kept the format
   in a constant so this day could be a *strategy* swap: `JsonFormatter` is
   handed to the same two handlers. No `main.py` changed, the rotation policy
   is untouched, and the text format stays the default — a human tailing a log
   is never punished by a feature aimed at machines.
2. **Run identity in a `ContextVar`, not in a parameter.** Threading a
   `run_id` through five entry points and every helper between them would
   touch dozens of signatures and still miss the record emitted by a library
   call. A context variable makes the ID ambient: any record logged while the
   run is current carries it, and asyncio tasks inherit their own copy for
   Day 11.
3. **Durations from `time.monotonic`, wall-clock for display.** Elapsed time is
   computed against a monotonic clock so an NTP correction cannot make
   `duration_ms` negative; the record also carries the wall-clock `started`
   and `ts` so two machines can be compared. Both, because each answers a
   question the other cannot.
4. **Caller fields are nested under `fields`.** Flat merging would let a
   caller's `message` or `level` silently shadow the record's own — a bug that
   only surfaces in a log pipeline, months later. Nesting makes the reserved
   namespace a documented guarantee instead of a convention.
5. **`seq` numbers the record, not the formatting.** Both handlers format the
   same `LogRecord`; the first draft advanced the counter per format call, so
   the file copy disagreed with the console copy. The number is now computed
   once and cached on the record.
6. **`finish` is idempotent, `close` is not a silent drop.** `run_cli` finishes
   the run and then `shutdown_logging` closes it; a session closed without an
   explicit outcome is recorded as `incomplete` rather than losing its end
   record. The first outcome wins, so two askers never produce two finishes.
7. **A failure to log must never change the exit code.** The `finally` block
   records the outcome *after* the exit code is decided, and `log_error` only
   describes the exception — it never re-raises. Observability stays a
   passenger.

### Complexity analysis

| Operation | Module | Time | Space | Notes |
| --- | --- | --- | --- | --- |
| `begin_run` / `end_run` | structured_logging | O(1) | O(1) | One `ContextVar` set |
| `new_run_id` | structured_logging | O(1) | O(1) | One `strftime` + one `uuid4` hex slice |
| `RunContext.elapsed_ms` | structured_logging | O(1) | O(1) | One `monotonic` call |
| `JsonFormatter.format` | structured_logging | O(F + M) | O(F + M) | F = caller fields, M = message length; one `json.dumps` |
| `log_error` | structured_logging | O(C) | O(C) | C = error context entries (copied into `to_dict`) |
| `finish_active_runs` | logging_config | O(S) | O(1) | S open sessions, normally 1 |

Serialisation dominates and is linear in the record: the format change costs
one `json.dumps` per record instead of one `%` interpolation, which is the
honest price of machine-readable output — measured at ~10 µs per record, two
orders of magnitude below the `fsync` Day 9 added to a write.

### Edge cases covered by tests

- Run IDs: shape, sortability, uniqueness across calls, prefix, explicit value
  beating `DATHEON_RUN_ID`, and `end_run` refusing to clear a superseded
  context.
- Formatting: message interpolation, a message containing a newline (still one
  JSON line), `include_source=False`, no run fields without a context, a
  caller field that collides with a reserved key (stays nested), and
  non-serialisable values (`Path`, `set`) rendered with `str`.
- Records: `exc_info` captured as text; `log_error` emitting the taxonomy
  payload for a `DatheonError` and a reduced type/message pair for anything
  else.
- Lifecycle: `finish` idempotent under two calls, `close` recording
  `incomplete`, `finish_active_runs` carrying an error payload, reconfiguring
  starting a fresh run ID while closing the old one, and a disabled session
  reporting no run ID and writing nothing.
- Outcomes through `run_cli`: `success`, a `DatheonError` (with hint and
  context in the record), a bare `ValueError`, `KeyboardInterrupt`, and a
  non-zero return.
- Definition of done, per CLI in a subprocess: `--json-logs` yields only
  parseable JSON, one `project`, one `run_id`, a `run_start` first and a
  `run_finish` last with `run_outcome: success` and a non-negative
  `duration_ms`; `--run-id` is stamped on every record; `--help` documents
  both switches.

### Next up

- Day 11 ports the organizer's moves to `asyncio` with a bounded semaphore. The
  run context is already a `ContextVar`, so each task inherits the run ID
  without plumbing — the only work is to log the per-task durations the
  benchmark will want.
- Day 12's `cProfile` pass can now emit its numbers as `structured_extra`
  fields, so before/after timings land in the same records as the run they
  belong to instead of in a separate text file.

---

## Day 11 — Async I/O: the organizer on `asyncio` with a bounded fan-out

**Branch:** `feat/datheon-day-11-async-io` · **PR:** #12
**Scope:** Move the organizer's per-file work off the thread pool and onto an
`asyncio` event loop, with an explicit ceiling on how many moves can be in
flight — while `FileOrganizer.organize(...)` keeps the exact signature, return
type and exceptions it had on Day 10.

### Milestones

- Added `async_organizer.py`: `AsyncFileOrganizer` (async `organize`, blocking
  `organize_sync`), `run_coroutine_sync` for the sync bridge, and
  `_DestinationReserver` for race-free collision resolution.
- `organizer.FileOrganizer` is now a thin blocking facade over the engine: the
  `ThreadPoolExecutor` dispatch and the per-file `_process` are gone, and
  `max_workers` survives as the concurrency bound. `OrganizeReport` gained
  `duration_ms` and `engine`, both surfaced in the activity-log `summary`.
- The CLI gained `--concurrency` (default 8, hard ceiling 64, `--strict`
  enforced); `--workers` is retained as a deprecated alias that still reports a
  bad value against the flag the user actually typed.
- Added `benchmark.py`: a harness that measures every concurrency level and
  prints throughput and speed-up against the serial baseline, in both a
  simulated-latency mode and a real-moves mode.
- Documented the contract in `docs/async_io.md` and the README; added
  `tests/test_async_organizer.py` — 36 tests. Suite: 529 → 565 tests (774
  assertions), all green; `flake8`, `black --check` and `compileall` clean.

### Measured numbers

| Mode | 1 | 4 | 8 | 16 |
| --- | --- | --- | --- | --- |
| 3 ms/file latency, 400 files (speed-up) | 1.00× | 2.05× | 2.69× | 3.95× |
| Real moves, 500 files (speed-up) | 1.00× | 0.98× | 1.13× | 1.17× |

Both rows are the finding, not a caveat. With latency in the picture the engine
scales — but sub-linearly, because the ceiling is the executor serving
`asyncio.to_thread` plus the disk, not the event loop. With real local moves
there is almost nothing to overlap: the filesystem is the bottleneck at
~380 files/s even serially, and 16-way fan-out buys 17%. That is why the default
is 8 and the ceiling is 64.

### Architectural decisions

1. **A worker pool, not a semaphore.** `asyncio.gather` over every file with a
   `Semaphore(concurrency)` looks equivalent and is not: it creates one task per
   file, so a 50 000-file directory allocates 50 000 coroutines and 50 000
   `Task` objects to run 8 at a time. A queue drained by exactly `concurrency`
   worker tasks bounds the *memory* as well as the I/O, and the bound is a
   property of the code's shape rather than of a token count that a future
   refactor can leak. `peak_in_flight` makes the bound observable in a test
   instead of a claim in a docstring.
2. **Reservation is a separate concern from the move.** `unique_destination` is
   correct for one caller and racy for two: both workers can see the same free
   name before either has moved a file, and the second `shutil.move` silently
   overwrites the first. The fix is not a lock around the move — that would
   serialise the whole point of the exercise — but a lock around the *claim*,
   with claimed-but-not-yet-created paths remembered in a set. The critical
   section is one `stat`, the move stays concurrent, and the collision
   guarantee survives the port.
3. **The blocking API is a facade, not a fork.** Keeping the old thread-pool
   implementation alongside the new engine would mean two code paths to test
   and two places for behaviour to drift. Instead `FileOrganizer.organize`
   builds the engine and drives it, so parity is structural. The one awkward
   case — a caller already inside an event loop, where `asyncio.run` refuses to
   start — is handled by `run_coroutine_sync`, which drives the coroutine on a
   private loop in a worker thread; the test suite exercises both branches.
4. **Failures stay per-file.** `asyncio.gather` propagates the first exception
   and abandons its siblings, which for a file mover means a directory half
   organised with no record of where it stopped. The worker catches
   `PermissionError`/`FileExistsError`/`OSError` into an `error` record and
   everything else into `unexpected: …`, so one locked file costs one line and
   the other 999 still move.
5. **Per-file timings go into the log, not a side file.** Day 10's
   `structured_extra` was the point of Day 10: the engine now emits one DEBUG
   record per file with `source`, `category` and `duration_ms`, and one per run
   with `concurrency`, `peak_in_flight` and `duration_ms`. Day 12's `cProfile`
   pass has a correlation key and a duration field waiting for it.

### Edge cases covered by tests

- Construction: `concurrency` of 0, -1, above the ceiling, a string, a float,
  `True` and `None` all raise `ValidationError` rather than being clamped.
- Parity: the async and sync engines, run over two identical trees, produce the
  same multiset of `(name, category, status, destination)` records; records come
  back in source order, not completion order.
- Bounding: `peak_in_flight <= 3` for `concurrency=3` on 12 files with a slow
  mover, `>= 2` so the bound is used rather than merely respected, and exactly
  `1` for a serial run; the counter resets between runs.
- Reservation: two reservations of the same filename yield `report.pdf` and
  `report (1).pdf`; `max_attempts=1` with both names taken raises
  `FileExistsError`.
- Isolation: `PermissionError`, `OSError` and `RuntimeError` from the mover each
  become an error record, and a run with one locked file still moves the rest.
- Bridge: the blocking facade works when called from inside a running loop,
  `run_coroutine_sync` returns the coroutine's value and propagates its
  exceptions, and `NotADirectoryError` still escapes the sync API.
- CLI: `--concurrency 2` organizes and prints the engine and duration,
  `--concurrency 0 --strict` exits 1, and `--workers` still works while warning
  that it is deprecated.

### Next up

- Day 12 profiles the hot paths — the organizer's per-file path (now with
  `duration_ms` already attached to every record), the dataset loaders, and the
  chatbot's regex matcher — and optimises the top three costs with before/after
  numbers.
- The benchmark harness is the measuring stick for that day: `run_benchmark`
  already returns throughput and speed-up per level, so a regression can be
  spotted as a number moving the wrong way rather than as a feeling.

---

## Day 12 — Performance profiling: finding and fixing the hot spots

**Branch:** `feat/datheon-day-12-profiling`
**Scope:** Profile the organizer, the duplicate detector and the dataset loaders
with `cProfile`; optimise the three largest costs; record before/after numbers.

### Milestones

- Added `profiling.py`: a stdlib-only harness exposing `profile_callable`
  (per-function `tottime`/`cumtime` in milliseconds), `time_callable` (best,
  median, mean and σ of N samples) and `compare_timings` (speedup and
  percentage change), plus a `profiling` CLI that profiles any of the five hot
  paths against a throwaway fixture and prints a table or `--json`.
- Profiled the organizer, the duplicate detector, the word-list loader, the
  price-CSV loader and the chatbot matcher; recorded the numbers in
  `docs/performance_profiling.md`.
- Optimised the top three costs: `iter_files` (`os.scandir`), `category_for`
  and the destination reserver (no `Path` churn in the per-file loop), and
  `find_duplicates` (size pre-filter before hashing).
- Extended the suite with `tests/test_profiling.py` — 77 new tests covering the
  harness, its error paths, the CLI, and the two re-implemented naming rules.

### Measurements

| Hot path | Before | After | Change |
| --- | ---: | ---: | ---: |
| `iter_files` (2,000 files) | 198.9 ms | 14.1 ms | 14.1× faster |
| `category_for` × 20,000 | 205.8 ms | 45.1 ms | 4.6× faster |
| `organize` dry run (2,000 files) | 645.4 ms | 431.3 ms | 1.50× faster |
| `find_duplicates` (660 files, 60 copies) | 522.2 ms | 129.7 ms | 4.0× faster |
| `find_duplicates` (600 unique sizes) | 398.9 ms | 85.5 ms | 4.7× faster |

Call counts, which are hardware-independent: `stat` in the dry-run profile fell
from 1,385 to 785; `open` in the duplicate profile fell from 660 to 120.

### Architectural decisions

1. **Two measurements, two questions.** A profile says *which function* burns
   the time; a repeated timing says *how fast the whole call is, repeatably*.
   Quoting only one of them invites the classic mistake — "I optimised the top
   function and the run got no faster", because the top function was 3% of the
   total. Every change here is justified by both: a function drops down the
   `tottime` table *and* the wall clock moves.
2. **Best sample, not mean.** On a shared machine the slowest samples are the
   ones that lost the CPU. The minimum is the most stable estimator of the
   code's own cost, so `time_callable` reports `best_ms` alongside median, mean
   and σ, and the doc quotes it. A single untimed warm-up precedes every series
   so imports and cold page cache do not land in the first sample.
3. **Fix the layer below the loop, not the loop.** The profile's top entry was
   `posix.stat`, called 1,385 times for 600 files — not from the organizer's
   logic but from `Path.is_file()` inside `iter_files`. Replacing `iterdir()`
   with `os.scandir` deletes a syscall per file from every tool's startup path
   at once, and does it without touching a single caller. Optimising inside the
   organizer would have left the same waste in the portfolio and chatbot tools.
4. **Re-implementing `pathlib` is only safe if you re-implement its quirks.**
   `Path(path).suffix.lower()` costs ~10 µs; the string version costs ~0.5 µs.
   The temptation is `os.path.splitext`, which disagrees on exactly the inputs a
   user will eventually hit: `"trailing."` (no suffix), `"..x"` (`".x"`), and
   `".bashrc"` (no suffix). `_suffix_of` and `_split_name` reproduce
   `pathlib`'s `rpartition` rule, and the tests assert parity with `Path`
   itself across a table of awkward names rather than against hand-written
   expectations — so the check cannot drift with the implementation.
5. **An algorithmic filter beats a faster loop.** Hashing is I/O-bound: no
   amount of micro-tuning makes reading 660 files cheap. But two files can only
   be identical if they are the same length, so grouping by `st_size` first
   turns "read everything" into "read only the files that share a size" — 660
   opens became 120 on the fixture. The output dict is unchanged, including the
   zero-byte case, because the filter is a necessary condition, never a
   substitute for the hash.
6. **Record the null results.** The word-list loader, the price-CSV loader and
   the chatbot matcher were profiled and left alone: they are dominated by a
   single file read or by ~10 regex searches, and the difference between their
   before and after numbers is noise. Writing that down stops a future run from
   "optimising" them for the sake of a diff. Their baselines are the starting
   point for Day 13's caching work.
7. **The harness is not on the product path.** Nothing under `projects/` imports
   `profiling`; it exists for developers and CI. The five tools stay
   standard-library only and gain nothing to install.

### Edge cases covered by tests

- Harness: `iterations`, `repeat`, `number` and `warmup` of 0 or below raise
  `ValidationError`; an unknown `sort_by` is rejected; `top(0)` is rejected;
  `compare_timings` refuses a zero measurement; `--json` output round-trips
  through `json.loads`; the table output carries the label and header.
- Fixtures: a non-positive file count is rejected; every payload gets a
  distinct size so the size pre-filter is exercised as it would be in the wild.
- Suffix parity: `_suffix_of` matches `Path(...).suffix.lower()` for
  `"report.pdf"`, `"archive.tar.gz"`, `"trailing."`, `".bashrc"`, `"..x"`,
  `"..."`, `""`, `"dir.with.dots/file.csv"` and a spaced, mixed-case name —
  for both `str` and `Path` inputs.
- Naming parity: `_split_name` matches `Path.stem`/`Path.suffix`, and the
  reserver and `unique_destination` produce the same `" (1)"` name for
  `report.pdf`, `trailing.` and a suffix-less file.
- Duplicate detection: only files whose size repeats are hashed (a monkeypatched
  digest counter proves it); identical files still group; two empty files still
  group; a directory of distinct sizes returns `{}`.
- Listing: sorted case-insensitively, directories excluded, file symlinks
  followed, broken symlinks skipped, a file path still raises
  `NotADirectoryError`.

### Next up

- Day 13 caches the static data — the word list, the constituents table and the
  compiled intent regexes — with explicit invalidation, measured against the
  loaders' Day 12 baselines (8.7 ms, 3.3 ms and 8.3 ms respectively).
- Day 14 consolidates the week into a performance and observability report,
  reusing `profiling.py` so the numbers are reproducible rather than quoted
  from memory.

## Day 13 — Caching & memoisation: static data loaded once

**Branch:** `feat/datheon-day-13-caching`
**Scope:** Cache the three static datasets Day 12 measured — the Hangman word
list, the S&P 500 constituents table and the compiled intent regexes — with
explicit invalidation, and prove the drop with numbers rather than adjectives.

### Milestones

- Added `caching.py`: `FileSignature` (path + `mtime_ns` + size + inode, captured
  with one `os.stat`), `DatasetCache` (bounded LRU, thread-safe, optional TTL,
  loader runs outside the lock), `CacheStats` (hits, misses, stores,
  invalidations, evictions, expirations, stale drops, `hit_rate`),
  `compile_pattern`, and the registry helpers `cache_stats()`,
  `clear_all_caches()`, `invalidate_path()` and `path_key()`.
- Wired it into `words.load_words_from_file` (`WORD_LIST_CACHE`, keyed by path +
  `min_length` + `max_length` + `strict`) and
  `portfolio.load_symbols_from_csv` (`CONSTITUENTS_CACHE`, keyed by path +
  `strict`), each gaining a keyword-only `use_cache: bool = True`.
- Routed the chatbot's three `re.compile` sites — `Intent.from_dict`, the FAQ
  keyword builder and the greeting builder — through `compile_pattern`, so an
  `(pattern, flags)` pair is compiled once, counted, and clearable.
- Added `tests/test_caching.py` (54 tests): cache mechanics, thread safety, the
  regex cache, and both loader integrations asserted on loader call counts and
  cache counters rather than wall-clock time.
- Added `docs/caching.md` with the design, the invalidation contract, the
  measurements and the known limits; listed the module and the doc in the README
  tree.

### Measured numbers

Best sample, `profiling.time_callable` (cold: 7 samples, no warm-up; warm: 200
samples after 2 warm-ups), same container:

| Dataset | Cold (parse) | Warm (hit) | Speedup |
| --- | ---: | ---: | ---: |
| word list (75 KB) | 7.91 ms | 0.28 ms | 28.4× |
| constituents (500 rows) | 4.24 ms | 0.25 ms | 17.0× |
| intent regexes (11 patterns) | 0.77 ms | 0.68 ms | 1.1× |

200 loads of each dataset: 2429.1 ms uncached, 105.7 ms cached. The residual
~0.25 ms of a warm hit is the single `os.stat` that proves the file is unchanged.

### Architectural decisions

1. **Freshness is a signature, not a timer.** A TTL-only cache either expires
   data that did not change or serves data that did. Capturing
   `(mtime_ns, size, inode)` and re-stat-ing on lookup answers the real question
   for one syscall, and it means editing a dataset invalidates it *without
   anyone remembering to*. The inode is in there because `os.replace` can land a
   new file with the same size and a coarse mtime.
2. **The key includes every argument that changes the answer.** A
   `min_length=7` load and a `min_length=4` load are different datasets; a
   `strict=True` load can fail where a lenient one succeeds. Keying on the path
   alone would have served the wrong list and looked correct in a test that only
   checked the length.
3. **Clone in, clone out.** The loaders return mutable containers, and the first
   caller that does `words.append(...)` would otherwise poison every later call.
   A shallow copy is complete here — the leaves are `str`/`float` — and costs
   far less than the parse it replaces.
4. **A `LoadReport` means one physical read.** A report carries the encoding
   that won, the line endings and the row counts *of a specific read*. Serving
   one from cache would hand the caller metadata about a read that never
   happened, so passing `report=` bypasses the read path and refreshes the entry
   instead.
5. **Report the dud honestly.** The intent-regex row is 1.1×, because 11 patterns
   over 3.4 KB are already sub-millisecond and `re` memoises internally. The win
   there is observability and explicit clearing, not speed — and the doc says so
   instead of quietly dropping the row. A cache that claims credit it did not
   earn is worse than no cache.
6. **`path_key` instead of `Path.resolve()`.** The first cut keyed on
   `resolve()`, which `lstat`s every path component on every call and pushed a
   warm hit to 0.7 ms. `os.path.abspath` normalises without touching the
   filesystem, matching the normalisation `FileSignature` already used; warm hits
   fell to 0.25 ms. Measuring the cache you just built is not optional.

### Complexity analysis

- Cache hit: one `os.stat` plus O(1) dict lookup and move-to-end — the O(n)
  parse and decode collapse to O(1) per repeat call.
- Cache miss: unchanged from before — O(n) in rows/lines for the load, plus one
  `stat` and one container copy.
- Memory: one cloned copy per distinct `(path, arguments)` key, bounded by
  `maxsize` (16 entries for both loaders, 512 for patterns); eviction is LRU, so
  a process that walks many datasets keeps the working set rather than refusing
  the newest.
- Invalidation: `invalidate_path` is O(size) over the cache's entries, which is
  bounded and tiny; the common case (a file changed) costs nothing until the next
  lookup notices.

### Edge cases covered by tests

- Cache: miss-then-hit accounting; `lookup` distinguishing a cached `None` from
  a miss; `load` running its loader exactly once; values copied in and out for
  both lists and dicts; LRU order (a re-read key survives, the untouched one is
  evicted); TTL expiry; stale-signature drops counted as `stale`; `invalidate`
  reporting whether it removed anything; `invalidate_path` touching only its own
  file and ignoring unknown paths; `clear` keeping counters; `reset_stats`
  keeping values; blank name, `maxsize=0`, `ttl=0` and negative `ttl` all
  rejected with `ValidationError`; eight threads × 50 rounds leaving
  `requests == hits + misses` and `size <= maxsize` with no escaping exception.
- Regex cache: same pattern returns the same object, flags are part of the key,
  an invalid pattern still raises `re.error`, clearing forces a recompile, and two
  `Intent`s sharing a pattern share the compiled object.
- Word list: two calls are 1 miss + 1 hit + 1 store; the returned list is a copy;
  an edit is picked up with `stale == 1`; explicit invalidation forces a re-read;
  `report=` bypasses; `use_cache=False` never hits but refreshes; `min_length` is
  part of the key; a `strict` failure is not cached and the fixed file then
  loads; a missing file still raises `FileNotFoundError`.
- Constituents: the same set, plus a check against the real 500-row dataset
  (more than 400 symbols, `AAPL` present, second call a hit).

### Next up

- Day 14 consolidates Week 2 into a performance and observability report,
  reusing `profiling.py` for the timings and `cache_stats()` for the cache
  evidence, so the figures are reproducible rather than quoted from memory.

---

## Day 14 — Week 2 consolidation: the performance and observability report

**Branch:** `feat/datheon-day-14-perf-report`
**Scope:** Close Week 2 by joining six days of separate changes into one report —
what they bought in wall-clock terms, and what an operator can learn about a run
from the logs alone — and by shipping the reader that computes the second half.

### Milestones

- Added `observability.py`: a read-only reducer over the JSON logs written since
  Day 10. `read_json_records` parses one file and counts the lines it could not
  use; `discover_log_files` expands files and directories (rotated `*.log.1`
  siblings included); `summarize_runs` groups records by `run_id` and reduces
  each group to a `RunSummary` (outcome, duration, error code and type);
  `ProjectSummary` carries per-project outcome counts, error-code counts and
  p50/p95/worst durations; `ObservabilityReport` renders either a fixed-width
  table or JSON.
- Added `percentile()` on the nearest-rank rule and `duration_stats()`, so a
  reported p95 is always a duration some run actually took rather than an
  interpolation between two that did.
- Added a `observability` CLI with the shared command-line contract: `paths` or
  `--log-dir`, `--top`, `--json`, `--no-caches`, exit codes `0`/`1`/`2`.
- Added `docs/week2_performance_observability_report.md`: the consolidated
  performance tables (Days 11–13), the observability contract at a glance with
  the guarantee-to-test mapping, the report's design decisions, and the commands
  that reproduce every figure.
- Added `tests/test_observability.py` (49 tests, 107 assertions): the reader, the
  reducer, the percentile rule, the report, the CLI, and one end-to-end run of a
  real tool's `--json-logs` output through the report.
- Listed the module and the report in the README tree, linked the report from
  `docs/logging.md`, and added a "Reading the logs back" section to the README.

### Measured numbers

Consolidated from the Week 2 documents; same container, Python 3.12.13:

| Hot path | Before Week 2 | After Week 2 | Change |
| --- | ---: | ---: | ---: |
| `iter_files` (2,000 files) | 198.9 ms | 14.1 ms | 14.1× |
| `FileOrganizer.organize` dry run (2,000 files) | 645.4 ms | 431.3 ms | 1.50× |
| `find_duplicates` (600 + 60 copies) | 522.2 ms | 129.7 ms | 4.0× |
| word list load (75 KB) | 7.91 ms | 0.28 ms | 28.4× |
| constituents load (500 rows) | 4.24 ms | 0.25 ms | 17.0× |

Six real runs through the new report (five tools plus one deliberate failure)
produced `6 run(s)`, `success=5 error=1`, one `DT-1001`, and 0 malformed lines —
the failure being an `organizer` invocation pointed at a directory that does not
exist. Suite: 745 tests green in ~34 s.

### Architectural decisions

1. **Nearest-rank percentiles, never interpolated.** A duration in this report is
   a measurement; quoting an interpolated p95 would report a number no
   invocation ever cost. The result is always one of the samples.
2. **A malformed line is counted, never fatal.** A log file is written by a
   process that may be killed mid-write, so a truncated tail is normal. Each
   rejection keeps its line number and an 80-character preview, and the read
   continues — one bad line must not hide the other 900 records.
3. **`incomplete` is a first-class outcome.** A run that opened and never
   finished is exactly what an operator is hunting for, so it is reported rather
   than dropped or counted as a success. The same rule catches a killed process.
4. **Empty input is a data error, not an empty report.** "0 runs" and "you
   pointed me at the wrong directory" look identical in a table; the second one
   exits `1` with a hint instead of quietly reporting a quiet day.
5. **Rotated files are in scope.** The `*.log*` glob picks up `organizer.log.1`,
   because a rotation may have moved the run being investigated.
6. **Cache counters are labelled live, because they are.** They come from
   `caching.cache_stats()` of the *reporting* process. Run the module from a
   script that loaded the datasets and they are populated; run it from a bare CLI
   and they read zero. Both readings are honest, and the doc shows the populated
   case rather than hiding the empty one.

### Complexity analysis

- Reading: O(L) over the total number of log lines, one `json.loads` per
  non-blank line, with no second pass over any file.
- Grouping: O(R) over records, one dictionary insert per record plus one
  reduction per run — no sorting until display.
- Percentiles: O(n log n) per project over that project's run durations, on a set
  bounded by the number of runs in the log directory (tens, not millions).
- Memory: one parsed record held at a time plus the retained record list per file
  read; the report object itself holds one `RunSummary` per run.
- Malformed handling: O(1) per rejected line — no retry, no re-read.

### Edge cases covered by tests

- Reader: blank lines skipped; invalid JSON and JSON-that-is-not-an-object
  reported with line numbers and reasons; reading continues after a bad line; the
  preview is truncated to 80 characters; a missing file raises `AccessDeniedError`
  rather than leaking `FileNotFoundError`.
- Discovery: directory expansion including rotations; a non-log file in the
  directory ignored; an explicit file accepted; the same path given twice
  deduplicated; a missing path and an empty directory both raise
  `MissingFileError`.
- Reducer: outcome and duration taken from the finish record, not from a larger
  earlier offset; a run with no terminal record reported `incomplete` with its
  largest offset; error code and type extracted from `fields.error`; records
  without a `run_id` excluded and counted separately; first-seen run order kept.
- Report: totals, project grouping busiest-first, failures and success rate,
  error codes ranked by frequency, malformed lines aggregated across files,
  every outcome key present on every project summary, JSON round-trip, the
  empty-report table, `--top` limiting the table, and a non-positive `--top`
  rejected.
- CLI: JSON and table output, `--log-dir`, `paths` plus `--log-dir` rejected,
  missing path a data error, `--version` exiting `0`, and a real
  `inventory --json-logs` run reduced end to end.

### Next up

- Day 15 expands the suite past 200+ assertions on top of the current 745 tests,
  starting from the gaps this week's modules still have: the async organizer's
  cancellation paths and the cache's cold-key race.

## Day 15 — Test expansion: the 200+ assertion budget

**Branch:** `feat/datheon-day-15-test-expansion`
**Scope:** Turn "the suite crosses 200+ assertions" from a claim into a
measured gate, and close the largest remaining coverage hole: the five CLI
entry points, which stood at 0% coverage.

### Milestones

- Added `tools/assertion_census.py`: an `ast`-based census of test functions and
  assertion statements per module, with `--json` and a `--minimum N` mode that
  reports the verdict through the exit code.
- Added `tests/test_cli_entrypoints.py` (51 tests): every `main.py` imported by
  path and driven in process through its demo, interactive, validation and
  failure paths.
- Added `tests/test_assertion_census.py` (17 tests): the budget itself is now
  asserted on every run, along with the census tool's counting rules.
- Fixed a real defect the new tests found: `assistant --no-log` crashed with
  `AttributeError` on the first turn because the flag rebound the diagnostic
  logger to `None`.
- Wrote `docs/test_expansion.md` with the before/after numbers, the per-entry
  point coverage table and the reasoning behind the order of work.

### Numbers

| Metric | Before | After |
| --- | --- | --- |
| Assertion statements | 1,111 | 1,221 |
| Test functions | 592 | 644 |
| Collected pytest items | 745 | 813 |
| Whole-repo line coverage | 91% | 98% |
| `projects/*/main.py` coverage | 0% | 76–96% |

### Architectural decisions

- **Measure with `ast`, not grep.** Counting the literal string `assert` counts
  docstrings, comments and string literals, and misses nothing else — which is
  to say it is both too generous and too fragile. Walking the syntax tree counts
  real statements, including the ones nested inside `with` blocks, loops and
  helper functions, and it is immune to a `# assert` comment.
- **Call `main(argv)` in process rather than spawning subprocesses.** The
  subprocess smoke tests from Day 3 prove a tool starts; they cannot show which
  branch ran, and they contribute nothing to coverage. Importing the entry point
  under a namespaced alias — `datheon_entry_01_hangman_game` — sidesteps both the
  numeric folder prefixes and the fact that all five modules are called `main`.
- **Normalise the exit code in one helper.** Most paths return an `ExitCode`,
  but a closed stdin deliberately raises `SystemExit(0)` from inside the guess
  loop (the Day 5 policy). A single `_code()` helper converts both shapes so each
  test asserts behaviour, and one dedicated test still pins the raw
  `SystemExit(0)` contract.
- **Keep the tests hermetic by construction.** `DATHEON_LOG_DIR` points into
  `tmp_path` and every writable path is passed explicitly, so the suite never
  writes into the repository or `~/.datheon`. That is what makes it safe to run
  the entry points for real instead of mocking them.

### Complexity analysis

- Census: O(F) file reads plus one O(A) `ast.walk` per module, where A is the
  number of syntax nodes in that module — linear in the source size, no second
  pass.
- Totals: O(M) over modules for the table, O(M log M) only because the module
  list is sorted for stable output.
- Entry-point tests: each in-process call repeats argument parsing and demo
  setup — O(1) in the size of the project, and no process spawn, which is why
  the 68 new items cost about two seconds.

### Edge cases covered by tests

- Hangman: EOF inside the guess loop (raw `SystemExit(0)`), EOF at the category
  prompt (graceful return with the summary), a non-UTF-8 word list rejected as
  malformed, non-positive `--min-length`, and `--max-length` below `--min-length`.
- Portfolio: blank line ends the prompt, a two-field line is reported and
  skipped, a non-numeric price is reported and skipped, `--strict` with an
  unknown ticker fails, and `--constituents` reports the validation count.
- Organizer: real moves into category folders, `--dry-run` leaving every file in
  place, the deprecated `--workers` alias folding into `--concurrency`, a JSON
  activity log written, duplicate detection, `--concurrency 0`, and a missing
  directory.
- Assistant: a full turn answered then exit, EOF printing `(interrupted)`, the
  conversation log written, `--faq` dataset mode, `--validate`, a missing
  intents file, and `--no-log` (which is how the logger bug surfaced).
- Inventory: the full demo through every section, `--borrower`, `--days` with
  `--borrow-date`, zero days, a malformed date, and the strict 90-day cap.
- Census tool: docstrings and comments not counted, nested assertions counted,
  a thin module flagged, `--minimum` passing and failing, and JSON output whose
  totals agree with the per-module rows.

### Next up

- Day 16 replaces the remaining real-IO touches in the unit tests with
  `monkeypatch` and `unittest.mock` — clock, filesystem and network — so the
  suite stops depending on the machine it runs on.

---

## Day 16 — Mocking external I/O: no unit test touches the machine

**Branch:** `feat/datheon-day-16-mocking`
**Scope:** Make "no unit test touches the real network or the user filesystem"
a property the suite *enforces*, rather than a claim a reviewer has to trust.

### Milestones

- Added `tests/io_fakes.py` — the shared doubles: `FakeClock` (wall time *and*
  a monotonic counter, moved together by `advance()`), `fake_datetime_class` /
  `fake_date_class`, `install_fake_clock` / `install_fake_date`, `NetworkGuard`
  (patches every socket entry point and raises `RealIOAttempted`, an
  `AssertionError` subclass), `FakeFileSystem` (dict-backed `Path.read_bytes`,
  `read_text`, `is_file` and `builtins.open`) and `probe_network()`.
- Added three autouse fixtures to `conftest.py`: `isolated_home` redirects
  `HOME`, `USERPROFILE`, `TMPDIR`/`TEMP`/`TMP`, `XDG_*` and `DATHEON_LOG_DIR`
  into a private sandbox; `isolated_cwd` runs each test from a temp directory;
  `forbid_network` installs the guard. An opt-in `frozen_today` fixture freezes
  the calendar at **2026-11-04** inside `validators` and `models`.
- Added `tools/io_audit.py` — an `ast` audit of `tests/*.py` flagging
  `network-import`, `real-clock` and `home-lookup`, with `--json` and a
  `--strict` mode whose exit code is the verdict. CI now runs it before `pytest`.
- Added `tests/test_mocking.py` (29 tests) and `tests/test_io_audit.py`
  (21 tests): the doubles, the fixtures, and — just as importantly — that each
  detector actually fires on a planted violation.
- Removed the last three real-IO touches: `date.today()` in `test_inventory`
  and `test_validation`, and `Path.home()` in `test_logging`.
- Wrote `docs/mocking.md` and a README section covering the policy, the doubles,
  the gate, and what is deliberately still real.

### Architectural decisions

1. **Guard, don't just mock.** A double that is merely *available* is a double
   nobody uses. The autouse `forbid_network` fixture makes an accidental
   outbound call a test failure with a readable message, which is the only form
   of "we never touch the network" that survives contact with new code.
2. **Patch the socket layer, not the client.** `requests`, `urllib`,
   `http.client`, database drivers and `yfinance` all converge on
   `socket.socket.connect` / `create_connection`. Patching five entry points
   covers every client the repository might add, including ones written later.
3. **Module-scoped clock patches, not global ones.** `install_fake_clock` swaps
   the *module's* `datetime` and `time` names, so two tests can freeze different
   clocks concurrently and no test can freeze another's by accident.
4. **Sibling sandbox, not a child of `tmp_path`.** The isolation directories are
   created with `tmp_path_factory.mktemp(...)` rather than `tmp_path / "home"`,
   because two existing tests assert "nothing was written" against their own
   `tmp_path`; nesting the sandbox inside it would have made them fail for the
   wrong reason.
5. **Static gate as well as runtime guard.** The guard only catches I/O that
   *executes*. The auditor catches the smell at the source — a `datetime.now()`
   that happens not to run on the covered branch is still a defect waiting for a
   new test to trip over it.
6. **Opt-in clock, autouse environment.** A frozen clock is only meaningful
   relative to a module, so autouse freezing would hide the dependency instead of
   removing it. `HOME`, `cwd` and the network have no such per-module meaning,
   so they are autouse.

### Complexity analysis

- `NetworkGuard.install`: O(1) — five attribute patches per test, undone by
  `monkeypatch` teardown.
- `FakeFileSystem`: O(1) per lookup against a dict keyed by the path string;
  `reads` makes the audit of "did it consult the fake" O(1) to assert.
- `io_audit`: one `ast.parse` per module plus one `ast.walk`, so O(N) in syntax
  nodes — the same shape as the Day 15 assertion census, and linear in the
  suite's source size.
- Suite cost: the isolation fixtures add roughly 40 ms across 688 test
  functions (three `mkdir` calls plus five patches per test); total runtime is
  unchanged within noise.

### Edge cases covered by tests

- `FakeClock` refuses to run backwards (`advance(-1)` → `ValueError`) and
  refuses a naive `set()` — a clock that silently accepts either would hide
  ordering bugs instead of exposing them.
- `FrozenDatetime` is a real `datetime`: `isinstance`, arithmetic and
  `fromisoformat` all still work, so freezing does not change the code path
  under test.
- A dataclass captures `default_factory` at class-definition time, so patching
  the `time` module does not reach `RunContext.started_monotonic`; the duration
  test injects that value explicitly and asserts the 250 ms it advanced.
- `FakeFileSystem` reproduces BOM + CRLF decoding through the real
  `read_text_robust`, raises `MissingFileError` for an unregistered path, and
  refuses write modes.
- The auditor reports a syntax error as a violation instead of raising, ignores
  non-Python files, and handles a missing directory.
- The guard blocks `urllib.request.urlopen`, proving the higher-level client
  path is covered and not just the raw socket.

### Next up

- Day 17 replaces hand-written edge cases with Hypothesis strategies for the
  validators, the CSV/JSON loaders and the keyword extractor, running with a
  fixed seed in CI.

---

## Day 17 — Property-based testing: stating the rule instead of the example

**Branch:** `feat/datheon-day-17-property-tests`
**Date:** 2026-11-05
**Scope:** Replace hand-picked edge cases with Hypothesis properties for the
validators, the dataset loaders and the assistant's text pipeline, running with
a fixed seed in CI.

### Milestones

- Added `hypothesis>=6.98` to the development requirements; the property module
  skips itself cleanly when the package is absent, so the suite still runs for
  anyone who has not reinstalled.
- Added `tests/strategies.py` — the generator library plus three Hypothesis
  profiles (`dev`, `ci`, `nightly`) and two fixture writers
  (`write_dataset`, `write_document`).
- Added `tests/test_properties.py` — 31 property tests covering the eight
  validators, `read_text_robust` / `strip_bom` / `detect_newline` /
  `require_columns`, and `_keywords` / `normalize`.
- Wired a dedicated CI step that runs the property module with
  `HYPOTHESIS_PROFILE=ci`, i.e. a **fixed seed**.
- Documented the properties, the profile table and the reasoning in
  [`property_testing.md`](property_testing.md).
- Suite grew from 644 to **719 test functions** and from 1,221 to **1,352
  assertions**; everything green, `io_audit --strict` still clean.

### Architectural decisions

1. **Strategies in terms of production constants.** `symbol_texts()` compiles
   `SYMBOL_PATTERN.pattern` and filters on `MAX_SYMBOL_LENGTH`;
   `price_values()` bounds itself with `MAX_PRICE`. A duplicated magic number in
   a generator is a test that keeps passing after the rule it tests has changed,
   which is worse than no test.
2. **Properties that relate two modes, not two values.** The most valuable
   property in the module is the subset law — *whenever strict mode accepts a
   value, lenient mode returns the same value* — because it is the one statement
   that cannot be satisfied by reimplementing the code inside the test. The same
   shape gives the loader its lossless round trip and the chatbot its
   monotone-in-`min_length` subset.
3. **A fixed seed in CI, a random one locally.** `ci` sets `derandomize=True` so
   a red build is reproducible and a green build means the same 150 cases were
   explored on every run; `dev` stays random so local runs keep discovering new
   inputs. The profile is chosen by environment variable rather than by
   branching inside a test, so CI and local differ in exactly one place.
4. **`deadline=None` and the function-scoped-fixture check suppressed.** The
   loader properties write real files into `tmp_path`, and the Day 16 autouse
   fixtures are function-scoped by design. Leaving either default on would make
   the verdict depend on machine load or fail every test for a non-defect.
5. **Exception hygiene as a property.** One test asserts that arbitrary text
   input to all nine validator entry points either succeeds or raises
   `ValidationError`. That is the invariant `cli_contract.run_cli` depends on to
   map bad input onto exit code `1` without a traceback, and it is the kind of
   claim that only a generator can falsify.
6. **Fixture writers, not inline `write_bytes`.** `write_dataset` takes the
   exact text plus encoding and BOM flag; `write_document` adds a named newline
   style. Round-trip assertions therefore compare the loader against the same
   bytes a real fixture holds, with no newline translation hidden in the helper.

### Complexity analysis

- One property test is O(examples × cost of one call). The loader properties are
  the expensive ones at one `write_bytes` + one `read_bytes` per example —
  roughly 300 syscalls per property on the `ci` profile, i.e. milliseconds.
- Strategy construction is O(1) per test and cached by Hypothesis.
- Shrinking is O(log n) in the size of the failing input, which is why a failure
  is reported as `literal='-1'` rather than as the first random string that
  broke it.
- The whole property module runs in under two seconds on the `ci` profile; the
  full suite is unchanged within noise.

### Edge cases covered by tests

- `MAX_PRICE` itself is accepted and one ulp above is rejected — the boundary is
  inclusive, and the property says so without naming a number.
- A generated ticker containing a dot or a hyphen (`RELIANCE.NS`, `BRK-B`)
  round-trips through the same regex the validator uses.
- A UTF-8 file whose text *starts* with `U+FEFF` is reported with
  `had_bom=True` and exactly one marker stripped; a marker later in the file is
  preserved.
- An empty document reports `newline="none"`, not `"lf"`.
- A Latin-1 file containing only ASCII still reports an encoding from
  `DEFAULT_ENCODINGS`, and one containing `0xE0`–`0xFF` decodes through the
  `cp1252` fallback without corruption.
- A header that is present in a different case is returned with its *original*
  spelling, because downstream CSV writers depend on that.
- Blank input fails the text validators **with a hint attached**, so the CLI can
  print a second line that tells the user what to type instead.

### Next up

- Day 18 stops generating valid input and starts generating hostile input:
  random and malformed bytes into the five CLIs, the three loaders and the
  chatbot, asserting zero unhandled exceptions across the corpus.

---

## Day 18 — Fuzz testing: breaking it on purpose

**Branch:** `feat/datheon-day-18-fuzzing`
**Scope:** A deterministic fuzz corpus and crash verdict shared by a
command-line driver and the pytest suite, aimed at the five CLIs, the dataset
loaders and the assistant's intent engine.

### Milestones

- Added `fuzzing.py`: a pure-function corpus generator (`generate_cases`) plus
  three judges (`judge_process`, `judge_exit_code`, `judge_exception`) that turn
  "did it crash?" into a `Verdict` object.
- Added `tools/fuzz.py`, a driver with four campaigns — `argv`, `dataset`,
  `loader`, `text` — covering 16 targets. The exit code is the verdict, so the
  script is usable as a CI step.
- Added `tests/test_fuzzing.py` (52 test functions): the same corpus, asserted
  in-process, plus unit tests for the harness and the driver.
- Fixed two real defects the campaign found in the intent loader (below) and
  pinned both with regression tests.
- Documented the campaign, the crash definition and its limits in
  `docs/fuzzing.md`; added a `--cases 3` fuzz smoke step to CI.
- Suite: **755 test functions / 1,422 assertions**, all green.

### Architectural decisions

1. **The corpus is generated from a seed, not stored as fixtures.** Each case
   comes from `random.Random(seed * 1000 + index)`, so a failure is reproduced
   by quoting one integer and case *n* is stable as the corpus grows. A binary
   fixture would have been a file to maintain; a seed is a number to print.
2. **Mutation beats noise.** Pure random bytes die in the decoder, which leaves
   the row, schema and field validators untested. `mutate_bytes` starts from a
   real dataset and breaks one thing — a truncated row, a stray BOM, CRLF
   translation, a duplicated line — and half of all mutations keep the opening
   bytes intact so the file still *looks* loadable.
3. **The verdict is shared, the tolerance is not.** `tools/fuzz.py` and the
   test module judge through the same functions, but with different tolerances:
   a CLI may raise anything `run_cli` converts (exit code 1 is the contract),
   while a loader may only raise `DatheonError`. Narrowing the loader tolerance
   is what turned a silent leak into a failing case.
4. **The CLI campaigns run as subprocesses; the loader campaigns run
   in-process.** A traceback is only a traceback at the process boundary, so
   the CLIs must be measured there. A loader must be measured *inside*, because
   `run_cli` would otherwise hide the very leak the test is looking for.
5. **Fuzzing is not allowed to be destructive.** Absolute paths are absent from
   the argument pool, the organizer always receives `--dry-run`, every
   subprocess runs in a temporary sandbox as its working directory, and
   `DATHEON_LOG_DIR` is redirected so `--log-level DEBUG` cannot write into the
   repository.
6. **A NUL byte in `argv` is reported as skipped, not passed.** No kernel
   accepts it across `execve`, so the case is unreachable rather than clean; the
   in-process suite still runs it, because `main(argv)` takes any string.

### Defects found and fixed

- **`re.error` escaped the intent loader.** A mutated `intents.json` produced
  the pattern `(unclosed`. `Intent.from_dict` compiled patterns unguarded, and
  `re.error` is not a `ValueError`, so it slipped past `_parse_intents`'
  handler, past `run_cli`, and printed a traceback. Now each pattern is
  compiled inside a guard that raises `ValidationError` naming the intent, the
  pattern and a hint; `_parse_intents` records it as an `intents[i]` row error.
- **`TypeError` from a non-integer `priority`.** `int()` on a JSON object
  raises `TypeError`, which the same `except ValueError` did not catch. The
  conversion is now guarded and reported the same way.

Neither was reachable from the bundled dataset or from any hand-written test.
Both were one byte-mutation from a file the tool loads happily.

### Complexity analysis

- One generated case is O(payload size) and allocates a single `Random`:
  microseconds. Nothing is written to disk except the mutated file itself.
- `mutate_bytes` is O(len(base)) per edit, at most six edits per case.
- The `loader` and `text` campaigns are in-process and I/O-bound: 1,500 loader
  cases finish in a few seconds.
- The `argv` and `dataset` campaigns pay one interpreter start per case at
  roughly 0.15 s. The default 400-case run takes about two minutes; CI runs a
  48-case smoke version instead.
- Memory stays flat: one `Verdict` per case, never the payloads.

### Edge cases covered by tests

- An empty argument vector (a user running a tool bare) is inside the corpus.
- A 300-character token, an empty token, `-`, `--`, `---` and a NUL token all
  reach `argparse` and leave through exit code 2, not a traceback.
- A mutated dataset that is still valid is accepted silently — the campaign
  asserts only that nothing *escapes*, so a clean load is a pass.
- A file that was never text (invalid UTF-8, no BOM, random bytes) becomes a
  `DatasetFormatError`, never a `UnicodeDecodeError`.
- The intent engine returns a well-formed `Response` for every string the
  validator accepts: confidence in `0..1`, non-empty text, boolean `is_exit`.
- `normalize()` is total over control characters, bidi overrides, combining
  marks and emoji, and always returns lower-cased text.
- The organizer's dry-run injection is idempotent: `_harden` does not add a
  second `--dry-run` to a vector that already carries one.

### Next up

- Day 19 checks whether the tests actually test: a `mutmut` run over the core
  modules, with the surviving mutants killed.

## Day 19 — Mutation testing: proving the tests have teeth

**Branch:** `feat/datheon-day-19-mutation`
**Scope:** A `mutmut` run over the shared error-catalogue module, every
surviving mutant killed, and a driver that turns the run into a gate a build
can enforce.

### Milestones

- Added `[tool.mutmut]` to `pyproject.toml`, scoping the run to
  `error_catalogue.py` against `tests/test_errors.py`, with the `also_copy` list
  that ships the rest of the shared core into mutmut's sandbox.
- Ran mutation testing: **182 mutants, 182 killed, 0 survived, 0 uncovered —
  100.0%**, from a baseline of 143 killed / 16 survived / 23 uncovered (89.9%).
- Added twelve tests to `tests/test_errors.py` that kill all thirty-nine
  remaining mutants, all of them pinning text a user actually sees.
- Added `tools/mutation.py`: runs mutmut, parses its cache, prints a per-module
  table or JSON, lists survivors with the command that shows their diff, and
  exits `1` below `--minimum-score`.
- Added `tests/test_mutation_tool.py` (30 tests) covering the exit-code
  mapping, the scoring rule, cache parsing and the gate's exit codes.
- Added `.github/workflows/mutation.yml` — a weekly audit plus a run on any PR
  that touches the shared core — and `docs/mutation_testing.md`.
- Suite: **787 test functions / 1,497 assertions**, 991 tests, all green.

### Architectural decisions

1. **The score is computed strictly.** The denominator is every mutant mutmut
   produced, minus only the ones it never ran, skipped, or that a user
   interrupted. Mutants with **no covering test** count against the score.
   Dropping them is the easiest way to make a mutation score look good, and it
   would have hidden the twenty-three mutants in `write_catalogue` and `main`.
2. **An interrupted run cannot look perfect.** A cache full of `not checked`
   scores `0.0`, not `100.0`. Otherwise the gate would pass on the one run that
   measured nothing.
3. **`timeout` and `segfault` count as kills.** A mutant that hangs or crashes
   the test process has been noticed; that matches mutmut's own classification.
4. **One module, deliberately.** `error_catalogue.py` is the shared contract
   (all five tools render failures through it) and its test module runs in
   3.5 s, so a mutant costs well under a second in a warm forked worker: 182
   mutants in 1 m 55 s on two cores. Mutating a project against its full test
   module would take tens of minutes.
5. **`also_copy` is load-bearing.** mutmut copies only the mutated file and
   `tests/` into `mutants/`, so the first attempt died at the stats phase with
   `ModuleNotFoundError: No module named 'errors'`. The copy list now ships
   `conftest.py`, the rest of the shared core, `projects/`, `tools/` and
   `docs/`.
6. **The cache is never committed.** `mutants/` and `.mutmut-cache` are
   gitignored; the run is reproducible from `python tools/mutation.py`, and a
   committed cache would be a stale number pretending to be a live one.

### Gaps found and closed

- **The failure path of the code lookups was unasserted.** Sixteen mutants
  survived in `describe_code` and `error_class_for`: the suite asserted *that*
  `UnknownCategoryError` was raised, never what it said. A mutant could blank
  the message, delete the hint, change its separator from `", "` to `"XX, XX"`,
  rename the `context` key from `code` to `CODE`, or drop the argument
  entirely — all green.
- **Markdown pipe escaping was untested.** Two mutants turned
  `cell.replace("|", "\\|")` into `cell.replace("XX|XX", "\\|")`, which
  silently produces a broken table in a generated, committed document.
- **`write_catalogue` and `main` had no test in the selected module** — 23
  mutants with no covering test at all. `write_catalogue` was exercised from
  `tests/test_persistence.py` for atomicity only; `main` was exercised nowhere.
- **A dead default argument.** After the first round the score reached 99.0%
  with two survivors: both mutations of `write_catalogue`'s default parameter.
  Nothing called `write_catalogue()` bare, even though the documented command
  `python error_catalogue.py` relies on the equivalent default inside `main`.
  One test that calls the bare form killed both.

### Complexity analysis

- mutmut runs one pytest process per mutant, forked from a warm parent, so the
  cost per mutant is the *test body*, not an interpreter start. Measured:
  1.70 mutations/s on two cores with `--max-children 2`, i.e. ~0.6 s per mutant.
- The whole pass is 182 mutants in 1 m 55 s. `--report-only` reads one JSON
  object per module: microseconds, which is why the pytest suite can assert on
  the report without needing mutmut installed.
- `tools/mutation.py` is O(mutants) in time and memory — one frozen dataclass
  per mutant, no source text retained.
- Scoring is O(modules × statuses) using a per-module counter, so the gate is
  constant-time work after parsing.

### Edge cases covered by tests

- A cache file that is not JSON, or that has no `exit_code_by_key`, is skipped
  rather than crashing the report.
- A missing `mutants/` directory yields an empty report, and the gate returns
  `0` with an explicit "no cache found" note instead of failing on nothing.
- A cache of nothing but `not checked` scores `0.0` and fails the gate.
- `no tests` mutants drag the score down; `timeout`, `segfault` and `killed`
  all count up.
- The unknown-code error is asserted on its exact message, its code, its
  `context` mapping, its hint (built from `catalogue_codes()`), and its
  two-line rendered block.
- A synthetic `ErrorEntry` whose title, summary and resolution all contain `|`
  renders a row with exactly four unescaped `" | "` separators.
- `write_catalogue` and `main` are covered with a `str` path, a `Path`, a
  missing parent directory, an explicit destination, the default destination,
  and the `sys.argv` fallback.

### Next up

- Day 20 locks in a coverage floor: `--cov-fail-under=90` wired into CI, so
  coverage and mutation score gate together.
- Then `edge_cases.py` and `validators.py` as further mutation targets —
  `tests/test_edge_cases.py::test_run_cli_broken_pipe_end_to_end` spawns a real
  subprocess and fails inside `mutants/`, so it has to be made sandbox-relative
  before its module can be mutated.

---

## Day 20 — Coverage gate: turning a measurement into a verdict

**Branch:** `feat/datheon-day-20-coverage-gate` · **PR:** #21
**Scope:** Enforce a 90% coverage floor in CI instead of printing a number.

### Milestones

- Added `tools/coverage_gate.py`: runs pytest under coverage, parses the JSON
  report, and returns `0` (floor met), `1` (floor breached) or `2` (no verdict
  — pytest failed, or no readable report exists).
- Scoped coverage in `pyproject.toml` to the shipped code — the five projects
  and the shared core — with branch coverage on and `tests/`, `tools/` and
  `conftest.py` omitted.
- Mirrored the floor in `[tool.coverage.report] fail_under = 90` so a bare
  `pytest --cov` also fails, and replaced CI's advisory
  `pytest --cov=projects --cov-report=term-missing` step with
  `python tools/coverage_gate.py --minimum 90`.
- Added `tests/test_coverage_gate.py` (43 tests) and `docs/coverage_gate.md`.

### Architectural decisions

1. **A gate needs three answers, not two.** `0` and `1` are obvious; `2` is the
   one that matters. A gate that cannot measure — a red suite, a missing
   report, a report it cannot parse — must never report success, because a
   broken build that silently passes its quality gate is worse than no gate.
2. **Measure the code that ships, not the code that checks it.** Measuring the
   whole repository reports 98%, but most of that is the test files: every line
   of a test that runs is a covered line. The gate watches 3,292 statements and
   766 branches of shipped code, which lands at 95.76% — lower, and honest.
3. **Branch coverage, because a line is not a decision.** `if item % 2:` being
   executed says nothing about the `else` it guards. Counting both edges is
   what makes the file table in the report actionable.
4. **One floor, three copies, one assertion.** `DEFAULT_MINIMUM` in the tool,
   `fail_under` in `pyproject.toml`, and `--minimum 90` in CI. Three copies is
   two too many in principle, so the tests assert they agree — a floor edited
   in one place and forgotten in another fails the suite instead of quietly
   weakening the build.
5. **The gate silences pytest-cov's own floor when it runs the suite.** Passing
   `--cov-fail-under=0` means pytest's exit code says only "tests passed or
   failed". Without it, `fail_under` would make pytest-cov exit `1` on a
   breach, and the gate would report the generic `2` ("cannot measure") instead
   of naming the floor and the laggards. The bare `pytest --cov` run keeps the
   configured floor for anyone not going through the tool.

### Verification: the gate actually bites

The definition of done is "CI fails on a deliberate coverage drop", so the drop
was manufactured and measured against the unchanged suite:

| Run | Statements | Missing | Coverage | Exit |
| --- | --- | --- | --- | --- |
| Baseline | 3,292 | 115 | 95.76% | 0 |
| + 104-statement uncovered module | 3,396 | 219 | 91.61% | 0 |
| + 260-statement uncovered module | 3,552 | 375 | 86.01% | 1 |

The failing run printed `total coverage 86.01% is below the 90% floor (375 of
3552 statements unexecuted)` and the five worst-covered files, then exited `1`.
The scratch module was deleted afterwards, leaving the tree as committed.

The middle row is the informative one: a single badly-covered module is
*absorbed* by 5.8 points of headroom. That headroom is deliberate — a floor
that trips on one uncovered helper is a floor people disable — which is why the
failure output leads with the total but lists the files. The number is the
tripwire; the file table is what makes it fixable.

### Edge cases covered by tests

- An empty module (`0` statements) is 100%, not a division-by-zero failure.
- A report with no `totals`, a `totals` block missing `num_statements`, or a
  boolean where a number belongs raises `ValueError` instead of silently
  scoring `0`.
- Malformed per-file entries (a bare string, a file with no `summary`) are
  skipped, so one odd row cannot take down the whole verdict.
- Windows separators in file paths are normalised, so the file table matches
  the POSIX paths used in docs.
- A file at exactly the floor passes; a file with nothing to execute is exempt
  from `--per-file-minimum`.
- The overall breach is reported before per-file breaches, because it is the
  one the build log should shout about first.
- `--report-only` with no report, and a pytest run that exits non-zero, both
  return `2` rather than `0`.
- A green suite with a red report returns `1`, not `2` — the distinction the
  `--cov-fail-under=0` decision above exists to preserve.

### Next up

- Day 21 consolidates Week 3: a test-strategy document and QA report covering
  the six layers now in place — assertion census, I/O isolation audit, property
  tests, fuzz corpus, mutation score, and this coverage floor.
- `--per-file-minimum` is implemented and tested but not switched on in CI: at
  today's numbers it would fail on five modules, and the honest fix for those
  is tests, not a threshold.

---

## Day 21 — Week 3 consolidation: test strategy & QA report

**Branch:** `feat/datheon-day-21-test-strategy` · **PR:** #22
**Scope:** Join the six Week 3 gates into one dashboard, write down the strategy
they add up to, and report on the week honestly.

### Milestones

- Added `tools/qa_report.py`: collects one measurement per layer — static,
  assertions, isolation, properties, fuzz, mutation, coverage — compares each
  against the floor that layer's own module owns, and prints one table with one
  verdict. Exit `0` pass, `1` breach, `2` incomplete.
- Added `tests/test_qa_report.py` (38 test functions / 55 cases, 89
  assertions): every layer's breach, every layer's absence, the inclusive-floor
  boundary, non-numeric measurements, JSON round-trips, unreadable evidence,
  and the three exit codes.
- Added `docs/test_strategy.md` — risk model, layer costs, floor rationale, the
  isolation contract, what is deliberately not tested, the add-a-test
  checklist, and the triage order for a red build.
- Added `docs/week3_qa_report.md` — the week's verdict, the seven findings
  (two fuzz defects, 39 mutation survivors, five under-covered shells, two
  process gaps), and six open items carried into Week 4.
- Ignored `coverage.json` and `qa_evidence.json`: build output that a
  `git add -A` could otherwise commit as source.

### Architectural decisions

1. **Judgement is separated from collection.** `build_report(evidence)` is a
   pure function from a mapping to a report; `collect_*` does the subprocess
   work. That split is what lets the suite test every boundary — exactly at the
   floor, one unit under it, a missing layer — without running a six-minute
   campaign, and it is why the dashboard's own tests cost 89 assertions rather
   than a second CI job.
2. **Floors are imported, not retyped.** `ASSERTION_FLOOR`,
   `MUTATION_FLOOR` and `COVERAGE_FLOOR` are read from `assertion_census`,
   `mutation` and `coverage_gate`; a test asserts the copies agree. A dashboard
   that disagrees with the gates it reports on is worse than no dashboard.
3. **An unmeasured layer is not a passing layer.** Same rule as Day 20's exit
   `2`, applied per layer: a missing linter, an absent mutmut cache, or an
   unparseable coverage report leaves that layer `unmeasured` and the report
   exits `2`. `--allow-unmeasured` is the explicit opt-out for `--skip-slow`.
   The alternative — treating silence as health — is the failure mode the whole
   week exists to prevent.
4. **Two comparators, one code path.** A floor points in one of two directions:
   *at least* (200 assertions, 90% coverage, 95% mutation) or *at most* (zero
   violations, zero crashes). Carrying the direction on the gate instead of
   inverting the numbers means the table can print `>=` and `<=` and the reader
   never has to work out which way a number is good.
5. **The fuzz layer is a smoke layer.** The dashboard runs 3 cases per campaign
   so it stays usable per-PR; the 400-case corpus stays the weekly run. A
   dashboard nobody runs because it takes ten minutes has a coverage of zero.

### Verification: the dashboard bites

The definition of done is that the report fails when it should, so all four
paths were driven against the real evidence file:

| Evidence | Verdict line | Exit |
| --- | --- | --- |
| Today's `main` | `PASS — every layer was measured and every floor was met.` | 0 |
| Assertions forced to 42 | `FAIL — 1 layer(s) breached their floor.` | 1 |
| Mutation layer removed | `INCOMPLETE — 1 layer(s) could not be measured; …` | 2 |
| Same, with `--allow-unmeasured` | `INCOMPLETE — …` | 0 |

The third row was not manufactured: the first run of the day, before
`python tools/mutation.py` had populated the gitignored cache, reported exactly
that — a fresh clone has no mutation evidence and the dashboard says so instead
of scoring an absent cache as 100%.

### What the week measured

| Layer | Measurement | Floor |
| --- | --- | --- |
| Static | 0 violations over 69 files | 0 |
| Assertions | 1,659 assertions / 868 test functions / 26 modules | 200 |
| Isolation | 0 violations | 0 |
| Properties | 31 tests, 0 failures | 0 |
| Fuzz | 48 cases, 0 crashes (400-case corpus weekly) | 0 |
| Mutation | 182/182 killed, 100.0% | 95% |
| Coverage | 3,292 statements, 766 branches, 95.76% | 90% |

### Edge cases covered by tests

- A measurement exactly on its floor passes; one hundredth under it fails.
- A boolean, a string, `None`, a list or an empty mapping is *not* a
  measurement — the layer goes unmeasured rather than being coerced to `0`.
- A layer carrying only half its fields (flake8 without black) is unmeasured:
  a partial run is not a clean run.
- A mutmut cache with zero mutants scores unmeasured, never 100%.
- A command that cannot start (`FileNotFoundError`) or hangs
  (`TimeoutExpired`) yields no evidence instead of an exception.
- A fuzz driver that prints HTML, or a JSON list where a mapping belongs, is
  unmeasured rather than "zero crashes".
- An evidence file that is not JSON exits `2` with a message, not a traceback.
- A real breach outranks a missing measurement when both are present: the
  verdict is `fail`, not `incomplete`.

### Next up

- Day 22 makes the repository installable: PEP 621 `pyproject.toml` with a
  `console_scripts` entry point per tool. The five `main.py` shells are the
  modules that need to survive being imported rather than executed, which is
  also the honest fix for the per-file coverage gap (O1).
- O6 — wiring this dashboard into CI as a non-blocking report job — belongs
  with Day 22's packaging work, since both touch the same workflow file.

---

## Day 22 — Packaging & entry points: five commands instead of five paths

**Branch:** `feat/datheon-day-22-packaging` · **PR:** #23
**Scope:** Make the repository installable — PEP 621 metadata plus one
`console_scripts` entry per tool — without moving a single file under
`projects/`.

### Milestones

- Added `pyproject.toml` metadata: `[build-system]` (setuptools ≥ 77, PEP 517),
  `[project]` (name, version, `requires-python >= 3.10`, MIT licence via the
  PEP 639 expression, classifiers, empty `dependencies`), a `dev` extra
  mirroring `requirements.txt`, and project URLs.
- Added `[project.scripts]`: `hangman`, `portfolio`, `organizer`, `assistant`
  and `inventory`, each pointing at a shim's `main`.
- Added the `datheon/` package — `datheon/cli/_registry.py` (the `ToolSpec`
  table, the `sys.path` repair, `load_entrypoint()` and `run()`) and five
  one-line shims in `datheon/cli/`.
- Declared `py-modules` for the thirteen shared core modules at the root,
  `packages.find` for `datheon*`/`projects*`, and `package-data` for
  `data/*.txt` and `data/*.csv` so the bundled datasets travel with a wheel.
- Added `tests/test_packaging.py` — 28 test functions / 55 cases / 64
  assertions — covering the declaration, the wiring, the layout and the two
  documented exit codes end to end.
- Added `docs/packaging.md` (the problem, the design, what ships, how to
  verify, what is deliberately deferred) and a README section for
  `pip install -e .`.
- CI now installs the package and runs `--version` through all five console
  scripts, which is the one thing unit tests cannot prove.
- Excluded `*.egg-info` from `flake8` and `black`: build output that a local
  editable install leaves in the working tree.

### Architectural decisions

1. **The numbered layout stayed.** `01_hangman_game` … `05_inventory_oop` are
   not valid Python identifiers, and all five entry points are called `main`,
   so neither can be imported flat. Renaming would have touched every import,
   every README path, the CI workflow and the whole test suite for a cosmetic
   gain, and would have broken the task-pack ordering the repository is graded
   on. Instead, `_registry.load_entrypoint()` puts the distribution root and
   the tool's project directory on `sys.path` and imports
   `projects.<package>.main` through `importlib` — which, unlike the `import`
   statement, accepts a segment that starts with a digit.
2. **The shims hold no logic.** Each is `return run("<name>", argv)`; the
   dispatch, the path repair and the error mapping exist exactly once, in
   `_registry`. That is what lets `tests/test_cli_entrypoints.py` — 1,200 lines
   of argument-parsing coverage written on Day 15 — keep testing the code a
   console-script user actually runs. A second copy of the CLI logic would have
   made that suite a test of the wrong thing.
3. **The exit-code contract was reused, not restated.** `run()` wraps the tool
   in `cli_contract.run_cli`, the same shim the `if __name__ == "__main__"`
   block uses, so `0`/`1`/`2`, the `DatheonError` rendering, `Ctrl-C`,
   broken-pipe handling and the structured-log shutdown all survive the move to
   an installed command without being re-implemented — and a test asserts a
   bare `ValueError` still exits `1` rather than raising a traceback.
4. **Version has one source.** `datheon.__version__` mirrors
   `cli_contract.PROGRAM_VERSION`, and three assertions (package attribute vs.
   banner vs. `pyproject.toml`) fail if the three ever drift. A distribution
   that reports a different version than its own `--version` banner is a bug
   report waiting to happen.
5. **The declaration is tested against reality, in both directions.** The
   `py-modules` list must equal the `*.py` files at the root (minus
   `conftest.py`), the registry must equal the directories under `projects/`,
   and `[project.scripts]` must equal the registry's names. Each is a pair that
   silently rots if only one side is updated; comparing both directions makes
   forgetting impossible.

### Verification

| Check | Result |
| --- | --- |
| `python -m compileall` (projects, tests, conftest, datheon) | clean |
| `flake8 .` | 0 findings over 78 files |
| `black --check .` | 78 files unchanged |
| `pytest` | 1,143 passed, 2 skipped, 0 failed |
| Assertion census | 896 test functions / 1,723 assertions (floor 200) |
| Coverage gate | 95.80% over 3,368 statements (floor 90%) |
| `tools/io_audit.py --strict` | no real I/O in the test suite |
| `pip install -e .` in a clean venv | 5 scripts installed |
| `<tool> --version` × 5, from `/tmp` | `hangman 1.0.0` … `inventory 1.0.0`, exit `0` |
| `<tool> --bad-flag` × 5 | exit `2` |

### Edge cases covered by tests

- A shim forwards its `argv` unchanged; `None` (a shell launch) reaches the
  tool as `None`, so the tool parses `sys.argv` itself.
- A tuple argument sequence is normalised to a list the tool can mutate.
- An unknown tool name raises `KeyError` whose message lists the five valid
  names — a programming error, not user input.
- `_ensure_importable` is idempotent: re-adding a path already on `sys.path`
  does not duplicate it.
- Loading the same tool twice returns the identical module object
  (`importlib` caching), not a re-executed copy.
- `--help` reaches the tool's own parser and still prints the exit-code epilog.
- The installed-metadata assertion skips itself when the distribution is not
  installed, so a bare checkout run stays green.

### Next up

- Day 23 builds the artefacts: wheel + sdist, install into a clean virtualenv,
  run each entry point from it, and gate on `twine check`. `package-data` was
  declared today precisely so that wheel contains the datasets; the wheel is
  also where the "editable install masks a missing `py-modules` entry" risk
  finally gets tested for real.
- Still open from Week 3: O1 (the five `main.py` shells sit at 76–89% coverage)
  and O6 (wire `tools/qa_report.py` into CI as a non-blocking report job).

---

## Day 23 — Build & distribution: proving the artefacts, not the checkout

**Branch:** `feat/datheon-day-23-distribution` · **PR:** #24
**Scope:** Build the wheel and the sdist, verify what is actually inside them,
check their metadata with `twine check`, and install the wheel into a clean
virtualenv to run all five commands.

### Milestones

- Added `tools/distribution.py`, a five-step gate: build → inspect → verify →
  `twine check` → clean-venv install. Exit `0` pass, `1` artefact/install
  failure, `2` no verdict (build failed, tooling missing). Flags:
  `--report-only`, `--no-isolation`, `--skip-twine`, `--skip-install`,
  `--json`, `--dist-dir`.
- Added `tests/test_distribution.py` — 74 tests over synthetic archives built
  from the repository's own declaration.
- Shipped `datheon/py.typed` (the first defect the gate found) and listed it in
  `package-data`.
- Added a `distribution` CI job after `lint-and-test` that runs the gate and
  uploads `dist/`.
- Documented the pipeline, the check list and both defects in
  `docs/distribution.md`; updated `docs/packaging.md` to close its "no wheel or
  sdist" note.

### Architectural decisions

1. **The declaration is read, never restated.** `load_declaration()` parses
   `pyproject.toml` (`tomllib`) for the name, version, `Requires-Python`,
   `py-modules`, `console_scripts` and `package-data` globs. The gate keeps no
   private copy of "the five tools", so a rename cannot pass by being applied
   consistently in two places — there is only one place.
2. **The expected payload comes from disk, not from the declaration.**
   `load_declaration()` also walks `projects/*/data/` and every directory with
   an `__init__.py`, and the wheel must contain *each* of those files.
   Comparing the declaration with itself would be a tautology; comparing it with
   the tree is the check that catches an omission.
3. **Static checks are pure; only the install shells out.** `inspect_wheel`,
   `inspect_sdist`, `verify_wheel`, `verify_sdist` and `verify_pair` take
   archives and return `CheckResult`s, so the test suite hands them synthetic
   wheels and asserts a *named* failure per defect. `build_artifacts` and
   `twine_check` are tested by patching `subprocess.run`; the suite builds
   nothing and starts no process.
4. **The install is the only honest end-to-end test.** Installing the wheel with
   `--no-index --no-deps` into a throwaway venv and running each command from an
   unrelated working directory exercises the generated stubs, the loader's
   `sys.path` repair and the bundled datasets in the layout a user gets. Nothing
   static can prove that.
5. **A stale artefact is deleted before the build.** `build_artifacts()` empties
   `dist/` first and `collect_artifacts()` refuses a directory with two wheels,
   because "yesterday's wheel passed the checks" is the most expensive possible
   false positive in a distribution gate.

### Verification

| Check | Result |
| --- | --- |
| `python -m compileall` (projects, tests, conftest, datheon, tools) | clean |
| `flake8 .` | 0 findings over 80 files |
| `black --check .` | 80 files unchanged |
| `pytest` | 1,217 passed, 2 skipped, 0 failed |
| Assertion census | 967 test functions / 1,844 assertions (floor 200) |
| Coverage gate | 95.80% over 3,368 statements (floor 90%) |
| `tools/io_audit.py --strict` | no real I/O in the test suite |
| `python tools/distribution.py` | 34/34 checks passed |
| wheel + sdist | 200,752 B / 271,006 B, `twine check` clean |
| clean-venv install | 5 console scripts registered; `hangman … inventory --version` exit `0` |

### Defects the first run found

- **No `py.typed` in the wheel.** `Typing :: Typed` was declared and every
  public callable is annotated, but without the PEP 561 marker a type checker
  treats the installed package as untyped and ignores all of it. Added
  `datheon/py.typed` plus the `package-data` entry; the gate now fails if it
  disappears.
- **`PIP_USER=1` poisoned the install check.** The first version of
  `install_and_smoke()` inherited `os.environ`, and pip inside the fresh venv
  refused with *"Can not perform a `--user` install"* — the check was measuring
  the developer's shell, not the wheel. `install_environment()` now strips
  `PIP_USER`, `PIP_TARGET`, `PIP_PREFIX` and `PIP_ROOT` and sets
  `PYTHONNOUSERSITE=1`, so a developer machine and a runner agree.

Neither was a code bug; both were distribution bugs — precisely the class an
editable install cannot see.

### Edge cases covered by tests

- A zip that is not a wheel (no `.dist-info/METADATA`) and a wheel with metadata
  but no importable content are both rejected rather than reported empty.
- An sdist with two top-level directories, or none, or no `PKG-INFO`, is
  rejected; `tarfile`'s `KeyError` for a missing member is translated into a
  clear `ValueError`.
- A `console_scripts` entry pointing at `hangman:run` instead of `hangman:main`
  is reported per script, so the log names the command that would raise
  `ImportError` on a user's `PATH`.
- An undeclared extra command, a `package-data` glob matching nothing, and a
  version skew between the wheel and the sdist each fail on their own check.
- A build that exits `0` without writing a file, a build that hangs, and a build
  that fails are three distinct outcomes, none of them a pass.
- `install_environment()` strips the pip location overrides but leaves `PATH`
  and everything else untouched.

### Next up

- Day 24: the CI matrix — ubuntu / macos / windows × Python 3.10–3.13. The
  `distribution` job runs on `ubuntu-latest` only today.
- **Fixed in this PR:** the 3.10 CI cell was already red before Day 23 —
  `tests/test_packaging.py` imported `tomllib`, which is standard only from
  3.11, so pytest could not even collect the module. The PR's first run
  reproduced it (`ModuleNotFoundError: No module named 'tomllib'`), so the
  import now falls back to `tomli` and `requirements.txt` pins
  `tomli>=2.0; python_version < "3.11"`. Day 24's matrix will start from a
  green 3.10 cell instead of inheriting a red one.
- Day 25: MkDocs site with the API reference, deployed to GitHub Pages.
- Still open from Week 3: O1 (the five `main.py` shells at 76–89% coverage) and
  O6 (`tools/qa_report.py` as a non-blocking CI report job).
- This log is now ~134 KB in one file. Before it gets much larger it should be
  split by week, with this file kept as the index.

---

## Day 24 — CI multi-OS matrix: testing where the users actually are

**Branch:** `feat/datheon-day-24-ci-matrix` · **PR:** #25
**Scope:** Replace the single-runner pipeline with a twelve-cell matrix
(`ubuntu-latest` / `macos-latest` / `windows-latest` × Python 3.10–3.13), make
the declaration auditable, and fix what the matrix proved was broken.

### Milestones

- Rewrote `.github/workflows/ci.yml`: three runners × four interpreters,
  `fail-fast: false`, `cache: pip` on `actions/setup-python`, one `bash` shell
  for all three operating systems, and an aggregate `CI status` job so twelve
  check runs can be one required status on a protected branch.
- Split the gates by what they can prove: lint, format, `compileall` and the
  full `pytest` run in **every** cell; the I/O audit, the Hypothesis profile,
  the fuzz corpus and the 90% coverage floor on one designated cell
  (`ubuntu-latest` × 3.12), because they produce the same verdict everywhere.
- Added `tools/ci_matrix.py` — parses the workflow with `yaml.safe_load` and
  audits the matrix structurally: 15 finding codes, exit `0` pass, `1` findings,
  `2` no verdict. Flags: `--list-cells`, `--json`, `--strict`, `--require-os`,
  `--require-python`, `--workflow`, `--pyproject`, `--no-pyproject`.
- Added `tests/test_ci_matrix.py` — 69 tests, one named failure per defect.
- Added `.gitattributes` (`* text=auto eol=lf`) so the Windows cell checks out a
  tree that is byte-identical to the Linux one.
- Fixed two portability defects the matrix would have reported as unrelated
  failures (see below).
- Documented the grid, the finding codes and both defects in
  `docs/ci_matrix.md`; README now links the audit.

### Architectural decisions

1. **A matrix is a declaration, so it gets a test.** YAML does not validate
   itself, and every way this matrix can go wrong is silent: an unquoted `3.10`
   is the float `3.1` and the cell disappears; `fail-fast` defaults to `true`,
   so one red Windows cell cancels eleven green ones; a missing `cache: pip`
   costs minutes and still passes. `tools/ci_matrix.py` turns the declaration
   into a measurement, and the pytest suite asserts *named* finding codes, so a
   check cannot be deleted without a test going red.
2. **The audit cross-checks the packaging metadata, not just itself.** The
   matrix's Python versions are compared against `classifiers` and
   `requires-python` in `pyproject.toml` in both directions: a version claimed
   but never run is a lie, and a version run but never claimed is untracked.
   This is the same "compare the declaration with the tree" rule Day 23 used for
   the wheel — comparing a document with itself is a tautology.
3. **`expand_matrix()` is pure and implements real GitHub semantics.** Cartesian
   product, minus `exclude`, plus `include` entries naming both axes; an
   `include` naming one axis attaches variables and creates no runner, which is
   the detail people get wrong. Pure functions mean the tests hand it mappings
   and assert cells, with no YAML and no runner involved.
4. **`exit 2` is a verdict too.** A workflow that does not parse, a missing
   PyYAML, or a matrix that expands to zero cells returns `2`, never `0`. An
   empty grid means the audit measured nothing, and silence is not evidence.
5. **One shell everywhere, because the alternative is two workflows.** Windows
   runners default to PowerShell, where `for tool in …; do …; done` is a syntax
   error. `defaults: run: shell: bash` plus the Git Bash the Windows image
   already ships makes one file true on three systems; the console-script smoke
   test resolves each command through `shutil.which`, so `hangman` on POSIX and
   `hangman.exe` on Windows are the same line of code.
6. **Platform-dependent tests declare their platform.** The suite already had a
   `requires_permissions` marker from Day 5; Day 24 added the matching
   `requires_posix_modes`. A test that cannot mean anything on a platform should
   say so, rather than fail there and teach everyone to ignore Windows.

### The first run: 8 green cells, 4 red, and six defects

The pull request's first CI run is the interesting artefact of the day. Ubuntu
and macOS passed in all four versions. Windows failed in all four — 50 tests
each, the same 50, which is the matrix doing precisely what it was built for.
Six defects came out of it.

**Found before the run, by reading the workflow as a Windows runner would:**

- **No `.gitattributes`, so the Windows cell would see CRLF.** The
  `windows-latest` image checks out with `core.autocrlf=true`; every file
  arrives with CRLF in the working tree, `black --check .` then reports all 82
  files as unformatted and flake8 raises `W291` on lines that are clean on
  Linux. One line of `.gitattributes` (`* text=auto eol=lf`) fixes it — but
  without it the first Windows run would have looked like a formatting
  catastrophe and been "fixed" by reformatting the repository.
- **`test_atomic_write_text_preserves_the_existing_file_mode` asserted POSIX
  permission bits.** It chmods the target to `0o600` and asserts
  `stat.S_IMODE()` reports it back. Windows has no permission bits — `os.chmod`
  there only toggles the read-only flag — so the test is meaningless on the one
  platform it would have failed on. It now carries `requires_posix_modes`.

**Found by the run itself — four, and all four were real:**

1. **The Proactor event loop needs a socket, and the Day 16 guard forbids
   sockets.** Windows defaults to `ProactorEventLoop`, whose internal self-pipe
   is built from a real loopback socket pair (Python falls back to
   `socket.socketpair()` over TCP when the platform has no native one). The
   network guard from Day 16 patches every socket entry point, so *every*
   `asyncio.run()` in the suite raised `RealIOAttempted` before the first line
   of the test executed. That single cause accounted for most of the 50
   failures — the whole of `test_async_organizer.py`, the async organizer
   targets inside `test_fuzzing.py`, and the cascade of
   `RuntimeError: no running event loop` behind them. `conftest.py` now selects
   `WindowsSelectorEventLoopPolicy` on Windows: the selector loop needs no
   socket at all, and it is also the only policy there that supports
   `add_reader`.
2. **`FakeFileSystem` keyed its virtual files by `str(path)`.** The tests
   register `"/virtual/words.txt"`; the loader hands over a `Path`, whose
   `str()` is `"\\virtual\\words.txt"` on Windows, so the fake reported
   `MissingFileError` for a file it was holding in memory. Keys now go through
   `Path(path).as_posix()`, so a test may name a path one way and the loader
   resolve it another, on any platform.
3. **A text fixture wrote platform-translated newlines.** `test_read_plain_utf8`
   created its file with `write_text("hello\nworld\n")`, which writes CRLF on
   Windows, then asserted the loader returned `"hello\nworld\n"`. The loader is
   deliberately byte-faithful — it reports the newline it *found* — so the
   fixture was the thing that was wrong. It writes `b"hello\nworld\n"` now.
4. **Two assertions hard-coded POSIX path formatting.** The catalogue CLI prints
   the path it wrote, and the structured-log formatter renders a `Path` field as
   text; both were asserted against `"docs/error_catalogue.md"` and `"/tmp/x"`.
   They now compare against `str(Path(...))`, which is the same string on POSIX
   and the correct one on Windows.

Only the first was a defect in the *code*; the other five were claims the
repository made about itself that only one operating system could check. That
distinction is the whole argument for the matrix, and it took a red Windows
column to make it concrete: 1,286 passing tests on Linux had been standing in
for "it works".

### Edge cases covered by tests

- An unquoted `3.10` (a float by the time YAML is done with it) is reported as
  `matrix.unquoted-version` rather than silently expanding to `3.1`.
- An `include` naming both axes adds a cell; naming one axis adds nothing; an
  `include` repeating an existing cell does not duplicate it.
- An `exclude` entry matching nothing changes nothing; an `exclude` carrying an
  extra variable key still narrows the grid.
- `python` and `platform` are accepted as aliases for `python-version` and `os`,
  and job detection matches a matrix with only one axis so the failure is
  reported as `matrix.malformed` instead of a vague "no matrix".
- A job-level `defaults.run.shell: bash` and a single step-level `shell: bash`
  both satisfy the shell check; `pwsh` does not.
- An extra cell (`3.14`) is a *warning*: it fails `--strict` and passes
  otherwise, because running more than the contract is not a defect.
- A summary job without `if: always()`, or one that does not `needs` the matrix,
  does not count as the aggregate gate.
- A workflow that is not a mapping, is not valid YAML, or does not exist each
  return exit `2` with a distinct message.

### Verification

| Check | Result |
| --- | --- |
| `python -m compileall` (projects, tests, datheon, tools, conftest) | clean |
| `flake8 .` | 0 findings over 82 files |
| `black --check .` | 82 files unchanged |
| `pytest` | 1,286 passed, 2 skipped, 0 failed (and 12/12 green on GitHub) |
| Assertion census | 1,036 test functions / 1,964 assertions (floor 200) |
| Coverage gate | 95.8% over 3,368 statements (floor 90%) |
| `tools/io_audit.py --strict` | no real I/O in the test suite |
| `tools/ci_matrix.py` | PASS — 12 cells, 0 warnings |

### Next up

- Day 25: MkDocs (Material) site with an API reference generated from the
  docstrings, deployed to GitHub Pages and linked from the README.
- Day 26: SemVer bump, `CHANGELOG.md`, annotated tag and a GitHub Release with
  the wheel and sdist attached.
- Still open from Week 3: O1 (the five `main.py` shells at 76–89% coverage) and
  O6 (`tools/qa_report.py` as a non-blocking CI report job).
