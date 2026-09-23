import math
from datetime import datetime
import unittest

from srmwse import coincidence
from srmwse.coincidence import CoincidenceError, Series
from srmwse.io import d2
from srmwse.io.d2 import D2AccessError


def series(parameter, device, pairs):
    return Series(parameter, device, pairs)


def at(second, value):
    return (f"2005-03-04T22:{second // 60:02d}:{second % 60:02d}", value)


WIDE = 86_400.0


def _split(*args, **kwargs):
    result = coincidence.transitions(*args, **kwargs)
    return result.increments, result.decreases


class TransitionTests(unittest.TestCase):
    def test_increment_carries_the_interval_that_contains_it(self):
        ups, downs = _split(
            series("P", "UNIT", [at(0, 3), at(60, 3), at(120, 5)]), max_gap_s=WIDE)
        self.assertEqual(downs, [])
        step, = ups
        self.assertEqual(step.delta, 2)
        self.assertEqual(step.window_start_utc, "2005-03-04T22:01:00")
        self.assertEqual(step.observed_utc, "2005-03-04T22:02:00")
        self.assertEqual(step.window_s, 60.0)

    def test_equal_values_are_not_transitions(self):
        ups, downs = _split(
            series("P", "UNIT", [at(0, 7), at(60, 7), at(120, 7)]), max_gap_s=WIDE)
        self.assertEqual((ups, downs), ([], []))

    def test_decrease_is_reported_rather_than_dropped(self):
        ups, downs = _split(
            series("P", "UNIT", [at(0, 9), at(60, 0)]), max_gap_s=WIDE)
        self.assertEqual(ups, [])
        drop, = downs
        self.assertEqual((drop.before, drop.after), (9, 0))

    def test_decrease_is_not_labelled_as_reset_or_wrap(self):
        _, downs = _split(series("P", "UNIT", [at(0, 9), at(60, 0)]), max_gap_s=WIDE)
        self.assertNotIn("reset", str(downs[0]).lower())

    def test_out_of_order_samples_are_refused(self):
        with self.assertRaises(CoincidenceError):
            coincidence.transitions(series("P", "UNIT", [at(120, 1), at(0, 2)]), max_gap_s=WIDE)

    def test_unparseable_timestamp_is_refused(self):
        with self.assertRaises(CoincidenceError):
            coincidence.transitions(series("P", "UNIT", [("not-a-time", 1), at(0, 2)]), max_gap_s=WIDE)


class GapTests(unittest.TestCase):

    def slice_boundary(self):
        return Series("NACW0D0A", "AOCS_CDMU",
                      [("2005-03-31T23:55:14", 270.0),
                       ("2006-10-01T00:03:53", 1049.0),
                       ("2006-10-01T00:12:25", 1050.0)])

    def test_increment_across_an_archive_gap_is_set_aside(self):
        result = coincidence.transitions(self.slice_boundary(), max_gap_s=2048)
        self.assertEqual([i.delta for i in result.increments], [1])
        self.assertEqual([i.delta for i in result.gap_spanning], [779])

    def test_set_aside_counts_do_not_reach_the_day_ranking(self):
        item = self.slice_boundary()
        result = coincidence.transitions(item, max_gap_s=2048)
        ranked = coincidence.rank_days(item, result.increments)
        self.assertEqual([r["counts"] for r in ranked], [1])

    def test_day_boundary_filter_holds_even_without_a_gap_bound(self):
        item = self.slice_boundary()
        result = coincidence.transitions(item, max_gap_s=WIDE * 1000)
        self.assertEqual(len(result.increments), 2)
        ranked = coincidence.rank_days(item, result.increments)
        self.assertEqual([r["counts"] for r in ranked], [1])

    def test_increment_spanning_midnight_belongs_to_neither_day(self):
        item = Series("P", "UNIT", [("2005-03-04T23:58:00", 0.0),
                                    ("2005-03-05T00:04:00", 5.0),
                                    ("2005-03-05T00:10:00", 6.0)])
        result = coincidence.transitions(item, max_gap_s=WIDE)
        self.assertEqual(coincidence.daily_totals(result.increments),
                         {"2005-03-05": 1})

    def test_within_one_day_reads_the_source_date_text(self):
        inside = coincidence.Increment("U", "P", 1, "2005-03-04T00:01:00",
                                       "2005-03-04T23:59:00", 86_280.0)
        across = coincidence.Increment("U", "P", 1, "2005-03-04T23:59:00",
                                       "2005-03-05T00:01:00", 120.0)
        self.assertTrue(coincidence.within_one_day(inside))
        self.assertFalse(coincidence.within_one_day(across))

    def test_gap_spanning_increment_cannot_form_a_coincidence(self):
        one = coincidence.transitions(self.slice_boundary(), max_gap_s=2048)
        other = Series("NACP2800", "NAVCAM_B",
                       [("2005-03-31T23:55:14", 0.0), ("2006-10-01T00:03:53", 1.0)])
        two = coincidence.transitions(other, max_gap_s=2048)
        self.assertEqual(
            coincidence.find_coincidences(one.increments + two.increments,
                                          window_s=600, min_devices=2), [])

    def test_median_interval_describes_the_cadence(self):
        item = Series("P", "UNIT", [at(0, 0), at(16, 0), at(32, 0), at(900, 0)])
        self.assertEqual(coincidence.median_interval(item), 16.0)

    def test_cadence_needs_two_samples(self):
        with self.assertRaises(CoincidenceError):
            coincidence.median_interval(Series("P", "UNIT", [at(0, 0)]))

    def test_non_positive_gap_bound_is_refused(self):
        with self.assertRaises(CoincidenceError):
            coincidence.transitions(self.slice_boundary(), max_gap_s=0)


class DeviceGroupingTests(unittest.TestCase):

    def test_two_parameters_of_one_device_are_a_single_device(self):
        ups = []
        for parameter in ("NACP2300", "NACW1L1I"):
            step, _ = _split(
                series(parameter, "STR_B", [at(0, 1), at(30, 2)]), max_gap_s=WIDE)
            ups.extend(step)
        self.assertEqual(len(ups), 2)
        self.assertEqual(
            coincidence.find_coincidences(ups, window_s=600, min_devices=2), [])

    def test_two_genuinely_distinct_devices_do_coincide(self):
        ups = []
        for parameter, device in (("NACP2300", "STR_B"), ("NACP2800", "NAVCAM_B")):
            step, _ = _split(
                series(parameter, device, [at(0, 1), at(30, 2)]), max_gap_s=WIDE)
            ups.extend(step)
        hit, = coincidence.find_coincidences(ups, window_s=600, min_devices=2)
        self.assertEqual(hit.devices, ("NAVCAM_B", "STR_B"))
        self.assertEqual(hit.total_delta, 2)

    def test_archive_parameters_map_to_their_documented_device(self):
        self.assertEqual(d2.device_of("NACP2300"), d2.device_of("NACW1L1I"))
        self.assertEqual(d2.device_of("NACP2301"), "STR_B")
        self.assertNotEqual(d2.device_of("NACP1800"), d2.device_of("NACP2800"))

    def test_unknown_parameter_is_refused_rather_than_assumed_independent(self):
        with self.assertRaises(D2AccessError):
            d2.device_of("NOTAPARAM")

    def test_every_channel_parameter_has_a_device(self):
        for parameters in d2.D2_CHANNELS.values():
            for parameter in parameters:
                self.assertIn(parameter, d2.D2_PARAMETERS)


class WindowTests(unittest.TestCase):
    def three_devices(self, seconds):
        ups = []
        for offset, (parameter, device) in zip(
                seconds, (("A", "ONE"), ("B", "TWO"), ("C", "THREE"))):
            step, _ = _split(
                series(parameter, device, [at(offset, 1), at(offset + 1, 2)]), max_gap_s=WIDE)
            ups.extend(step)
        return ups

    def test_window_is_inclusive_of_its_far_edge(self):
        ups = self.three_devices([0, 100, 200])
        self.assertEqual(
            len(coincidence.find_coincidences(ups, window_s=200, min_devices=3)), 1)

    def test_window_one_second_too_narrow_excludes_the_far_edge(self):
        ups = self.three_devices([0, 100, 200])
        self.assertEqual(
            coincidence.find_coincidences(ups, window_s=199, min_devices=3), [])

    def test_spread_beyond_the_window_is_not_a_coincidence(self):
        ups = self.three_devices([0, 100, 400])
        self.assertEqual(
            coincidence.find_coincidences(ups, window_s=200, min_devices=3), [])

    def test_a_single_device_is_never_a_coincidence(self):
        with self.assertRaises(CoincidenceError):
            coincidence.find_coincidences([], window_s=600, min_devices=1)

    def test_non_positive_window_is_refused(self):
        with self.assertRaises(CoincidenceError):
            coincidence.find_coincidences([], window_s=0, min_devices=2)

    def test_no_increments_yields_no_coincidence(self):
        self.assertEqual(
            coincidence.find_coincidences([], window_s=600, min_devices=2), [])


class NullModelTests(unittest.TestCase):
    def build(self, cadence_s, count):
        samples = [at(i * cadence_s, 0) for i in range(count)]
        return samples

    def test_null_is_reproducible_for_a_seed(self):
        one = series("A", "ONE", self.build(1, 60))
        two = series("B", "TWO", self.build(1, 60))
        ups, _ = _split(
            series("A", "ONE", [at(0, 1), at(1, 2)]), max_gap_s=WIDE)
        first = coincidence.chance_rate([one, two], ups, window_s=10,
                                        min_devices=2, trials=50, seed=7)
        second = coincidence.chance_rate([one, two], ups, window_s=10,
                                         min_devices=2, trials=50, seed=7)
        self.assertEqual(first["p_at_least_one"], second["p_at_least_one"])

    def test_increment_without_a_series_is_refused(self):
        one = series("A", "ONE", self.build(1, 10))
        ups, _ = _split(series("Z", "OTHER", [at(0, 1), at(1, 2)]), max_gap_s=WIDE)
        with self.assertRaises(CoincidenceError):
            coincidence.chance_rate([one], ups, window_s=10, min_devices=2,
                                    trials=5, seed=1)

    def test_more_increments_than_samples_is_refused(self):
        one = series("A", "ONE", self.build(1, 2))
        ups = [coincidence.Increment("ONE", "A", 1, "2005-03-04T22:00:00",
                                     "2005-03-04T22:00:00", 0.0)] * 5
        with self.assertRaises(CoincidenceError):
            coincidence.chance_rate([one], ups, window_s=10, min_devices=2,
                                    trials=5, seed=1)

    def test_zero_trials_is_refused(self):
        one = series("A", "ONE", self.build(1, 10))
        with self.assertRaises(CoincidenceError):
            coincidence.chance_rate([one], [], window_s=10, min_devices=2,
                                    trials=0, seed=1)

    def test_null_uses_each_parameter_own_sampling_grid(self):
        dense = series("A", "ONE", self.build(1, 600))
        sparse = series("B", "TWO", [at(0, 0), at(590, 0)])
        ups = []
        for parameter, device, moment in (("A", "ONE", 0), ("B", "TWO", 590)):
            step, _ = _split(
                series(parameter, device, [at(moment, 1), at(moment + 1, 2)]), max_gap_s=WIDE)
            ups.extend(step)
        result = coincidence.chance_rate([dense, sparse], ups, window_s=5,
                                         min_devices=2, trials=200, seed=3)
        self.assertLess(result["p_at_least_one"], 0.2)


class TargetIntervalTests(unittest.TestCase):

    def hit(self, start, end):
        return coincidence.Coincidence(start, end, ("ONE", "TWO"), 2, ())

    def test_hit_inside_the_interval_counts(self):
        h = self.hit("2005-03-04T22:11:25", "2005-03-04T22:20:59")
        self.assertTrue(coincidence.overlaps(h, "2005-03-04T21:10:00",
                                             "2005-03-04T23:10:00"))

    def test_hit_outside_the_interval_does_not(self):
        h = self.hit("2005-03-06T12:00:00", "2005-03-06T12:05:00")
        self.assertFalse(coincidence.overlaps(h, "2005-03-04T21:10:00",
                                              "2005-03-04T23:10:00"))

    def test_hit_straddling_the_edge_counts(self):
        h = self.hit("2005-03-04T23:05:00", "2005-03-04T23:15:00")
        self.assertTrue(coincidence.overlaps(h, "2005-03-04T21:10:00",
                                             "2005-03-04T23:10:00"))

    def test_reversed_interval_is_refused(self):
        with self.assertRaises(CoincidenceError):
            coincidence.overlaps(self.hit("2005-03-04T22:00:00", "2005-03-04T22:01:00"),
                                 "2005-03-04T23:00:00", "2005-03-04T21:00:00")

    def test_filtering_keeps_only_the_named_interval(self):
        hits = [self.hit("2005-03-04T22:11:00", "2005-03-04T22:20:00"),
                self.hit("2005-03-20T09:00:00", "2005-03-20T09:05:00")]
        kept = coincidence.hits_within(hits, "2005-03-04T21:10:00",
                                       "2005-03-04T23:10:00")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].start_utc, "2005-03-04T22:11:00")

    def test_restricted_null_is_never_looser_than_the_unrestricted_one(self):
        grid = [(f"2005-03-04T{h:02d}:{m:02d}:00", 0.0)
                for h in range(24) for m in range(0, 60, 2)]
        one = Series("A", "ONE", grid)
        two = Series("B", "TWO", grid)
        ups = []
        for parameter, device in (("A", "ONE"), ("B", "TWO")):
            step = coincidence.transitions(
                Series(parameter, device,
                       [("2005-03-04T22:10:00", 1.0), ("2005-03-04T22:12:00", 2.0)]),
                max_gap_s=WIDE)
            ups.extend(step.increments)
        anywhere = coincidence.chance_rate([one, two], ups, window_s=600,
                                           min_devices=2, trials=200, seed=11)
        named = coincidence.chance_rate([one, two], ups, window_s=600,
                                        min_devices=2, trials=200, seed=11,
                                        within=("2005-03-04T21:10:00",
                                                "2005-03-04T23:10:00"))
        self.assertLessEqual(named["p_at_least_one"], anywhere["p_at_least_one"])
        self.assertEqual(named["restricted_to_interval"],
                         ["2005-03-04T21:10:00", "2005-03-04T23:10:00"])

    def test_zero_hits_reports_a_bound_not_a_zero_probability(self):
        grid = [(f"2005-03-04T{h:02d}:00:00", 0.0) for h in range(24)]
        one, two = Series("A", "ONE", grid), Series("B", "TWO", grid)
        step = coincidence.transitions(
            Series("A", "ONE", [("2005-03-04T22:00:00", 1.0),
                                ("2005-03-04T23:00:00", 2.0)]), max_gap_s=WIDE)
        result = coincidence.chance_rate([one, two], step.increments, window_s=1,
                                         min_devices=2, trials=100, seed=5)
        self.assertEqual(result["p_at_least_one"], 0.0)
        self.assertAlmostEqual(result["p_upper_95"], 0.03)


class SwingbyRegisterTests(unittest.TestCase):

    def test_every_swingby_carries_its_source_wording(self):
        from srmwse.cli import SWINGBYS
        for name, entry in SWINGBYS.items():
            self.assertTrue(entry["source"], f"{name} has no source")
            self.assertIn("closest_approach_utc", entry)
            datetime.fromisoformat(entry["closest_approach_utc"])

    def test_mars_is_registered_as_the_control(self):
        from srmwse.cli import SWINGBYS
        self.assertFalse(SWINGBYS["mars"]["trapped_belts"])
        self.assertTrue(all(SWINGBYS[e]["trapped_belts"]
                            for e in ("earth_1", "earth_2", "earth_3")))

    def test_interval_brackets_the_published_time(self):
        from srmwse.cli import target_interval, SWINGBYS, TARGET_HALF_WIDTH_S
        start, end = target_interval("earth_3")
        moment = datetime.fromisoformat(SWINGBYS["earth_3"]["closest_approach_utc"])
        self.assertEqual((moment - datetime.fromisoformat(start)).total_seconds(),
                         TARGET_HALF_WIDTH_S)
        self.assertEqual((datetime.fromisoformat(end) - moment).total_seconds(),
                         TARGET_HALF_WIDTH_S)

    def test_unknown_event_is_refused(self):
        from srmwse.cli import target_interval
        with self.assertRaises(ValueError):
            target_interval("jupiter")


class PoissonTests(unittest.TestCase):
    def test_known_value(self):
        self.assertAlmostEqual(coincidence.poisson_upper_tail(1, 1.0),
                               1 - math.exp(-1.0), places=12)

    def test_deep_tail_does_not_collapse_to_zero(self):
        value = coincidence.poisson_upper_tail(50, 1.68)
        self.assertGreater(value, 0.0)
        self.assertLess(value, 1e-40)

    def test_tail_is_never_negative(self):
        for k in range(0, 60):
            self.assertGreaterEqual(coincidence.poisson_upper_tail(k, 1.68), 0.0)

    def test_tail_is_monotonic(self):
        values = [coincidence.poisson_upper_tail(k, 2.0) for k in range(1, 30)]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_zero_threshold_is_certain(self):
        self.assertEqual(coincidence.poisson_upper_tail(0, 1.68), 1.0)

    def test_silent_channel_has_no_tail(self):
        self.assertEqual(coincidence.poisson_upper_tail(3, 0.0), 0.0)


class RankingTests(unittest.TestCase):
    def test_days_are_ranked_by_counts_gained(self):
        samples = [("2005-01-19T00:00:00", 0), ("2005-01-19T12:00:00", 0),
                   ("2005-01-20T00:00:00", 0), ("2005-01-20T12:00:00", 5),
                   ("2005-01-21T00:00:00", 5), ("2005-01-21T12:00:00", 6)]
        item = series("NACW0D0A", "AOCS_CDMU", samples)
        ups, _ = _split(item, max_gap_s=WIDE)
        ranked = coincidence.rank_days(item, ups)
        self.assertEqual(ranked[0]["day"], "2005-01-20")
        self.assertEqual(ranked[0]["counts"], 5)
        self.assertEqual(coincidence.covered_days(item), 3)

    def test_ranking_needs_samples(self):
        with self.assertRaises(CoincidenceError):
            coincidence.rank_days(series("P", "UNIT", []), [])


if __name__ == "__main__":
    unittest.main()
