# Error catalogue

Every error a Datheon tool raises on purpose derives from `DatheonError`,
so `except DatheonError` catches all of them. Each class carries a stable
machine-readable code, a process exit code, and the guidance below.

Exit codes: `0` success · `1` user or data error · `2` usage error.

| Code | Class | Title | Meaning | What to do |
| --- | --- | --- | --- | --- |
| DT-0000 | DatheonError | Unexpected error | An unexpected error occurred inside a Datheon tool. | Re-run the command; if the error persists, report it with the message above. |
| DT-1000 | InputError | Input error | The value supplied on the command line or at a prompt was rejected. | Correct the value and run the command again; `--help` lists the accepted form. |
| DT-1001 | ValidationError | Invalid input | A field, flag or prompt answer failed validation. | Read the message: it names the field and quotes the offending value. Supply a value in the documented form, or relax the check by omitting `--strict`. |
| DT-1002 | InputClosed | Input stream closed | Standard input reached end-of-file, or the read was interrupted with Ctrl-C. | Nothing to fix: the session ended normally. Re-run the tool to start a new one. |
| DT-1003 | UsageError | Usage error | An unknown flag, a missing value or a malformed argument was supplied. | Run the tool with `--help` to see the accepted arguments. |
| DT-1004 | UnknownCategoryError | Unknown category | A requested named group, such as a Hangman word-bank category, does not exist. | Pick one of the categories named in the error message. |
| DT-2000 | FilesystemError | Filesystem error | A file or directory the tool needs is missing, unreadable or the wrong kind. | Check the path passed on the command line and the permissions on it. |
| DT-2001 | MissingFileError | File not found | The path does not name an existing regular file. | Check the spelling of the path; sample datasets live under each project's `data/`. |
| DT-2002 | AccessDeniedError | Permission denied | The path exists but the operating system refused access to it. | Fix the permissions on the path, or run the tool as a user that may read it. |
| DT-2003 | DirectoryExpectedError | Not a directory | A command that walks a folder was given a path that is not a directory. | Pass the folder you want the tool to work on, not a file. |
| DT-2100 | DatasetError | Dataset error | A dataset file was read but violates its schema or row format. | Check the file against the documented schema; `--validate` reports bad rows. |
| DT-2101 | DatasetFormatError | Malformed dataset | The dataset was read successfully but violates its schema or row format. | Open the file at the reported line and fix the row; `--validate` reports every offending line before anything is used. |
| DT-2102 | DatasetEncodingError | Undecodable dataset | None of the configured text encodings could decode the file's bytes. | Re-save the file as UTF-8, or widen the encoding chain passed to the loader. |
| DT-3000 | OperationError | Operation failed | A domain operation could not be completed given the current state. | Read the message for the state that blocked the operation, then retry. |
| DT-3001 | InventoryError | Inventory operation refused | A checkout, return or registration violated the library's rules. | Check the item id and its current loan state before retrying. |
| DT-3002 | OrganizerError | File operation failed | A file could not be classified or moved into its category folder. | Check free space and permissions on the source and destination folders. |
| DT-3003 | PortfolioError | Portfolio operation refused | A holding, price update or summary export violated the portfolio's rules. | Check the ticker and the price, then retry the command. |
| DT-3004 | AssistantError | Assistant unavailable | The intent engine could not be constructed or queried. | Check that `intents.json` defines at least one valid intent. |
| DT-3005 | PersistenceError | Write failed | A file could not be staged or published; the destination was left unchanged. | Check that the destination directory is writable and has free space, and that no other program holds the file open, then re-run the command. |
| DT-3006 | SchemaVersionError | Unsupported document version | A saved document has no schema version, or one this tool cannot read. | Re-create the document with the current tool. If it was written by a newer version, upgrade the Datheon tools first. |
| DT-4000 | ConfigurationError | Configuration error | A mapping, limit or setting the tool depends on is missing or invalid. | Review the configuration mentioned in the message and correct it. |

## Builtin compatibility

The leaf classes also inherit the builtin exception they replace, so
handlers written before this taxonomy existed keep working:

| Class | Also catchable as |
| --- | --- |
| `ValidationError` | `ValueError` |
| `UnknownCategoryError` | `KeyError` |
| `FilesystemError` | `OSError` |
| `MissingFileError` | `OSError`, `FileNotFoundError` |
| `AccessDeniedError` | `OSError`, `PermissionError` |
| `DirectoryExpectedError` | `OSError`, `NotADirectoryError` |
| `DatasetError` | `ValueError` |
| `DatasetFormatError` | `ValueError` |
| `DatasetEncodingError` | `ValueError` |
| `PersistenceError` | `OSError` |
| `SchemaVersionError` | `OSError` |

## Rendering an error for the user

`DatheonError.render()` produces the block the CLIs print, and
`DatheonError.to_dict()` produces the structured form for logs:

```python
from errors import ValidationError

exc = ValidationError('symbol: must not be empty', hint='e.g. AAPL')
print(exc.render())
# ! [DT-1001] symbol: must not be empty
#   hint: e.g. AAPL
print(exc.to_dict())
# {'code': 'DT-1001', 'type': 'ValidationError', 'message': 'symbol: ...', ...}
```

## Regenerating this document

The tables above are generated from the classes in `errors.py`. Edit the
class metadata, never this file, then rewrite it from the repository
root:

```bash
python error_catalogue.py
```

A different destination path may be passed as the first argument.
