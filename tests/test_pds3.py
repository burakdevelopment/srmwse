import unittest
from decimal import Decimal

from srmwse.io.pds3 import Pds3ParseError, parse_label, parse_product, parse_table

ROW_BYTES = 41


def label(record_bytes=ROW_BYTES, file_records=2, rows=None, columns=2,
          row_bytes=None, start_byte=25, column_bytes=15, data_type="ASCII_INTEGER",
          record_type="FIXED_LENGTH", pointer='"TEST.TAB"'):
    rows = file_records if rows is None else rows
    row_bytes = record_bytes if row_bytes is None else row_bytes
    return f"""PDS_VERSION_ID                 = PDS3

/* FILE CHARACTERISTICS DATA ELEMENTS */

RECORD_TYPE                    = {record_type}
RECORD_BYTES                   = {record_bytes}
FILE_RECORDS                   = {file_records}
^TABLE                         = {pointer}
DATA_SET_ID                    = "RO-X-HK-3-EDAC-V1.0"
DATA_SET_NAME                  = "ROSETTA RADIATION DATA CORRECTION
                                  ENGINEERING DATA"
ROSETTA:SUBSYSTEM_DATA_TYPE    = "AOCS_AND_DMS_EDAC_CNTR"

OBJECT                         = TABLE
 INTERCHANGE_FORMAT            = ASCII
 ROWS                          = {rows}
 COLUMNS                       = {columns}
 ROW_BYTES                     = {row_bytes}
 DESCRIPTION                   = "This table gives the time profile of the
                                  BTSTP: EDAC Counter parameter "
 OBJECT                        = COLUMN
     COLUMN_NUMBER             = 1
     NAME                      = "TIME OF MEASUREMENT"
     UNIT                      = "N/A"
     DATA_TYPE                 = TIME
     START_BYTE                = 1
     BYTES                     = 23
 END_OBJECT                    = COLUMN
 OBJECT                        = COLUMN
     COLUMN_NUMBER             = 2
     NAME                      = "VALUE"
     DATA_TYPE                 = "{data_type}"
     START_BYTE                = {start_byte}
     BYTES                     = {column_bytes}
 END_OBJECT                    = COLUMN
END_OBJECT                     = TABLE
END
"""


def record(stamp="2006-12-01T00:06:02.708", value="1171"):
    return f"{stamp:<23} {value:>15}".encode("ascii")[:ROW_BYTES].ljust(ROW_BYTES)


class LabelStructureTests(unittest.TestCase):
    def test_nested_column_objects_do_not_discard_table_keys(self):
        parsed = parse_label(label())
        self.assertEqual(parsed.table.rows, 2)
        self.assertEqual(parsed.table.row_bytes, ROW_BYTES)
        self.assertEqual(len(parsed.table.columns), 2)

    def test_columns_are_read_from_the_label_not_guessed(self):
        time_column, value_column = parse_label(label()).table.columns
        self.assertEqual((time_column.name, time_column.start_byte, time_column.bytes_),
                         ("TIME OF MEASUREMENT", 1, 23))
        self.assertEqual((value_column.data_type, value_column.start_byte), ("ASCII_INTEGER", 25))

    def test_multi_line_quoted_value_is_joined(self):
        parsed = parse_label(label())
        self.assertEqual(parsed.keywords["DATA_SET_NAME"],
                         "ROSETTA RADIATION DATA CORRECTION ENGINEERING DATA")
        self.assertIn("BTSTP: EDAC Counter", parsed.table.description)

    def test_comments_are_ignored(self):
        self.assertEqual(parse_label(label()).record_bytes, ROW_BYTES)

    def test_table_pointer_is_required(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(label().replace('^TABLE                         = "TEST.TAB"', ""))


class LabelConsistencyTests(unittest.TestCase):

    def test_rows_disagreeing_with_file_records_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(label(file_records=2, rows=3))

    def test_declared_column_count_must_match_the_defined_columns(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(label(columns=3))

    def test_row_bytes_disagreeing_with_record_bytes_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(label(row_bytes=ROW_BYTES + 1))

    def test_column_reaching_past_the_row_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(label(start_byte=35, column_bytes=15))

    def test_unclosed_object_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(label().replace("END_OBJECT                     = TABLE", ""))

    def test_mismatched_end_object_is_refused(self):
        broken = label().replace("END_OBJECT                    = COLUMN",
                                 "END_OBJECT                    = TABLE", 1)
        with self.assertRaises(Pds3ParseError):
            parse_label(broken)

    def test_non_pds3_text_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_label("# Exported Parameter(s) from GRAINS\n")

    def test_variable_length_records_are_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(label(record_type="STREAM"))


class TableTests(unittest.TestCase):
    def test_table_is_split_using_the_declared_layout(self):
        parsed = parse_product(label(file_records=2),
                               record("2006-12-01T00:06:02.708", "1171")
                               + record("2006-12-01T00:14:34.708", "1172"))
        self.assertEqual([row.values for row in parsed.rows],
                         [("2006-12-01T00:06:02.708", 1171), ("2006-12-01T00:14:34.708", 1172)])

    def test_byte_length_disagreeing_with_the_label_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_product(label(file_records=2), record())

    def test_time_column_keeps_its_source_text(self):
        parsed = parse_product(label(file_records=1), record("2006-12-01T00:06:02.708"))
        self.assertIsInstance(parsed.rows[0].values[0], str)
        self.assertEqual(parsed.metadata["time_system"], "unresolved")

    def test_source_fields_are_preserved_alongside_converted_values(self):
        parsed = parse_product(label(file_records=1), record(value="1171"))
        self.assertEqual(parsed.rows[0].source_fields[1].strip(), "1171")

    def test_empty_field_is_missing_not_zero(self):
        parsed = parse_product(label(file_records=1), record(value=""))
        self.assertIsNone(parsed.rows[0].values[1])

    def test_non_integer_in_an_integer_column_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_product(label(file_records=1), record(value="n/a"))

    def test_real_column_keeps_decimal_precision(self):
        parsed = parse_product(label(file_records=1, data_type="ASCII_REAL"),
                               record(value="0.1"))
        self.assertEqual(parsed.rows[0].values[1], Decimal("0.1"))

    def test_record_numbers_are_one_based_and_sequential(self):
        parsed = parse_product(label(file_records=2), record() + record())
        self.assertEqual([row.record_number for row in parsed.rows], [1, 2])

    def test_non_ascii_record_is_refused(self):
        payload = bytearray(record())
        payload[0] = 0xFF
        with self.assertRaises(Pds3ParseError):
            parse_table(parse_label(label(file_records=1)), bytes(payload))

    def test_bytes_are_required_for_the_table(self):
        with self.assertRaises(TypeError):
            parse_table(parse_label(label(file_records=1)), "not bytes")


if __name__ == "__main__":
    unittest.main()


class SremDialectTests(unittest.TestCase):

    def label(self, **overrides):
        keys = {
            "record_bytes": 20, "file_records": 3, "rows": 2, "start": 2,
            "items": "\n    ITEMS                    = 3\n"
                     "    ITEM_BYTES               = 5\n"
                     "    ITEM_OFFSET              = 5",
        }
        keys.update(overrides)
        return f"""PDS_VERSION_ID = "PDS3"
RECORD_TYPE = FIXED_LENGTH
RECORD_BYTES = {keys['record_bytes']}
FILE_RECORDS = {keys['file_records']}
^SREM_COUNT_RATES_TABLE = ("SREM.TAB", {keys['start']})
OBJECT = SREM_COUNT_RATES_TABLE
  ROWS = {keys['rows']}
  COLUMNS = 2
  ROW_BYTES = 20
  OBJECT = COLUMN
    NAME = "TIME"
    DATA_TYPE = TIME
    START_BYTE = 1
    BYTES = 4
  END_OBJECT = COLUMN
  OBJECT = COLUMN
    NAME = "COUNT RATES"
    DATA_TYPE = ASCII_REAL
    START_BYTE = 6
    BYTES = 15{keys['items']}
  END_OBJECT = COLUMN
END_OBJECT = SREM_COUNT_RATES_TABLE
END
"""

    def table(self):
        return (b"HEADER LINE PADDED  "
                b"t001  1.0  2.0  3.0"
                b" "
                b"t002  4.0  5.0  6.0"
                b" ")

    def test_named_table_object_and_matching_pointer(self):
        label = parse_label(self.label())
        self.assertEqual(label.table_pointer, "SREM.TAB")
        self.assertEqual(label.table_start_record, 2)

    def test_repeating_column_expands_to_one_column_per_item(self):
        label = parse_label(self.label())
        self.assertEqual(len(label.table.columns), 4)
        items = [c for c in label.table.columns if c.item_index]
        self.assertEqual([c.item_index for c in items], [1, 2, 3])
        self.assertEqual([c.start_byte for c in items], [6, 11, 16])
        self.assertTrue(all(c.bytes_ == 5 for c in items))

    def test_expanded_items_share_the_declaration_number(self):
        label = parse_label(self.label())
        self.assertEqual({c.number for c in label.table.columns if c.item_index}, {2})

    def test_header_records_are_not_rows(self):
        parsed = parse_product(self.label(), self.table())
        self.assertEqual(len(parsed.rows), 2)
        self.assertEqual(parsed.metadata["header_record_count"], 1)
        self.assertEqual(parsed.rows[0].values[0], "t001")
        self.assertEqual(str(parsed.rows[0].values[1]), "1.0")
        self.assertEqual(str(parsed.rows[1].values[3]), "6.0")

    def test_columns_count_declarations_not_expanded_items(self):
        parse_label(self.label())

    def test_rows_must_reach_the_end_of_the_file(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(self.label(rows=1))

    def test_pointer_start_before_the_first_record_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(self.label(start=0))

    def test_items_without_item_bytes_is_refused(self):
        with self.assertRaises(Pds3ParseError):
            parse_label(self.label(items="\n    ITEMS = 3"))

    def test_items_overrunning_the_declared_bytes_is_refused(self):
        overrun = ("\n    ITEMS                    = 4"
                   "\n    ITEM_BYTES               = 5"
                   "\n    ITEM_OFFSET              = 5")
        with self.assertRaises(Pds3ParseError):
            parse_label(self.label(items=overrun))

    def test_stride_narrower_than_the_item_is_refused(self):
        overlap = ("\n    ITEMS                    = 3"
                   "\n    ITEM_BYTES               = 5"
                   "\n    ITEM_OFFSET              = 3")
        with self.assertRaises(Pds3ParseError):
            parse_label(self.label(items=overlap))

    def test_pointer_naming_a_different_table_is_refused(self):
        text = self.label().replace("^SREM_COUNT_RATES_TABLE", "^OTHER_TABLE")
        with self.assertRaises(Pds3ParseError):
            parse_label(text)

    def test_label_without_any_table_object_is_refused(self):
        text = self.label().replace("SREM_COUNT_RATES_TABLE", "SREM_NOTES")
        with self.assertRaises(Pds3ParseError):
            parse_label(text)
