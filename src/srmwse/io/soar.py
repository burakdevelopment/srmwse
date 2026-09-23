from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from srmwse import __version__

TAP_SYNC_URL = "https://soar.esac.esa.int/soar-sl-tap/tap/sync"
DATA_URL = "https://soar.esac.esa.int/soar-sl-tap/data"

CDF_V3_MAGIC = 0xCDF30001
CDF_COMPRESSED = 0xCCCC0001
CDF_UNCOMPRESSED = 0x0000FFFF

MAX_QUERY_BYTES = 4 * 1024 * 1024
MAX_ITEM_BYTES = 16 * 1024 * 1024


class SoarAccessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DataItem:

    data_item_id: str
    filename: str
    level: str
    descriptor: str
    begin_time: str
    end_time: str
    filesize: int
    file_format: str


def _get(url: str, *, max_bytes: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": f"SRMWSE/{__version__} (research archive client)"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise SoarAccessError("Archive response exceeds the configured byte limit")
    return payload


def query(adql: str) -> list[dict]:
    url = f"{TAP_SYNC_URL}?" + urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql}
    )
    document = json.loads(_get(url, max_bytes=MAX_QUERY_BYTES))
    metadata = document.get("metadata")
    rows = document.get("data")
    if not isinstance(metadata, list) or not isinstance(rows, list):
        raise SoarAccessError("TAP response is not in the expected metadata/data shape")
    columns = [column["name"] for column in metadata]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def find_data_items(
    *, instrument: str, descriptor: str, level: str, start: str, end: str
) -> list[DataItem]:
    for value in (instrument, descriptor, level, start, end):
        if "'" in value or "\\" in value:
            raise ValueError("Query parameters must not contain quotes or backslashes")
    rows = query(
        "SELECT data_item_id, filename, level, descriptor, begin_time, end_time, "
        "filesize, file_format FROM v_sc_data_item "
        f"WHERE instrument='{instrument}' AND descriptor='{descriptor}' "
        f"AND level='{level}' AND begin_time>='{start}' AND begin_time<'{end}' "
        "ORDER BY begin_time"
    )
    items = []
    for row in rows:
        try:
            items.append(
                DataItem(
                    data_item_id=row["data_item_id"], filename=row["filename"],
                    level=row["level"], descriptor=row["descriptor"],
                    begin_time=row["begin_time"], end_time=row["end_time"],
                    filesize=int(row["filesize"]), file_format=row["file_format"],
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SoarAccessError(f"Unexpected TAP row shape: {error}") from error
    return items


def describe_container(payload: bytes) -> dict:
    if len(payload) < 8:
        raise SoarAccessError("Response is too short to be a CDF container")
    magic = int.from_bytes(payload[:4], "big")
    marker = int.from_bytes(payload[4:8], "big")
    if magic != CDF_V3_MAGIC:
        raise SoarAccessError(f"Expected a CDF v3 container, found magic 0x{magic:08X}")
    if marker == CDF_COMPRESSED:
        compression = "compressed"
    elif marker == CDF_UNCOMPRESSED:
        compression = "uncompressed"
    else:
        compression = f"unknown:0x{marker:08X}"
    return {"container": "CDF", "version_magic": f"0x{magic:08X}",
            "compression": compression, "decoded": False}


def download_item(item: DataItem) -> bytes:
    url = f"{DATA_URL}?" + urllib.parse.urlencode(
        {"retrieval_type": "LAST_PRODUCT", "data_item_id": item.data_item_id,
         "product_type": "SCIENCE"}
    )
    payload = _get(url, max_bytes=MAX_ITEM_BYTES)
    if len(payload) != item.filesize:
        raise SoarAccessError(
            f"{item.data_item_id}: archive declared {item.filesize} bytes, received {len(payload)}"
        )
    return payload


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
