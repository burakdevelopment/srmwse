from decimal import Decimal
import unittest

from srmwse.io.edac import (
    ExportParseError,
    UnsupportedExportError,
    decode_export,
    parse_export,
)


def grains(rows, *, delimiter="\t", channels=("NDMW0D0A - AVG - 1 Non [VEX]",),
           declared=None, statistics=True):
    joined = delimiter.join(channels)
    stats = []
    if statistics:
        stats = [
            delimiter.join([label.split(" - ", 1)[0], ".", f"{label[:8]} cntr", "FLOAT", "1", "0", "1", "0"])
            for label in channels
        ]
    lines = [
        "# Exported Parameter(s) from GRAINS",
        "# Parameter List:",
        joined if declared is None else declared,
        "# Time Window Start (Time Date):",
        "2012-03-01 00:00:00.000",
        "# Number of parameters:",
        str(len(channels)),
        "# " + delimiter.join(["Name", "Unit", "Description", "Data", "Type", "Max", "Min", "Avg", "StdDev"]),
        "# Per-Orbit Statistics:",
        "",
        *stats,
        "# DATE TIME" + delimiter + joined,
        *rows,
    ]
    return "\n".join(lines) + "\n"


def ares(rows, *, channel="0.NST82031", written=None):
    lines = [
        '"Parameter SINGLE Export from ARES"',
        '"Parameter List:"',
        f'"{channel}"',
        '"Time Window Start (yyyy DDD HH mm ss.SSS):"',
        '"2015 162 00 00 00.000"',
        '"Number of parameters:"',
        "1",
        '"Name"\t"Unit"\t"Description"\t"Data Type"',
        f'"{channel}"\t"[1]"\t"STR1 Seu Counter"\t"UNSIGNED_INTEGER"',
        "",
        f'"DATE TIME"\t"{channel}"',
        *rows,
        "",
        '"Number of all written samples: "',
        f'"{len(rows) if written is None else written}"',
    ]
    return "\n".join(lines) + "\n"


class FormatDetectionTests(unittest.TestCase):
    def test_operator_message_log_is_unsupported_not_malformed(self):
        log = "HIS: EVENT: 43904: HIS: SRAM  EDAC Correctable 1 Errors Between 0x40 - 0x40.\n"
        with self.assertRaises(UnsupportedExportError):
            parse_export(log)

    def test_malformed_parameter_export_is_not_reported_as_unsupported(self):
        broken = grains(["2012-03-01 00:00:00.000\t1"]).replace("# DATE TIME", "# DATA TIME")
        with self.assertRaises(ExportParseError) as raised:
            parse_export(broken)
        self.assertNotIsInstance(raised.exception, UnsupportedExportError)

    def test_unsupported_error_remains_catchable_as_parse_error(self):
        self.assertTrue(issubclass(UnsupportedExportError, ExportParseError))


class LosslessValueTests(unittest.TestCase):
    def test_grains_preserves_source_timestamps_and_exact_values(self):
        export = parse_export(
            grains(["2012-03-07 00:00:31.811\t2024", "2012-03-07 00:01:35.812\t2025"])
        )
        self.assertEqual(export.format_name, "GRAINS")
        self.assertEqual(export.channels, ("NDMW0D0A",))
        self.assertEqual(
            [sample.source_timestamp for sample in export.samples],
            ["2012-03-07 00:00:31.811", "2012-03-07 00:01:35.812"],
        )
        self.assertEqual([sample.values for sample in export.samples], [(Decimal("2024"),), (Decimal("2025"),)])

    def test_reader_asserts_no_time_system(self):
        export = parse_export(grains(["2012-03-07 00:00:00.000\t1"]))
        self.assertEqual(export.metadata["time_system"], "unresolved")

    def test_decimal_values_avoid_binary_float_rounding(self):
        export = parse_export(grains(["2012-03-07 00:00:00.000\t0.1"]))
        self.assertEqual(export.samples[0].values[0], Decimal("0.1"))

    def test_empty_value_is_missing_not_zero(self):
        export = parse_export(grains(["2012-03-07 00:00:00.000\t"]))
        self.assertIsNone(export.samples[0].values[0])

    def test_source_line_numbers_are_retained(self):
        export = parse_export(grains(["2012-03-07 00:00:00.000\t1", "2012-03-07 00:01:00.000\t2"]))
        first, second = export.samples
        self.assertEqual(second.source_line, first.source_line + 1)


class DelimiterAndShapeTests(unittest.TestCase):
    def test_comma_delimited_grains_export_is_supported(self):
        export = parse_export(
            grains(
                ["2017-09-05 00:00:35.123,0"],
                delimiter=",",
                channels=("ASA11F0L - AVG - 1 Non [EXM2016]",),
            )
        )
        self.assertEqual(export.metadata["delimiter"], ",")
        self.assertEqual(export.channels, ("ASA11F0L",))

    def test_multi_channel_export_keeps_column_order(self):
        channels = tuple(f"NSM0079{n} - AVG - 1 Non [SOL]" for n in (8, 9))
        export = parse_export(
            grains(["2022-09-04 00:00:00.821\t4160\t33687"], channels=channels)
        )
        self.assertEqual(export.channels, ("NSM00798", "NSM00799"))
        self.assertEqual(export.samples[0].values, (Decimal("4160"), Decimal("33687")))

    def test_column_count_mismatch_is_rejected(self):
        with self.assertRaises(ExportParseError):
            parse_export(grains(["2012-03-07 00:00:00.000\t1\t2"]))

    def test_blank_row_inside_data_table_is_rejected(self):
        body = ["2012-03-07 00:00:00.000\t1", "", "2012-03-07 00:01:00.000\t2"]
        with self.assertRaises(ExportParseError):
            parse_export(grains(body))


class HeaderAgreementTests(unittest.TestCase):
    def test_parameter_count_must_match_the_data_header(self):
        export = grains(["2012-03-07 00:00:00.000\t1"]).replace(
            "# Number of parameters:\n1", "# Number of parameters:\n2"
        )
        with self.assertRaises(ExportParseError):
            parse_export(export)

    def test_parameter_list_must_match_the_data_header(self):
        with self.assertRaises(ExportParseError):
            parse_export(
                grains(["2012-03-07 00:00:00.000\t1"], declared="NDMW0D0G - AVG - 1 Non [VEX]")
            )

    def test_duplicate_channel_identifiers_are_rejected(self):
        duplicated = ("NDMW0D0A - AVG - 1 Non [VEX]",) * 2
        with self.assertRaises(ExportParseError):
            parse_export(grains(["2012-03-07 00:00:00.000\t1\t2"], channels=duplicated))


class TimestampSyntaxTests(unittest.TestCase):
    def test_invalid_calendar_date_is_rejected(self):
        with self.assertRaises(ExportParseError):
            parse_export(grains(["2012-02-30 00:00:00.000\t1"]))

    def test_hour_24_is_rejected(self):
        with self.assertRaises(ExportParseError):
            parse_export(grains(["2012-03-07 24:00:00.000\t1"]))

    def test_lexical_second_60_is_retained_for_later_time_analysis(self):
        export = parse_export(grains(["2012-06-30 23:59:60.000\t1"]))
        self.assertEqual(export.samples[0].source_timestamp, "2012-06-30 23:59:60.000")

    def test_day_of_year_366_is_rejected_in_a_common_year(self):
        with self.assertRaises(ExportParseError):
            parse_export(ares(['"2015 366 00 00 00.000"\t"1"']))

    def test_day_of_year_366_is_accepted_in_a_leap_year(self):
        export = parse_export(ares(['"2016 366 00 00 00.000"\t"1"']))
        self.assertEqual(export.samples[0].source_timestamp, "2016 366 00 00 00.000")


class AresTests(unittest.TestCase):
    def test_ares_export_is_parsed_with_its_day_of_year_timestamps(self):
        export = parse_export(ares(['"2015 162 00 00 52.166"\t"76"']))
        self.assertEqual(export.format_name, "ARES")
        self.assertEqual(export.channels, ("0.NST82031",))
        self.assertEqual(export.samples[0].source_timestamp, "2015 162 00 00 52.166")
        self.assertEqual(export.samples[0].values[0], Decimal("76"))

    def test_written_sample_footer_must_agree_with_parsed_rows(self):
        with self.assertRaises(ExportParseError):
            parse_export(ares(['"2015 162 00 00 52.166"\t"76"'], written=99))

    def test_missing_footer_is_rejected(self):
        without_footer = ares(['"2015 162 00 00 52.166"\t"76"'])
        without_footer = without_footer[: without_footer.index('"Number of all written samples: "')]
        with self.assertRaises(ExportParseError):
            parse_export(without_footer)


class ChannelDescriptorTests(unittest.TestCase):

    def test_descriptor_is_captured_from_the_header_block(self):
        export = parse_export(grains(["2012-03-07 00:00:00.000\t1"]))
        descriptor, = export.descriptors
        self.assertEqual(descriptor.channel, "NDMW0D0A")
        self.assertEqual(descriptor.data_type, "FLOAT")
        self.assertEqual(descriptor.statistics, ("1", "0", "1", "0"))

    def test_descriptors_follow_data_header_channel_order(self):
        channels = tuple(f"NSM0079{n} - AVG - 1 Non [SOL]" for n in (8, 9))
        export = parse_export(grains(["2022-09-04 00:00:00.821\t1\t2"], channels=channels))
        self.assertEqual([d.channel for d in export.descriptors], ["NSM00798", "NSM00799"])

    def test_missing_descriptor_row_is_not_an_error(self):
        export = parse_export(grains(["2012-03-07 00:00:00.000\t1"], statistics=False))
        self.assertEqual(export.descriptors, ())
        self.assertEqual(len(export.samples), 1)

    def test_bare_parameter_list_entry_is_not_a_descriptor(self):
        export = parse_export(ares(['"2015 162 00 00 52.166"\t"76"']))
        descriptor, = export.descriptors
        self.assertEqual(descriptor.description, "STR1 Seu Counter")
        self.assertEqual(descriptor.data_type, "UNSIGNED_INTEGER")

    def test_descriptor_row_is_still_preserved_verbatim_in_raw_headers(self):
        export = parse_export(grains(["2012-03-07 00:00:00.000\t1"]))
        descriptor, = export.descriptors
        self.assertIn("# Exported Parameter(s) from GRAINS", export.raw_headers)
        source_row = "\t".join(
            [descriptor.channel, descriptor.unit, descriptor.description,
             descriptor.data_type, *descriptor.statistics]
        )
        self.assertIn(source_row, export.raw_headers)


class DecodingTests(unittest.TestCase):
    def test_byte_order_mark_is_accepted(self):
        payload = grains(["2012-03-07 00:00:00.000\t1"]).encode("utf-8-sig")
        self.assertEqual(len(decode_export(payload).samples), 1)

    def test_undecodable_bytes_are_reported_with_a_line_number(self):
        payload = grains(["2012-03-07 00:00:00.000\t1"]).encode() + b"\xff\xfe"
        with self.assertRaises(ExportParseError) as raised:
            decode_export(payload)
        self.assertIn("line", str(raised.exception))

    def test_bytes_are_rejected_by_the_text_entry_point(self):
        with self.assertRaises(TypeError):
            parse_export(b"# Exported Parameter(s) from GRAINS\n")


if __name__ == "__main__":
    unittest.main()
