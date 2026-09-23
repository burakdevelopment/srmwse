from calendar import isleap
from datetime import UTC, datetime
import re


def parse_timestamp(value: str, *, time_system: str, format_name: str) -> datetime:
    if time_system != "UTC":
        raise ValueError("Only explicitly verified UTC is supported; clock conversion is required")
    if format_name == "iso":
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value):
            raise ValueError("Expected a full date and time with seconds")
        if result.tzinfo is not None and result.utcoffset().total_seconds() != 0:
            raise ValueError("Non-UTC offset contradicts UTC source contract")
        return result.replace(tzinfo=UTC)
    if format_name == "ares_doy":
        if not re.fullmatch(r"\d{4} \d{3} \d{2} \d{2} \d{2}(?:\.\d{1,6})?", value):
            raise ValueError("Expected ARES YYYY DDD HH MM SS[.ffffff]")
        year, day, *_ = value.split()
        if not 1 <= int(day) <= (366 if isleap(int(year)) else 365):
            raise ValueError("Day of year is outside the source year")
        fmt = "%Y %j %H %M %S.%f" if "." in value else "%Y %j %H %M %S"
        return datetime.strptime(value, fmt).replace(tzinfo=UTC)
    raise ValueError(f"Unsupported timestamp format: {format_name}")
