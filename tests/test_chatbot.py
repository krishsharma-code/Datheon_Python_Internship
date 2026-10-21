"""Tests for the rule-based terminal assistant."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chatbot import (
    ConversationLogger,
    Intent,
    IntentEngine,
    Response,
    normalize,
)

INTENTS_PATH = (
    Path(__file__).resolve().parents[1] / "projects" / "04_terminal_assistant" / "intents.json"
)


# --- normalize ---------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Hello   World  ", "hello world"),
        ("WHAT?", "what?"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# --- Intent.from_dict --------------------------------------------------
def test_intent_from_dict_builds_patterns():
    intent = Intent.from_dict({"name": "x", "patterns": ["\\bhi\\b"], "responses": ["yo"]})
    assert intent.name == "x"
    assert intent.score("hi there") == 1
    assert intent.score("bye") == 0


@pytest.mark.parametrize(
    "data",
    [
        {"patterns": ["a"]},
        {"name": "x"},
        {"name": "x", "patterns": []},
        {"name": "x", "patterns": "notalist"},
    ],
)
def test_intent_from_dict_invalid(data):
    with pytest.raises(ValueError):
        Intent.from_dict(data)


# --- IntentEngine construction ----------------------------------------
def test_engine_requires_intents():
    with pytest.raises(ValueError):
        IntentEngine([])


def test_engine_from_json_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        IntentEngine.from_json(tmp_path / "nope.json")


def test_engine_from_json_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        IntentEngine.from_json(path)


def test_engine_from_json_no_intents(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"fallbacks": ["hi"]}), encoding="utf-8")
    with pytest.raises(ValueError):
        IntentEngine.from_json(path)


# --- respond -----------------------------------------------------------
@pytest.fixture()
def engine():
    return IntentEngine.from_json(INTENTS_PATH)


def test_respond_greeting(engine):
    response = engine.respond("Hello there")
    assert isinstance(response, Response)
    assert response.intent == "greeting"
    assert response.confidence > 0
    assert response.is_exit is False


def test_respond_business_hours(engine):
    response = engine.respond("What are your opening hours?")
    assert response.intent == "business_hours"


def test_respond_exit(engine):
    response = engine.respond("bye")
    assert response.intent == "exit"
    assert response.is_exit is True
    assert response.confidence == 1.0


def test_respond_exit_is_case_insensitive(engine):
    assert engine.respond("  QUIT ").is_exit is True


def test_respond_fallback_on_unknown(engine):
    response = engine.respond("zxcvbnm qwerty")
    assert response.intent == "fallback"
    assert response.confidence == 0.0


def test_respond_empty_input_is_fallback(engine):
    response = engine.respond("   ")
    assert response.intent == "fallback"


def test_confidence_scales_with_matches():
    engine = IntentEngine(
        [
            Intent.from_dict(
                {
                    "name": "multi",
                    "patterns": ["\\bapple\\b", "\\bbanana\\b", "\\bcherry\\b"],
                    "responses": ["fruit"],
                }
            )
        ]
    )
    low = engine.respond("apple")
    high = engine.respond("apple banana cherry")
    assert low.confidence == 0.5
    assert high.confidence > low.confidence
    assert high.confidence == 1.0


def test_priority_breaks_ties():
    engine = IntentEngine(
        [
            Intent.from_dict({"name": "low", "patterns": ["\\bfoo\\b"], "priority": 1}),
            Intent.from_dict({"name": "high", "patterns": ["\\bfoo\\b"], "priority": 5}),
        ]
    )
    assert engine.respond("foo").intent == "high"


def test_responses_rotate():
    engine = IntentEngine(
        [Intent.from_dict({"name": "g", "patterns": ["\\bhi\\b"], "responses": ["one", "two"]})]
    )
    first = engine.respond("hi").text
    second = engine.respond("hi").text
    assert {first, second} == {"one", "two"}


# --- ConversationLogger ------------------------------------------------
def test_conversation_logger_writes_jsonl(tmp_path):
    logger = ConversationLogger(tmp_path / "conv.jsonl")
    logger.log("user", "hello")
    logger.log("assistant", "hi there", intent="greeting", confidence=0.5)

    lines = (tmp_path / "conv.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["role"] == "user"
    assert second["intent"] == "greeting"
    assert second["confidence"] == 0.5


def test_conversation_logger_creates_parent_dirs(tmp_path):
    logger = ConversationLogger(tmp_path / "nested" / "conv.jsonl")
    logger.log("user", "hi")
    assert (tmp_path / "nested" / "conv.jsonl").exists()


FAQ_PATH = (
    Path(__file__).resolve().parents[1] / "projects" / "04_terminal_assistant" / "sample_faq.json"
)


# --- from_faq_json -----------------------------------------------------
@pytest.fixture()
def faq_engine():
    return IntentEngine.from_faq_json(FAQ_PATH)


def test_keywords_extraction():
    from chatbot import _keywords

    kws = _keywords("What are your hours of operation?")
    assert "hours" in kws
    assert "operation" in kws
    assert "what" not in kws
    assert "your" not in kws


def test_faq_engine_greeting(faq_engine):
    assert faq_engine.respond("hello").intent == "greeting"


def test_faq_engine_hours(faq_engine):
    response = faq_engine.respond("What are your hours of operation?")
    assert response.intent == "faq_1"
    assert "Monday" in response.text


def test_faq_engine_submit(faq_engine):
    assert faq_engine.respond("how do i submit my internship project").intent == "faq_2"


def test_faq_engine_certificate(faq_engine):
    assert faq_engine.respond("when will i receive my certificate").intent == "faq_3"


def test_faq_engine_fallback(faq_engine):
    assert faq_engine.respond("zzz qqq").intent == "fallback"


def test_from_faq_json_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        IntentEngine.from_faq_json(tmp_path / "nope.json")


def test_from_faq_json_invalid(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError):
        IntentEngine.from_faq_json(path)


def test_from_faq_json_no_faqs(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"greetings": ["hi"]}), encoding="utf-8")
    with pytest.raises(ValueError):
        IntentEngine.from_faq_json(path)
