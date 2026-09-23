from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


class Pds3ParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Column:
    number: int
    name: str
    data_type: str
    start_byte: int
    bytes_: int
    unit: str | None = None
    format_: str | None = None
    item_index: int | None = None


@dataclass(frozen=True, slots=True)
class TableDescription:
    rows: int
    columns: tuple[Column, ...]
    row_bytes: int
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Label:
    keywords: dict[str, str]
    table: TableDescription
    record_bytes: int
    file_records: int
    table_pointer: str
    table_start_record: int = 1


@dataclass(frozen=True, slots=True)
class TableRow:
    source_fields: tuple[str, ...]
    values: tuple[object, ...]
    record_number: int


@dataclass(frozen=True, slots=True)
class ParsedTable:
    label: Label
    rows: tuple[TableRow, ...]
    metadata: dict[str, str] = field(default_factory=dict)


_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_ASSIGNMENT = re.compile(r"^\s*([A-Za-z0-9_:^]+)\s*=\s*(.*)$")


def _error(message: str) -> Pds3ParseError:
    return Pds3ParseError(message)


def _tokenise(text: str) -> list[tuple[str, str]]:
    cleaned = _COMMENT.sub(" ", text)
    pairs: list[tuple[str, str]] = []
    pending_key: str | None = None
    pending: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.rstrip()
        if pending_key is not None:
            pending.append(line.strip())
            joined = " ".join(pending)
            if joined.count('"') % 2 == 0:
                pairs.append((pending_key, joined.strip()))
                pending_key, pending = None, []
            continue
        match = _ASSIGNMENT.match(line)
        if match is None:
            continue
        key, value = match.group(1).upper(), match.group(2).strip()
        if value.count('"') % 2 == 1:
            pending_key, pending = key, [value]
            continue
        pairs.append((key, value))
    if pending_key is not None:
        raise _error(f"unterminated quoted value for {pending_key}")
    return pairs


def _unquote(value: str) -> str:
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return re.sub(r"\s+", " ", value[1:-1]).strip()
    return value.strip()


def _require_int(pairs: dict[str, str], key: str) -> int:
    if key not in pairs:
        raise _error(f"label is missing {key}")
    try:
        return int(_unquote(pairs[key]))
    except ValueError as exc:
        raise _error(f"{key} is not an integer: {pairs[key]!r}") from exc


def parse_label(text: str) -> Label:
    if not isinstance(text, str):
        raise TypeError("parse_label expects str")
    pairs = _tokenise(text)
    if not pairs or pairs[0][0] != "PDS_VERSION_ID":
        raise _error("not a PDS3 label (missing leading PDS_VERSION_ID)")

    top: dict[str, str] = {}
    columns: list[Column] = []
    table_keys: dict[str, str] = {}
    table_object = ""
    declarations = 0
    stack: list[tuple[str, dict[str, str]]] = []
    for key, value in pairs:
        if key == "OBJECT":
            stack.append((_unquote(value).upper(), {}))
            continue
        if key == "END_OBJECT":
            name = _unquote(value).upper()
            if not stack or stack[-1][0] != name:
                raise _error(f"END_OBJECT = {name} does not close the open object")
            _, keys = stack.pop()
            if name == "COLUMN":
                declarations += 1
                columns.extend(_build_columns(keys, declarations))
            elif name == "TABLE" or name.endswith("_TABLE"):
                if table_object and table_object != name:
                    raise _error(f"label declares two tables: {table_object} and {name}")
                table_object = name
                table_keys.update(keys)
            continue
        if stack:
            stack[-1][1][key] = value
        else:
            top[key] = value

    if stack:
        raise _error(f"unclosed OBJECT: {stack[-1][0]}")
    if not table_object:
        raise _error("label declares no TABLE object")
    if not columns:
        raise _error("label declares no COLUMN objects")
    if _unquote(top.get("RECORD_TYPE", "")).upper() != "FIXED_LENGTH":
        raise _error("only FIXED_LENGTH records are supported")

    record_bytes = _require_int(top, "RECORD_BYTES")
    file_records = _require_int(top, "FILE_RECORDS")
    rows = _require_int(table_keys, "ROWS")
    declared_columns = _require_int(table_keys, "COLUMNS")
    row_bytes = _require_int(table_keys, "ROW_BYTES")
    if declared_columns != declarations:
        raise _error(f"COLUMNS says {declared_columns} but {declarations} were defined")
    if row_bytes != record_bytes:
        raise _error(f"ROW_BYTES {row_bytes} disagrees with RECORD_BYTES {record_bytes}")
    for column in columns:
        if column.start_byte < 1 or column.start_byte + column.bytes_ - 1 > row_bytes:
            raise _error(f"column {column.name!r} lies outside the declared row")

    pointer_key = f"^{table_object}"
    if pointer_key not in top:
        raise _error(f"label has no {pointer_key} pointer")
    pointer, start_record = _parse_pointer(top[pointer_key])
    if start_record + rows - 1 != file_records:
        raise _error(f"TABLE ROWS {rows} starting at record {start_record} "
                     f"disagrees with FILE_RECORDS {file_records}")
    return Label({k: _unquote(v) for k, v in top.items()},
                 TableDescription(rows, tuple(columns), row_bytes,
                                  _unquote(table_keys["DESCRIPTION"]) if "DESCRIPTION" in table_keys else None),
                 record_bytes, file_records, pointer, start_record)


_POINTER = re.compile(r'^\(\s*"([^"]+)"\s*,\s*(\d+)\s*\)$')


def _parse_pointer(value: str) -> tuple[str, int]:
    text = value.strip()
    match = _POINTER.match(text)
    if match:
        start = int(match.group(2))
        if start < 1:
            raise _error(f"table pointer starts at record {start}")
        return match.group(1), start
    name = _unquote(text)
    if not name or "(" in name:
        raise _error(f"unreadable table pointer: {value!r}")
    return name, 1


def _build_columns(keys: dict[str, str], position: int) -> list[Column]:
    for required in ("NAME", "DATA_TYPE", "START_BYTE", "BYTES"):
        if required not in keys:
            raise _error(f"COLUMN object is missing {required}")
    number = _require_int(keys, "COLUMN_NUMBER") if "COLUMN_NUMBER" in keys else position
    common = {
        "name": _unquote(keys["NAME"]),
        "data_type": _unquote(keys["DATA_TYPE"]).upper(),
        "unit": _unquote(keys["UNIT"]) if "UNIT" in keys else None,
        "format_": _unquote(keys["FORMAT"]) if "FORMAT" in keys else None,
    }
    start_byte = _require_int(keys, "START_BYTE")
    total_bytes = _require_int(keys, "BYTES")
    if "ITEMS" not in keys:
        return [Column(number=number, start_byte=start_byte, bytes_=total_bytes, **common)]

    items = _require_int(keys, "ITEMS")
    if items < 1:
        raise _error(f"column {common['name']!r}: ITEMS must be positive")
    if "ITEM_BYTES" not in keys:
        raise _error(f"column {common['name']!r}: ITEMS given without ITEM_BYTES")
    item_bytes = _require_int(keys, "ITEM_BYTES")
    stride = _require_int(keys, "ITEM_OFFSET") if "ITEM_OFFSET" in keys else item_bytes
    if item_bytes < 1 or stride < item_bytes:
        raise _error(f"column {common['name']!r}: ITEM_OFFSET {stride} cannot "
                     f"hold ITEM_BYTES {item_bytes}")
    span = stride * (items - 1) + item_bytes
    if span > total_bytes:
        raise _error(f"column {common['name']!r}: {items} items of {item_bytes} "
                     f"at stride {stride} need {span} bytes but BYTES says {total_bytes}")
    return [Column(number=number, start_byte=start_byte + index * stride,
                   bytes_=item_bytes, item_index=index + 1, **common)
            for index in range(items)]


def _convert(text: str, column: Column) -> object:
    stripped = text.strip()
    if not stripped:
        return None
    if column.data_type == "ASCII_INTEGER":
        try:
            return int(stripped)
        except ValueError as exc:
            raise _error(f"column {column.name!r}: {stripped!r} is not an integer") from exc
    if column.data_type in {"ASCII_REAL", "FLOAT", "REAL"}:
        try:
            return Decimal(stripped)
        except InvalidOperation as exc:
            raise _error(f"column {column.name!r}: {stripped!r} is not a number") from exc
    return stripped


def parse_table(label: Label, data: bytes) -> ParsedTable:
    if not isinstance(data, bytes):
        raise TypeError("parse_table expects bytes")
    expected = label.record_bytes * label.file_records
    if len(data) != expected:
        raise _error(
            f"table is {len(data)} bytes but the label describes "
            f"{label.file_records} records of {label.record_bytes} ({expected})"
        )
    rows: list[TableRow] = []
    first = label.table_start_record - 1
    for index in range(first, label.file_records):
        record = data[index * label.record_bytes:(index + 1) * label.record_bytes]
        try:
            text = record.decode("ascii")
        except UnicodeDecodeError as exc:
            raise _error(f"record {index + 1} is not ASCII") from exc
        fields, values = [], []
        for column in label.table.columns:
            start = column.start_byte - 1
            raw = text[start:start + column.bytes_]
            fields.append(raw)
            values.append(_convert(raw, column))
        rows.append(TableRow(tuple(fields), tuple(values), index + 1))
    if len(rows) != label.table.rows:
        raise _error(f"read {len(rows)} rows but the label declares {label.table.rows}")
    metadata = {
        "time_system": "unresolved",
        "record_kind": "cumulative_counter_samples",
        "data_set_id": label.keywords.get("DATA_SET_ID", ""),
        "subsystem_data_type": label.keywords.get("ROSETTA:SUBSYSTEM_DATA_TYPE", ""),
        "header_record_count": first,
    }
    return ParsedTable(label, tuple(rows), metadata)


def parse_product(label_text: str, data: bytes) -> ParsedTable:
    return parse_table(parse_label(label_text), data)
