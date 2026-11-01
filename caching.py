"""Dataset caching and memoisation with explicit invalidation (Day 13).

Day 12 profiled the three static datasets the tools reload over and over — the
Hangman word list (8.7 ms per load), the S&P 500 constituents table (3.3 ms) and
the assistant's ``intents.json`` (8.3 ms) — and found nothing to optimise
*inside* the loaders: their cost is one file read, one decode, and one pass of
row validation. The only way to make the second load cheaper than the first is
to not do it, which is what this module provides.

The design is deliberately small and boring, because a cache that is cleverer
than its invalidation rule is a bug factory:

* :class:`FileSignature` is the freshness token — ``(path, mtime_ns, size,
  inode)`` captured with a single :func:`os.stat` call. Comparing a captured
  signature with the current one is what makes a hit *safe*: a file edited,
  replaced, truncated or re-created invalidates its own entries.
* :class:`DatasetCache` is a bounded, thread-safe, optionally time-limited
  mapping from a caller-chosen key to a stored value plus the signature it was
  read under. It is an LRU (``OrderedDict`` + ``move_to_end``), so a cache that
  fills up forgets the least recently used dataset rather than refusing to
  admit a new one.
* Explicit invalidation is part of the public API, not an afterthought:
  :meth:`DatasetCache.invalidate` drops one key,
  :meth:`DatasetCache.invalidate_path` drops every entry read from one file, and
  :meth:`DatasetCache.clear` drops everything. :func:`invalidate_path` and
  :func:`clear_all_caches` do the same across the shared caches below.
* :class:`CacheStats` records hits, misses, stores, invalidations, evictions,
  expirations and stale-signature drops, so "the cache works" is a number
  (``hit_rate``) rather than a feeling.

Three shared caches are created here and used by the tools:

===============  ===============================  ==========================
Cache            Caches                            Invalidated by
===============  ===============================  ==========================
``WORD_LIST``    :func:`words.load_words_from_file`  editing/removing ``words.txt``
``CONSTITUENTS`` :func:`portfolio.load_symbols_from_csv`  editing ``constituents.csv``
``PATTERN``      :func:`compile_pattern` (intent regexes)  :func:`clear_all_caches`
===============  ===============================  ==========================

Values are cloned on the way in and on the way out (a shallow ``list``/``dict``
copy), so a caller that mutates the list it was handed cannot corrupt what the
next caller sees.

Everything here is standard-library only. The five tools import it; it imports
nothing from them, so there is no cycle and no extra install.
"""

from __future__ import annotations

import os
import re
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Hashable, List, Optional, Tuple, TypeVar, Union

from errors import ValidationError

__all__ = [
    "CONSTITUENTS_CACHE",
    "PATTERN_CACHE",
    "WORD_LIST_CACHE",
    "CacheStats",
    "DatasetCache",
    "FileSignature",
    "cache_stats",
    "clear_all_caches",
    "compile_pattern",
    "file_signature",
    "invalidate_path",
    "path_key",
    "registered_caches",
]

#: Type of the values a :class:`DatasetCache` stores.
T = TypeVar("T")

#: Live caches, held weakly so an ad-hoc cache built by a test disappears with
#: the test instead of lingering in the registry for the life of the process.
_REGISTRY: "weakref.WeakSet[DatasetCache]" = weakref.WeakSet()


def _clone(value: Any) -> Any:
    """Return a shallow copy of a container, or ``value`` itself.

    The cached values are lists and dicts of immutable leaves (``str``,
    ``float``), so a shallow copy is enough to stop one caller's ``append`` or
    ``pop`` from being seen by the next caller.

    Args:
        value: The stored or freshly computed value.

    Returns:
        A new ``list``/``dict``/``set``/``tuple`` for those types, otherwise the
        value unchanged (patterns and scalars are immutable and shared).
    """
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, set):
        return set(value)
    if isinstance(value, tuple):
        return tuple(value)
    return value


@dataclass(frozen=True)
class FileSignature:
    """The freshness token for one file on disk.

    Attributes:
        path: Absolute path of the file.
        mtime_ns: Modification time in nanoseconds (``st_mtime_ns``).
        size: Size in bytes.
        inode: Inode number, or ``0`` on platforms that do not report one. The
            inode is what catches a file replaced by ``os.replace`` with the
            same size and a coarse timestamp.
    """

    path: str
    mtime_ns: int
    size: int
    inode: int = 0

    @classmethod
    def capture(cls, path: Union[str, Path]) -> Optional["FileSignature"]:
        """Stat ``path`` and return its signature.

        Args:
            path: The file to stat.

        Returns:
            The signature, or ``None`` when the path cannot be stat-ed (missing,
            unreadable, or not a file).
        """
        target = os.path.abspath(os.fspath(path))
        try:
            stat_result = os.stat(target)
        except OSError:
            return None
        return cls(
            path=target,
            mtime_ns=stat_result.st_mtime_ns,
            size=stat_result.st_size,
            inode=getattr(stat_result, "st_ino", 0),
        )

    def is_current(self) -> bool:
        """Return ``True`` when the file still matches this signature."""
        return FileSignature.capture(self.path) == self


def path_key(path: Union[str, Path]) -> str:
    """Return the canonical string used to key entries for one dataset file.

    :func:`os.path.abspath` normalises the path without touching the filesystem
    (unlike :meth:`pathlib.Path.resolve`, which ``lstat``s every component), so
    building a cache key costs no syscalls. Every signature is captured under
    the same normalisation, which is what makes :func:`invalidate_path` line up
    with the keys the loaders use.

    Args:
        path: The dataset path, as a string or a :class:`~pathlib.Path`.

    Returns:
        The absolute, normalised path string.
    """
    return os.path.abspath(os.fspath(path))


def file_signature(path: Union[str, Path]) -> Optional[FileSignature]:
    """Return the current :class:`FileSignature` of ``path``.

    Args:
        path: The file to stat.

    Returns:
        The signature, or ``None`` when the file does not exist.
    """
    return FileSignature.capture(path)


@dataclass(frozen=True)
class CacheStats:
    """A snapshot of one cache's counters.

    Attributes:
        name: The cache's name.
        hits: Lookups served from the cache.
        misses: Lookups that found nothing usable.
        stores: Values written into the cache.
        invalidations: Entries dropped by an explicit ``invalidate*``/``clear``.
        evictions: Entries dropped because the cache was full.
        expirations: Entries dropped because their TTL had elapsed.
        stale: Entries dropped because the file behind them had changed.
        size: Entries currently held.
        maxsize: The configured bound.
        ttl: The configured time-to-live in seconds, if any.
    """

    name: str
    hits: int = 0
    misses: int = 0
    stores: int = 0
    invalidations: int = 0
    evictions: int = 0
    expirations: int = 0
    stale: int = 0
    size: int = 0
    maxsize: int = 0
    ttl: Optional[float] = None

    @property
    def requests(self) -> int:
        """Total lookups, hits plus misses."""
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        """Fraction of lookups served from the cache, in ``[0.0, 1.0]``.

        A cache that has never been asked anything reports ``0.0`` rather than
        raising, so ``stats.hit_rate`` is always safe to print.
        """
        requests = self.requests
        return (self.hits / requests) if requests else 0.0

    def as_dict(self) -> Dict[str, Any]:
        """Return the snapshot as a JSON-serialisable dictionary."""
        return {
            "name": self.name,
            "hits": self.hits,
            "misses": self.misses,
            "requests": self.requests,
            "hit_rate": round(self.hit_rate, 4),
            "stores": self.stores,
            "invalidations": self.invalidations,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "stale": self.stale,
            "size": self.size,
            "maxsize": self.maxsize,
            "ttl": self.ttl,
        }

    def describe(self) -> str:
        """Return a one-line human summary of the counters."""
        return (
            f"{self.name}: {self.hits} hit(s) / {self.misses} miss(es) "
            f"({self.hit_rate:.0%} hit rate), {self.size}/{self.maxsize} entries"
        )


@dataclass
class _Entry:
    """One cached value plus what is needed to judge its freshness."""

    value: Any
    signature: Optional[FileSignature]
    stored_at: float


class DatasetCache:
    """A bounded, thread-safe cache of dataset values with explicit invalidation.

    Args:
        name: A short name used in :meth:`stats` and in the module registry.
        maxsize: Maximum number of entries held; the least recently used entry
            is evicted when the limit is reached. Defaults to ``32``.
        ttl: Optional time-to-live in seconds. When set, an entry older than
            ``ttl`` is treated as a miss. Defaults to ``None`` (no time limit —
            freshness comes from the file signature).

    Raises:
        ValidationError: If ``name`` is empty, ``maxsize`` is below 1, or
            ``ttl`` is not positive. Also a :class:`ValueError`.

    Example:
        >>> cache = DatasetCache("demo", maxsize=2)
        >>> cache.put("k", [1, 2])
        >>> cache.get("k")
        [1, 2]
        >>> cache.invalidate("k")
        True
        >>> cache.get("k") is None
        True
    """

    def __init__(
        self,
        name: str,
        *,
        maxsize: int = 32,
        ttl: Optional[float] = None,
    ) -> None:
        if not str(name).strip():
            raise ValidationError(
                "A cache needs a non-empty name.",
                hint="pass a short label such as 'word_list'",
                context={"field": "name"},
            )
        if maxsize < 1:
            raise ValidationError(
                f"maxsize must be at least 1, got {maxsize}.",
                hint="use a positive entry limit",
                context={"field": "maxsize", "value": maxsize},
            )
        if ttl is not None and ttl <= 0:
            raise ValidationError(
                f"ttl must be positive, got {ttl}.",
                hint="use a positive number of seconds, or None for no time limit",
                context={"field": "ttl", "value": ttl},
            )
        self.name = str(name)
        self.maxsize = int(maxsize)
        self.ttl = ttl
        self._entries: "OrderedDict[Hashable, _Entry]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._stores = 0
        self._invalidations = 0
        self._evictions = 0
        self._expirations = 0
        self._stale = 0
        _REGISTRY.add(self)

    # -- lookup ---------------------------------------------------------

    def lookup(
        self,
        key: Hashable,
        signature: Optional[FileSignature] = None,
    ) -> Tuple[bool, Any]:
        """Look ``key`` up and report whether the entry was usable.

        Unlike :meth:`get`, this distinguishes "not cached" from "cached value is
        ``None``", which :meth:`load` needs so that a loader legitimately
        returning ``None`` is not re-run on every call.

        Args:
            key: The cache key.
            signature: When given, an entry read under a different signature is
                dropped and treated as a miss.

        Returns:
            ``(True, value)`` on a hit, ``(False, None)`` on a miss.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return False, None
            if self._is_expired(entry):
                del self._entries[key]
                self._expirations += 1
                self._misses += 1
                return False, None
            if (
                signature is not None
                and entry.signature is not None
                and entry.signature != signature
            ):
                del self._entries[key]
                self._stale += 1
                self._misses += 1
                return False, None
            self._entries.move_to_end(key)
            self._hits += 1
            return True, _clone(entry.value)

    def get(
        self,
        key: Hashable,
        signature: Optional[FileSignature] = None,
    ) -> Optional[T]:
        """Return the cached value for ``key``, or ``None`` on a miss.

        Args:
            key: The cache key.
            signature: Optional freshness token (see :meth:`lookup`).

        Returns:
            A copy of the cached value, or ``None``.
        """
        hit, value = self.lookup(key, signature)
        return value if hit else None

    def put(
        self,
        key: Hashable,
        value: Any,
        signature: Optional[FileSignature] = None,
    ) -> None:
        """Store ``value`` under ``key``, evicting the oldest entry if full.

        Args:
            key: The cache key.
            value: The value to store; containers are shallow-copied so the
                caller keeps ownership of what it passed.
            signature: The file signature the value was read under, if any.
        """
        with self._lock:
            self._entries[key] = _Entry(_clone(value), signature, time.monotonic())
            self._entries.move_to_end(key)
            self._stores += 1
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
                self._evictions += 1

    def load(
        self,
        key: Hashable,
        signature: Optional[FileSignature],
        loader: Callable[[], T],
    ) -> T:
        """Return the cached value, computing it with ``loader`` on a miss.

        The loader runs **outside** the lock, so a slow parse does not block
        other threads reading unrelated keys. Two threads racing on the same
        cold key may both call ``loader``; the last write wins and both callers
        receive a correct value.

        Args:
            key: The cache key.
            signature: The file signature ``loader`` will read under.
            loader: Zero-argument callable that produces the value.

        Returns:
            The cached or freshly computed value.
        """
        hit, value = self.lookup(key, signature)
        if hit:
            return value
        fresh = loader()
        self.put(key, fresh, signature)
        return _clone(fresh)

    # -- invalidation ---------------------------------------------------

    def invalidate(self, key: Hashable) -> bool:
        """Drop one entry.

        Args:
            key: The key to drop.

        Returns:
            ``True`` if an entry was present and removed.
        """
        with self._lock:
            if key not in self._entries:
                return False
            del self._entries[key]
            self._invalidations += 1
            return True

    def invalidate_path(self, path: Union[str, Path]) -> int:
        """Drop every entry that was read from ``path``.

        Args:
            path: The dataset file whose entries should go.

        Returns:
            How many entries were removed.
        """
        target = os.path.abspath(os.fspath(path))
        with self._lock:
            doomed = [
                key
                for key, entry in self._entries.items()
                if entry.signature is not None and entry.signature.path == target
            ]
            for key in doomed:
                del self._entries[key]
                self._invalidations += 1
            return len(doomed)

    def clear(self) -> int:
        """Drop every entry, keeping the counters for post-mortem inspection.

        Returns:
            How many entries were removed.
        """
        with self._lock:
            removed = len(self._entries)
            self._entries.clear()
            self._invalidations += removed
            return removed

    def reset_stats(self) -> None:
        """Zero every counter without touching the cached values."""
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._stores = 0
            self._invalidations = 0
            self._evictions = 0
            self._expirations = 0
            self._stale = 0

    # -- introspection --------------------------------------------------

    def _is_expired(self, entry: _Entry) -> bool:
        """Return ``True`` when ``entry`` has outlived this cache's TTL."""
        if self.ttl is None:
            return False
        return (time.monotonic() - entry.stored_at) > self.ttl

    def keys(self) -> List[Hashable]:
        """Return the cached keys, least recently used first."""
        with self._lock:
            return list(self._entries)

    def stats(self) -> CacheStats:
        """Return a snapshot of this cache's counters and size."""
        with self._lock:
            return CacheStats(
                name=self.name,
                hits=self._hits,
                misses=self._misses,
                stores=self._stores,
                invalidations=self._invalidations,
                evictions=self._evictions,
                expirations=self._expirations,
                stale=self._stale,
                size=len(self._entries),
                maxsize=self.maxsize,
                ttl=self.ttl,
            )

    def __len__(self) -> int:
        """Number of entries currently held."""
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: object) -> bool:
        """Whether ``key`` is present (freshness is *not* checked)."""
        with self._lock:
            return key in self._entries

    def __repr__(self) -> str:
        """Developer-facing representation."""
        return f"DatasetCache(name={self.name!r}, maxsize={self.maxsize}, size={len(self)})"


#: Cached ``words.load_words_from_file`` results, keyed by
#: ``(path, min_length, max_length, strict)``.
WORD_LIST_CACHE: DatasetCache = DatasetCache("word_list", maxsize=16)

#: Cached ``portfolio.load_symbols_from_csv`` results, keyed by
#: ``(path, strict)``.
CONSTITUENTS_CACHE: DatasetCache = DatasetCache("constituents", maxsize=16)

#: Compiled intent regular expressions, keyed by ``(pattern, flags)``.
PATTERN_CACHE: DatasetCache = DatasetCache("intent_patterns", maxsize=512)


def compile_pattern(pattern: str, flags: int = re.IGNORECASE) -> "re.Pattern[str]":
    """Compile ``pattern`` once and reuse it.

    :mod:`re` already memoises the last few hundred patterns internally, but its
    cache is invisible and cannot be cleared or measured. Routing intent
    compilation through :data:`PATTERN_CACHE` makes both possible: the same
    pattern compiled by two intents is compiled once, a miss is a number in
    :func:`cache_stats`, and :func:`clear_all_caches` empties it on demand.

    Args:
        pattern: The regular-expression source.
        flags: Compilation flags. Defaults to :data:`re.IGNORECASE`, matching
            the intent matcher's case-insensitive contract.

    Returns:
        The compiled pattern (a shared, immutable object).

    Raises:
        re.error: If ``pattern`` is not a valid regular expression.
    """
    key = (pattern, flags)
    hit, cached = PATTERN_CACHE.lookup(key)
    if hit:
        return cached
    compiled = re.compile(pattern, flags)
    PATTERN_CACHE.put(key, compiled)
    return compiled


def registered_caches() -> List[DatasetCache]:
    """Return every live cache, sorted by name."""
    return sorted(_REGISTRY, key=lambda cache: cache.name)


def cache_stats() -> Dict[str, Dict[str, Any]]:
    """Return the counters of every live cache, keyed by cache name."""
    return {cache.name: cache.stats().as_dict() for cache in registered_caches()}


def clear_all_caches() -> int:
    """Empty every live cache.

    Returns:
        The total number of entries removed.
    """
    return sum(cache.clear() for cache in registered_caches())


def invalidate_path(path: Union[str, Path]) -> int:
    """Drop, from every live cache, the entries read from ``path``.

    This is the call to make after a tool rewrites a dataset it also reads —
    for example a test that patches ``words.txt`` in place.

    Args:
        path: The dataset file whose cached entries should go.

    Returns:
        The total number of entries removed.
    """
    return sum(cache.invalidate_path(path) for cache in registered_caches())
