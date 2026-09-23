import unittest

from srmwse.io.his import (
    HisLogParseError,
    decode_log,
    looks_like_his_log,
    parse_log,
)


def sram(count=1, start="409fe214", end="409fe214", shown="2022/09/04-07:00:15",
         coarse=715590015, fine=32953, region="SRAM"):
    separator = "  " if region == "SRAM" else " "
    return (
        f"HIS: EVENT: 43904: HIS: {region}{separator}EDAC Correctable {count} Errors "
        f"Between 0x{start} - 0x{end}.  (Time {shown} [{coarse}, {fine}])"
    )


def fpga(count=1, source="FSW", shown="2022/09/11-21:50:46", coarse=716248246, fine=35897):
    return (
        f"HIS: EVENT: 43915: HIS: CDH FPGA SRAM/BRAM had {count} (with rollover) EDAC "
        f"errors. Last read source was {source}. Single/Correctable occurred.  "
        f"(Time {shown} [{coarse}, {fine}])"
    )


def log(*lines):
    return "\r\n".join(lines) + "\r\n"


class MessageTemplateTests(unittest.TestCase):
    def test_sram_range_message_is_parsed(self):
        event, = parse_log(log(sram())).events
        self.assertEqual(event.event_id, 43904)
        self.assertEqual(event.region, "SRAM")
        self.assertEqual(event.reported_errors, 1)
        self.assertIsNone(event.read_source)
        self.assertFalse(event.rollover_flagged)

    def test_other_region_message_is_parsed(self):
        event, = parse_log(log(sram(region="OTHER"))).events
        self.assertEqual(event.region, "OTHER")

    def test_fpga_message_is_parsed_with_its_read_source(self):
        parsed = parse_log(log(fpga(source="FSW"), fpga(source="FPGA")))
        self.assertEqual([e.read_source for e in parsed.events], ["FSW", "FPGA"])
        self.assertEqual({e.region for e in parsed.events}, {"CDH_FPGA"})
        self.assertTrue(all(e.rollover_flagged for e in parsed.events))

    def test_fpga_message_reports_no_address_range(self):
        event, = parse_log(log(fpga())).events
        self.assertIsNone(event.address_start)
        self.assertIsNone(event.address_span)

    def test_regions_are_listed_in_first_appearance_order(self):
        parsed = parse_log(log(fpga(), sram(), sram(region="OTHER"), sram()))
        self.assertEqual(parsed.regions, ("CDH_FPGA", "SRAM", "OTHER"))

    def test_unrecognised_template_fails_rather_than_being_skipped(self):
        with self.assertRaises(HisLogParseError):
            parse_log(log(sram(), "HIS: EVENT: 99999: HIS: something entirely new."))


class AddressRangeTests(unittest.TestCase):

    def test_single_cell_event_has_zero_span(self):
        event, = parse_log(log(sram(start="409fe214", end="409fe214"))).events
        self.assertEqual(event.address_span, 0)

    def test_multi_cell_event_preserves_its_span(self):
        event, = parse_log(log(sram(count=2, start="40e51404", end="40e51414"))).events
        self.assertEqual(event.address_span, 0x10)
        self.assertEqual(event.reported_errors, 2)

    def test_addresses_are_preserved_as_written(self):
        event, = parse_log(log(sram(start="401e1890", end="401e1890"))).events
        self.assertEqual(event.address_start, "401e1890")
        self.assertEqual(event.address_end, "401e1890")

    def test_reversed_address_range_is_rejected(self):
        with self.assertRaises(HisLogParseError):
            parse_log(log(sram(start="40e51414", end="40e51404")))


class TimeFieldTests(unittest.TestCase):
    def test_onboard_clock_fields_are_preserved_as_integers(self):
        event, = parse_log(log(sram(coarse=715590015, fine=32953))).events
        self.assertEqual(event.obt_coarse, 715590015)
        self.assertEqual(event.obt_fine, 32953)

    def test_displayed_timestamp_is_preserved_verbatim(self):
        event, = parse_log(log(sram(shown="2022/09/06-02:14:07"))).events
        self.assertEqual(event.source_timestamp, "2022/09/06-02:14:07")

    def test_reader_asserts_no_time_system(self):
        parsed = parse_log(log(sram()))
        self.assertEqual(parsed.metadata["time_system"], "unresolved")

    def test_impossible_calendar_date_is_rejected(self):
        with self.assertRaises(HisLogParseError):
            parse_log(log(sram(shown="2022/02/30-07:00:15")))

    def test_hour_24_is_rejected(self):
        with self.assertRaises(HisLogParseError):
            parse_log(log(sram(shown="2022/09/04-24:00:15")))


class ProvenanceTests(unittest.TestCase):
    def test_raw_line_and_source_line_number_are_retained(self):
        text = log(sram(), fpga())
        first, second = parse_log(text).events
        self.assertEqual(first.source_line, 1)
        self.assertEqual(second.source_line, 2)
        self.assertEqual(second.raw_line, fpga())

    def test_reported_errors_is_the_stated_count(self):
        event, = parse_log(log(sram(count=2, start="40e51404", end="40e51414"))).events
        self.assertEqual(event.reported_errors, 2)


class FormatDetectionTests(unittest.TestCase):
    def test_parameter_export_is_not_mistaken_for_a_his_log(self):
        self.assertFalse(looks_like_his_log("# Exported Parameter(s) from GRAINS\n"))
        with self.assertRaises(HisLogParseError):
            parse_log("# Exported Parameter(s) from GRAINS\n")

    def test_his_log_is_detected_from_its_first_nonempty_line(self):
        self.assertTrue(looks_like_his_log("\r\n" + log(sram())))

    def test_empty_log_is_rejected(self):
        with self.assertRaises(HisLogParseError):
            parse_log("\r\n\r\n")


class DecodingTests(unittest.TestCase):
    def test_byte_order_mark_is_accepted(self):
        self.assertEqual(len(decode_log(log(sram()).encode("utf-8-sig")).events), 1)

    def test_undecodable_bytes_are_reported_with_a_line_number(self):
        with self.assertRaises(HisLogParseError) as raised:
            decode_log(log(sram()).encode() + b"\xff\xfe")
        self.assertIn("line", str(raised.exception))

    def test_bytes_are_rejected_by_the_text_entry_point(self):
        with self.assertRaises(TypeError):
            parse_log(log(sram()).encode())


if __name__ == "__main__":
    unittest.main()
