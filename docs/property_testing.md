# Property-based testing with Hypothesis (Day 17)

> **Rule:** a rule that must hold for *every* input is stated as a property and
> checked against generated inputs, not illustrated with three hand-picked
> examples.

Days 1–16 built a suite of examples: a bad symbol, a BOM-prefixed file, a CRLF
file, an over-long name, an empty directory. Examples are precise and readable,
but they only prove that the cases we *thought of* work. This document records
what Day 17 adds, why each property was chosen, and how the suite stays
reproducible.

---

## 1. Why properties

The validators, the dataset loader and the assistant's text pipeline all take
*unbounded* input: whatever a user types, whatever a file contains. The example
suite covered roughly thirty representative values per function. Hypothesis
covers fifty (locally) to a thousand (nightly) per property, and it does not
sample uniformly — it shrinks any counter-example to the smallest input that
still fails, so a bug report arrives as `literal='-1'` rather than a 200-character
string of noise.

Three classes of defect are only reachable this way:

| Defect class | Example caught by a property, not an example |
| --- | --- |
| Boundary off-by-one | `MAX_PRICE` itself is accepted; one ulp above is rejected |
| Unintended exception type | A validator raising `ValueError`/`AttributeError` instead of `ValidationError` |
| Asymmetry between modes | Strict mode accepting something lenient mode rejects |

The second is the one that matters most in this repository: `cli_contract.run_cli`
maps `ValidationError` onto exit code `1`. Any other exception escapes as an
unhandled traceback and exit code `1`-with-a-stack-trace, which is exactly what
Day 5 spent a branch eliminating.

---

## 2. Where the code lives

| File | Role |
| --- | --- |
| `tests/strategies.py` | The generators, the Hypothesis profiles and two fixture writers (`write_dataset`, `write_document`) |
| `tests/test_properties.py` | 31 property tests over the validators, the loader and the chatbot pipeline |

The strategies are expressed in terms of production constants rather than
duplicated literals:

```python
SYMBOL_REGEX = SYMBOL_PATTERN.pattern          # from validators
price_values = st.floats(min_value=0.0, max_value=MAX_PRICE, ...)
symbol_texts = st.from_regex(SYMBOL_REGEX, fullmatch=True).filter(
    lambda value: len(value) <= MAX_SYMBOL_LENGTH
)
```

Changing `MAX_SYMBOL_LENGTH` or the ticker shape therefore changes the generator
with it — the domain cannot silently drift away from the rule it is testing.

---

## 3. The properties

### 3.1 Validators

| Property | Statement |
| --- | --- |
| Round trip | A generated ticker is returned upper-cased and still matches `SYMBOL_PATTERN` |
| Normalisation | Lenient mode trims and upper-cases any non-blank text instead of rejecting it |
| Subset | Whenever strict mode accepts a value, lenient mode returns the same value |
| Blank rejection | Whitespace-only input fails every text validator *with a hint attached* |
| Inclusivity | `minimum == maximum == value` accepts `value`; either bound shifted by one rejects it |
| Sign | A negative price or quantity is a data error in both modes |
| Plain literals | Anything strict mode accepts is a plain decimal/exponent literal |
| Length budgets | Names are accepted exactly when non-blank and ≤ 60 chars; messages when ≤ 500 |
| Dates | Past dates round-trip through `date` and ISO text; far-future dates need `allow_future=True` |
| Exception hygiene | Arbitrary text input either validates or raises `ValidationError` — never another type |

### 3.2 Dataset loader

| Property | Statement |
| --- | --- |
| Lossless decoding | A UTF-8 file decodes back to its own text, minus at most one leading BOM |
| BOM handling | `strip_bom` removes at most one marker, never touches the rest, and is idempotent |
| Newline detection | Every constructed style (`lf`/`crlf`/`cr`) is reported as itself; a mixed document is `mixed` |
| Windows round trip | BOM + CRLF input loads with `had_bom=True`, `newline="crlf"` and the text intact |
| Encoding chain | Latin-1/cp1252 bytes decode to the original characters, and the reported encoding is in `DEFAULT_ENCODINGS` |
| Column matching | A header is found by its lower-cased, stripped spelling and returned *as written* |
| Missing columns | A candidate that is not present raises `DatasetFormatError` with a hint |

### 3.3 Assistant text pipeline

| Property | Statement |
| --- | --- |
| Keyword definition | Extraction equals the reference implementation: first-seen order, de-duplicated, ≥ `min_length`, stop words dropped |
| Shape | Every keyword is lower-case `[a-z0-9]+` and a subsequence of the source tokens |
| Monotonicity | Raising the length floor only ever removes keywords — the result stays a subset |
| Normalisation | `normalize` is idempotent, single-spaced and lower-case |

---

## 4. Reproducibility: profiles and seeds

Hypothesis generates from a random seed by default, which is good for finding
bugs and bad for reproducing them. Three profiles are registered in
`tests/strategies.py` and selected with the `HYPOTHESIS_PROFILE` variable:

| Profile | Examples | Seed | Used by |
| --- | --- | --- | --- |
| `dev` (default) | 50 | random | local `pytest` |
| `ci` | 150 | fixed (`derandomize=True`) | GitHub Actions |
| `nightly` | 1,000 | fixed | soak runs before a release |

CI sets `HYPOTHESIS_PROFILE=ci` on a dedicated step, so the same commit always
explores the same 150 cases per property. A red CI run is reproducible with:

```bash
HYPOTHESIS_PROFILE=ci pytest -q tests/test_properties.py
```

Two settings are shared by every profile:

- **`deadline=None`** — the suite writes real files into `tmp_path`; a
  per-example wall-clock budget would make the verdict depend on machine load.
- **`suppress_health_check=[too_slow, function_scoped_fixture]`** — `tmp_path` is
  function-scoped by pytest's design, and the Day 16 autouse isolation fixtures
  are function-scoped too, so the health check would fire on every test for a
  reason that is not a defect.

---

## 5. How this fits the isolation policy

The Day 16 rule still holds: no property test reads the network, the wall clock,
or anything outside the pytest sandbox. Two details make that possible:

1. **Dates are generated, not read.** `past_dates()` draws from 1990–2020 and
   `future_dates()` from 2090–2999, so no test calls `date.today()` — the
   `tools/io_audit.py --strict` gate would flag it if one did.
2. **Files are written through the fixture writers.** `write_dataset` takes the
   exact text plus an encoding and an optional BOM, so a round-trip assertion
   compares the loader against the same bytes a real fixture would hold — with
   no newline translation hidden in the helper.

---

## 6. Complexity

- Strategy construction is O(1) per test; Hypothesis caches the generator.
- Each property test is O(examples × cost of one call). The loader properties are
  the expensive ones — one `write_bytes` + one `read_bytes` per example, so
  roughly 150 × 2 syscalls in CI, which is milliseconds.
- The whole property module runs in well under two seconds on the `ci` profile;
  it adds no measurable time to the suite as a whole.
- Shrinking is O(log n) in the failing input's size — the reason a failure is
  reported as `-1` rather than as the first random value that broke.

---

## 7. What the properties found

Nothing catastrophic — which is the honest result, and worth recording. The
suite did surface two domain mistakes in the *tests* rather than the code:

1. The first draft of the price property generated negative literals and fed
   them to `validate_price`, which correctly rejected them. The strategy, not
   the validator, was wrong: `plain_number_texts()` is now explicitly
   non-negative.
2. The first draft of the "existing file" property built paths of random depth,
   so one example could create `tmp/a` as a file and the next need `tmp/a` as a
   directory. The property now uses a fixed parent directory.

Both are the kind of mistake a property suite makes loud and an example suite
hides: the generator is part of the specification, and a wrong generator
produces a wrong test.

---

## 8. Next up

Day 18 stops generating *valid* input and starts generating hostile input:
random and malformed bytes into the five CLIs, the three loaders and the
chatbot, asserting zero unhandled exceptions across the corpus.
