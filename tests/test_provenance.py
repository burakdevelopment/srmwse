from copy import deepcopy
from contextlib import ExitStack
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from srmwse.io import d1
from srmwse.io.d1 import (D1_DATASET_ID, D1_DOWNLOAD_URL, D1_LICENSE,
                         D1_MD5, D1_SIZE, read_url, validate_archive, validate_metadata)


def metadata_fixture():
    return {"id": 22700146, "version": 1, "doi": D1_DATASET_ID,
            "license": {"url": D1_LICENSE},
            "files": [{"id": 40379297, "name": "EDAC.zip", "size": D1_SIZE,
                       "download_url": D1_DOWNLOAD_URL, "computed_md5": D1_MD5}]}


class ProvenanceTests(unittest.TestCase):
    def test_pinned_metadata_is_accepted(self):
        self.assertEqual(validate_metadata(metadata_fixture())["version"], 1)

    def test_changed_identity_or_license_fails(self):
        for key, value in [("version", 2), ("id", 9), ("doi", "other"),
                           ("license", {"url": "https://example.org"}), ("files", [])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                source = deepcopy(metadata_fixture())
                source[key] = value
                validate_metadata(source)

    def test_changed_publisher_file_fails(self):
        for key, value in [("size", 0), ("id", 8), ("computed_md5", "0" * 32)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                source = metadata_fixture()
                source["files"][0][key] = value
                validate_metadata(source)

    def test_malformed_metadata_fails_with_contract_error(self):
        for source in [None, [], {**metadata_fixture(), "license": None},
                       {**metadata_fixture(), "license": {"url": None}},
                       {**metadata_fixture(), "files": None},
                       {**metadata_fixture(), "files": [None]},
                       {**metadata_fixture(), "version": True},
                       {**metadata_fixture(), "version": 1.0}]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_metadata(source)

    def test_bad_archive_size_and_digest_fail(self):
        for payload in [b"", b"x" * D1_SIZE]:
            with self.assertRaises(ValueError):
                validate_archive(payload)

    @patch("srmwse.io.d1.urllib.request.urlopen")
    def test_network_reads_are_bounded(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b"12345"
        with self.assertRaises(ValueError):
            read_url(D1_DOWNLOAD_URL, max_bytes=4)

    def test_audit_uses_the_same_bytes_it_validates(self):
        stream = BytesIO()
        member = b"original fixture bytes"
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("source.txt", member)
        payload = stream.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        with patch.object(Path, "read_bytes", return_value=payload) as read_bytes, \
                patch.object(d1, "D1_SIZE", len(payload)), \
                patch.object(d1, "D1_MD5", hashlib.md5(payload, usedforsecurity=False).hexdigest()):
            result = d1.audit_archive(Path("nonexistent-synthetic-archive.zip"), expected_sha256=digest)
        read_bytes.assert_called_once_with()
        self.assertEqual(result["archive_sha256"], digest)
        self.assertEqual(result["members"][0]["sha256"], hashlib.sha256(member).hexdigest())


class OfflineCacheTests(unittest.TestCase):

    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.data_dir = Path(self.resources.enter_context(TemporaryDirectory()))
        self.directory = self.data_dir / "D1" / "v1"
        self.archive = self.directory / "EDAC.zip"
        self.receipt_path = self.directory / "receipt.json"
        self.payload = b"SYNTHETIC D1 CACHE FIXTURE, NOT SCIENTIFIC DATA"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.md5 = hashlib.md5(self.payload, usedforsecurity=False).hexdigest()
        self.resources.enter_context(patch.object(d1, "D1_SIZE", len(self.payload)))
        self.resources.enter_context(patch.object(d1, "D1_MD5", self.md5))
        self.network = self.resources.enter_context(
            patch.object(d1, "read_url", side_effect=AssertionError("Offline test attempted network access"))
        )
        self.metadata = metadata_fixture()
        self.metadata["files"][0].update(size=len(self.payload), computed_md5=self.md5)
        self.metadata_payload = json.dumps(self.metadata).encode()

    def fetch(self):
        return d1.fetch_d1(self.data_dir, expected_sha256=self.digest)

    def make_cache(self):
        with patch.object(d1, "read_url", side_effect=[self.metadata_payload, self.payload]), \
                patch.object(d1, "utc_now", return_value="2026-09-14T15:00:00Z"):
            return self.fetch()

    def replace_receipt(self, receipt):
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    def test_frozen_sha_is_required_even_when_publisher_checks_match(self):
        self.assertEqual(d1.D1_SHA256,
                         "bc377daddbb58a0ef44fc258b93ba808267d13783df6579f0e1da778350810f0")
        with self.assertRaisesRegex(ValueError, "frozen snapshot"):
            d1.validate_archive(self.payload)
        self.assertEqual(d1.validate_archive(self.payload, expected_sha256=self.digest), self.digest)
        with self.assertRaisesRegex(ValueError, "frozen snapshot"):
            d1.validate_archive(self.payload, expected_sha256="0" * 64)

    def test_fetch_preserves_source_bytes_and_original_receipt(self):
        receipt = self.make_cache()
        original_receipt_bytes = self.receipt_path.read_bytes()
        self.assertEqual(self.archive.read_bytes(), self.payload)
        self.assertEqual((self.directory / receipt["metadata_filename"]).read_bytes(),
                         self.metadata_payload)
        with patch.object(d1, "utc_now", side_effect=AssertionError("Cache retrieval was redated")):
            self.assertEqual(self.fetch(), receipt)
        self.assertEqual(self.receipt_path.read_bytes(), original_receipt_bytes)
        self.network.assert_not_called()

    def test_cached_archive_cannot_bypass_frozen_sha_default(self):
        self.make_cache()
        with self.assertRaisesRegex(ValueError, "frozen snapshot"):
            d1.fetch_d1(self.data_dir)
        self.network.assert_not_called()

    def test_missing_receipt_does_not_invent_retrieval_provenance(self):
        self.make_cache()
        self.receipt_path.unlink()
        with self.assertRaisesRegex(ValueError, "no provenance receipt"):
            self.fetch()
        self.assertFalse(self.receipt_path.exists())
        self.network.assert_not_called()

    def test_every_required_receipt_field_is_checked(self):
        receipt = self.make_cache()
        for key in receipt:
            with self.subTest(missing=key), self.assertRaises(ValueError):
                incomplete = {field: value for field, value in receipt.items() if field != key}
                self.replace_receipt(incomplete)
                self.fetch()
        self.network.assert_not_called()

    def test_wrong_receipt_identity_and_license_cannot_relabel_cached_bytes(self):
        receipt = self.make_cache()
        changes = {
            "source_id": "D2", "canonical_url": "https://example.org/different",
            "dataset_id": "other", "release": "2", "filename": "other.zip",
            "byte_size": len(self.payload) + 1, "sha256": "0" * 64,
            "publisher_md5": "0" * 32, "license": "CC0", "license_url": "https://example.org/",
            "time_system": "UTC", "local_path": "elsewhere/EDAC.zip",
            "metadata_url": "https://example.org/unversioned",
            "retrieval_utc": "2026-09-14T15:00:00", "parser_version": "",
        }
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.replace_receipt({**receipt, key: value})
                self.fetch()
        self.network.assert_not_called()

    def test_malformed_receipts_fail_with_contract_errors(self):
        receipt = self.make_cache()
        variants = [None, [], {**receipt, "metadata_sha256": None},
                    {**receipt, "metadata_sha256": "g" * 64},
                    {**receipt, "byte_size": float(len(self.payload))},
                    {**receipt, "retrieval_utc": "invalid"},
                    {**receipt, "retrieval_utc": "2026-09-14T15:00:00+03:00"}]
        for variant in variants:
            with self.subTest(receipt=variant), self.assertRaises(ValueError):
                self.replace_receipt(variant)
                self.fetch()

    def test_metadata_filename_cannot_escape_cache_directory(self):
        receipt = self.make_cache()
        outside = self.directory.parent / "outside.json"
        outside.write_bytes(self.metadata_payload)
        names = ["../outside.json", "..\\outside.json", str(outside.resolve()),
                 "nested/" + receipt["metadata_filename"]]
        for filename in names:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.replace_receipt({**receipt, "metadata_filename": filename})
                self.fetch()
        self.assertEqual(outside.read_bytes(), self.metadata_payload)
        self.network.assert_not_called()

    def test_corrupt_cached_archive_is_rejected_without_redownload(self):
        self.make_cache()
        for corrupted in (self.payload[:-1], b"X" + self.payload[1:]):
            with self.subTest(payload=corrupted), self.assertRaises(ValueError):
                self.archive.write_bytes(corrupted)
                self.fetch()
        self.network.assert_not_called()

    def test_missing_or_corrupt_metadata_is_rejected(self):
        receipt = self.make_cache()
        metadata_path = self.directory / receipt["metadata_filename"]
        metadata_path.write_bytes(self.metadata_payload + b" ")
        with self.assertRaisesRegex(ValueError, "metadata checksum"):
            self.fetch()
        metadata_path.unlink()
        with self.assertRaisesRegex(ValueError, "missing or unreadable"):
            self.fetch()
        self.network.assert_not_called()

    def test_self_consistent_hash_cannot_bypass_pinned_metadata_contract(self):
        receipt = self.make_cache()
        metadata = deepcopy(self.metadata)
        metadata["license"]["url"] = "https://example.org/not-reviewed"
        payload = json.dumps(metadata).encode()
        digest = hashlib.sha256(payload).hexdigest()
        filename = f"metadata-{digest}.json"
        (self.directory / filename).write_bytes(payload)
        self.replace_receipt({**receipt, "metadata_filename": filename, "metadata_sha256": digest})
        with self.assertRaisesRegex(ValueError, "license"):
            self.fetch()
        self.network.assert_not_called()

    def test_original_parser_version_survives_a_cache_read(self):
        receipt = self.make_cache()
        receipt["parser_version"] = "0.0.1"
        self.replace_receipt(receipt)
        self.assertEqual(self.fetch()["parser_version"], "0.0.1")

    def test_existing_receipt_is_immutable_even_if_archive_is_missing(self):
        self.make_cache()
        original = self.receipt_path.read_bytes()
        self.archive.unlink()
        with patch.object(d1, "read_url", side_effect=[self.metadata_payload, self.payload]), \
                patch.object(d1, "utc_now", return_value="2026-09-15T15:00:00Z"):
            with self.assertRaisesRegex(ValueError, "immutable source"):
                self.fetch()
        self.assertEqual(self.receipt_path.read_bytes(), original)
        self.assertFalse(self.archive.exists())

    def test_immutable_write_is_idempotent_and_rejects_changed_bytes(self):
        path = self.data_dir / "immutable.bin"
        d1.write_immutable(path, self.payload)
        d1.write_immutable(path, self.payload)
        with self.assertRaisesRegex(ValueError, "immutable source"):
            d1.write_immutable(path, b"changed source")
        self.assertEqual(path.read_bytes(), self.payload)
