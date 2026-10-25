"""Word banks for the Hangman game.

Two sources of secret words are supported:

* :data:`WORDS_BY_CATEGORY` — small, hand-curated themed banks used by
  default, and
* an external newline-delimited word list (for example the official Datheon
  ``google-10000-english`` list) loaded with :func:`load_words_from_file`.

:func:`get_random_word` and :func:`get_random_word_from_list` pick a secret
word for a round.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

from dataset_loader import LoadReport, read_text_robust
from edge_cases import check_file_size

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


#: Words shorter than this are skipped when loading an external word list.
MIN_WORD_LENGTH: int = 4


def load_words_from_file(
    path: Union[str, Path],
    min_length: int = MIN_WORD_LENGTH,
    max_length: Optional[int] = None,
    *,
    strict: bool = False,
    report: Optional[LoadReport] = None,
) -> List[str]:
    """Load and sanitise a newline-delimited word list from a text file.

    Each line is stripped, lower-cased, and kept only if it is purely
    alphabetic and its length falls within ``[min_length, max_length]``.
    Duplicates are removed while preserving first-seen order. The file is read
    through :func:`dataset_loader.read_text_robust`, so a UTF-8 BOM, CRLF line
    endings, and legacy 8-bit encodings are all tolerated.

    A line that is not a single alphabetic word is a *malformed row*: in
    lenient mode it is skipped and recorded as a warning (when ``report`` is
    given); in strict mode it is recorded as an error and, once loading
    finishes, raises :class:`~dataset_loader.DatasetFormatError` naming the
    offending line numbers.

    Args:
        path: Path to the word list (one word per line).
        min_length: Minimum accepted word length. Defaults to
            :data:`MIN_WORD_LENGTH`.
        max_length: Optional maximum accepted word length.
        strict: When ``True``, reject a file containing any malformed row.
        report: Optional :class:`~dataset_loader.LoadReport` to populate with
            the file's encoding metadata and any malformed-row issues.

    Returns:
        A list of unique, lower-case, alphabetic words.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        PermissionError: If ``path`` cannot be read.
        ValueError: If ``min_length`` is less than 1, exceeds ``max_length``,
            or ``path`` is larger than
            :data:`edge_cases.MAX_INPUT_FILE_BYTES`.
        dataset_loader.DatasetFormatError: In strict mode, if any line is not
            a single alphabetic word.
    """
    if min_length < 1:
        raise ValueError("min_length must be at least 1.")
    if max_length is not None and max_length < min_length:
        raise ValueError("max_length must not be smaller than min_length.")

    file_path = Path(path)
    check_file_size(file_path, field="word list")
    source = read_text_robust(file_path, field="word list")
    local = report if report is not None else LoadReport(file_path)
    local.source = source

    words: List[str] = []
    seen: set = set()
    for line_no, line in enumerate(source.text.splitlines(), start=1):
        local.rows_checked += 1
        word = line.strip().lower()
        if not word:
            continue
        if not word.isalpha():
            local.add(
                f"non-alphabetic entry {word!r}",
                line=line_no,
                raw=line,
                severity="error" if strict else "warning",
            )
            continue
        if len(word) < min_length:
            continue
        if max_length is not None and len(word) > max_length:
            continue
        if word in seen:
            continue
        seen.add(word)
        words.append(word)
    local.rows_accepted = len(words)
    local.raise_if_errors("word list")
    return words


def get_random_word_from_list(
    words: Sequence[str],
    rng: Optional[random.Random] = None,
) -> str:
    """Return a random word from an explicit sequence.

    Args:
        words: The pool of candidate words.
        rng: Optional deterministic generator for tests.

    Returns:
        A randomly chosen word.

    Raises:
        ValueError: If ``words`` is empty.
    """
    if not words:
        raise ValueError("Cannot pick a word from an empty list.")
    chooser = rng if rng is not None else random
    return chooser.choice(list(words))
