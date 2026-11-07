"""Tests for the shared exception taxonomy and the user-facing error catalogue.

Day 7 introduced :mod:`errors` as the single root of every failure a Datheon
tool raises on purpose. These tests pin three contracts:

* **structure** — one root, unique codes, sane exit codes, and builtin
  compatibility for every leaf class;
* **behaviour** — rendering, structured output and the fluent helpers;
* **documentation** — ``docs/error_catalogue.md`` is generated from the code,
  so the file on disk must match :func:`errors.render_catalogue_markdown`.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

import errors as errors_module
from cli_contract import ExitCode, run_cli
from dataset_loader import DatasetFormatError, read_text_robust
from edge_cases import InputClosed, check_file_size, iter_files
from errors import (
    EXIT_DATA_ERROR,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
    AccessDeniedError,
    AssistantError,
    ConfigurationError,
    DatheonError,
    DatasetEncodingError,
    DatasetError,
    DatasetFormatError as TaxonomyDatasetFormatError,
    DirectoryExpectedError,
    FilesystemError,
    InputError,
    InventoryError,
    MissingFileError,
    OperationError,
    OrganizerError,
    PortfolioError,
    UnknownCategoryError,
    UsageError,
    ValidationError,
    exit_code_for,
)
import error_catalogue
from error_catalogue import (
    ErrorEntry,
    catalogue,
    catalogue_codes,
    describe_code,
    error_class_for,
    main as catalogue_main,
    render_catalogue_markdown,
    write_catalogue,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Every concrete class in the taxonomy, in catalogue order.
ALL_CLASSES = tuple(error_class_for(code) for code in catalogue_codes())


# --- structure ---------------------------------------------------------
def test_every_catalogue_class_derives_from_the_base():
    for cls in ALL_CLASSES:
        assert issubclass(cls, DatheonError), cls.__name__


def test_base_class_itself_is_an_exception():
    assert issubclass(DatheonError, Exception)
    assert DatheonError("boom").message == "boom"


def test_catalogue_codes_are_unique():
    codes = catalogue_codes()
    assert len(codes) == len(set(codes))


def test_catalogue_codes_are_well_formed():
    for code in catalogue_codes():
        assert code.startswith("DT-")
        assert len(code) == 7
        assert code[3:].isdigit()


def test_catalogue_lists_parents_before_children():
    seen = []
    for cls in ALL_CLASSES:
        for parent in cls.__mro__[1:]:
            if parent in seen:
                continue
            if issubclass(parent, DatheonError) and parent is not cls:
                assert parent in seen, f"{cls.__name__} listed before its parent {parent.__name__}"
        seen.append(cls)


def test_catalogue_entries_describe_their_class():
    for entry in catalogue():
        cls = error_class_for(entry.code)
        assert entry.name == cls.__name__
        assert entry.title == cls.title
        assert entry.summary == cls.summary
        assert entry.resolution == cls.resolution
        assert entry.exit_code == cls.exit_code
        assert entry.as_row() == (
            entry.code,
            entry.name,
            entry.title,
            entry.summary,
            entry.resolution,
        )


def test_every_entry_has_non_empty_documentation():
    for entry in catalogue():
        assert entry.title.strip()
        assert entry.summary.strip()
        assert entry.resolution.strip()


@pytest.mark.parametrize(
    "cls, builtin",
    [
        (ValidationError, ValueError),
        (UnknownCategoryError, KeyError),
        (FilesystemError, OSError),
        (MissingFileError, FileNotFoundError),
        (AccessDeniedError, PermissionError),
        (DirectoryExpectedError, NotADirectoryError),
        (DatasetError, ValueError),
        (DatasetEncodingError, DatasetFormatError),
    ],
)
def test_leaf_classes_stay_compatible_with_their_builtin(cls, builtin):
    assert issubclass(cls, builtin)
    with pytest.raises(builtin):
        raise cls("compatibility probe")


def test_usage_error_maps_onto_the_usage_exit_code():
    assert UsageError.exit_code == EXIT_USAGE_ERROR
    assert int(ExitCode.USAGE_ERROR) == EXIT_USAGE_ERROR


def test_non_usage_errors_map_onto_the_data_exit_code():
    for cls in ALL_CLASSES:
        if cls is UsageError:
            continue
        assert cls.exit_code == EXIT_DATA_ERROR, cls.__name__


def test_exit_code_constants_match_the_cli_contract():
    assert int(ExitCode.OK) == EXIT_SUCCESS
    assert int(ExitCode.DATA_ERROR) == EXIT_DATA_ERROR
    assert int(ExitCode.USAGE_ERROR) == EXIT_USAGE_ERROR


# --- behaviour ---------------------------------------------------------
def test_str_returns_only_the_message():
    exc = ValidationError("symbol: must not be empty", hint="e.g. AAPL")
    assert str(exc) == "symbol: must not be empty"


def test_render_prefixes_the_catalogue_code():
    exc = ValidationError("symbol: must not be empty")
    assert exc.render() == "! [DT-1001] symbol: must not be empty"


def test_render_can_omit_the_code():
    exc = ValidationError("symbol: must not be empty")
    assert exc.render(show_code=False) == "! symbol: must not be empty"


def test_render_puts_the_hint_on_its_own_line():
    exc = ValidationError("symbol: must not be empty", hint="e.g. AAPL")
    rendered = exc.render()
    assert rendered.splitlines() == [
        "! [DT-1001] symbol: must not be empty",
        "  hint: e.g. AAPL",
    ]


def test_to_dict_is_structured_and_complete():
    exc = ValidationError("bad", hint="try again", context={"field": "symbol"})
    payload = exc.to_dict()
    assert payload == {
        "code": "DT-1001",
        "type": "ValidationError",
        "message": "bad",
        "hint": "try again",
        "context": {"field": "symbol"},
        "exit_code": EXIT_DATA_ERROR,
    }


def test_to_dict_context_is_a_copy():
    context = {"field": "symbol"}
    exc = ValidationError("bad", context=context)
    exc.context["extra"] = 1
    assert "extra" not in context


def test_repr_names_the_class_and_code():
    exc = UsageError("nope")
    assert repr(exc) == "UsageError(code='DT-1003', message='nope')"


def test_with_context_merges_and_returns_self():
    exc = DatasetFormatError("bad row")
    assert exc.with_context(path="prices.csv", line=7) is exc
    assert exc.context == {"path": "prices.csv", "line": 7}
    exc.with_context(line=9)
    assert exc.context["line"] == 9


def test_with_hint_sets_a_missing_hint():
    exc = OrganizerError("move failed")
    assert exc.with_hint("check permissions") is exc
    assert exc.hint == "check permissions"
    assert exc.render().endswith("hint: check permissions")


def test_with_hint_does_not_overwrite_an_existing_hint():
    exc = OrganizerError("move failed", hint="first")
    exc.with_hint("second")
    assert exc.hint == "first"


def test_per_instance_code_override():
    exc = DatheonError("odd condition", code="DT-9999")
    assert exc.code == "DT-9999"
    assert DatheonError.code == "DT-0000"


def test_empty_message_is_allowed():
    assert str(DatheonError()) == ""


# --- catalogue lookups -------------------------------------------------
def test_describe_code_round_trips():
    entry = describe_code("DT-2101")
    assert entry.name == "DatasetFormatError"
    assert entry.exit_code == EXIT_DATA_ERROR


def test_error_class_for_round_trips():
    assert error_class_for("DT-3001") is InventoryError


def test_describe_code_rejects_unknown_codes():
    with pytest.raises(UnknownCategoryError) as excinfo:
        describe_code("DT-0001")
    assert "DT-0001" in str(excinfo.value)
    assert "Known codes" in (excinfo.value.hint or "")


def test_error_class_for_rejects_unknown_codes():
    with pytest.raises(UnknownCategoryError):
        error_class_for("XX-1234")


def test_catalogue_covers_every_exported_class():
    exported = {
        name
        for name in errors_module.__all__
        if isinstance(getattr(errors_module, name), type)
        and issubclass(getattr(errors_module, name), DatheonError)
    }
    catalogued = {entry.name for entry in catalogue()}
    assert exported == catalogued


# --- exit_code_for -----------------------------------------------------
def test_exit_code_for_reads_the_taxonomy_attribute():
    assert exit_code_for(ValidationError("bad")) == EXIT_DATA_ERROR
    assert exit_code_for(UsageError("bad")) == EXIT_USAGE_ERROR


def test_exit_code_for_reads_system_exit():
    assert exit_code_for(SystemExit(2)) == 2
    assert exit_code_for(SystemExit(None)) == EXIT_SUCCESS


def test_exit_code_for_defaults_to_data_error():
    assert exit_code_for(ValueError("plain")) == EXIT_DATA_ERROR
    assert exit_code_for(OSError("plain")) == EXIT_DATA_ERROR


# --- integration with the shared CLI contract --------------------------
def test_run_cli_renders_a_taxonomy_error_and_returns_its_code(capsys):
    def boom() -> int:
        raise ValidationError("symbol: must not be empty", hint="e.g. AAPL")

    assert run_cli(boom) == EXIT_DATA_ERROR
    err = capsys.readouterr().err
    assert "! [DT-1001] symbol: must not be empty" in err
    assert "hint: e.g. AAPL" in err


def test_run_cli_returns_two_for_a_usage_error(capsys):
    def boom() -> int:
        raise UsageError("unknown flag --nope")

    assert run_cli(boom) == EXIT_USAGE_ERROR
    assert "DT-1003" in capsys.readouterr().err


def test_run_cli_still_maps_plain_value_error(capsys):
    def boom() -> int:
        raise ValueError("not a DatheonError")

    assert run_cli(boom) == EXIT_DATA_ERROR
    assert "! not a DatheonError" in capsys.readouterr().err


# --- integration with the modules rewritten on Day 7 -------------------
def test_validator_errors_carry_a_hint():
    from validators import validate_symbol

    with pytest.raises(ValidationError) as excinfo:
        validate_symbol("   ")
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.value.hint is not None
    assert "e.g." in excinfo.value.hint


def test_missing_dataset_raises_the_taxonomy_error(tmp_path):
    with pytest.raises(MissingFileError) as excinfo:
        read_text_robust(tmp_path / "absent.csv")
    assert isinstance(excinfo.value, FileNotFoundError)
    assert excinfo.value.context["path"].endswith("absent.csv")


def test_check_file_size_reports_the_taxonomy_code(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 32)
    with pytest.raises(ValidationError) as excinfo:
        check_file_size(big, max_bytes=8)
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.value.code == "DT-1001"
    assert "too large" in str(excinfo.value)


def test_iter_files_rejects_a_non_directory(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("hi")
    with pytest.raises(DirectoryExpectedError) as excinfo:
        iter_files(target)
    assert isinstance(excinfo.value, NotADirectoryError)


def test_unknown_word_category_is_both_key_error_and_taxonomy_error():
    from words import get_random_word

    with pytest.raises(UnknownCategoryError) as excinfo:
        get_random_word("nonsense-category")
    assert isinstance(excinfo.value, KeyError)
    assert "available categories" in (excinfo.value.hint or "")


def test_inventory_error_derives_from_the_base():
    from inventory import InventoryManager
    from models import Book

    manager = InventoryManager()
    manager.add_item(Book("BK-1", "Dune", 499.0, "Frank Herbert", 412))
    with pytest.raises(InventoryError) as excinfo:
        manager.add_item(Book("BK-1", "Dune Messiah", 599.0, "Frank Herbert", 336))
    assert isinstance(excinfo.value, DatheonError)
    assert excinfo.value.exit_code == EXIT_DATA_ERROR


def test_input_closed_is_a_taxonomy_error(monkeypatch):
    import builtins

    def fake_input(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(builtins, "input", fake_input)
    from edge_cases import safe_input

    with pytest.raises(InputClosed) as excinfo:
        safe_input("> ")
    assert isinstance(excinfo.value, DatheonError)
    assert excinfo.value.hint is not None


def test_chatbot_intent_validation_uses_the_taxonomy():
    from chatbot import Intent

    with pytest.raises(ValidationError):
        Intent.from_dict({"name": "greet", "patterns": []})


def test_portfolio_holding_validation_uses_the_taxonomy():
    from portfolio import Holding

    with pytest.raises(ValidationError) as excinfo:
        Holding(symbol="AAPL", shares=0, buy_price=10.0)
    assert excinfo.value.context["field"] == "shares"


def test_taxonomy_dataset_format_error_is_the_loader_class():
    assert TaxonomyDatasetFormatError is DatasetFormatError
    assert issubclass(AssistantError, OperationError)
    assert issubclass(PortfolioError, OperationError)
    assert issubclass(ConfigurationError, DatheonError)
    assert issubclass(InputError, DatheonError)


# --- documentation -----------------------------------------------------
def test_committed_catalogue_matches_the_generator():
    path = ROOT / "docs" / "error_catalogue.md"
    assert path.is_file(), "docs/error_catalogue.md is missing"
    assert path.read_text(encoding="utf-8") == render_catalogue_markdown()


def test_catalogue_markdown_lists_every_code():
    markdown = render_catalogue_markdown()
    for code in catalogue_codes():
        assert f"| {code} |" in markdown


# --- mutation-testing hardening (Day 19) --------------------------------
#
# The Day 19 mutmut run left sixteen mutants alive in :mod:`error_catalogue`:
# every one of them hid inside the *failure* path of ``describe_code`` /
# ``error_class_for`` (the message, hint and ``context`` of the
# ``UnknownCategoryError``) or inside the table-cell pipe escaping of
# ``render_catalogue_markdown``. The suite asserted that the error *was* raised
# but never what it said, so a mutant could blank the message, drop the hint,
# rename the context key or stop escaping ``|`` and still pass.
#
# These tests pin the exact text a user sees. They are intentionally strict —
# an error message is a user interface, not an implementation detail.


def _unknown_code_error(lookup, code):
    """Call ``lookup(code)`` and return the :class:`UnknownCategoryError` it raises."""
    with pytest.raises(UnknownCategoryError) as excinfo:
        lookup(code)
    return excinfo.value


@pytest.mark.parametrize("lookup", [describe_code, error_class_for])
def test_unknown_code_error_names_the_offending_code(lookup):
    exc = _unknown_code_error(lookup, "DT-0001")

    assert exc.message == "Unknown error code 'DT-0001'."
    assert exc.code == UnknownCategoryError.code
    assert exc.context == {"code": "DT-0001"}


@pytest.mark.parametrize("lookup", [describe_code, error_class_for])
def test_unknown_code_error_hint_lists_every_known_code(lookup):
    exc = _unknown_code_error(lookup, "XX-1234")

    assert exc.hint == f"Known codes: {', '.join(catalogue_codes())}"
    # The hint is the whole point of the error: it must actually name codes.
    assert "DT-1001" in exc.hint


@pytest.mark.parametrize("lookup", [describe_code, error_class_for])
def test_unknown_code_error_renders_the_code_and_the_hint(lookup):
    rendered = _unknown_code_error(lookup, "NOPE").render()

    assert rendered.splitlines()[0] == f"! [{UnknownCategoryError.code}] Unknown error code 'NOPE'."
    assert rendered.splitlines()[1].startswith("  hint: Known codes: ")


def test_render_catalogue_escapes_pipes_inside_table_cells(monkeypatch):
    """A ``|`` in any cell must be escaped, or the Markdown table breaks."""
    entry = ErrorEntry(
        code="DT-9999",
        name="FakeError",
        title="A | B",
        summary="left|right",
        resolution="pick | one",
        exit_code=EXIT_DATA_ERROR,
        bases=(),
    )
    monkeypatch.setattr(error_catalogue, "catalogue", lambda: (entry,))

    markdown = render_catalogue_markdown()
    row = next(line for line in markdown.splitlines() if line.startswith("| DT-9999 |"))

    assert "A \\| B" in row
    assert "left\\|right" in row
    assert "pick \\| one" in row
    # Five cells means exactly four *unescaped* separators.
    assert row.count(" | ") == 4


def test_write_catalogue_writes_the_rendered_document(tmp_path):
    target = tmp_path / "catalogue.md"

    written = write_catalogue(target)

    assert written == target
    assert target.read_text(encoding="utf-8") == render_catalogue_markdown()


def test_write_catalogue_creates_missing_parent_directories(tmp_path):
    target = tmp_path / "deeply" / "nested" / "catalogue.md"

    written = write_catalogue(target)

    assert written == target
    assert target.is_file()


def test_write_catalogue_accepts_a_string_path(tmp_path):
    target = tmp_path / "catalogue.md"

    assert write_catalogue(str(target)) == target


def test_catalogue_main_writes_the_default_path_and_reports_it(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert catalogue_main([]) == EXIT_SUCCESS

    written = tmp_path / "docs" / "error_catalogue.md"
    assert written.read_text(encoding="utf-8") == render_catalogue_markdown()
    assert capsys.readouterr().out.strip() == "wrote docs/error_catalogue.md"


def test_catalogue_main_writes_to_an_explicit_destination(tmp_path, capsys):
    target = tmp_path / "custom.md"

    assert catalogue_main([str(target)]) == EXIT_SUCCESS

    assert target.read_text(encoding="utf-8") == render_catalogue_markdown()
    assert capsys.readouterr().out.strip() == f"wrote {target}"


def test_catalogue_main_reads_sys_argv_when_called_without_arguments(tmp_path, monkeypatch, capsys):
    target = tmp_path / "from-argv.md"
    monkeypatch.setattr(sys, "argv", ["error_catalogue.py", str(target)])

    assert catalogue_main() == EXIT_SUCCESS

    assert target.is_file()
    assert capsys.readouterr().out.strip() == f"wrote {target}"


def test_write_catalogue_defaults_to_the_docs_path(tmp_path, monkeypatch):
    """The no-argument form is the one the plan's docs command documents."""
    monkeypatch.chdir(tmp_path)

    written = write_catalogue()

    assert written == pathlib.Path("docs") / "error_catalogue.md"
    assert (tmp_path / "docs" / "error_catalogue.md").read_text(
        encoding="utf-8"
    ) == render_catalogue_markdown()
