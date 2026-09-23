from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from srmwse.cli import LARGE_STEP_THRESHOLD, main, summarise_counter_steps


class CommandTests(unittest.TestCase):
    def test_demo_is_deterministic_and_labels_synthetic_data(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo.json"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["demo", "--output", str(output)]), 0)
                first = output.read_bytes()
                self.assertEqual(main(["demo", "--output", str(output)]), 0)
            self.assertEqual(first, output.read_bytes())
            report = json.loads(first)
            self.assertEqual(report["data_kind"], "synthetic")
            self.assertIsNone(report["scientific_claim"])
            self.assertEqual(report["unresolved_semantics_valid_increment_count"], 0)
            self.assertEqual(report["rows"][2]["count_delta"], 0)
            self.assertIsNone(report["rows"][6]["count_delta"])

    def test_missing_archive_fails_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["audit-d1", "--data-dir", directory]), 1)
            self.assertIn("fetch-d1", error.getvalue())

    def test_channel_rates_without_an_archive_fails_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["channel-rates", "--data-dir", directory]), 1)
            self.assertIn("fetch-d1", error.getvalue())


class CounterStepSummaryTests(unittest.TestCase):

    def test_positive_steps_are_summed_per_source_day(self):
        result = summarise_counter_steps([10, 12, 15], {"2022-09-04", "2022-09-05"})
        self.assertEqual(result["raw_positive_step_sum"], 5)
        self.assertEqual(result["raw_step_sum_per_source_day"], 2.5)

    def test_large_steps_are_reported_separately_so_one_burst_cannot_dominate(self):
        values = [0, 5, 5 + LARGE_STEP_THRESHOLD, 5 + LARGE_STEP_THRESHOLD + 5]
        result = summarise_counter_steps(values, {"2022-09-04"})
        self.assertEqual(result["large_step_count"], 1)
        self.assertEqual(result["large_step_sum"], LARGE_STEP_THRESHOLD)
        self.assertEqual(result["raw_step_sum_per_source_day_excluding_large"], 10)

    def test_negative_transition_is_counted_but_never_given_a_magnitude(self):
        result = summarise_counter_steps([63952, 2561, 2600], {"2022-09-06"})
        self.assertEqual(result["negative_transition_count"], 1)
        self.assertEqual(result["raw_positive_step_sum"], 39)

    def test_summary_never_claims_resolved_semantics(self):
        result = summarise_counter_steps([1, 2], {"2022-09-04"})
        self.assertEqual(result["semantics_status"], "unresolved")

    def test_flat_series_reports_zero_rather_than_failing(self):
        result = summarise_counter_steps([7, 7, 7], {"2022-09-04"})
        self.assertEqual(result["raw_positive_step_sum"], 0)
        self.assertEqual(result["raw_step_sum_per_source_day"], 0)
