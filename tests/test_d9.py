import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from srmwse.io import d9
from srmwse.io.d9 import D9AccessError

PAYLOAD = b"PDS_VERSION_ID = PDS3\n"
DIGEST = hashlib.md5(PAYLOAD, usedforsecurity=False).hexdigest()
LABEL = "data/srem_l2_20050304.lbl"
TABLE = "data/srem_l2_20050304.tab"


def manifest(*entries):
    return "\n".join(f"{d}  {p}" for d, p in entries).encode("ascii") + b"\n"


class ChannelTableTests(unittest.TestCase):

    def test_fifteen_channels_in_archive_order(self):
        self.assertEqual(len(d9.SREM_CHANNELS), 15)
        self.assertEqual([c.index for c in d9.SREM_CHANNELS.values()], list(range(15)))

    def test_first_and_last_channel_are_where_the_label_puts_them(self):
        self.assertEqual(d9.SREM_CHANNELS["TC1"].index, 0)
        self.assertEqual(d9.SREM_CHANNELS["S34"].index, 14)

    def test_l1_matched_channel_is_the_one_above_49_mev(self):
        channel = d9.SREM_CHANNELS[d9.L1_MATCHED_CHANNEL]
        self.assertEqual(channel.proton_mev, (49, None))

    def test_proton_only_channels_quote_no_electron_response(self):
        self.assertEqual(set(d9.PROTON_ONLY), {"S25", "C1", "C2", "C3"})
        for name in d9.PROTON_ONLY:
            self.assertIsNone(d9.SREM_CHANNELS[name].electron_mev)

    def test_channels_that_respond_to_electrons_are_not_proton_only(self):
        for name in ("TC1", "TC3", "C4"):
            self.assertNotIn(name, d9.PROTON_ONLY)
            self.assertIsNotNone(d9.SREM_CHANNELS[name].electron_mev)


class DaySelectionTests(unittest.TestCase):
    CHECKSUMS = {LABEL: DIGEST, TABLE: DIGEST}

    def test_window_spans_the_days_either_side(self):
        self.assertEqual(d9.days_around("2005-03-04", 1),
                         ("2005-03-03", "2005-03-04", "2005-03-05"))

    def test_zero_half_width_is_the_day_itself(self):
        self.assertEqual(d9.days_around("2005-03-04", 0), ("2005-03-04",))

    def test_window_crosses_a_month_boundary(self):
        self.assertEqual(d9.days_around("2005-03-01", 1),
                         ("2005-02-28", "2005-03-01", "2005-03-02"))

    def test_negative_half_width_is_refused(self):
        with self.assertRaises(ValueError):
            d9.days_around("2005-03-04", -1)

    def test_products_are_selected_from_the_manifest(self):
        found, = d9.products_for("ear1", ("2005-03-04",), self.CHECKSUMS)
        self.assertEqual((found.phase, found.day), ("ear1", "2005-03-04"))
        self.assertEqual(found.table_path, TABLE)

    def test_a_day_the_dataset_does_not_cover_is_refused_not_skipped(self):
        with self.assertRaises(D9AccessError) as caught:
            d9.products_for("ear1", ("2005-03-04", "2005-03-05"), self.CHECKSUMS)
        self.assertIn("2005-03-05", str(caught.exception))

    def test_label_without_its_table_is_refused(self):
        with self.assertRaises(D9AccessError):
            d9.products_for("ear1", ("2005-03-04",), {LABEL: DIGEST})

    def test_empty_selection_is_refused(self):
        with self.assertRaises(D9AccessError):
            d9.products_for("ear1", ("1999-01-01",), self.CHECKSUMS)

    def test_unknown_phase_is_refused(self):
        with self.assertRaises(ValueError):
            d9.products_for("jupiter", ("2005-03-04",), self.CHECKSUMS)

    def test_malformed_day_is_refused(self):
        with self.assertRaises(ValueError):
            d9.products_for("ear1", ("04/03/2005",), self.CHECKSUMS)


class ManifestTests(unittest.TestCase):
    def test_manifest_is_keyed_by_dataset_relative_path(self):
        with patch.object(d9, "_get", return_value=manifest((DIGEST, LABEL))):
            self.assertEqual(d9.fetch_checksums("ear1"), {LABEL: DIGEST})

    def test_unrecognised_line_is_refused(self):
        with patch.object(d9, "_get", return_value=b"not a checksum\n"):
            with self.assertRaises(D9AccessError):
                d9.fetch_checksums("ear1")

    def test_contradictory_duplicate_is_refused(self):
        payload = manifest((DIGEST, LABEL), ("0" * 32, LABEL))
        with patch.object(d9, "_get", return_value=payload):
            with self.assertRaises(D9AccessError):
                d9.fetch_checksums("ear1")

    def test_empty_manifest_is_refused(self):
        with patch.object(d9, "_get", return_value=b"\n"):
            with self.assertRaises(D9AccessError):
                d9.fetch_checksums("ear1")

    def test_each_phase_maps_to_its_own_dataset(self):
        datasets = [d9.dataset_for(p) for p in d9.D9_DATASETS]
        self.assertEqual(len(datasets), len(set(datasets)))
        self.assertTrue(all(d.startswith("ro-x-srem-2-") for d in datasets))


class RetrievalTests(unittest.TestCase):
    def test_published_digest_is_verified_and_bytes_stored(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "out.lbl"
            with patch.object(d9, "_get", return_value=PAYLOAD):
                record = d9.retrieve("ear1", LABEL, {LABEL: DIGEST}, destination)
            self.assertEqual(record["checksum_origin"], "publisher_manifest")
            self.assertEqual(destination.read_bytes(), PAYLOAD)

    def test_digest_disagreement_is_refused(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "out.lbl"
            with patch.object(d9, "_get", return_value=b"other bytes"):
                with self.assertRaises(D9AccessError):
                    d9.retrieve("ear1", LABEL, {LABEL: DIGEST}, destination)
            self.assertFalse(destination.exists())

    def test_cached_file_is_revalidated(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "out.lbl"
            destination.write_bytes(b"tampered")
            with self.assertRaises(D9AccessError):
                d9.retrieve("ear1", LABEL, {LABEL: DIGEST}, destination)

    def test_path_outside_the_dataset_is_never_requested(self):
        with self.assertRaises(ValueError):
            d9._get("ro-x-srem-2-ear1-v1.0", "../../etc/passwd", max_bytes=10)


class CountRateTests(unittest.TestCase):
    class FakeRow:
        def __init__(self, values, number=1):
            self.values, self.record_number = values, number

    class FakeParsed:
        def __init__(self, rows):
            self.rows = rows

    def sample(self, rates, moment="2005-03-04T22:11:00"):
        return self.FakeRow((moment, *rates, *([0.1] * 15)))

    def test_rates_are_keyed_by_channel_name(self):
        rates = [float(i) for i in range(15)]
        parsed = self.FakeParsed([self.sample(rates)])
        (moment, values), = d9.count_rates(parsed)
        self.assertEqual(moment, "2005-03-04T22:11:00")
        self.assertEqual(values["TC1"], 0.0)
        self.assertEqual(values["TC2"], 5.0)
        self.assertEqual(values["S34"], 14.0)

    def test_sample_with_a_missing_value_is_dropped_whole(self):
        rates = [1.0] * 15
        rates[7] = -1.0e31
        parsed = self.FakeParsed([self.sample(rates), self.sample([2.0] * 15)])
        self.assertEqual(len(d9.count_rates(parsed)), 1)

    def test_short_record_is_refused_rather_than_padded(self):
        parsed = self.FakeParsed([self.FakeRow(("2005-03-04T22:11:00", 1.0, 2.0))])
        with self.assertRaises(D9AccessError):
            d9.count_rates(parsed)


if __name__ == "__main__":
    unittest.main()


class SurveyWindowTests(unittest.TestCase):

    CHECKSUMS = {LABEL: DIGEST, TABLE: DIGEST}

    def test_missing_day_still_refused_by_default(self):
        with self.assertRaises(D9AccessError):
            d9.products_for("ear1", ("2005-03-04", "2005-03-05"), self.CHECKSUMS)

    def test_survey_mode_keeps_what_is_there(self):
        found = d9.products_for("ear1", ("2005-03-04", "2005-03-05"),
                                self.CHECKSUMS, allow_missing=True)
        self.assertEqual([p.day for p in found], ["2005-03-04"])

    def test_survey_mode_still_refuses_an_empty_result(self):
        with self.assertRaises(D9AccessError):
            d9.products_for("ear1", ("1999-01-01",), self.CHECKSUMS,
                            allow_missing=True)

    def test_missing_days_are_nameable_rather_than_merely_absent(self):
        absent = d9.missing_days("ear1", ("2005-03-03", "2005-03-04", "2005-03-05"),
                                 self.CHECKSUMS)
        self.assertEqual(absent, ["2005-03-03", "2005-03-05"])

    def test_no_missing_days_is_an_empty_list(self):
        self.assertEqual(d9.missing_days("ear1", ("2005-03-04",), self.CHECKSUMS), [])
