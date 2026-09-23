from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence


class StatsError(ValueError):
    pass


def average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start
        while stop + 1 < len(order) and values[order[stop + 1]] == values[order[start]]:
            stop += 1
        shared = (start + stop) / 2 + 1
        for position in range(start, stop + 1):
            ranks[order[position]] = shared
        start = stop + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys):
        raise StatsError("spearman needs two series of the same length")
    if len(xs) < 2:
        raise StatsError("spearman needs at least two points")
    rx, ry = average_ranks(xs), average_ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    numerator = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    spread = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return numerator / spread if spread else 0.0


@dataclass(frozen=True, slots=True)
class MannWhitneyResult:
    u: float
    n_treatment: int
    n_control: int
    median_treatment: float
    median_control: float
    rank_biserial: float
    p_one_sided: float
    trials: int
    seed: int


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def mann_whitney(treatment: Sequence[float], control: Sequence[float], *,
                 trials: int, seed: int) -> MannWhitneyResult:
    if trials < 1:
        raise StatsError("trials must be positive")
    if not treatment or not control:
        raise StatsError("both groups need at least one value")
    combined = list(treatment) + list(control)
    ranks = average_ranks(combined)
    n_treatment = len(treatment)
    n_control = len(control)

    def statistic(rank_sum: float) -> float:
        return rank_sum - n_treatment * (n_treatment + 1) / 2

    observed = statistic(sum(ranks[:n_treatment]))
    rng = random.Random(seed)
    at_least = 0
    for _ in range(trials):
        drawn = rng.sample(ranks, n_treatment)
        if statistic(sum(drawn)) >= observed:
            at_least += 1
    p_value = (at_least + 1) / (trials + 1)
    return MannWhitneyResult(
        u=observed, n_treatment=n_treatment, n_control=n_control,
        median_treatment=_median(treatment), median_control=_median(control),
        rank_biserial=2 * observed / (n_treatment * n_control) - 1,
        p_one_sided=p_value, trials=trials, seed=seed,
    )
