from __future__ import annotations

import hashlib
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from srmwse import __version__

D9_BASE_URL = "https://pdssbn.astro.umd.edu/holdings/"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_INDEX_BYTES = 8 * 1024 * 1024

D9_DATASETS = {
    "cr1": "ro-x-srem-2-cr1-v1.0",
    "cvp2": "ro-x-srem-2-cvp2-v1.0",
    "ear1": "ro-x-srem-2-ear1-v1.0",
    "cr2": "ro-x-srem-2-cr2-v1.0",
    "mars": "ro-x-srem-2-mars-v1.0",
    "cr3": "ro-x-srem-2-cr3-v1.0",
    "ear2": "ro-x-srem-2-ear2-v1.0",
    "ast1": "ro-x-srem-2-ast1-v1.0",
    "ear3": "ro-x-srem-2-ear3-v1.0",
}

D9_COVERAGE = {
    "cr1": ("2004-08-30", "2004-09-05"),
    "cvp2": ("2004-09-06", "2004-10-13"),
    "ear1": ("2004-10-21", "2005-04-04"),
    "cr2": ("2005-04-05", "2006-07-28"),
    "mars": ("2006-07-29", "2007-05-28"),
    "cr3": ("2007-05-29", "2007-09-12"),
    "ear2": ("2007-09-13", "2008-01-27"),
    "ast1": ("2008-08-04", "2008-10-05"),
    "ear3": ("2009-09-14", "2009-12-13"),
}

SremBand = tuple[float, float | None]


@dataclass(frozen=True, slots=True)
class SremChannel:
    name: str
    index: int
    proton_mev: SremBand | None
    electron_mev: SremBand | None


_CHANNELS = (
    ("TC1", (27, None), (2.00, None)),
    ("S12", (26, None), (2.08, None)),
    ("S13", (27, None), (2.23, None)),
    ("S14", (24, 542), (3.20, None)),
    ("S15", (23, 434), (8.18, None)),
    ("TC2", (49, None), (2.80, None)),
    ("S25", (48, 270), None),
    ("C1", (43, 86), None),
    ("C2", (52, 278), None),
    ("C3", (76, 450), None),
    ("C4", (164, None), (8.10, None)),
    ("TC3", (12, None), (0.80, None)),
    ("S32", (12, None), (0.75, None)),
    ("S33", (12, None), (1.05, None)),
    ("S34", (12, None), (2.08, None)),
)

SREM_CHANNELS = {
    name: SremChannel(name, index, protons, electrons)
    for index, (name, protons, electrons) in enumerate(_CHANNELS)
}

PROTON_ONLY = tuple(c.name for c in SREM_CHANNELS.values() if c.electron_mev is None)

L1_MATCHED_CHANNEL = "TC2"

_CHECKSUM_LINE = re.compile(r"^([0-9a-f]{32})\s+(\S+)\s*$")
_SAFE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.?(?:/|$))[a-z0-9_.-]+(?:/[a-z0-9_.-]+)*$")
_PRODUCT = re.compile(r"^data/srem_l2_(\d{8})\.(lbl|tab)$")


class D9AccessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SremProduct:

    phase: str
    day: str
    label_path: str
    table_path: str


def _get(dataset: str, path: str, *, max_bytes: int) -> bytes:
    full = f"{dataset}/{path}"
    if not _SAFE_PATH.match(full):
        raise ValueError(f"Refusing to request an unexpected archive path: {full!r}")
    request = urllib.request.Request(
        D9_BASE_URL + full,
        headers={"User-Agent": f"SRMWSE/{__version__} (research archive client)"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise D9AccessError(f"{full}: response exceeds the configured byte limit")
    return payload


def dataset_for(phase: str) -> str:
    if phase not in D9_DATASETS:
        raise ValueError(f"Unknown D9 phase: {phase!r}")
    return D9_DATASETS[phase]


def phase_covering(first: str, last: str) -> str:
    matches = [phase for phase, (start, end) in D9_COVERAGE.items()
               if start <= first and last <= end]
    if not matches:
        raise D9AccessError(
            f"No single recorded phase spans {first}..{last}; coverage is "
            + ", ".join(f"{p} {s}..{e}" for p, (s, e) in sorted(D9_COVERAGE.items())))
    if len(matches) > 1:
        raise D9AccessError(f"{first}..{last} is spanned by several phases: {matches}")
    return matches[0]


def fetch_checksums(phase: str) -> dict[str, str]:
    dataset = dataset_for(phase)
    text = _get(dataset, "index/checksum.tab",
                max_bytes=MAX_INDEX_BYTES).decode("ascii", "strict")
    checksums: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = _CHECKSUM_LINE.match(line.strip())
        if match is None:
            raise D9AccessError(f"{dataset} checksum.tab line {number}: unrecognised entry")
        digest, path = match.group(1), match.group(2)
        if path in checksums and checksums[path] != digest:
            raise D9AccessError(f"{dataset} checksum.tab lists {path} twice, differently")
        checksums[path] = digest
    if not checksums:
        raise D9AccessError(f"{dataset} checksum.tab is empty")
    return checksums


def days_between(first: str, last: str) -> tuple[str, ...]:
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    if end < start:
        raise ValueError(f"{last} precedes {first}")
    span = (end - start).days
    return tuple((start + timedelta(days=offset)).isoformat()
                 for offset in range(span + 1))


def days_around(centre: str, half_width_days: int) -> tuple[str, ...]:
    if half_width_days < 0:
        raise ValueError("half_width_days cannot be negative")
    middle = date.fromisoformat(centre)
    return tuple((middle + timedelta(days=offset)).isoformat()
                 for offset in range(-half_width_days, half_width_days + 1))


def products_for(phase: str, days: tuple[str, ...], checksums: dict[str, str], *,
                 allow_missing: bool = False) -> list[SremProduct]:
    dataset_for(phase)
    found: list[SremProduct] = []
    missing: list[str] = []
    for day in days:
        date.fromisoformat(day)
        stem = f"data/srem_l2_{day.replace('-', '')}"
        label, table = f"{stem}.lbl", f"{stem}.tab"
        if label not in checksums:
            missing.append(day)
            continue
        if table not in checksums:
            raise D9AccessError(f"{label} is listed but {table} is not")
        found.append(SremProduct(phase, day, label, table))
    if not found:
        raise D9AccessError(f"No D9 products for {phase} on {list(days)}")
    if missing and not allow_missing:
        raise D9AccessError(
            f"{phase} does not cover {missing}; choose a phase whose dataset spans "
            f"the window, narrow the window, or pass allow_missing to survey across it"
        )
    return found


def missing_days(phase: str, days: tuple[str, ...],
                 checksums: dict[str, str]) -> list[str]:
    dataset_for(phase)
    return [day for day in days
            if f"data/srem_l2_{day.replace('-', '')}.lbl" not in checksums]


def retrieve(phase: str, path: str, checksums: dict[str, str],
             destination: Path) -> dict:
    dataset = dataset_for(phase)
    expected = checksums.get(path)
    if expected is None:
        raise D9AccessError(f"{path} is not listed in the {dataset} checksum manifest")
    if destination.exists():
        payload, origin = destination.read_bytes(), "cache"
    else:
        payload, origin = _get(dataset, path, max_bytes=MAX_FILE_BYTES), "network"
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    if digest != expected:
        raise D9AccessError(f"{path}: MD5 {digest} differs from the published {expected}")
    if origin == "network":
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(payload)
    return {"dataset_id": dataset, "archive_path": path, "byte_size": len(payload),
            "md5": digest, "checksum_origin": "publisher_manifest", "source": origin}


def count_rates(parsed) -> list[tuple[str, dict[str, float]]]:
    missing = -1.0e31
    samples: list[tuple[str, dict[str, float]]] = []
    for row in parsed.rows:
        if len(row.values) < 16:
            raise D9AccessError(
                f"record {row.record_number}: expected 15 count rates, found "
                f"{len(row.values) - 1}")
        rates = [float(value) for value in row.values[1:16]]
        if any(rate <= missing for rate in rates):
            continue
        samples.append((str(row.values[0]),
                        {name: rates[channel.index]
                         for name, channel in SREM_CHANNELS.items()}))
    return samples
