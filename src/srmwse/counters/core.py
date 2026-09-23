from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntFlag
from math import isfinite
from urllib.parse import urlsplit


class QualityFlag(IntFlag):

    VALID = 0
    RESET = 1 << 0
    WRAP = 1 << 1
    GAP = 1 << 2
    CLOCK = 1 << 3
    MODE = 1 << 4
    SCRUB = 1 << 5
    SAT = 1 << 6
    THERMAL = 1 << 7
    SCHEMA = 1 << 8
    UNKNOWN = 1 << 9
    UNKNOWN_SEMANTICS = 1 << 10
    ORDERING = 1 << 11
    MISSING = 1 << 12

    Q0_VALID = VALID
    Q1_RESET = RESET
    Q2_WRAP = WRAP
    Q3_GAP = GAP
    Q4_CLOCK = CLOCK
    Q5_MODE = MODE
    Q6_SCRUB = SCRUB
    Q7_SAT = SAT
    Q8_THERMAL = THERMAL
    Q9_SCHEMA = SCHEMA
    Q10_UNKNOWN = UNKNOWN


_ALL_FLAGS = (1 << 13) - 1
_ENDPOINT_ISSUES = (
    QualityFlag.CLOCK
    | QualityFlag.MODE
    | QualityFlag.SCRUB
    | QualityFlag.SAT
    | QualityFlag.THERMAL
    | QualityFlag.SCHEMA
    | QualityFlag.UNKNOWN
    | QualityFlag.UNKNOWN_SEMANTICS
    | QualityFlag.ORDERING
    | QualityFlag.MISSING
)


@dataclass(frozen=True, slots=True)
class CounterSample:

    timestamp: datetime
    raw_counter: int | None
    source_timestamp: str
    quality_flags: QualityFlag = QualityFlag.VALID
    reset_confirmed: bool = False
    wrap_confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("timestamp must be timezone-aware UTC")
        if self.raw_counter is not None:
            if isinstance(self.raw_counter, bool) or not isinstance(self.raw_counter, int):
                raise TypeError("raw_counter must be an integer or None")
            if self.raw_counter < 0:
                raise ValueError("raw_counter cannot be negative")
        if not isinstance(self.source_timestamp, str) or not self.source_timestamp.strip():
            raise ValueError("source_timestamp must retain a nonempty original timestamp")
        if not isinstance(self.quality_flags, QualityFlag):
            raise TypeError("quality_flags must be a QualityFlag")
        if int(self.quality_flags) < 0 or int(self.quality_flags) & ~_ALL_FLAGS:
            raise ValueError("quality_flags contains unsupported bits")
        if not isinstance(self.reset_confirmed, bool) or not isinstance(self.wrap_confirmed, bool):
            raise TypeError("reset_confirmed and wrap_confirmed must be bool")
        if self.reset_confirmed and self.wrap_confirmed:
            raise ValueError("a transition cannot be confirmed as both reset and wrap")


@dataclass(frozen=True, slots=True)
class CounterSemantics:

    status: str = "unresolved"
    bit_width: int | None = None
    interval_exposure_supported: bool = False
    max_gap_s: float = 3600.0
    evidence_url: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"unresolved", "verified"}:
            raise ValueError("status must be 'unresolved' or 'verified'")
        if self.bit_width is not None:
            if isinstance(self.bit_width, bool) or not isinstance(self.bit_width, int):
                raise TypeError("bit_width must be an integer or None")
            if self.bit_width <= 0:
                raise ValueError("bit_width must be positive")
        if not isinstance(self.interval_exposure_supported, bool):
            raise TypeError("interval_exposure_supported must be bool")
        if isinstance(self.max_gap_s, bool) or not isinstance(self.max_gap_s, (int, float)):
            raise TypeError("max_gap_s must be a finite positive number")
        if not isfinite(self.max_gap_s) or self.max_gap_s <= 0:
            raise ValueError("max_gap_s must be a finite positive number")
        if self.evidence_url is not None:
            if not isinstance(self.evidence_url, str):
                raise TypeError("evidence_url must be a string or None")
            parsed = urlsplit(self.evidence_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("evidence_url must be an absolute HTTP(S) URL")
        if self.status == "verified" and self.evidence_url is None:
            raise ValueError("verified semantics require an evidence_url")
        if self.status != "verified" and self.interval_exposure_supported:
            raise ValueError("interval exposure requires verified semantics")


@dataclass(frozen=True, slots=True)
class CounterInterval:

    t_start_utc: datetime | None
    t_end_utc: datetime
    source_timestamp: str
    raw_counter: int | None
    count_delta: int | None
    exposure_s: float | None
    quality_flags: QualityFlag


def process_counter(
    samples: Sequence[CounterSample], semantics: CounterSemantics
) -> list[CounterInterval]:
    if not isinstance(semantics, CounterSemantics):
        raise TypeError("semantics must be CounterSemantics")
    if not isinstance(samples, Sequence):
        raise TypeError("samples must be a sequence of CounterSample objects")
    for sample in samples:
        if not isinstance(sample, CounterSample):
            raise TypeError("every sample must be a CounterSample")
        if (
            sample.raw_counter is not None
            and semantics.bit_width is not None
            and sample.raw_counter.bit_length() > semantics.bit_width
        ):
            raise ValueError("raw_counter is outside the declared unsigned bit width")

    rows: list[CounterInterval] = []
    previous: CounterSample | None = None
    previous_endpoint_flags = QualityFlag.VALID
    latest_timestamp: datetime | None = None
    for sample in samples:
        flags = sample.quality_flags
        if semantics.status != "verified":
            flags |= QualityFlag.UNKNOWN_SEMANTICS
        if sample.raw_counter is None:
            flags |= QualityFlag.MISSING
        if sample.reset_confirmed:
            flags |= QualityFlag.RESET
        if sample.wrap_confirmed:
            flags |= QualityFlag.WRAP
        if latest_timestamp is not None and sample.timestamp <= latest_timestamp:
            flags |= QualityFlag.ORDERING
        endpoint_flags = flags & _ENDPOINT_ISSUES
        delta: int | None = None
        exposure: float | None = None
        if previous is not None:
            flags |= previous_endpoint_flags
            elapsed = (sample.timestamp - previous.timestamp).total_seconds()
            if elapsed > semantics.max_gap_s:
                flags |= QualityFlag.GAP
            if elapsed <= 0:
                flags |= QualityFlag.ORDERING

            if flags & QualityFlag.WRAP and (
                not sample.wrap_confirmed or semantics.bit_width is None
            ):
                flags |= QualityFlag.UNKNOWN
            blocked = flags & ~QualityFlag.WRAP
            if not blocked and previous.raw_counter is not None and sample.raw_counter is not None:
                candidate = sample.raw_counter - previous.raw_counter
                if candidate < 0:
                    if sample.wrap_confirmed and semantics.bit_width is not None:
                        delta = (1 << semantics.bit_width) + candidate
                    else:
                        flags |= QualityFlag.UNKNOWN
                elif sample.wrap_confirmed:
                    flags |= QualityFlag.UNKNOWN
                else:
                    delta = candidate
                if delta is not None and semantics.interval_exposure_supported:
                    exposure = elapsed
        rows.append(
            CounterInterval(
                t_start_utc=previous.timestamp if previous is not None else None,
                t_end_utc=sample.timestamp,
                source_timestamp=sample.source_timestamp,
                raw_counter=sample.raw_counter,
                count_delta=delta,
                exposure_s=exposure,
                quality_flags=flags,
            )
        )
        previous = sample
        previous_endpoint_flags = endpoint_flags
        if latest_timestamp is None or sample.timestamp > latest_timestamp:
            latest_timestamp = sample.timestamp
    return rows
