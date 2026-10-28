"""Rule-based interactive terminal assistant.

The assistant recognises intent from free-text input using regular-expression
patterns, scores each candidate intent, and returns a contextual response with
a confidence value. Unrecognised input falls back gracefully. Every exchange
can be appended to a persistent JSONL conversation log.

Typical use::

    from chatbot import IntentEngine, ConversationLogger

    engine = IntentEngine.from_json("intents.json")
    reply = engine.respond("what are your hours?")
    print(reply.text, reply.confidence)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

from dataset_loader import DatasetFormatError, LoadReport, TextSource, read_text_robust
from edge_cases import check_file_size
from errors import ValidationError
from persistence import SCHEMA_VERSION, SCHEMA_VERSION_FIELD, atomic_append_text

#: Inputs that end the session.
DEFAULT_EXIT_COMMANDS = ("bye", "exit", "quit", "goodbye", "stop")

#: Words ignored when deriving intent keywords from a FAQ question.
STOPWORDS = frozenset(
    {
        "a",
        "about",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "get",
        "give",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "me",
        "might",
        "must",
        "my",
        "need",
        "of",
        "on",
        "or",
        "our",
        "ours",
        "over",
        "please",
        "she",
        "should",
        "so",
        "some",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "us",
        "want",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
    }
)


def _keywords(text: str, min_length: int = 4) -> List[str]:
    """Return the significant keywords in ``text``.

    Lower-cases the input, extracts alphanumeric tokens, and drops tokens that
    are shorter than ``min_length`` or that appear in :data:`STOPWORDS`.

    Args:
        text: The source text (typically an FAQ question).
        min_length: Minimum keyword length.

    Returns:
        A list of keywords in first-seen order, de-duplicated.
    """
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    seen: set = set()
    keywords: List[str] = []
    for token in tokens:
        if len(token) < min_length or token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords


def normalize(text: str) -> str:
    """Normalise raw user input for matching.

    Lower-cases the text, strips surrounding whitespace, and collapses internal
    runs of whitespace to a single space.

    Args:
        text: Raw user input.

    Returns:
        The normalised string (empty string if ``text`` is falsy).
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass(frozen=True)
class Response:
    """The assistant's reply to a single user turn.

    Attributes:
        intent: The matched intent name (``"fallback"`` if none matched).
        confidence: Match confidence in the range 0.0–1.0.
        text: The response text to show the user.
        is_exit: ``True`` when the user asked to end the session.
    """

    intent: str
    confidence: float
    text: str
    is_exit: bool = False


@dataclass
class Intent:
    """A single recognisable intent.

    Attributes:
        name: Unique intent identifier.
        patterns: Compiled regular expressions that signal this intent.
        responses: Candidate responses; one is chosen at reply time.
        priority: Tie-breaker when two intents score equally (higher wins).
    """

    name: str
    patterns: List[re.Pattern] = field(default_factory=list)
    responses: List[str] = field(default_factory=list)
    priority: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Intent":
        """Build an :class:`Intent` from a JSON-style dictionary.

        Args:
            data: Dictionary with ``name``, ``patterns``, and optional
                ``responses`` and ``priority`` keys.

        Returns:
            The constructed intent.

        Raises:
            ValueError: If ``name`` or ``patterns`` is missing or malformed.
        """
        name = data.get("name")
        raw_patterns = data.get("patterns")
        if not name or not isinstance(raw_patterns, list) or not raw_patterns:
            raise ValidationError(
                f"Intent requires a name and non-empty patterns: {data!r}",
                hint="each intent needs a 'name' string and a non-empty 'patterns' list",
                context={"field": "patterns"},
            )
        compiled = [re.compile(p, re.IGNORECASE) for p in raw_patterns]
        responses = data.get("responses") or [f"[{name}]"]
        if not isinstance(responses, list):
            raise ValidationError(
                f"Intent {name!r} responses must be a list.",
                hint="set 'responses' to a list of strings",
                context={"field": "responses", "intent": str(name)},
            )
        return cls(
            name=str(name),
            patterns=compiled,
            responses=[str(r) for r in responses],
            priority=int(data.get("priority", 0)),
        )

    def score(self, normalized_text: str) -> int:
        """Count how many of this intent's patterns match the input."""
        return sum(1 for pattern in self.patterns if pattern.search(normalized_text))


def _load_json_object(source: TextSource, path: Path) -> Dict[str, object]:
    """Parse a dataset file as a JSON object, with a clear failure message.

    Args:
        source: The decoded dataset file.
        path: The originating path, used in error messages.

    Returns:
        The parsed top-level JSON object.

    Raises:
        DatasetFormatError: If the text is not valid JSON, or the top level is
            not a JSON object.
    """
    try:
        data = json.loads(source.text)
    except json.JSONDecodeError as exc:
        raise DatasetFormatError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DatasetFormatError(
            f"{path}: expected a JSON object at the top level, got {type(data).__name__}"
        )
    return data


def _parse_intents(entries: Sequence[object], report: LoadReport) -> List["Intent"]:
    """Build :class:`Intent` objects from raw JSON entries, reporting bad ones.

    Each entry is validated in turn; a malformed entry is recorded as an error
    (with its ``intents[i]`` index) instead of aborting on the first one, so a
    single run surfaces *every* problem in the dataset.

    Args:
        entries: The raw ``intents`` list from the JSON file.
        report: The report to populate with per-entry issues.

    Returns:
        The intents that were successfully built.
    """
    intents: List[Intent] = []
    for index, entry in enumerate(entries):
        where = f"intents[{index}]"
        report.rows_checked += 1
        if not isinstance(entry, dict):
            report.add(f"{where}: expected an object, got {type(entry).__name__}", severity="error")
            continue
        try:
            intents.append(Intent.from_dict(entry))
        except ValueError as exc:
            report.add(f"{where}: {exc}", severity="error")
            continue
        report.rows_accepted += 1
    return intents


class IntentEngine:
    """Matches normalised input against a set of intents.

    Args:
        intents: The intents to match against.
        fallbacks: Responses used when nothing matches.
        exit_commands: Normalised strings that end the session.

    Raises:
        ValueError: If ``intents`` is empty.
    """

    def __init__(
        self,
        intents: Sequence[Intent],
        fallbacks: Optional[Sequence[str]] = None,
        exit_commands: Optional[Sequence[str]] = None,
    ) -> None:
        if not intents:
            raise ValidationError(
                "IntentEngine requires at least one intent.",
                hint="load intents.json, or pass at least one Intent",
                context={"field": "intents"},
            )
        self.intents: List[Intent] = list(intents)
        self.fallbacks: List[str] = list(
            fallbacks or ["I'm not sure I understood that. Could you rephrase?"]
        )
        self.exit_commands = {normalize(c) for c in (exit_commands or DEFAULT_EXIT_COMMANDS)}
        self._fallback_index = 0
        self._response_index: Dict[str, int] = {}

    @classmethod
    def from_json(
        cls,
        path: Union[str, Path],
        *,
        strict: bool = False,
        report: Optional[LoadReport] = None,
    ) -> "IntentEngine":
        """Load an engine from a JSON dataset.

        The file is decoded through :func:`dataset_loader.read_text_robust`, so
        a UTF-8 BOM and legacy encodings are tolerated. Schema validation
        requires a JSON object at the top level with a non-empty ``intents``
        list; each entry must be an object that yields a valid
        :class:`Intent`. A malformed entry is reported with its ``intents[i]``
        index and aborts the load.

        Args:
            path: Path to a JSON file with ``intents``, optional ``fallbacks``
                and ``exit_commands`` keys.
            strict: Accepted for API symmetry; intent entries are always
                validated as errors.
            report: Optional :class:`~dataset_loader.LoadReport` to populate
                with the file's metadata and any per-entry issues.

        Returns:
            A configured :class:`IntentEngine`.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
            PermissionError: If ``path`` cannot be read.
            ValueError: If the file is not valid JSON, is not a JSON object,
                lacks intents, has a malformed intent entry, or exceeds
                :data:`edge_cases.MAX_INPUT_FILE_BYTES`.
        """
        json_path = Path(path)
        check_file_size(json_path, field="intent file")
        source = read_text_robust(json_path, field="intent file")
        local = report if report is not None else LoadReport(json_path)
        local.source = source
        data = _load_json_object(source, json_path)

        raw_intents = data.get("intents")
        if not isinstance(raw_intents, list) or not raw_intents:
            raise DatasetFormatError(f"no 'intents' list found in {json_path}.")
        intents = _parse_intents(raw_intents, local)
        local.raise_if_errors("intent file")
        return cls(
            intents,
            fallbacks=data.get("fallbacks"),
            exit_commands=data.get("exit_commands"),
        )

    @classmethod
    def from_faq_json(
        cls,
        path: Union[str, Path],
        min_keyword_length: int = 4,
        *,
        strict: bool = False,
        report: Optional[LoadReport] = None,
    ) -> "IntentEngine":
        """Build an engine from a FAQ dataset (as supplied by Datheon).

        The dataset is expected to contain a ``faqs`` list of
        ``{"question": ..., "answer": ...}`` objects and, optionally, a
        ``greetings`` list. Each FAQ becomes an intent whose patterns are the
        significant keywords of its question. The file is decoded through
        :func:`dataset_loader.read_text_robust` (BOM / encoding tolerant).

        An unusable FAQ entry is a *malformed row*: it is skipped and recorded
        as a warning (escalated to an error under ``strict``), always with its
        ``faqs[i]`` index.

        Args:
            path: Path to the FAQ JSON file.
            min_keyword_length: Minimum length of a derived keyword.
            strict: When ``True``, reject a dataset containing any malformed
                FAQ entry.
            report: Optional :class:`~dataset_loader.LoadReport` to populate
                with the file's metadata and any per-entry issues.

        Returns:
            A configured :class:`IntentEngine`.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
            PermissionError: If ``path`` cannot be read.
            ValueError: If the file is not valid JSON, is not a JSON object,
                has no usable FAQs, or exceeds
                :data:`edge_cases.MAX_INPUT_FILE_BYTES`.
        """
        json_path = Path(path)
        check_file_size(json_path, field="FAQ file")
        source = read_text_robust(json_path, field="FAQ file")
        local = report if report is not None else LoadReport(json_path)
        local.source = source
        data = _load_json_object(source, json_path)

        faqs = data.get("faqs")
        if not isinstance(faqs, list) or not faqs:
            raise DatasetFormatError(f"no 'faqs' list found in {json_path}.")

        intents: List[Intent] = []

        greetings = [g for g in (data.get("greetings") or []) if str(g).strip()]
        if greetings:
            greeting_pattern = (
                r"\b(" + "|".join(re.escape(str(g).strip().lower()) for g in greetings) + r")\b"
            )
            intents.append(
                Intent(
                    name="greeting",
                    patterns=[re.compile(greeting_pattern, re.IGNORECASE)],
                    responses=["Hello! How can I help you today?"],
                    priority=1,
                )
            )

        severity = "error" if strict else "warning"
        for index, entry in enumerate(faqs):
            where = f"faqs[{index}]"
            local.rows_checked += 1
            if not isinstance(entry, dict):
                local.add(
                    f"{where}: expected an object, got {type(entry).__name__}",
                    severity=severity,
                )
                continue
            question = str(entry.get("question", "")).strip()
            answer = str(entry.get("answer", "")).strip()
            if not question or not answer:
                local.add(f"{where}: missing question or answer", severity=severity)
                continue
            keywords = _keywords(question, min_keyword_length)
            if not keywords:
                local.add(f"{where}: no usable keywords in question", severity=severity)
                continue
            pattern = r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b"
            intents.append(
                Intent(
                    name=f"faq_{index + 1}",
                    patterns=[re.compile(pattern, re.IGNORECASE)],
                    responses=[answer],
                    priority=2,
                )
            )
            local.rows_accepted += 1

        local.raise_if_errors("FAQ file")
        if not intents:
            raise DatasetFormatError(f"no usable FAQ entries found in {json_path}.")
        return cls(intents)

    def _next_response(self, intent: Intent) -> str:
        """Rotate through an intent's responses so repeats feel natural."""
        if not intent.responses:
            return f"[{intent.name}]"
        index = self._response_index.get(intent.name, 0)
        self._response_index[intent.name] = index + 1
        return intent.responses[index % len(intent.responses)]

    def _next_fallback(self) -> str:
        """Rotate through the fallback responses."""
        text = self.fallbacks[self._fallback_index % len(self.fallbacks)]
        self._fallback_index += 1
        return text

    @staticmethod
    def _confidence(score: int) -> float:
        """Map a raw pattern-match count to a 0.0–1.0 confidence value."""
        if score <= 0:
            return 0.0
        return round(min(1.0, 0.5 + 0.25 * (score - 1)), 2)

    def respond(self, text: str) -> Response:
        """Produce a response to a user turn.

        Args:
            text: Raw user input.

        Returns:
            A :class:`Response`. The ``intent`` is ``"exit"`` when the user
            asked to stop, and ``"fallback"`` when nothing matched.
        """
        normalized = normalize(text)

        if not normalized:
            return Response("fallback", 0.0, self._next_fallback())

        if normalized in self.exit_commands:
            return Response("exit", 1.0, "Goodbye! Have a great day. 👋", is_exit=True)

        best_intent: Optional[Intent] = None
        best_score = 0
        for intent in self.intents:
            score = intent.score(normalized)
            if score > best_score or (
                score == best_score
                and score > 0
                and best_intent is not None
                and intent.priority > best_intent.priority
            ):
                best_intent, best_score = intent, score

        if best_intent is None or best_score == 0:
            return Response("fallback", 0.0, self._next_fallback())

        return Response(
            best_intent.name,
            self._confidence(best_score),
            self._next_response(best_intent),
        )


class ConversationLogger:
    """Appends conversation turns to a JSONL file.

    Each turn is one JSON object on its own line, stamped with the persistence
    schema version so a future reader can tell which format produced it. A turn
    is published atomically (the file is rewritten through a temporary file and
    :func:`os.replace`), which costs O(size) per turn but guarantees the log
    never ends in a half-written line — the failure mode a plain append leaves
    behind after a crash.

    Args:
        path: Destination ``.jsonl`` path. The parent directory is created if
            needed.
    """

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, role: str, text: str, intent: str = "", confidence: float = 0.0) -> None:
        """Append one turn to the log.

        Args:
            role: ``"user"`` or ``"assistant"``.
            text: The turn's text.
            intent: Matched intent (assistant turns).
            confidence: Match confidence (assistant turns).
        """
        entry = {
            SCHEMA_VERSION_FIELD: SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "text": text,
            "intent": intent,
            "confidence": confidence,
        }
        atomic_append_text(self.path, json.dumps(entry, ensure_ascii=False) + "\n")
