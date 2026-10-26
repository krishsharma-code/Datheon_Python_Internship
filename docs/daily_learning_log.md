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
