"""Tests for the hardened dataset loaders (Day 6).

Each test pins one aspect of the loader-hardening contract: BOM / CRLF /
legacy-encoding tolerance, CSV schema validation, and malformed-row reporting
with line numbers — the definition of done being that a corrupt fixture is
*detected and reported*, never silently accepted.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import chatbot
import portfolio
import words
from dataset_loader import (
    DEFAULT_ENCODINGS,
    DatasetFormatError,
    LoadReport,
    detect_newline,
    read_text_robust,
    require_columns,
    strip_bom,
)

ROOT = Path(__file__).resolve().parents[1]

#: A UTF-8 byte-order mark, as it appears on disk.
BOM = b"\xef\xbb\xbf"

HANGMAN_WORDS = ROOT / "projects" / "01_hangman_game" / "data" / "words.txt"
PORTFOLIO_PRICES = ROOT / "projects" / "02_portfolio_tracker" / "data" / "sample_prices.csv"
ASSISTANT_INTENTS = ROOT / "projects" / "04_terminal_assistant" / "intents.json"
ASSISTANT_FAQ = ROOT / "projects" / "04_terminal_assistant" / "sample_faq.json"


def _run_cli(script: str, *args: str) -> subprocess.CompletedProcess:
    """Run a CLI script in a subprocess with a closed (EOF) stdin."""
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=str(ROOT),
        input="",
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- strip_bom / detect_newline ---------------------------------------
def test_strip_bom_removes_leading_mark():
    assert strip_bom("\ufeffhello") == "hello"


def test_strip_bom_leaves_clean_text_alone():
    assert strip_bom("hello") == "hello"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("no breaks here", "none"),
        ("a\nb\n", "lf"),
        ("a\r\nb\r\n", "crlf"),
        ("a\rb\r", "cr"),
        ("a\r\nb\nc", "mixed"),
    ],
)
def test_detect_newline(text, expected):
    assert detect_newline(text) == expected


# --- read_text_robust --------------------------------------------------
def test_read_plain_utf8(tmp_path):
    path = tmp_path / "plain.txt"
    path.write_text("hello\nworld\n", encoding="utf-8")
    source = read_text_robust(path)
    assert source.text == "hello\nworld\n"
    assert source.encoding in DEFAULT_ENCODINGS
    assert source.newline == "lf"
    assert source.had_bom is False


def test_read_strips_utf8_bom(tmp_path):
    path = tmp_path / "bom.txt"
    path.write_bytes(BOM + b"hello\nworld\n")
    source = read_text_robust(path)
    assert source.had_bom is True
    assert source.text.startswith("hello")
    assert "\ufeff" not in source.text


def test_read_detects_crlf(tmp_path):
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"a\r\nb\r\n")
    source = read_text_robust(path)
    assert source.newline == "crlf"


def test_read_falls_back_to_cp1252(tmp_path):
    path = tmp_path / "latin.txt"
    # 0xE9 is "é" in cp1252/latin-1 but is not valid standalone UTF-8.
    path.write_bytes(b"caf\xe9\n")
    source = read_text_robust(path)
    assert source.encoding == "cp1252"
    assert source.text == "café\n"


def test_read_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_text_robust(tmp_path / "nope.txt")


def test_read_undecodable_when_chain_excludes_latin1(tmp_path):
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\xff\xfe\x00\x00")
    with pytest.raises(DatasetFormatError):
        read_text_robust(path, encodings=("utf-8-sig",))


# --- require_columns ---------------------------------------------------
def test_require_columns_matches_case_insensitively():
    assert require_columns(["Symbol", "Price"], ("symbol", "ticker"), field="ticker") == "Symbol"


def test_require_columns_prefers_earlier_candidate():
    assert require_columns(["ticker", "symbol"], ("symbol", "ticker"), field="ticker") == "symbol"


def test_require_columns_missing_raises():
    with pytest.raises(DatasetFormatError) as excinfo:
        require_columns(["name", "value"], ("symbol",), field="ticker column")
    assert "ticker column" in str(excinfo.value)


# --- LoadReport --------------------------------------------------------
def test_report_starts_clean():
    report = LoadReport("demo.csv")
    assert report.is_clean
    assert not report.has_errors
    assert report.issues == []


def test_report_records_errors_and_warnings():
    report = LoadReport("demo.csv")
    report.warning("soft problem", line=2, raw="x,y")
    report.error("hard problem", line=3, raw="a,b")
    assert len(report.warnings) == 1
    assert len(report.errors) == 1
    assert report.has_errors
    assert not report.is_clean


def test_report_raise_if_errors_carries_line_numbers():
    report = LoadReport("demo.csv")
    report.error("bad row", line=7)
    with pytest.raises(DatasetFormatError) as excinfo:
        report.raise_if_errors("price file")
    assert "line 7" in str(excinfo.value)


def test_report_raise_if_errors_is_noop_without_errors():
    report = LoadReport("demo.csv")
    report.warning("only a warning", line=1)
    report.raise_if_errors("price file")  # must not raise


def test_report_render_lists_source_and_issues():
    report = LoadReport("demo.csv")
    report.rows_checked = 3
    report.rows_accepted = 2
    report.error("bad row", line=4, raw="a,b")
    rendered = report.render()
    assert "demo.csv" in rendered
    assert "2/3 rows accepted" in rendered
    assert "line 4" in rendered


# --- word list ---------------------------------------------------------
def test_words_tolerates_bom_and_crlf(tmp_path):
    path = tmp_path / "words.txt"
    path.write_bytes(BOM + b"python\r\nvariable\r\nfunction\r\n")
    assert words.load_words_from_file(path) == ["python", "variable", "function"]


def test_words_lenient_records_warning_for_bad_row(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("python\n12345\nvariable\n", encoding="utf-8")
    report = LoadReport(path)
    result = words.load_words_from_file(path, report=report)
    assert result == ["python", "variable"]
    assert len(report.warnings) == 1
    assert report.warnings[0].line == 2
    assert not report.has_errors


def test_words_strict_rejects_bad_row_with_line_number(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("python\n12345\nvariable\n", encoding="utf-8")
    with pytest.raises(DatasetFormatError) as excinfo:
        words.load_words_from_file(path, strict=True)
    assert "line 2" in str(excinfo.value)


def test_words_reads_cp1252_accented_word(tmp_path):
    path = tmp_path / "words.txt"
    path.write_bytes(b"caf\xe9\n")
    assert words.load_words_from_file(path) == ["café"]


def test_bundled_word_list_still_loads():
    loaded = words.load_words_from_file(HANGMAN_WORDS, min_length=words.MIN_WORD_LENGTH)
    assert len(loaded) > 1000


# --- portfolio CSVs ----------------------------------------------------
def test_prices_tolerates_bom_and_crlf(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_bytes(BOM + b"symbol,price\r\nAAPL,150.5\r\nmsft,400\r\n")
    assert portfolio.load_prices_from_csv(path) == {"AAPL": 150.5, "MSFT": 400.0}


def test_prices_strict_reports_missing_price_with_line(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("symbol,price\nAAPL,\nMSFT,400\n", encoding="utf-8")
    with pytest.raises(DatasetFormatError) as excinfo:
        portfolio.load_prices_from_csv(path, strict=True)
    assert "line 2" in str(excinfo.value)


def test_prices_lenient_skips_missing_price_with_warning(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("symbol,price\nAAPL,\nMSFT,400\n", encoding="utf-8")
    report = LoadReport(path)
    result = portfolio.load_prices_from_csv(path, report=report)
    assert result == {"MSFT": 400.0}
    assert len(report.warnings) == 1
    assert report.warnings[0].line == 2


def test_prices_invalid_number_always_raises_with_line(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("symbol,price\nAAPL,150\nMSFT,not-a-number\n", encoding="utf-8")
    with pytest.raises(DatasetFormatError) as excinfo:
        portfolio.load_prices_from_csv(path)
    assert "line 3" in str(excinfo.value)


def test_prices_missing_column_names_the_column(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("name,value\nfoo,bar\n", encoding="utf-8")
    with pytest.raises(DatasetFormatError) as excinfo:
        portfolio.load_prices_from_csv(path)
    assert "column" in str(excinfo.value)


def test_symbols_tolerates_bom_and_crlf(tmp_path):
    path = tmp_path / "constituents.csv"
    path.write_bytes(BOM + b"Symbol,Security\r\nAAPL,Apple Inc.\r\nMSFT,Microsoft Corp.\r\n")
    symbols = portfolio.load_symbols_from_csv(path)
    assert symbols == {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corp."}


def test_symbols_strict_reports_blank_ticker(tmp_path):
    path = tmp_path / "constituents.csv"
    path.write_text("Symbol,Security\nAAPL,Apple\n,Missing\n", encoding="utf-8")
    with pytest.raises(DatasetFormatError) as excinfo:
        portfolio.load_symbols_from_csv(path, strict=True)
    assert "line 3" in str(excinfo.value)


def test_bundled_constituents_still_load():
    symbols = portfolio.load_symbols_from_csv(
        ROOT / "projects" / "02_portfolio_tracker" / "data" / "constituents.csv"
    )
    assert len(symbols) > 400
    assert "AAPL" in symbols


# --- assistant JSON ----------------------------------------------------
def test_intents_tolerates_bom(tmp_path):
    path = tmp_path / "intents.json"
    payload = {"intents": [{"name": "greet", "patterns": ["hi"], "responses": ["hello"]}]}
    path.write_bytes(BOM + json.dumps(payload).encode("utf-8"))
    engine = chatbot.IntentEngine.from_json(path)
    assert engine.respond("hi").intent == "greet"


def test_intents_top_level_must_be_object(tmp_path):
    path = tmp_path / "intents.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(DatasetFormatError) as excinfo:
        chatbot.IntentEngine.from_json(path)
    assert "top level" in str(excinfo.value)


def test_intents_malformed_entry_names_index(tmp_path):
    path = tmp_path / "intents.json"
    payload = {
        "intents": [
            {"name": "ok", "patterns": ["hi"]},
            {"name": "bad", "patterns": []},
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DatasetFormatError) as excinfo:
        chatbot.IntentEngine.from_json(path)
    assert "intents[1]" in str(excinfo.value)


def test_faq_tolerates_bom(tmp_path):
    path = tmp_path / "faq.json"
    payload = {"faqs": [{"question": "what are your hours", "answer": "9 to 6"}]}
    path.write_bytes(BOM + json.dumps(payload).encode("utf-8"))
    engine = chatbot.IntentEngine.from_faq_json(path)
    assert engine.respond("what are your hours").intent == "faq_1"


def test_faq_lenient_skips_bad_entry_with_warning(tmp_path):
    path = tmp_path / "faq.json"
    payload = {
        "faqs": [
            {"question": "what are your hours", "answer": "9 to 6"},
            {"question": "", "answer": ""},
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = LoadReport(path)
    engine = chatbot.IntentEngine.from_faq_json(path, report=report)
    assert engine.respond("what are your hours").intent == "faq_1"
    assert len(report.warnings) == 1
    assert report.warnings[0].message.startswith("faqs[1]")


def test_faq_strict_rejects_bad_entry(tmp_path):
    path = tmp_path / "faq.json"
    payload = {"faqs": [{"question": "", "answer": ""}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DatasetFormatError) as excinfo:
        chatbot.IntentEngine.from_faq_json(path, strict=True)
    assert "faqs[0]" in str(excinfo.value)


def test_bundled_faq_still_loads():
    engine = chatbot.IntentEngine.from_faq_json(ASSISTANT_FAQ)
    assert engine.respond("hello").intent == "greeting"


# --- CLI --validate mode ----------------------------------------------
def test_hangman_validate_good_dataset_exits_zero():
    result = _run_cli(
        "projects/01_hangman_game/main.py", "--validate", "--words-file", str(HANGMAN_WORDS)
    )
    assert result.returncode == 0
    assert "OK:" in result.stdout


def test_hangman_validate_corrupt_dataset_exits_one(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("python\n12345\nvariable\n", encoding="utf-8")
    result = _run_cli("projects/01_hangman_game/main.py", "--validate", "--words-file", str(path))
    assert result.returncode == 1
    assert "line 2" in result.stdout
    assert "Traceback" not in result.stderr


def test_portfolio_validate_good_dataset_exits_zero():
    result = _run_cli(
        "projects/02_portfolio_tracker/main.py", "--validate", "--prices", str(PORTFOLIO_PRICES)
    )
    assert result.returncode == 0
    assert "OK:" in result.stdout


def test_portfolio_validate_corrupt_dataset_exits_one(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("symbol,price\nAAPL,not-a-number\n", encoding="utf-8")
    result = _run_cli("projects/02_portfolio_tracker/main.py", "--validate", "--prices", str(path))
    assert result.returncode == 1
    assert "line 2" in result.stdout
    assert "Traceback" not in result.stderr


def test_assistant_validate_good_dataset_exits_zero():
    result = _run_cli(
        "projects/04_terminal_assistant/main.py", "--validate", "--intents", str(ASSISTANT_INTENTS)
    )
    assert result.returncode == 0
    assert "OK:" in result.stdout


def test_assistant_validate_corrupt_dataset_exits_one(tmp_path):
    path = tmp_path / "intents.json"
    path.write_text(json.dumps({"intents": [{"name": "bad", "patterns": []}]}), encoding="utf-8")
    result = _run_cli(
        "projects/04_terminal_assistant/main.py", "--validate", "--intents", str(path)
    )
    assert result.returncode == 1
    assert "intents[0]" in result.stdout
    assert "Traceback" not in result.stderr
