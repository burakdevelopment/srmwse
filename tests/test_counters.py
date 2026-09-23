from datetime import UTC, datetime, timedelta, timezone
import unittest

from srmwse.counters import CounterSample, CounterSemantics, QualityFlag, process_counter


BASE_TIME = datetime(2012, 3, 7, tzinfo=UTC)


def sample(value, seconds=0, **kwargs):
    return CounterSample(
        timestamp=BASE_TIME + timedelta(seconds=seconds),
        raw_counter=value,
        source_timestamp=f"synthetic-original:{seconds}",
        **kwargs,
    )


def verified(**kwargs):
    return CounterSemantics(
        status="verified", evidence_url="https://example.org/synthetic-counter-contract", **kwargs
    )


class CounterHygieneTests(unittest.TestCase):
    def test_first_sample_and_raw_provenance_are_preserved(self):
        samples = [sample(10), sample(14, 60), sample(14, 90)]
        result = process_counter(samples, verified(interval_exposure_supported=True))
        self.assertEqual(len(result), len(samples))
        self.assertIsNone(result[0].t_start_utc)
        self.assertEqual([row.count_delta for row in result], [None, 4, 0])
        self.assertEqual([row.exposure_s for row in result], [None, 60, 30])
        for original, row in zip(samples, result, strict=True):
            self.assertEqual(row.raw_counter, original.raw_counter)
            self.assertEqual(row.source_timestamp, original.source_timestamp)
            self.assertEqual(row.t_end_utc, original.timestamp)

    def test_unknown_semantics_never_produces_quantitative_values(self):
        result = process_counter([sample(10), sample(100, 60)], CounterSemantics())
        for row in result:
            self.assertIsNone(row.count_delta)
            self.assertIsNone(row.exposure_s)
            self.assertTrue(row.quality_flags & QualityFlag.UNKNOWN_SEMANTICS)

    def test_elapsed_time_is_not_automatically_exposure(self):
        result = process_counter([sample(10), sample(15, 30)], verified())
        self.assertEqual(result[1].count_delta, 5)
        self.assertIsNone(result[1].exposure_s)

    def test_document_reported_negative_jump_does_not_auto_wrap(self):
        result = process_counter([sample(63952), sample(2561, 60)], verified(bit_width=16))
        self.assertIsNone(result[1].count_delta)
        self.assertIsNone(result[1].exposure_s)
        self.assertTrue(result[1].quality_flags & QualityFlag.UNKNOWN)
        self.assertFalse(result[1].quality_flags & QualityFlag.WRAP)

    def test_wrap_requires_explicit_confirmation_and_width(self):
        inputs = [sample(65535), sample(2, 60, wrap_confirmed=True)]
        result = process_counter(inputs, verified(bit_width=16, interval_exposure_supported=True))
        self.assertEqual(result[1].count_delta, 3)
        self.assertEqual(result[1].exposure_s, 60)
        self.assertEqual(result[1].quality_flags, QualityFlag.WRAP)
        self.assertIsNone(process_counter(inputs, verified())[1].count_delta)
        unconfirmed = [sample(65535), sample(2, 60)]
        self.assertIsNone(process_counter(unconfirmed, verified(bit_width=16))[1].count_delta)

    def test_wrap_quality_bit_is_not_sufficient_confirmation(self):
        result = process_counter(
            [sample(65535), sample(2, 60, quality_flags=QualityFlag.WRAP)], verified(bit_width=16)
        )
        self.assertIsNone(result[1].count_delta)
        self.assertTrue(result[1].quality_flags & QualityFlag.UNKNOWN)

    def test_wrap_confirmation_on_nonnegative_difference_is_ambiguous(self):
        result = process_counter(
            [sample(10), sample(12, 60, wrap_confirmed=True)], verified(bit_width=16)
        )
        self.assertIsNone(result[1].count_delta)
        self.assertTrue(result[1].quality_flags & QualityFlag.UNKNOWN)

    def test_confirmed_reset_invalidates_crossing_and_restarts_segment(self):
        result = process_counter(
            [sample(63952), sample(2561, 60, reset_confirmed=True), sample(2562, 120)],
            verified(bit_width=16, interval_exposure_supported=True),
        )
        self.assertEqual([row.count_delta for row in result], [None, None, 1])
        self.assertEqual([row.exposure_s for row in result], [None, None, 60])
        self.assertTrue(result[1].quality_flags & QualityFlag.RESET)

    def test_missing_is_not_zero_and_no_interval_bridges_it(self):
        result = process_counter(
            [sample(10), sample(None, 60), sample(14, 120), sample(14, 180)],
            verified(interval_exposure_supported=True),
        )
        self.assertEqual([row.count_delta for row in result], [None, None, None, 0])
        self.assertEqual([row.exposure_s for row in result], [None, None, None, 60])
        self.assertTrue(result[1].quality_flags & QualityFlag.MISSING)
        self.assertTrue(result[2].quality_flags & QualityFlag.MISSING)

    def test_duplicate_timestamp_preserved_and_cannot_form_count_interval(self):
        result = process_counter(
            [sample(10), sample(12, 60), sample(15, 60), sample(17, 120), sample(18, 180)],
            verified(interval_exposure_supported=True),
        )
        self.assertEqual([row.raw_counter for row in result], [10, 12, 15, 17, 18])
        self.assertEqual([row.count_delta for row in result], [None, 2, None, None, 1])
        self.assertTrue(result[2].quality_flags & QualityFlag.ORDERING)
        self.assertTrue(result[3].quality_flags & QualityFlag.ORDERING)

    def test_stale_packets_cannot_create_overlapping_intervals(self):
        samples = [sample(10), sample(20, 300), sample(12, 60), sample(14, 120),
                   sample(22, 360), sample(23, 420)]
        result = process_counter(samples, verified(interval_exposure_supported=True))
        self.assertEqual([row.t_end_utc for row in result], [s.timestamp for s in samples])
        self.assertEqual([row.count_delta for row in result], [None, 10, None, None, None, 1])
        for index in (2, 3, 4):
            self.assertTrue(result[index].quality_flags & QualityFlag.ORDERING)
            self.assertIsNone(result[index].exposure_s)

    def test_gap_threshold_inclusive_and_crossing_quarantined(self):
        result = process_counter(
            [sample(0), sample(2, 60), sample(5, 121), sample(6, 181)],
            verified(max_gap_s=60, interval_exposure_supported=True),
        )
        self.assertEqual([row.count_delta for row in result], [None, 2, None, 1])
        self.assertTrue(result[2].quality_flags & QualityFlag.GAP)
        self.assertIsNone(result[2].exposure_s)

    def test_endpoint_issues_quarantine_both_adjacent_intervals(self):
        for flag in (QualityFlag.CLOCK, QualityFlag.MODE, QualityFlag.SCRUB,
                     QualityFlag.SAT, QualityFlag.THERMAL, QualityFlag.SCHEMA, QualityFlag.UNKNOWN):
            with self.subTest(flag=flag):
                result = process_counter(
                    [sample(0), sample(7700, 60, quality_flags=flag), sample(7701, 120),
                     sample(7702, 180)], verified(interval_exposure_supported=True),
                )
                self.assertEqual([row.count_delta for row in result], [None, None, None, 1])
                self.assertEqual([row.exposure_s for row in result], [None, None, None, 60])
                self.assertTrue(result[2].quality_flags & flag)

    def test_monotonic_increment_and_exposure_conservation(self):
        samples = [sample(20)]
        elapsed = 0
        total = 20
        for index in range(1, 100):
            elapsed += index % 7 + 1
            total += index % 5
            samples.append(sample(total, elapsed))
        result = process_counter(samples, verified(interval_exposure_supported=True))
        self.assertEqual(sum(row.count_delta or 0 for row in result), total - 20)
        self.assertEqual(sum(row.exposure_s or 0 for row in result), elapsed)
        self.assertTrue(all(row.quality_flags == QualityFlag.VALID for row in result))

    def test_empty_sequence(self):
        self.assertEqual(process_counter([], CounterSemantics()), [])


class CounterContractTests(unittest.TestCase):
    def test_invalid_counter_values_fail_loudly(self):
        for value in (True, False, 1.0, 1.5, "2", -1):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                sample(value)

    def test_counter_outside_declared_width_is_rejected(self):
        with self.assertRaises(ValueError):
            process_counter([sample(65536)], verified(bit_width=16))

    def test_naive_or_non_utc_timestamps_are_rejected(self):
        for timestamp in (datetime(2012, 3, 7),
                          datetime(2012, 3, 7, tzinfo=timezone(timedelta(hours=3)))):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                CounterSample(timestamp, 1, "original")

    def test_semantics_requires_real_boolean_positive_values_and_evidence(self):
        invalid = (
            {"status": "resolved"}, {"status": "verified"},
            {"bit_width": 0}, {"bit_width": True}, {"bit_width": 16.0},
            {"max_gap_s": 0}, {"max_gap_s": -1}, {"max_gap_s": True},
            {"max_gap_s": float("inf")}, {"max_gap_s": float("nan")},
            {"interval_exposure_supported": 1}, {"interval_exposure_supported": True},
            {"evidence_url": "not-a-source"}, {"evidence_url": "file:///private/doc"},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                CounterSemantics(**kwargs)

    def test_contradictory_confirmations_rejected(self):
        with self.assertRaises(ValueError):
            sample(1, reset_confirmed=True, wrap_confirmed=True)

    def test_unknown_quality_bits_rejected(self):
        with self.assertRaises(ValueError):
            sample(1, quality_flags=QualityFlag(1 << 20))

    def test_flag_values_are_stable(self):
        self.assertEqual(QualityFlag.Q0_VALID, 0)
        for index, name in enumerate(("Q1_RESET", "Q2_WRAP", "Q3_GAP", "Q4_CLOCK",
                                      "Q5_MODE", "Q6_SCRUB", "Q7_SAT", "Q8_THERMAL",
                                      "Q9_SCHEMA", "Q10_UNKNOWN")):
            self.assertEqual(getattr(QualityFlag, name), 1 << index)
        self.assertEqual(QualityFlag.UNKNOWN_SEMANTICS, 1 << 10)
        self.assertEqual(QualityFlag.ORDERING, 1 << 11)
        self.assertEqual(QualityFlag.MISSING, 1 << 12)


if __name__ == "__main__":
    unittest.main()
