# `cli_contract`

One `argparse` surface for all five tools — `--version`, the documented exit codes and consistent help text. Every `main.py` under `projects/` builds its parser through `build_parser`, so the five tools behave alike from a shell.

::: cli_contract
