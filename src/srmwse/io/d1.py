from datetime import UTC, datetime, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import urllib.request
import zipfile

from srmwse import __version__

D1_METADATA_URL = "https://api.figshare.com/v2/articles/22700146/versions/1"
D1_DOWNLOAD_URL = "https://ndownloader.figshare.com/files/40379297"
D1_DATASET_ID = "10.25392/leicester.data.22700146.v1"
D1_SIZE = 3_067_650
D1_MD5 = "0c011bfa14936c688b6e1beebdbd68bb"
D1_SHA256 = "bc377daddbb58a0ef44fc258b93ba808267d13783df6579f0e1da778350810f0"
D1_LICENSE = "https://creativecommons.org/licenses/by/4.0/"
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_url(url: str, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"SRMWSE/{__version__} (research archive client)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError("Archive response exceeds the configured byte limit")
    return payload


def validate_metadata(metadata: dict) -> dict:
    if not isinstance(metadata, dict):
        raise ValueError("D1 metadata must be a JSON object")
    if (type(metadata.get("id")) is not int or metadata.get("id") != 22700146
            or type(metadata.get("version")) is not int or metadata.get("version") != 1):
        raise ValueError("Unexpected D1 article identity or version")
    if metadata.get("doi") != D1_DATASET_ID:
        raise ValueError("Unexpected D1 dataset DOI")
    license_data = metadata.get("license")
    license_url = license_data.get("url") if isinstance(license_data, dict) else None
    if not isinstance(license_url, str) or license_url.rstrip("/") != D1_LICENSE.rstrip("/"):
        raise ValueError("D1 license differs from the reviewed metadata")
    files = metadata.get("files", [])
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
        raise ValueError("D1 v1 file inventory changed")
    file = files[0]
    expected = {"id": 40379297, "name": "EDAC.zip", "size": D1_SIZE,
                "download_url": D1_DOWNLOAD_URL, "computed_md5": D1_MD5}
    if any(type(file.get(key)) is not type(value) or file.get(key) != value
           for key, value in expected.items()):
        raise ValueError("D1 file contract differs from the reviewed metadata")
    return metadata


def inspect_source() -> dict:
    return validate_metadata(json.loads(read_url(D1_METADATA_URL, max_bytes=1024 * 1024)))


def validate_archive(payload: bytes, *, expected_sha256: str | None = None) -> str:
    if len(payload) != D1_SIZE:
        raise ValueError(f"D1 byte size mismatch: expected {D1_SIZE}, received {len(payload)}")
    if hashlib.md5(payload, usedforsecurity=False).hexdigest() != D1_MD5:
        raise ValueError("D1 checksum mismatch against the publisher's versioned metadata")
    digest = sha256_bytes(payload)
    if digest != (expected_sha256 or D1_SHA256):
        raise ValueError("D1 SHA-256 differs from the frozen snapshot")
    return digest


def write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"Refusing to overwrite immutable source: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _validate_receipt(receipt: dict, digest: str) -> str:
    if not isinstance(receipt, dict):
        raise ValueError("D1 receipt must be a JSON object")
    expected = {
        "source_id": "D1", "canonical_url": D1_DOWNLOAD_URL,
        "dataset_id": D1_DATASET_ID, "release": "1", "filename": "EDAC.zip",
        "byte_size": D1_SIZE, "sha256": digest, "publisher_md5": D1_MD5,
        "license": "CC-BY-4.0", "license_url": D1_LICENSE,
        "time_system": "unresolved", "local_path": "D1/v1/EDAC.zip",
        "metadata_url": D1_METADATA_URL,
    }
    if any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value
           for key, value in expected.items()):
        raise ValueError("D1 receipt does not match the frozen source contract")
    metadata_digest = receipt.get("metadata_sha256")
    if (not isinstance(metadata_digest, str) or len(metadata_digest) != 64
            or any(char not in "0123456789abcdef" for char in metadata_digest)):
        raise ValueError("D1 receipt has no valid metadata SHA-256")
    metadata_filename = f"metadata-{metadata_digest}.json"
    if receipt.get("metadata_filename") != metadata_filename:
        raise ValueError("D1 receipt metadata filename does not match its checksum")
    retrieved = receipt.get("retrieval_utc")
    if not isinstance(retrieved, str):
        raise ValueError("D1 receipt has no valid UTC retrieval timestamp")
    try:
        timestamp = datetime.fromisoformat(retrieved)
    except ValueError as error:
        raise ValueError("D1 receipt has no valid UTC retrieval timestamp") from error
    if timestamp.utcoffset() != timedelta(0):
        raise ValueError("D1 receipt retrieval timestamp must be timezone-aware UTC")
    parser_version = receipt.get("parser_version")
    if not isinstance(parser_version, str) or not parser_version.strip():
        raise ValueError("D1 receipt has no parser version")
    return metadata_filename


def fetch_d1(data_dir: Path, *, expected_sha256: str | None = None) -> dict:
    directory = data_dir / "D1" / "v1"
    archive = directory / "EDAC.zip"
    receipt_path = directory / "receipt.json"
    if archive.exists():
        digest = validate_archive(archive.read_bytes(), expected_sha256=expected_sha256)
        if not receipt_path.exists():
            raise ValueError("Cached D1 archive has no provenance receipt; use a new data directory")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        metadata_path = directory / _validate_receipt(receipt, digest)
        if metadata_path.resolve().parent != directory.resolve():
            raise ValueError("Cached metadata path escapes the D1 cache directory")
        try:
            metadata_payload = metadata_path.read_bytes()
        except OSError as error:
            raise ValueError("Cached D1 metadata is missing or unreadable") from error
        if sha256_bytes(metadata_payload) != receipt["metadata_sha256"]:
            raise ValueError("Cached metadata checksum mismatch")
        validate_metadata(json.loads(metadata_payload))
        return receipt
    metadata_payload = read_url(D1_METADATA_URL, max_bytes=1024 * 1024)
    validate_metadata(json.loads(metadata_payload))
    payload = read_url(D1_DOWNLOAD_URL)
    digest = validate_archive(payload, expected_sha256=expected_sha256)
    metadata_digest = sha256_bytes(metadata_payload)
    receipt = {
        "source_id": "D1", "canonical_url": D1_DOWNLOAD_URL,
        "dataset_id": D1_DATASET_ID, "release": "1", "retrieval_utc": utc_now(),
        "filename": "EDAC.zip", "byte_size": len(payload), "sha256": digest,
        "publisher_md5": D1_MD5, "license": "CC-BY-4.0", "license_url": D1_LICENSE,
        "time_system": "unresolved", "parser_version": __version__,
        "local_path": "D1/v1/EDAC.zip", "metadata_url": D1_METADATA_URL,
        "metadata_filename": f"metadata-{metadata_digest}.json", "metadata_sha256": metadata_digest,
    }
    write_immutable(directory / receipt["metadata_filename"], metadata_payload)
    write_immutable(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode())
    write_immutable(archive, payload)
    return receipt


def audit_archive(archive: Path, *, expected_sha256: str | None = None) -> dict:
    payload = archive.read_bytes()
    digest = validate_archive(payload, expected_sha256=expected_sha256)
    members = []
    with zipfile.ZipFile(BytesIO(payload)) as source:
        names = source.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive member names")
        if sum(info.file_size for info in source.infolist()) > 128 * 1024 * 1024:
            raise ValueError("Uncompressed archive exceeds audit budget")
        for info in source.infolist():
            if info.is_dir():
                continue
            content = source.read(info)
            members.append({"filename": info.filename, "byte_size": len(content),
                            "sha256": sha256_bytes(content)})
    return {"source_id": "D1", "dataset_id": D1_DATASET_ID, "archive_sha256": digest,
            "parser_version": __version__, "status": "structural_audit_only",
            "time_system": "unresolved", "quantitative_channels": [], "members": members}
