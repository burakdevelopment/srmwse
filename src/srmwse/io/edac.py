from __future__ import annotations

import calendar
import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache


class ExportParseError(ValueError):
    pass


class UnsupportedExportError(ExportParseError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedSample:
    source_timestamp: str
    values: tuple[Decimal | None, ...]
    source_line: int


@dataclass(frozen=True, slots=True)
class ChannelDescriptor:

    channel: str
    unit: str
    description: str
    data_type: str
    statistics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedExport:
    format_name: str
    channels: tuple[str, ...]
    samples: tuple[ParsedSample, ...]
    metadata: dict[str, str]
    raw_headers: tuple[str, ...]
    column_labels: tuple[str, ...]
    raw_footer: tuple[str, ...] = ()
    descriptors: tuple[ChannelDescriptor, ...] = ()


_CHANNEL = re.compile(r"[A-Za-z0-9_.]+", re.ASCII)
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_GRAINS_TIME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{3})")
_ARES_TIME = re.compile(r"([0-9]{4}) ([0-9]{3}) ([0-9]{2}) ([0-9]{2}) ([0-9]{2})\.([0-9]{3})")
_ARES_FOOTER = "Number of all written samples:"


def _error(line: int, message: str) -> ExportParseError:
    return ExportParseError(f"line {line}: {message}")


def _fields(line: str, delimiter: str, number: int) -> list[str]:
    try:
        return next(csv.reader([line], delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise _error(number, f"malformed delimited row: {exc}") from exc


def _channel(label: str, format_name: str, line: int) -> str:
    channel = label.split(" - ", 1)[0] if format_name == "GRAINS" else label
    if not _CHANNEL.fullmatch(channel):
        raise _error(line, f"invalid channel label {label!r}")
    return channel


@lru_cache(maxsize=4096)
def _valid_day(day: str) -> bool:
    try:
        date.fromisoformat(day)
    except ValueError:
        return False
    return True


def _check_time(value: str, format_name: str, line: int) -> None:
    pattern = _GRAINS_TIME if format_name == "GRAINS" else _ARES_TIME
    match = pattern.fullmatch(value)
    valid = match is not None
    if match is not None:
        parts = match.groups()
        if format_name == "GRAINS":
            valid = _valid_day(parts[0])
            hour, minute, second = map(int, parts[1:4])
        else:
            year, day = map(int, parts[:2])
            valid = year > 0 and 1 <= day <= 365 + calendar.isleap(year)
            hour, minute, second = map(int, parts[2:5])
        valid = valid and hour < 24 and minute < 60 and second <= 60
    if not valid:
        raise _error(line, f"invalid {format_name} source timestamp {value!r}")


def _value(value: str, line: int, channel: str) -> Decimal | None:
    stripped = value.strip()
    if not stripped:
        return None
    if not _NUMBER.fullmatch(stripped):
        raise _error(line, f"invalid numeric value for {channel}: {value!r}")
    return Decimal(stripped)


def _descriptors(
    header_lines: list[str], channels: tuple[str, ...], delimiter: str
) -> tuple[ChannelDescriptor, ...]:
    known = set(channels)
    found: dict[str, ChannelDescriptor] = {}
    for line in header_lines:
        if line.startswith("#") or not line.strip():
            continue
        try:
            row = [field.strip('"') for field in next(csv.reader([line], delimiter=delimiter, strict=True))]
        except csv.Error:
            continue
        if len(row) < 4 or row[0] not in known or row[0] in found:
            continue
        found[row[0]] = ChannelDescriptor(row[0], row[1], row[2], row[3], tuple(row[4:]))
    return tuple(found[channel] for channel in channels if channel in found)


def decode_export(data: bytes) -> ParsedExport:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        line = data[:exc.start].count(b"\n") + 1
        raise _error(line, f"invalid UTF-8 at byte {exc.start}") from exc
    return parse_export(text)


def parse_export(text: str) -> ParsedExport:
    if not isinstance(text, str):
        raise TypeError("parse_export expects str; use decode_export for bytes")
    lines = text.removeprefix("\ufeff").splitlines()
    if not lines:
        raise _error(1, "empty export")
    if lines[0] == "# Exported Parameter(s) from GRAINS":
        format_name = "GRAINS"
    elif lines[0] == '"Parameter SINGLE Export from ARES"':
        format_name = "ARES"
    else:
        raise UnsupportedExportError(
            "line 1: unsupported export format (expected GRAINS or ARES SINGLE)"
        )

    header_index = None
    for index, line in enumerate(lines):
        if (format_name == "GRAINS" and line.startswith("# DATE TIME")) or (
            format_name == "ARES" and line.startswith('"DATE TIME"')
        ):
            header_index = index
            break
    if header_index is None:
        raise _error(len(lines), "missing DATE TIME data header")
    header_line = lines[header_index]
    delimiter = "\t" if format_name == "ARES" or "\t" in header_line else ","
    body_header = header_line.removeprefix("# ") if format_name == "GRAINS" else header_line
    fields = _fields(body_header, delimiter, header_index + 1)
    if format_name == "ARES" and fields[-1:] == [""]:
        fields.pop()
    if len(fields) < 2 or fields[0] != "DATE TIME":
        raise _error(header_index + 1, "invalid DATE TIME data header")
    labels = tuple(fields[1:])
    channels = tuple(_channel(label, format_name, header_index + 1) for label in labels)
    if len(set(channels)) != len(channels):
        raise _error(header_index + 1, "duplicate channel identifiers")

    metadata: dict[str, str] = {"delimiter": delimiter, "time_system": "unresolved"}
    metadata_lines: dict[str, int] = {}
    for index, line in enumerate(lines[:header_index]):
        name = line.removeprefix("# ").strip('"').strip()
        if name.endswith(":") and name != "Per-Orbit Statistics:":
            if index + 1 >= header_index:
                raise _error(index + 1, f"missing metadata value for {name}")
            key = name[:-1]
            if key in metadata:
                raise _error(index + 1, f"duplicate metadata field {key}")
            metadata[key] = lines[index + 1]
            metadata_lines[key] = index + 2

    for key in ("Parameter List", "Number of parameters"):
        if key not in metadata:
            raise _error(header_index + 1, f"missing {key} metadata")
    count_text = metadata["Number of parameters"].strip('"')
    if not re.fullmatch(r"[0-9]+", count_text) or int(count_text) != len(channels):
        raise _error(metadata_lines["Number of parameters"], "parameter count disagrees with data header")
    declared = _fields(metadata["Parameter List"], delimiter, metadata_lines["Parameter List"])
    if format_name == "ARES" and declared[-1:] == [""]:
        declared.pop()
    declared_channels = tuple(_channel(x, format_name, metadata_lines["Parameter List"]) for x in declared)
    if declared_channels != channels:
        raise _error(header_index + 1, "Parameter List disagrees with data header")

    descriptors = _descriptors(lines[:header_index], channels, delimiter)

    samples: list[ParsedSample] = []
    footer_index = None
    blank_index = None
    for index in range(header_index + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            if blank_index is None:
                blank_index = index
            continue
        row = _fields(line, delimiter, index + 1)
        if format_name == "ARES" and len(row) == 1 and row[0].strip() == _ARES_FOOTER:
            footer_index = index
            break
        if blank_index is not None:
            raise _error(blank_index + 1, "blank row inside data table")
        if format_name == "ARES" and len(row) == len(channels) + 2 and row[-1] == "":
            row.pop()
        if len(row) != len(channels) + 1:
            raise _error(index + 1, f"expected {len(channels) + 1} columns, found {len(row)}")
        _check_time(row[0], format_name, index + 1)
        values = tuple(_value(value, index + 1, channel) for channel, value in zip(channels, row[1:], strict=True))
        samples.append(ParsedSample(row[0], values, index + 1))

    raw_footer: tuple[str, ...] = ()
    if format_name == "ARES":
        if footer_index is None:
            raise _error(len(lines), "missing ARES written-sample footer")
        footer_rows = [(i, value) for i, value in enumerate(lines[footer_index + 1:], footer_index + 1) if value.strip()]
        if len(footer_rows) != 1:
            raise _error(footer_index + 1, "ARES footer must contain exactly one written-sample count")
        index, value = footer_rows[0]
        counts = _fields(value, delimiter, index + 1)
        if len(counts) != 1 or not re.fullmatch(r"[0-9]+", counts[0]):
            raise _error(index + 1, "invalid ARES written-sample count")
        if int(counts[0]) != len(samples):
            raise _error(index + 1, "ARES written-sample count disagrees with parsed rows")
        metadata["Number of all written samples"] = counts[0]
        raw_footer = tuple(lines[blank_index if blank_index is not None else footer_index:])
    if not samples:
        raise _error(header_index + 1, "export contains no samples")
    return ParsedExport(format_name, channels, tuple(samples), metadata,
                        tuple(lines[:header_index + 1]), labels, raw_footer, descriptors)
