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
