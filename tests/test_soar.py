import json
import unittest
from unittest.mock import patch

from srmwse.io import soar
from srmwse.io.soar import CDF_COMPRESSED, CDF_V3_MAGIC, DataItem, SoarAccessError


def tap_document(rows, columns=("data_item_id", "filename", "level", "descriptor",
                                "begin_time", "end_time", "filesize", "file_format")):
    return json.dumps({
        "metadata": [{"name": name} for name in columns],
        "data": [list(row) for row in rows],
    }).encode()


def cdf_bytes(magic=CDF_V3_MAGIC, marker=CDF_COMPRESSED, body=b"\x00" * 32):
    return magic.to_bytes(4, "big") + marker.to_bytes(4, "big") + body


def item(filesize=100, item_id="solo_L2_epd-het-sun-rates_20220906"):
    return DataItem(data_item_id=item_id, filename=f"{item_id}_V03.cdf", level="L2",
                    descriptor="epd-het-sun-rates", begin_time="2022-09-06T00:00:00.0",
                    end_time="2022-09-07T00:00:00.0", filesize=filesize,
                    file_format="CDF")


class QueryTests(unittest.TestCase):
    def test_rows_are_zipped_with_their_column_names(self):
        payload = tap_document([("id-1", "f.cdf", "L2", "d", "b", "e", 10, "CDF")])
        with patch.object(soar, "_get", return_value=payload):
            rows = soar.query("SELECT 1")
        self.assertEqual(rows[0]["filename"], "f.cdf")
        self.assertEqual(rows[0]["filesize"], 10)

    def test_response_without_metadata_or_data_is_refused(self):
        with patch.object(soar, "_get", return_value=b'{"rows": []}'):
            with self.assertRaises(SoarAccessError):
                soar.query("SELECT 1")

    def test_row_shorter_than_its_header_is_refused(self):
        payload = json.dumps({"metadata": [{"name": "a"}, {"name": "b"}],
                              "data": [["only-one"]]}).encode()
        with patch.object(soar, "_get", return_value=payload):
            with self.assertRaises(ValueError):
                soar.query("SELECT 1")


class FindDataItemsTests(unittest.TestCase):
    CONTRACT = {"instrument": "EPD", "descriptor": "epd-het-sun-rates",
                "level": "L2", "start": "2022-09-04", "end": "2022-09-12"}

    def test_items_are_built_from_the_archive_row(self):
        payload = tap_document([("id-1", "f.cdf", "L2", "epd-het-sun-rates",
                                 "2022-09-06T00:00:00.0", "2022-09-07T00:00:00.0", "42", "CDF")])
        with patch.object(soar, "_get", return_value=payload):
            found, = soar.find_data_items(**self.CONTRACT)
        self.assertEqual(found.data_item_id, "id-1")
        self.assertEqual(found.filesize, 42)

    def test_missing_column_is_refused_rather_than_defaulted(self):
        payload = tap_document([("id-1", "f.cdf")], columns=("data_item_id", "filename"))
        with patch.object(soar, "_get", return_value=payload):
            with self.assertRaises(SoarAccessError):
                soar.find_data_items(**self.CONTRACT)

    def test_quote_in_a_parameter_is_rejected(self):
        contract = dict(self.CONTRACT, descriptor="epd' OR '1'='1")
        with self.assertRaises(ValueError):
            soar.find_data_items(**contract)

    def test_backslash_in_a_parameter_is_rejected(self):
        contract = dict(self.CONTRACT, level="L2\\")
        with self.assertRaises(ValueError):
            soar.find_data_items(**contract)


class ContainerTests(unittest.TestCase):
    def test_cdf_v3_container_is_recognised_without_being_decoded(self):
        described = soar.describe_container(cdf_bytes())
        self.assertEqual(described["container"], "CDF")
        self.assertEqual(described["compression"], "compressed")
        self.assertFalse(described["decoded"])

    def test_unknown_compression_marker_is_reported_not_guessed(self):
        described = soar.describe_container(cdf_bytes(marker=0x12345678))
        self.assertEqual(described["compression"], "unknown:0x12345678")

    def test_payload_that_is_not_a_cdf_is_refused(self):
        with self.assertRaises(SoarAccessError):
            soar.describe_container(b"<html>not a cdf at all</html>")

    def test_truncated_payload_is_refused(self):
        with self.assertRaises(SoarAccessError):
            soar.describe_container(b"\xcd\xf3")


class DownloadTests(unittest.TestCase):
    def test_payload_matching_the_declared_size_is_returned(self):
        payload = cdf_bytes(body=b"\x00" * 92)
        with patch.object(soar, "_get", return_value=payload):
            self.assertEqual(soar.download_item(item(filesize=len(payload))), payload)

    def test_size_disagreement_with_the_archive_is_refused(self):
        payload = cdf_bytes()
        with patch.object(soar, "_get", return_value=payload):
            with self.assertRaises(SoarAccessError):
                soar.download_item(item(filesize=len(payload) + 1))

    def test_reads_are_bounded(self):
        oversized = b"x" * (soar.MAX_QUERY_BYTES + 1)

        class _Response:
            status = 200

            def read(self, size=None):
                return oversized[:size] if size else oversized

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with patch("urllib.request.urlopen", return_value=_Response()):
            with self.assertRaises(SoarAccessError):
                soar.query("SELECT 1")


if __name__ == "__main__":
    unittest.main()
