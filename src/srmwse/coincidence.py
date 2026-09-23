from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random
from typing import Iterable, Sequence


class CoincidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Series:

    parameter: str
    device: str
    samples: Sequence[tuple[str, float]]


@dataclass(frozen=True, slots=True)
class Increment:

    device: str
    parameter: str
    delta: int
    window_start_utc: str
    observed_utc: str
    window_s: float


@dataclass(frozen=True, slots=True)
class Decrease:

    device: str
    parameter: str
    observed_utc: str
    before: float
    after: float


@dataclass(frozen=True, slots=True)
class Coincidence:

    start_utc: str
    end_utc: str
    devices: tuple[str, ...]
    total_delta: int
    increments: tuple[Increment, ...]


@dataclass(frozen=True, slots=True)
class TransitionSet:

    increments: list[Increment]
    decreases: list[Decrease]
    gap_spanning: list[Increment]
    max_gap_s: float


def _parse(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise CoincidenceError(f"Unparseable timestamp: {text!r}") from error


def median_interval(series: Series) -> float:
    samples = series.samples
    if len(samples) < 2:
        raise CoincidenceError(f"{series.parameter}: too few samples for a cadence")
    spacings = sorted((_parse(b[0]) - _parse(a[0])).total_seconds()
                      for a, b in zip(samples, samples[1:]))
    middle = len(spacings) // 2
    if len(spacings) % 2:
        return spacings[middle]
    return (spacings[middle - 1] + spacings[middle]) / 2


def transitions(series: Series, *, max_gap_s: float) -> TransitionSet:
    if max_gap_s <= 0:
        raise CoincidenceError("max_gap_s must be positive")
    ups: list[Increment] = []
    downs: list[Decrease] = []
    gapped: list[Increment] = []
    samples = series.samples
    for index in range(1, len(samples)):
        previous_time, previous_value = samples[index - 1]
        current_time, current_value = samples[index]
        if current_value == previous_value:
            continue
        if current_value < previous_value:
            downs.append(Decrease(series.device, series.parameter, current_time,
                                  previous_value, current_value))
            continue
        span = (_parse(current_time) - _parse(previous_time)).total_seconds()
        if span < 0:
            raise CoincidenceError(
                f"{series.parameter}: samples are not in time order at {current_time}")
        step = Increment(series.device, series.parameter,
                         int(current_value - previous_value),
                         previous_time, current_time, span)
        (gapped if span > max_gap_s else ups).append(step)
    return TransitionSet(ups, downs, gapped, max_gap_s)


def find_coincidences(increments: Iterable[Increment], *, window_s: float,
                      min_devices: int) -> list[Coincidence]:
    if min_devices < 2:
        raise CoincidenceError("A coincidence needs at least two devices")
    if window_s <= 0:
        raise CoincidenceError("window_s must be positive")
    ordered = sorted(increments, key=lambda i: _parse(i.observed_utc))
    if not ordered:
        return []
    times = [_parse(i.observed_utc) for i in ordered]
    found: list[Coincidence] = []
    for start in range(len(ordered)):
        limit = times[start].timestamp() + window_s
        end = start
        while end + 1 < len(ordered) and times[end + 1].timestamp() <= limit:
            end += 1
        group = ordered[start:end + 1]
        devices = sorted({i.device for i in group})
        if len(devices) < min_devices:
            continue
        candidate = Coincidence(group[0].observed_utc, group[-1].observed_utc,
                                tuple(devices), sum(i.delta for i in group),
                                tuple(group))
        if found and _parse(candidate.start_utc) <= _parse(found[-1].end_utc):
            if len(candidate.devices) > len(found[-1].devices):
                found[-1] = candidate
            continue
        found.append(candidate)
    return found


def overlaps(hit: Coincidence, start_utc: str, end_utc: str) -> bool:
    if _parse(start_utc) > _parse(end_utc):
        raise CoincidenceError("Interval end precedes its start")
    return (_parse(hit.start_utc) <= _parse(end_utc)
            and _parse(hit.end_utc) >= _parse(start_utc))


def hits_within(hits: Iterable[Coincidence], start_utc: str,
                end_utc: str) -> list[Coincidence]:
    return [hit for hit in hits if overlaps(hit, start_utc, end_utc)]


def chance_rate(series: Sequence[Series], increments: Sequence[Increment], *,
                window_s: float, min_devices: int, trials: int,
                seed: int, within: tuple[str, str] | None = None) -> dict:
    if trials < 1:
        raise CoincidenceError("trials must be positive")
    grids: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    devices: dict[str, str] = {}
    for item in series:
        grids[item.parameter] = [t for t, _ in item.samples]
        devices[item.parameter] = item.device
        counts[item.parameter] = 0
    for increment in increments:
        if increment.parameter not in counts:
            raise CoincidenceError(
                f"{increment.parameter} has no series to draw a null from")
        counts[increment.parameter] += 1

    rng = random.Random(seed)
    at_least_one = 0
    total = 0
    for _ in range(trials):
        drawn: list[Increment] = []
        for parameter, wanted in counts.items():
            grid = grids[parameter]
            if wanted == 0:
                continue
            if wanted > len(grid):
                raise CoincidenceError(
                    f"{parameter}: more increments than samples to place them on")
            for moment in rng.sample(grid, wanted):
                drawn.append(Increment(devices[parameter], parameter, 1,
                                       moment, moment, 0.0))
        hits = find_coincidences(drawn, window_s=window_s, min_devices=min_devices)
        if within is not None:
            hits = hits_within(hits, *within)
        total += len(hits)
        at_least_one += bool(hits)
    return {
        "trials": trials, "seed": seed, "window_s": window_s,
        "min_devices": min_devices,
        "restricted_to_interval": list(within) if within else None,
        "mean_coincidences_per_trial": total / trials,
        "trials_with_at_least_one": at_least_one,
        "p_at_least_one": at_least_one / trials,
        "p_upper_95": 3.0 / trials if at_least_one == 0 else None,
        "null_model": (
            "Per-parameter increment counts and per-parameter sampling grids "
            "held fixed; increment times re-drawn without replacement from the "
            "parameter's own observed sample times."
            + (" Only coincidences overlapping the named interval are counted."
               if within else "")
        ),
    }


def within_one_day(increment: Increment) -> bool:
    return increment.window_start_utc[:10] == increment.observed_utc[:10]


def daily_totals(increments: Iterable[Increment]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for increment in increments:
        if not within_one_day(increment):
            continue
        day = increment.observed_utc[:10]
        totals[day] = totals.get(day, 0) + increment.delta
    return totals


def covered_days(series: Series) -> int:
    return len({timestamp[:10] for timestamp, _ in series.samples})


def poisson_upper_tail(k: int, mean: float) -> float:
    if k <= 0:
        return 1.0
    if mean <= 0:
        return 0.0
    from math import exp, lgamma, log
    log_mean = log(mean)

    def term(i: int) -> float:
        return exp(i * log_mean - mean - lgamma(i + 1))

    if k <= mean:
        return max(0.0, 1.0 - sum(term(i) for i in range(k)))
    total = 0.0
    i = k
    while True:
        value = term(i)
        total += value
        if value <= total * 1e-18 or i > k + 10_000:
            return total
        i += 1


def rank_days(series: Series, increments: Sequence[Increment]) -> list[dict]:
    totals = daily_totals(increments)
    days = covered_days(series)
    if days == 0:
        raise CoincidenceError(f"{series.parameter}: no samples to rank")
    mean = sum(totals.values()) / days
    ranked = []
    for day, count in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        ranked.append({"day": day, "counts": count,
                       "poisson_upper_tail": poisson_upper_tail(count, mean),
                       "expected_days_at_least_this_high": poisson_upper_tail(count, mean) * days})
    return ranked
