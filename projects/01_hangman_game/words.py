"""Category-based word banks for the Hangman game.

Each category maps to a list of lowercase, alphabetic words. The
:func:`get_random_word` helper picks a secret word for a round.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional

#: Secret words grouped by theme. Keep every entry lowercase and alphabetic.
WORDS_BY_CATEGORY: Dict[str, List[str]] = {
    "programming": [
        "python",
        "variable",
        "function",
        "iterator",
        "decorator",
        "compiler",
        "algorithm",
        "recursion",
        "boolean",
        "inheritance",
    ],
    "animals": [
        "elephant",
        "giraffe",
        "penguin",
        "kangaroo",
        "dolphin",
        "cheetah",
        "octopus",
        "squirrel",
        "flamingo",
    ],
    "countries": [
        "india",
        "brazil",
        "canada",
        "germany",
        "japan",
        "australia",
        "egypt",
        "portugal",
        "thailand",
    ],
    "technology": [
        "keyboard",
        "monitor",
        "network",
        "database",
        "firewall",
        "protocol",
        "gateway",
        "processor",
        "bandwidth",
    ],
}

#: Category used when the caller does not specify one.
DEFAULT_CATEGORY: str = "programming"


def categories() -> List[str]:
    """Return the sorted list of available category names.

    Returns:
        A new list of category keys, sorted alphabetically.
    """
    return sorted(WORDS_BY_CATEGORY)


def get_random_word(
    category: str = DEFAULT_CATEGORY,
    rng: Optional[random.Random] = None,
) -> str:
    """Return a random word from ``category``.

    Args:
        category: The word-bank key to draw from. Defaults to
            :data:`DEFAULT_CATEGORY`.
        rng: An optional :class:`random.Random` instance, allowing tests to
            inject a deterministic generator. Defaults to the module-level
            :mod:`random` functions.

    Returns:
        A single lowercase word.

    Raises:
        KeyError: If ``category`` is not a known word bank.
    """
    if category not in WORDS_BY_CATEGORY:
        raise KeyError(f"Unknown category: {category!r}. Available: {', '.join(categories())}")
    chooser = rng if rng is not None else random
    return chooser.choice(WORDS_BY_CATEGORY[category])
