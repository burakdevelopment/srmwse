import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from srmwse.io import d2
from srmwse.io.d2 import D2AccessError

PAYLOAD = b"PDS_VERSION_ID = PDS3\n"
DIGEST = hashlib.md5(PAYLOAD, usedforsecurity=False).hexdigest()
LABEL = "data/aocs_and_dms_edac_cntr/2006_q4/ros_hk_nacw0d0a_2006_q4.lbl"
TABLE = "data/aocs_and_dms_edac_cntr/2006_q4/ros_hk_nacw0d0a_2006_q4.tab"


def manifest(*entries):
    return "\n".join(f"{digest}  {path}" for digest, path in entries).encode("ascii") + b"\n"


class ChecksumManifestTests(unittest.TestCase):
    def test_manifest_is_keyed_by_archive_path(self):
        with patch.object(d2, "_get", return_value=manifest((DIGEST, LABEL))):
            self.assertEqual(d2.fetch_checksums(), {LABEL: DIGEST})

    def test_unrecognised_line_is_refused(self):
        with patch.object(d2, "_get", return_value=b"not a checksum line\n"):
            with self.assertRaises(D2AccessError):
                d2.fetch_checksums()

    def test_contradictory_duplicate_entry_is_refused(self):
        payload = manifest((DIGEST, LABEL), ("0" * 32, LABEL))
        with patch.object(d2, "_get", return_value=payload):
            with self.assertRaises(D2AccessError):
                d2.fetch_checksums()

    def test_identical_duplicate_entry_is_tolerated(self):
        with patch.object(d2, "_get", return_value=manifest((DIGEST, LABEL), (DIGEST, LABEL))):
            self.assertEqual(d2.fetch_checksums(), {LABEL: DIGEST})

    def test_empty_manifest_is_refused(self):
        with patch.object(d2, "_get", return_value=b"\n"):
            with self.assertRaises(D2AccessError):
                d2.fetch_checksums()


class ProductSelectionTests(unittest.TestCase):
    CHECKSUMS = {LABEL: DIGEST, TABLE: DIGEST}

    def test_products_are_selected_from_the_manifest(self):
        found, = d2.products_for("aocs_and_dms_edac_cntr", ("2006_q4",), self.CHECKSUMS)
        self.assertEqual((found.parameter, found.period), ("NACW0D0A", "2006_q4"))
        self.assertEqual(found.table_path, TABLE)

    def test_monthly_products_inside_a_quarterly_directory_are_found(self):
        stem = "data/str_a_edac_cntr/2005_q1/ros_hk_nacp1301_2005_01"
        checksums = {f"{stem}.lbl": DIGEST, f"{stem}.tab": DIGEST}
        found, = d2.products_for("str_a_edac_cntr", ("2005_q1",), checksums)
        self.assertEqual((found.parameter, found.period), ("NACP1301", "2005_01"))

    def test_every_product_in_the_directory_is_taken(self):
        entries = {}
        for parameter in ("nacp1300", "nacp1301"):
            for month in ("01", "02"):
                stem = f"data/str_a_edac_cntr/2005_q1/ros_hk_{parameter}_2005_{month}"
                entries[f"{stem}.lbl"] = DIGEST
                entries[f"{stem}.tab"] = DIGEST
        self.assertEqual(len(d2.products_for("str_a_edac_cntr", ("2005_q1",), entries)), 4)

    def test_unexpected_parameter_in_a_channel_directory_is_refused(self):
        stem = "data/str_a_edac_cntr/2005_q1/ros_hk_nacw0d0a_2005_01"
        checksums = {f"{stem}.lbl": DIGEST, f"{stem}.tab": DIGEST}
        with self.assertRaises(D2AccessError):
            d2.products_for("str_a_edac_cntr", ("2005_q1",), checksums)

    def test_unrecognised_product_name_is_refused(self):
        stem = "data/str_a_edac_cntr/2005_q1/unexpected_file"
        checksums = {f"{stem}.lbl": DIGEST, f"{stem}.tab": DIGEST}
        with self.assertRaises(D2AccessError):
            d2.products_for("str_a_edac_cntr", ("2005_q1",), checksums)

    def test_unknown_channel_is_refused(self):
        with self.assertRaises(ValueError):
            d2.products_for("not_a_channel", ("2006_q4",), self.CHECKSUMS)

    def test_malformed_quarter_is_refused(self):
        with self.assertRaises(ValueError):
            d2.products_for("aocs_and_dms_edac_cntr", ("2006Q4",), self.CHECKSUMS)

    def test_label_without_its_table_is_refused(self):
        with self.assertRaises(D2AccessError):
            d2.products_for("aocs_and_dms_edac_cntr", ("2006_q4",), {LABEL: DIGEST})

    def test_empty_selection_is_refused_rather_than_returned(self):
        with self.assertRaises(D2AccessError):
            d2.products_for("str_a_edac_cntr", ("2006_q4",), self.CHECKSUMS)


class RetrievalTests(unittest.TestCase):
    def test_published_digest_is_verified_and_bytes_are_stored(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "out.lbl"
            with patch.object(d2, "_get", return_value=PAYLOAD):
                record = d2.retrieve(LABEL, {LABEL: DIGEST}, destination)
            self.assertEqual(record["md5"], DIGEST)
            self.assertEqual(record["checksum_origin"], "publisher_manifest")
            self.assertEqual(destination.read_bytes(), PAYLOAD)

    def test_digest_disagreement_is_refused(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "out.lbl"
            with patch.object(d2, "_get", return_value=b"different bytes"):
                with self.assertRaises(D2AccessError):
                    d2.retrieve(LABEL, {LABEL: DIGEST}, destination)
            self.assertFalse(destination.exists())

    def test_path_absent_from_the_manifest_is_refused(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(D2AccessError):
                d2.retrieve(LABEL, {}, Path(directory) / "out.lbl")

    def test_cached_file_is_revalidated_rather_than_trusted(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "out.lbl"
            destination.write_bytes(b"tampered")
            with self.assertRaises(D2AccessError):
                d2.retrieve(LABEL, {LABEL: DIGEST}, destination)

    def test_cached_file_is_not_refetched(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "out.lbl"
            destination.write_bytes(PAYLOAD)
            with patch.object(d2, "_get", side_effect=AssertionError("network used")):
                record = d2.retrieve(LABEL, {LABEL: DIGEST}, destination)
            self.assertEqual(record["source"], "cache")

    def test_unexpected_archive_path_is_never_requested(self):
        with self.assertRaises(ValueError):
            d2._get("../../etc/passwd", max_bytes=10)


if __name__ == "__main__":
    unittest.main()
