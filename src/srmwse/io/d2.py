from __future__ import annotations

import hashlib
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from srmwse import __version__

D2_DATASET_ID = "RO-X-HK-3-EDAC-V1.0"
D2_BASE_URL = "https://pdssbn.astro.umd.edu/holdings/ro-x-hk-3-edac-v1.0/"
D2_CHECKSUM_PATH = "index/checksum.tab"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_INDEX_BYTES = 8 * 1024 * 1024

D2_CHANNELS = {
    "aocs_and_dms_edac_cntr": ("NACW0D0A", "NDMW0D0A"),
    "navcam_a_edac_cntr": ("NACP1800", "NACP1801", "NACW1R0Q", "NACW1R0R"),
    "navcam_b_edac_cntr": ("NACP2800", "NACP2801", "NACW1R1K", "NACW1R1L"),
    "str_a_edac_cntr": ("NACP1300", "NACP1301", "NACW1L0P", "NACW1L0Q"),
    "str_b_edac_cntr": ("NACP2300", "NACP2301", "NACW1L1I", "NACW1L1J"),
    "str_nb_seu_found": ("NACW1K0H", "NACW1K2X"),
}

_CHECKSUM_LINE = re.compile(r"^([0-9a-f]{32})\s+(\S+)\s*$")
_SAFE_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.?(?:/|$))[a-z0-9_.]+(?:/[a-z0-9_.]+)*$")


class D2AccessError(RuntimeError):
    pass


D2_PARAMETERS = {
    "NACW0D0A": ("AOCS_CDMU", "unspecified", "BTSTP: EDAC Counter"),
    "NDMW0D0A": ("DMS", "unspecified", "EDAC counter"),
    "NACP1800": ("NAVCAM_A", "program_ram", "CAM A PRAM EDAC Sec Cntr"),
    "NACW1R0Q": ("NAVCAM_A", "program_ram", "CAM A Pr RAM EDAC Cntr"),
    "NACP1801": ("NAVCAM_A", "data_ram", "CAM A DRAM EDAC Sec Cntr"),
    "NACW1R0R": ("NAVCAM_A", "data_ram", "CAM A Dt RAM EDAC Cntr"),
    "NACP2800": ("NAVCAM_B", "program_ram", "CAM B PRAM EDAC Sec Cntr"),
    "NACW1R1K": ("NAVCAM_B", "program_ram", "CAM B Pr RAM EDAC Cntr"),
    "NACP2801": ("NAVCAM_B", "data_ram", "CAM B DRAM EDAC Sec Cntr"),
    "NACW1R1L": ("NAVCAM_B", "data_ram", "CAM B Dt RAM EDAC Cntr"),
    "NACP1300": ("STR_A", "program_ram", "STR A PRAM EDAC Sec Cntr"),
    "NACW1L0P": ("STR_A", "program_ram", "STR A Pr RAM EDAC Cntr"),
    "NACP1301": ("STR_A", "data_ram", "STR A DRAM EDAC Sec Cntr"),
    "NACW1L0Q": ("STR_A", "data_ram", "STR A Dt RAM EDAC Cntr"),
    "NACP2300": ("STR_B", "program_ram", "STR B PRAM EDAC Sec Cntr"),
    "NACW1L1I": ("STR_B", "program_ram", "STR B Pr RAM EDAC Cntr"),
    "NACP2301": ("STR_B", "data_ram", "STR B DRAM EDAC Sec Cntr"),
    "NACW1L1J": ("STR_B", "data_ram", "STR B Dt RAM EDAC Cntr"),
    "NACW1K0H": ("STR_A", "seu_flag", "STR A Nb SEU Found"),
    "NACW1K2X": ("STR_B", "seu_flag", "STR B Nb SEU Found"),
}

D2_FLAG_PARAMETERS = frozenset({"NACW1K0H", "NACW1K2X"})


def device_of(parameter: str) -> str:
    try:
        return D2_PARAMETERS[parameter][0]
    except KeyError:
        raise D2AccessError(
            f"{parameter} has no recorded device; refusing to treat it as independent"
        ) from None


@dataclass(frozen=True, slots=True)
class Product:

    channel: str
    parameter: str
    period: str
    label_path: str
    table_path: str


def _get(path: str, *, max_bytes: int) -> bytes:
    if not _SAFE_PATH.match(path):
        raise ValueError(f"Refusing to request an unexpected archive path: {path!r}")
    request = urllib.request.Request(
        D2_BASE_URL + path,
        headers={"User-Agent": f"SRMWSE/{__version__} (research archive client)"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise D2AccessError(f"{path}: response exceeds the configured byte limit")
    return payload


def fetch_checksums() -> dict[str, str]:
    text = _get(D2_CHECKSUM_PATH, max_bytes=MAX_INDEX_BYTES).decode("ascii", "strict")
    checksums: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = _CHECKSUM_LINE.match(line.strip())
        if match is None:
            raise D2AccessError(f"checksum.tab line {number}: unrecognised entry")
        digest, path = match.group(1), match.group(2)
        if path in checksums and checksums[path] != digest:
            raise D2AccessError(f"checksum.tab lists {path} twice with different digests")
        checksums[path] = digest
    if not checksums:
        raise D2AccessError("checksum.tab is empty")
    return checksums


_PRODUCT_STEM = re.compile(r"^ros_hk_([a-z0-9]+)_(\d{4}_(?:q[1-4]|\d{2}))$")


def products_for(channel: str, quarters: tuple[str, ...], checksums: dict[str, str]) -> list[Product]:
    if channel not in D2_CHANNELS:
        raise ValueError(f"Unknown D2 channel: {channel!r}")
    expected_parameters = set(D2_CHANNELS[channel])
    found: list[Product] = []
    for quarter in quarters:
        if not re.fullmatch(r"\d{4}_q[1-4]", quarter):
            raise ValueError(f"Quarter must look like 2006_q4, got {quarter!r}")
        prefix = f"data/{channel}/{quarter}/"
        for label_path in sorted(p for p in checksums if p.startswith(prefix) and p.endswith(".lbl")):
            match = _PRODUCT_STEM.match(label_path[len(prefix):-len(".lbl")])
            if match is None:
                raise D2AccessError(f"Unrecognised product name: {label_path}")
            parameter = match.group(1).upper()
            if parameter not in expected_parameters:
                raise D2AccessError(
                    f"{label_path} holds {parameter}, which the user guide does not "
                    f"list for {channel}"
                )
            table_path = label_path[:-len(".lbl")] + ".tab"
            if table_path not in checksums:
                raise D2AccessError(f"{label_path} is listed but {table_path} is not")
            found.append(Product(channel, parameter, match.group(2), label_path, table_path))
    if not found:
        raise D2AccessError(f"No D2 products found for {channel} in {list(quarters)}")
    return found


def retrieve(path: str, checksums: dict[str, str], destination: Path) -> dict:
    expected = checksums.get(path)
    if expected is None:
        raise D2AccessError(f"{path} is not listed in the archive checksum manifest")
    if destination.exists():
        payload, origin = destination.read_bytes(), "cache"
    else:
        payload, origin = _get(path, max_bytes=MAX_FILE_BYTES), "network"
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    if digest != expected:
        raise D2AccessError(f"{path}: MD5 {digest} differs from the published {expected}")
    if origin == "network":
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(payload)
    return {"archive_path": path, "byte_size": len(payload), "md5": digest,
            "checksum_origin": "publisher_manifest", "source": origin}
