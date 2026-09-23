import unittest

from srmwse import stats
from srmwse.stats import StatsError


class RankTests(unittest.TestCase):
    def test_distinct_values_rank_from_one(self):
        self.assertEqual(stats.average_ranks([10, 20, 30]), [1.0, 2.0, 3.0])

    def test_order_does_not_matter_to_the_rank_of_a_value(self):
        self.assertEqual(stats.average_ranks([30, 10, 20]), [3.0, 1.0, 2.0])

    def test_ties_share_their_average_rank(self):
        self.assertEqual(stats.average_ranks([5, 5, 9]), [1.5, 1.5, 3.0])

    def test_three_way_tie(self):
        self.assertEqual(stats.average_ranks([7, 7, 7]), [2.0, 2.0, 2.0])

    def test_all_ranks_sum_to_the_triangular_number(self):
        values = [1, 1, 2, 3, 3, 3, 8]
        n = len(values)
        self.assertAlmostEqual(sum(stats.average_ranks(values)), n * (n + 1) / 2)


class SpearmanTests(unittest.TestCase):
    def test_perfect_agreement(self):
        self.assertAlmostEqual(stats.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)

    def test_perfect_disagreement(self):
        self.assertAlmostEqual(stats.spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_monotone_but_not_linear_still_perfect(self):
        self.assertAlmostEqual(stats.spearman([1, 2, 3, 4], [1, 4, 9, 16]), 1.0)

    def test_a_constant_series_has_no_correlation(self):
        self.assertEqual(stats.spearman([1, 2, 3], [5, 5, 5]), 0.0)

    def test_mismatched_lengths_are_refused(self):
        with self.assertRaises(StatsError):
            stats.spearman([1, 2, 3], [1, 2])

    def test_a_single_point_is_refused(self):
        with self.assertRaises(StatsError):
            stats.spearman([1], [1])


class MannWhitneyTests(unittest.TestCase):
    def test_cleanly_separated_groups_are_significant(self):
        result = stats.mann_whitney([10, 11, 12, 13], [1, 2, 3, 4, 5],
                                    trials=2000, seed=1)
        self.assertEqual(result.u, 20.0)
        self.assertAlmostEqual(result.rank_biserial, 1.0)
        self.assertLess(result.p_one_sided, 0.01)

    def test_identical_groups_are_not_significant(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8]
        result = stats.mann_whitney(values, values, trials=2000, seed=1)
        self.assertAlmostEqual(result.rank_biserial, 0.0)
        self.assertGreater(result.p_one_sided, 0.2)

    def test_a_group_that_is_smaller_is_not_significant_one_sided(self):
        result = stats.mann_whitney([1, 2, 3], [10, 11, 12, 13],
                                    trials=2000, seed=1)
        self.assertEqual(result.u, 0.0)
        self.assertAlmostEqual(result.rank_biserial, -1.0)
        self.assertGreater(result.p_one_sided, 0.9)

    def test_p_value_can_never_be_zero(self):
        trials = 2000
        result = stats.mann_whitney(list(range(50, 62)), list(range(12)),
                                    trials=trials, seed=1)
        self.assertGreater(result.p_one_sided, 0.0)
        self.assertGreaterEqual(result.p_one_sided, 1 / (trials + 1))
        self.assertLess(result.p_one_sided, 0.01)

    def test_p_value_tracks_the_exact_answer_on_a_tiny_case(self):
        result = stats.mann_whitney([100, 101], [1, 2], trials=20000, seed=4)
        self.assertAlmostEqual(result.p_one_sided, 1 / 6, delta=0.011)

    def test_ties_between_groups_land_at_no_effect(self):
        result = stats.mann_whitney([5, 5], [5, 5], trials=500, seed=3)
        self.assertAlmostEqual(result.rank_biserial, 0.0)

    def test_medians_are_reported_for_both_groups(self):
        result = stats.mann_whitney([10, 20, 30], [1, 3], trials=200, seed=2)
        self.assertEqual(result.median_treatment, 20)
        self.assertEqual(result.median_control, 2)

    def test_group_sizes_are_reported(self):
        result = stats.mann_whitney([1, 2, 3], [4, 5], trials=200, seed=2)
        self.assertEqual((result.n_treatment, result.n_control), (3, 2))

    def test_same_seed_gives_the_same_p_value(self):
        args = ([9, 8, 7], [1, 2, 3, 4], )
        first = stats.mann_whitney(*args, trials=500, seed=7)
        second = stats.mann_whitney(*args, trials=500, seed=7)
        self.assertEqual(first.p_one_sided, second.p_one_sided)

    def test_empty_group_is_refused(self):
        with self.assertRaises(StatsError):
            stats.mann_whitney([], [1, 2], trials=100, seed=1)

    def test_zero_trials_is_refused(self):
        with self.assertRaises(StatsError):
            stats.mann_whitney([1], [2], trials=0, seed=1)

    def test_count_like_data_with_heavy_ties_behaves(self):
        quiet = [1] * 9 + [2] * 5 + [3] * 6 + [4] * 4 + [5] * 5 + [6, 7, 7, 11]
        high = [11, 16, 25, 64]
        result = stats.mann_whitney(high, quiet, trials=5000, seed=11)
        self.assertGreater(result.rank_biserial, 0.8)
        self.assertLess(result.p_one_sided, 0.01)


if __name__ == "__main__":
    unittest.main()
