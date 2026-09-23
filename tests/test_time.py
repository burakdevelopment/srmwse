import unittest
from datetime import UTC, datetime

from srmwse.time import parse_timestamp


class TimestampTests(unittest.TestCase):
    def test_utc_requires_explicit_contract(self):
        for scale in ["unknown", "TDB", "SCLK", "SCET"]:
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                parse_timestamp("2012-03-07 00:00:00", time_system=scale, format_name="iso")

    def test_iso_preserves_fraction(self):
        self.assertEqual(
            parse_timestamp("2012-03-07 00:00:00.125", time_system="UTC", format_name="iso"),
            datetime(2012, 3, 7, microsecond=125000, tzinfo=UTC),
        )

    def test_doy_year_boundaries(self):
        self.assertEqual(
            parse_timestamp("2012 366 23 59 59.500", time_system="UTC", format_name="ares_doy"),
            datetime(2012, 12, 31, 23, 59, 59, 500000, tzinfo=UTC),
        )
        for value in ["2011 366 00 00 00", "2012 000 00 00 00", "2012 367 00 00 00"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_timestamp(value, time_system="UTC", format_name="ares_doy")

    def test_does_not_silently_normalize_invalid_times(self):
        for value in ["2012-03-07", "2012-03-07 00:00:00+03:00", "2016-12-31 23:59:60Z"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_timestamp(value, time_system="UTC", format_name="iso")
