# `projects`

The five tools, in task-pack order.

The directories are numbered (`01_hangman_game` … `05_inventory_oop`) because the
task pack is numbered, and a numeric prefix is not a valid Python identifier —
which is exactly why `datheon` exists: it puts these directories on `sys.path`
and imports the plain module names inside them.

Each project follows the same shape: a pure, UI-independent core module
(`hangman.py`, `portfolio.py`, `organizer.py`, `chatbot.py`, `inventory.py`)
plus a thin interactive `main.py`. The core is what the tests import; `main.py`
is what a shell user runs.

| Directory | Command | Core module |
| --- | --- | --- |
| `01_hangman_game` | `hangman` | `hangman.py` |
| `02_portfolio_tracker` | `portfolio` | `portfolio.py` |
| `03_file_organizer_automation` | `organizer` | `organizer.py`, `async_organizer.py` |
| `04_terminal_assistant` | `assistant` | `chatbot.py` |
| `05_inventory_oop` | `inventory` | `models.py`, `inventory.py` |

`projects` itself is a namespace with no behaviour of its own beyond that
`sys.path` note; the reference below is deliberately short.

::: projects
