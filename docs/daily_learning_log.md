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
