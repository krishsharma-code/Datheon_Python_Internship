# API reference

The five tools under `projects/` are applications: their public surface is a
command line. The modules documented here are the **shared core** they are built
on, and that surface is a Python API — imported by the tools, by the tests, and
by anyone extending the repository.

Every page below is generated from the source by
[mkdocstrings](https://mkdocstrings.github.io/): the signature you see is the
signature in the file, and the description is the docstring that sits above it.
There is no second copy to fall out of date — `tools/docs_site.py` fails the
build if a module shipped in `pyproject.toml` has no page here.

## Core modules

| Module | Responsibility |
| --- | --- |
| [`errors`](errors.md) | Exception taxonomy: `DatheonError` and its per-domain subclasses |
| [`error_catalogue`](error_catalogue.md) | The user-facing catalogue generated from that taxonomy |
| [`cli_contract`](cli_contract.md) | One `argparse` surface and one exit-code contract for all five tools |
| [`validators`](validators.md) | Strict validators for symbols, quantities, prices, paths, letters, dates |
| [`edge_cases`](edge_cases.md) | Shared handling for the failure modes a shell user actually hits |
| [`dataset_loader`](dataset_loader.md) | Schema validation, encoding tolerance and malformed-row reporting |
| [`persistence`](persistence.md) | Atomic, versioned writes with read-back validation |
| [`caching`](caching.md) | Dataset and regex caches with explicit invalidation |
| [`logging_config`](logging_config.md) | `--log-level`, console and rotating file handlers |
| [`structured_logging`](structured_logging.md) | JSON records with run IDs, durations and outcomes |
| [`observability`](observability.md) | Turns those records into the Week 2 report |
| [`profiling`](profiling.md) | Deterministic `cProfile` measurement and comparison |
| [`fuzzing`](fuzzing.md) | Seeded fuzz corpus and crash verdicts |
| [`datheon`](datheon.md) | Distribution package: the five console-script shims |

## Using the core from your own code

```python
from errors import ValidationError
from validators import validate_symbol
from persistence import save_document

symbol = validate_symbol("infy")          # -> "INFY"
save_document("holdings.json", {"symbols": [symbol]}, schema_version=1)
```

Raising anything else from repository code is a defect: `errors` is the only
place an exception class is defined, and `tests/test_errors.py` asserts that
every raised error derives from `DatheonError`.

## Conventions

- **Type hints everywhere.** The package ships `py.typed`, so a type checker
  uses these annotations in an installed copy, not just in the checkout.
- **Google-style docstrings.** The handler is configured for them in
  `mkdocs.yml`, which is why `Args:` and `Returns:` render as structured blocks.
- **Public means no leading underscore.** Names starting with `_` are internal
  and are filtered out of this reference on purpose.
