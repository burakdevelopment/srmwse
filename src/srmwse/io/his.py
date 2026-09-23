from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


class HisLogParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HisEvent:

    event_id: int
    region: str
    reported_errors: int
    address_start: str | None
    address_end: str | None
    address_span: int | None
    read_source: str | None
    rollover_flagged: bool
    source_timestamp: str
    obt_coarse: int
    obt_fine: int
    source_line: int
    raw_line: str


@dataclass(frozen=True, slots=True)
class ParsedHisLog:
    format_name: str
    events: tuple[HisEvent, ...]
    regions: tuple[str, ...]
    metadata: dict[str, str]


_PREFIX = r"HIS: EVENT: (?P<event_id>\d+): HIS: "
_SUFFIX = (
    r"\s*\(Time (?P<shown>\d{4}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2}) "
    r"\[(?P<coarse>\d+), (?P<fine>\d+)\]\)"
)

_RANGE = re.compile(
    _PREFIX
    + r"(?P<region>SRAM|OTHER)\s+EDAC Correctable (?P<count>\d+) Errors Between "
    + r"0x(?P<start>[0-9a-fA-F]+) - 0x(?P<end>[0-9a-fA-F]+)\."
    + _SUFFIX
    + r"$"
)
_FPGA = re.compile(
    _PREFIX
    + r"CDH FPGA SRAM/BRAM had (?P<count>\d+) \(with rollover\) EDAC errors\. "
    + r"Last read source was (?P<source>FSW|FPGA)\. Single/Correctable occurred\."
    + _SUFFIX
    + r"$"
)

_FPGA_REGION = "CDH_FPGA"
_SIGNATURE = re.compile(r"^HIS: EVENT: \d+: HIS: ")


def _error(line: int, message: str) -> HisLogParseError:
    return HisLogParseError(f"line {line}: {message}")


def _check_shown_timestamp(value: str, line: int) -> None:
    try:
        datetime.strptime(value, "%Y/%m/%d-%H:%M:%S")
    except ValueError as exc:
        raise _error(line, f"invalid displayed timestamp {value!r}") from exc


def looks_like_his_log(text: str) -> bool:
    for line in text.splitlines():
        if line.strip():
            return bool(_SIGNATURE.match(line))
    return False


def decode_log(data: bytes) -> ParsedHisLog:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        line = data[: exc.start].count(b"\n") + 1
        raise _error(line, f"invalid UTF-8 at byte {exc.start}") from exc
    return parse_log(text)


def parse_log(text: str) -> ParsedHisLog:
    if not isinstance(text, str):
        raise TypeError("parse_log expects str; use decode_log for bytes")
    lines = text.removeprefix("﻿").splitlines()
    if not any(line.strip() for line in lines):
        raise _error(1, "empty log")
    if not looks_like_his_log(text):
        raise _error(1, "unsupported log format (expected HIS EDAC event messages)")

    events: list[HisEvent] = []
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        match = _RANGE.match(line)
        if match is not None:
            _check_shown_timestamp(match["shown"], index)
            start = int(match["start"], 16)
            end = int(match["end"], 16)
            if end < start:
                raise _error(index, "address range ends before it starts")
            events.append(
                HisEvent(
                    event_id=int(match["event_id"]),
                    region=match["region"],
                    reported_errors=int(match["count"]),
                    address_start=match["start"],
                    address_end=match["end"],
                    address_span=end - start,
                    read_source=None,
                    rollover_flagged=False,
                    source_timestamp=match["shown"],
                    obt_coarse=int(match["coarse"]),
                    obt_fine=int(match["fine"]),
                    source_line=index,
                    raw_line=line,
                )
            )
            continue
        match = _FPGA.match(line)
        if match is not None:
            _check_shown_timestamp(match["shown"], index)
            events.append(
                HisEvent(
                    event_id=int(match["event_id"]),
                    region=_FPGA_REGION,
                    reported_errors=int(match["count"]),
                    address_start=None,
                    address_end=None,
                    address_span=None,
                    read_source=match["source"],
                    rollover_flagged=True,
                    source_timestamp=match["shown"],
                    obt_coarse=int(match["coarse"]),
                    obt_fine=int(match["fine"]),
                    source_line=index,
                    raw_line=line,
                )
            )
            continue
        raise _error(index, f"unrecognised HIS message template: {line[:80]!r}")

    regions = tuple(dict.fromkeys(event.region for event in events))
    metadata = {
        "time_system": "unresolved",
        "onboard_clock_fields": "coarse,fine",
        "record_kind": "reported_error_events",
    }
    return ParsedHisLog("HIS_EDAC_MESSAGES", tuple(events), regions, metadata)
