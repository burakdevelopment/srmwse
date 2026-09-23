import argparse
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys
import zipfile

from srmwse import __version__
from srmwse import coincidence, stats
from srmwse.counters import CounterSample, CounterSemantics, QualityFlag, process_counter
from srmwse.io.d1 import (audit_archive, fetch_d1, inspect_source, utc_now,
                          write_immutable, write_json)
from srmwse.io.edac import UnsupportedExportError, parse_export
from srmwse.io.his import decode_log, looks_like_his_log
from srmwse.io import d2, d9, soar
from srmwse.io.d2 import D2_DATASET_ID
from srmwse.io.pds3 import parse_product


def demo(output: Path) -> dict:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    samples = [
        CounterSample(start + timedelta(minutes=i), value,
                      (start + timedelta(minutes=i)).isoformat(), reset_confirmed=(i == 4))
        for i, value in enumerate([100, 102, 102, 110, 0, 1, None, 3, 4])
    ]
    semantics = CounterSemantics(
        status="verified", bit_width=16, interval_exposure_supported=True,
        max_gap_s=120, evidence_url="https://example.org/synthetic-counter-contract",
    )
    rows = process_counter(samples, semantics)
    unresolved = process_counter(samples, CounterSemantics())
    serialized = []
    for row in rows:
        record = asdict(row)
        record["t_start_utc"] = row.t_start_utc.isoformat() if row.t_start_utc else None
        record["t_end_utc"] = row.t_end_utc.isoformat()
        record["quality_flags"] = int(row.quality_flags)
        record["quality_names"] = [flag.name for flag in QualityFlag if flag & row.quality_flags]
        serialized.append(record)
    report = {
        "schema_version": "0.1.0", "experiment_id": "synthetic_counter_contract_demo",
        "software_version": __version__, "data_kind": "synthetic",
        "status": "software_demonstration_only", "scientific_claim": None,
        "description": "Synthetic cumulative counter with a zero increment, confirmed reset and missing observation.",
        "sample_count": len(samples), "valid_increment_count": sum(r.count_delta is not None for r in rows),
        "unresolved_semantics_valid_increment_count": sum(r.count_delta is not None for r in unresolved),
        "rows": serialized,
    }
    write_json(output, report)
    return report


def _audit_event_log(payload: bytes) -> dict:
    if not looks_like_his_log(payload.decode("utf-8-sig", errors="replace")):
        return {"format": "unrecognised", "parse_status": "not_parsed",
                "sample_count": None, "reason": "no supported export or log signature"}
    log = decode_log(payload)
    regions: dict[str, dict[str, int]] = {}
    for event in log.events:
        summary = regions.setdefault(event.region, {"events": 0, "reported_errors": 0})
        summary["events"] += 1
        summary["reported_errors"] += event.reported_errors
    multi_cell = sum(1 for e in log.events if e.address_span not in (0, None))
    return {
        "format": log.format_name, "parse_status": "parsed",
        "channels": list(log.regions), "sample_count": len(log.events),
        "first_source_timestamp": log.events[0].source_timestamp if log.events else None,
        "last_source_timestamp": log.events[-1].source_timestamp if log.events else None,
        "missing_value_count": 0,
        "record_kind": log.metadata["record_kind"],
        "reported_error_total": sum(e.reported_errors for e in log.events),
        "multi_cell_event_count": multi_cell,
        "events_by_region": regions,
    }


def audit_d1(data_dir: Path) -> dict:
    archive = data_dir / "D1" / "v1" / "EDAC.zip"
    if not archive.exists():
        raise ValueError("D1 archive is missing; run fetch-d1 first")
    receipt = fetch_d1(data_dir)
    report = audit_archive(archive, expected_sha256=receipt["sha256"])
    with zipfile.ZipFile(archive) as source:
        for member in report["members"]:
            payload = source.read(member["filename"])
            try:
                parsed = parse_export(payload.decode("utf-8-sig", errors="strict"))
            except UnsupportedExportError:
                member.update(_audit_event_log(payload))
                continue
            member.update({
                "format": parsed.format_name, "parse_status": "parsed", "channels": list(parsed.channels),
                "archive_declared_channels": [asdict(d) for d in parsed.descriptors],
                "sample_count": len(parsed.samples),
                "first_source_timestamp": parsed.samples[0].source_timestamp if parsed.samples else None,
                "last_source_timestamp": parsed.samples[-1].source_timestamp if parsed.samples else None,
                "missing_value_count": sum(v is None for sample in parsed.samples for v in sample.values),
            })
    report["source_file_count"] = len(report["members"])
    report["parsed_file_count"] = sum(m["parse_status"] == "parsed" for m in report["members"])
    report["source_sample_count"] = sum(m["sample_count"] or 0 for m in report["members"])
    report["status"] = "parsed_source_audit_only"
    return report


LARGE_STEP_THRESHOLD = 1000


def summarise_counter_steps(values: list[int], source_days: set[str]) -> dict:
    steps = [b - a for a, b in zip(values, values[1:])]
    positive = [s for s in steps if s > 0]
    large = [s for s in positive if s >= LARGE_STEP_THRESHOLD]
    small = [s for s in positive if s < LARGE_STEP_THRESHOLD]
    day_count = len(source_days)
    return {
        "source_day_count": day_count,
        "raw_positive_step_sum": sum(positive),
        "raw_step_sum_per_source_day": round(sum(positive) / day_count, 3),
        "large_step_threshold": LARGE_STEP_THRESHOLD,
        "large_step_count": len(large),
        "large_step_sum": sum(large),
        "raw_step_sum_per_source_day_excluding_large": round(sum(small) / day_count, 3),
        "negative_transition_count": sum(1 for s in steps if s < 0),
        "semantics_status": "unresolved",
    }


def channel_rates(data_dir: Path) -> dict:
    archive = data_dir / "D1" / "v1" / "EDAC.zip"
    if not archive.exists():
        raise ValueError("D1 archive is missing; run fetch-d1 first")
    receipt = fetch_d1(data_dir)
    channels: list[dict] = []
    with zipfile.ZipFile(archive) as source:
        for info in sorted(source.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            try:
                parsed = parse_export(source.read(info).decode("utf-8-sig", errors="strict"))
            except UnsupportedExportError:
                continue
            mission = info.filename.split("/")[1]
            for index, channel in enumerate(parsed.channels):
                values, days = [], set()
                for sample in parsed.samples:
                    value = sample.values[index]
                    if value is None:
                        continue
                    values.append(int(value))
                    days.add(sample.source_timestamp[:10])
                if len(values) < 2 or not days:
                    continue
                channels.append({"member": info.filename, "mission": mission,
                                 "channel": channel, **summarise_counter_steps(values, days)})
    return {
        "source_id": "D1", "dataset_id": receipt["dataset_id"],
        "archive_sha256": receipt["sha256"], "parser_version": __version__,
        "status": "exploration_only",
        "interpretation": (
            "Raw counter differences for channels with unresolved semantics. "
            "Not an error rate, event rate or particle rate. Source days are "
            "calendar days present in the source timestamps, whose time system "
            "is itself unresolved."
        ),
        "time_system": "unresolved", "quantitative_channels": [],
        "channels": channels,
    }


D5_CONTRACT = {"instrument": "EPD", "descriptor": "epd-het-sun-rates", "level": "L2",
               "start": "2022-09-04", "end": "2022-09-12"}


def fetch_d5(data_dir: Path) -> dict:
    directory = data_dir / "D5" / f"{D5_CONTRACT['descriptor']}-{D5_CONTRACT['level']}"
    items = soar.find_data_items(**D5_CONTRACT)
    if not items:
        raise ValueError("SOAR returned no products for the pinned D5 contract")
    records = []
    for item in items:
        target = directory / item.filename
        receipt_path = directory / f"{item.filename}.receipt.json"
        if target.exists():
            payload = target.read_bytes()
            if not receipt_path.exists():
                raise ValueError(f"{item.filename}: cached file has no provenance receipt")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("sha256") != soar.sha256_bytes(payload):
                raise ValueError(f"{item.filename}: cached bytes differ from their receipt")
            retrieved = receipt["retrieval_utc"]
        else:
            payload, retrieved = soar.download_item(item), utc_now()
            write_immutable(target, payload)
            write_immutable(receipt_path, (json.dumps(
                {"data_item_id": item.data_item_id, "filename": item.filename,
                 "retrieval_utc": retrieved, "sha256": soar.sha256_bytes(payload),
                 "declared_byte_size": item.filesize, "parser_version": __version__},
                indent=2) + "\n").encode())
        records.append({
            "data_item_id": item.data_item_id, "filename": item.filename,
            "descriptor": item.descriptor, "level": item.level,
            "begin_time": item.begin_time, "end_time": item.end_time,
            "declared_byte_size": item.filesize, "received_byte_size": len(payload),
            "sha256": soar.sha256_bytes(payload), "checksum_origin": "computed_on_retrieval",
            "retrieval_utc": retrieved, "file_format": item.file_format,
            "local_path": str(target.relative_to(data_dir)).replace("\\", "/"),
            **soar.describe_container(payload),
        })
    manifest = {
        "source_id": "D5", "archive": "ESA Solar Orbiter Archive (SOAR)",
        "tap_url": soar.TAP_SYNC_URL, "contract": dict(D5_CONTRACT),
        "parser_version": __version__, "status": "access_check_only",
        "interpretation": (
            "Product availability and byte-level provenance for a same-spacecraft "
            "reference instrument. No CDF content is decoded, so no flux, time "
            "correlation or physical overlap is established here."
        ),
        "time_system": "unresolved", "quantitative_channels": [],
        "item_count": len(records),
        "declared_coverage": {"first_begin_time": records[0]["begin_time"],
                              "last_end_time": records[-1]["end_time"]},
        "items": records,
    }
    return manifest


D2_DEFAULT_CHANNEL = "aocs_and_dms_edac_cntr"
D2_DEFAULT_QUARTERS = ("2006_q4",)


def fetch_d2(data_dir: Path, channel: str, quarters: tuple[str, ...]) -> dict:
    directory = data_dir / "D2" / D2_DATASET_ID
    checksums = d2.fetch_checksums()
    products = d2.products_for(channel, quarters, checksums)
    records = []
    for product in products:
        label_record = d2.retrieve(product.label_path, checksums,
                                   directory / product.label_path)
        table_record = d2.retrieve(product.table_path, checksums,
                                   directory / product.table_path)
        parsed = parse_product(
            (directory / product.label_path).read_bytes().decode("ascii", "strict"),
            (directory / product.table_path).read_bytes(),
        )
        values = [row.values[-1] for row in parsed.rows]
        integers = [v for v in values if isinstance(v, int)]
        records.append({
            "channel": product.channel, "parameter": product.parameter,
            "period": product.period,
            "label": label_record, "table": table_record,
            "record_count": len(parsed.rows),
            "declared_description": parsed.label.table.description,
            "columns": [c.name for c in parsed.label.table.columns],
            "first_source_timestamp": parsed.rows[0].source_fields[0].strip() if parsed.rows else None,
            "last_source_timestamp": parsed.rows[-1].source_fields[0].strip() if parsed.rows else None,
            "missing_value_count": sum(v is None for v in values),
            "negative_transition_count": sum(1 for a, b in zip(integers, integers[1:]) if b < a),
            "semantics_status": "unresolved",
        })
    return {
        "source_id": "D2", "dataset_id": D2_DATASET_ID, "base_url": d2.D2_BASE_URL,
        "requested_slice": {"channel": channel, "quarters": list(quarters)},
        "parser_version": __version__, "status": "parsed_source_audit_only",
        "interpretation": (
            "A named slice of the second archive family, verified against the "
            "publisher's checksum manifest and parsed with its own labels. "
            "Counter semantics remain unresolved; no channel is admitted."
        ),
        "time_system": "unresolved", "quantitative_channels": [],
        "product_count": len(records),
        "record_total": sum(r["record_count"] for r in records),
        "products": records,
    }


COINCIDENCE_WINDOW_S = 600.0
COINCIDENCE_MIN_DEVICES = 3
COINCIDENCE_TRIALS = 2000
COINCIDENCE_SEED = 20050304
TOP_DAYS = 5
DAY_S = 86_400.0

SWINGBYS = {
    "earth_1": {"closest_approach_utc": "2005-03-04T22:10:00", "body": "Earth",
                "altitude_km": 1900, "quarter": "2005_q1", "trapped_belts": True,
                "source": "ESA: 'Closest approach will occur around 22h10 UT', "
                          "'1900 km from the surface'",
                "role": "discovery (not a test)"},
    "mars": {"closest_approach_utc": "2007-02-25T01:54:00", "body": "Mars",
             "altitude_km": 250, "quarter": "2007_q1", "trapped_belts": False,
             "source": "ESA: 'The closest approach of the swing-by will take place "
                       "at 01:54 UT, 25 February 2007', '250 km above the surface'",
             "role": "negative control"},
    "earth_2": {"closest_approach_utc": "2007-11-13T20:57:00", "body": "Earth",
                "altitude_km": 5301, "quarter": "2007_q4", "trapped_belts": True,
                "source": "ESA: 'Closest approach is foreseen at 21:57 CET' "
                          "(= 20:57 UTC), '5301km from Earth's surface'",
                "role": "test"},
    "earth_3": {"closest_approach_utc": "2009-11-13T07:45:40", "body": "Earth",
                "altitude_km": 2480, "quarter": "2009_q4", "trapped_belts": True,
                "source": "ESA status report 124: 'DoY 317.07:45:40 UTC ... "
                          "closest approach', 'about 2480 km from the Earth's surface'",
                "role": "test"},
}
TARGET_HALF_WIDTH_S = 3600


def _d2_series(data_dir: Path, quarter: str | None = None) -> list[coincidence.Series]:
    root = data_dir / "D2" / D2_DATASET_ID / "data"
    if not root.exists():
        raise ValueError("No D2 products found; run fetch-d2 first")
    by_parameter: dict[str, list[tuple[str, float]]] = {}
    for label_path in sorted(root.rglob("*.lbl")):
        if quarter is not None and label_path.parent.name != quarter:
            continue
        table_path = label_path.with_suffix(".tab")
        if not table_path.exists():
            raise ValueError(f"{label_path.name} has no table beside it")
        parameter = label_path.stem.split("_")[2].upper()
        if parameter in d2.D2_FLAG_PARAMETERS:
            continue
        parsed = parse_product(label_path.read_text(encoding="ascii"),
                               table_path.read_bytes())
        rows = by_parameter.setdefault(parameter, [])
        for row in parsed.rows:
            value = row.values[-1]
            if value is None:
                continue
            rows.append((row.source_fields[0].strip(), float(value)))
    series = []
    for parameter, rows in sorted(by_parameter.items()):
        rows.sort(key=lambda pair: pair[0])
        series.append(coincidence.Series(parameter, d2.device_of(parameter), rows))
    return series


def target_interval(event: str) -> tuple[str, str]:
    from datetime import timedelta
    if event not in SWINGBYS:
        raise ValueError(f"Unknown swing-by: {event!r}")
    moment = datetime.fromisoformat(SWINGBYS[event]["closest_approach_utc"])
    half = timedelta(seconds=TARGET_HALF_WIDTH_S)
    return ((moment - half).isoformat(), (moment + half).isoformat())


def coincidence_scan(data_dir: Path, window_s: float, min_devices: int,
                     trials: int, seed: int, event: str | None = None) -> dict:
    quarter = SWINGBYS[event]["quarter"] if event is not None else None
    series = _d2_series(data_dir, quarter)
    if not series:
        raise ValueError(f"No D2 products downloaded for {quarter}; run fetch-d2 first")
    increments: list[coincidence.Increment] = []
    decreases: list[coincidence.Decrease] = []
    gap_spanning: list[coincidence.Increment] = []
    per_parameter = []
    for item in series:
        cadence = coincidence.median_interval(item)
        split = coincidence.transitions(item, max_gap_s=window_s)
        daily_split = coincidence.transitions(item, max_gap_s=DAY_S)
        ups, downs = split.increments, split.decreases
        increments.extend(ups)
        decreases.extend(downs)
        gap_spanning.extend(split.gap_spanning)
        per_parameter.append({
            "parameter": item.parameter, "device": item.device,
            "memory_region": d2.D2_PARAMETERS[item.parameter][1],
            "declared_description": d2.D2_PARAMETERS[item.parameter][2],
            "sample_count": len(item.samples),
            "covered_days": coincidence.covered_days(item),
            "median_interval_s": cadence, "max_gap_s": split.max_gap_s,
            "daily_increment_count": sum(
                1 for u in daily_split.increments if coincidence.within_one_day(u)),
            "increment_count": len(ups), "counts_gained": sum(u.delta for u in ups),
            "decrease_count": len(downs),
            "gap_spanning_increment_count": len(split.gap_spanning),
            "gap_spanning_counts_set_aside": sum(g.delta for g in split.gap_spanning),
            "most_extreme_days": (coincidence.rank_days(item, daily_split.increments)[:TOP_DAYS]
                                  if daily_split.increments else []),
        })
    hits = coincidence.find_coincidences(increments, window_s=window_s,
                                         min_devices=min_devices)
    null = coincidence.chance_rate(series, increments, window_s=window_s,
                                   min_devices=min_devices, trials=trials, seed=seed)
    prediction = None
    if event is not None:
        start, end = target_interval(event)
        inside = coincidence.hits_within(hits, start, end)
        prediction = {
            "event": event, **SWINGBYS[event],
            "analysed_quarter": quarter,
            "interval_utc": [start, end],
            "half_width_s": TARGET_HALF_WIDTH_S,
            "hits_inside_interval": len(inside),
            "predicted_hit": bool(inside),
            "null_restricted": coincidence.chance_rate(
                series, increments, window_s=window_s, min_devices=min_devices,
                trials=trials, seed=seed, within=(start, end)),
            "coincidences_inside": [
                {"start_utc": h.start_utc, "end_utc": h.end_utc,
                 "devices": list(h.devices), "device_count": len(h.devices),
                 "total_delta": h.total_delta} for h in inside],
        }
    return {
        "prediction": prediction,
        "source_id": "D2", "dataset_id": D2_DATASET_ID,
        "parser_version": __version__, "status": "exploration_only",
        "interpretation": (
            "Time coincidence between raw counter steps on physically distinct "
            "units. Counters are grouped by device, so two parameters reporting "
            "one counter count once. Semantics remain unresolved: this is not a "
            "measured particle event, flux or dose, and the windows were not "
            "chosen in advance."
        ),
        "time_system": "unresolved", "quantitative_channels": [],
        "parameters": per_parameter,
        "device_count": len({s.device for s in series}),
        "increment_count": len(increments),
        "decrease_count": len(decreases),
        "gap_spanning_increment_count": len(gap_spanning),
        "gap_spanning_counts_set_aside": sum(g.delta for g in gap_spanning),
        "gap_handling": (
            f"An increment observed across an interval wider than the {window_s:g} s "
            "coincidence window is set aside: its own timing uncertainty exceeds "
            "the agreement a coincidence would claim. Separately, the daily "
            "ranking excludes increments that span a calendar-day boundary. The "
            "counts are real in both cases; what is missing is a time to put them at."
        ),
        "decreases": [asdict(d) for d in decreases],
        "gap_spanning": [asdict(g) for g in gap_spanning],
        "null": null,
        "coincidence_count": len(hits),
        "coincidences": [
            {"start_utc": hit.start_utc, "end_utc": hit.end_utc,
             "devices": list(hit.devices), "device_count": len(hit.devices),
             "total_delta": hit.total_delta,
             "increments": [asdict(i) for i in hit.increments]}
            for hit in hits
        ],
    }


D9_EVENT_PHASE = {"earth_1": "ear1", "earth_2": "ear2", "earth_3": "ear3", "mars": "mars"}
D9_HALF_WIDTH_DAYS = 1


def daily_srem(samples: list[tuple[str, dict[str, float]]]) -> list[dict]:
    by_day: dict[str, list[dict[str, float]]] = {}
    for moment, rates in samples:
        by_day.setdefault(moment[:10], []).append(rates)
    reported = (*d9.PROTON_ONLY, d9.L1_MATCHED_CHANNEL)
    rows = []
    for day in sorted(by_day):
        day_rates = by_day[day]
        entry: dict[str, object] = {"day": day, "sample_count": len(day_rates)}
        for name in reported:
            values = sorted(r[name] for r in day_rates)
            entry[name] = {"median_per_s": values[len(values) // 2],
                           "max_per_s": values[-1]}
        rows.append(entry)
    return rows


def fetch_d9(data_dir: Path, *, event: str | None = None, phase: str | None = None,
             first: str | None = None, last: str | None = None) -> dict:
    if event is not None:
        if event not in D9_EVENT_PHASE:
            raise ValueError(f"No SREM phase recorded for {event!r}")
        phase = D9_EVENT_PHASE[event]
        closest = SWINGBYS[event]["closest_approach_utc"]
        days = d9.days_around(closest[:10], D9_HALF_WIDTH_DAYS)
    else:
        if not (phase and first and last):
            raise ValueError("Give --event, or --phase with --from and --to")
        closest = None
        days = d9.days_between(first, last)
    checksums = d9.fetch_checksums(phase)
    products = d9.products_for(phase, days, checksums)
    directory = data_dir / "D9" / d9.dataset_for(phase)

    receipts, samples = [], []
    for product in products:
        label = d9.retrieve(phase, product.label_path, checksums,
                            directory / product.label_path)
        table = d9.retrieve(phase, product.table_path, checksums,
                            directory / product.table_path)
        parsed = parse_product(
            (directory / product.label_path).read_bytes().decode("ascii", "strict"),
            (directory / product.table_path).read_bytes())
        day_samples = d9.count_rates(parsed)
        samples.extend(day_samples)
        receipts.append({
            "day": product.day, "label": label, "table": table,
            "sample_count": len(day_samples),
            "declared_phase": parsed.label.keywords.get("MISSION_PHASE_NAME", ""),
            "declared_start": parsed.label.keywords.get("START_TIME", ""),
            "declared_stop": parsed.label.keywords.get("STOP_TIME", ""),
        })

    if closest is not None:
        start, end = target_interval(event)
        inside = [rates for moment, rates in samples if start <= moment <= end]
        hour = int(closest[11:13])
        outside = [rates for moment, rates in samples
                   if abs(int(moment[11:13]) - hour) >= 4]
    else:
        start, end = days[0], days[-1]
        inside, outside = [r for _, r in samples], [r for _, r in samples]
    channels = {}
    for name in d9.SREM_CHANNELS:
        if not inside or not outside:
            continue
        background = sorted(r[name] for r in outside)[len(outside) // 2]
        peak = max(r[name] for r in inside)
        channels[name] = {
            "median_background_per_s": background,
            "peak_in_interval_per_s": peak,
            "ratio": (peak / background) if background > 0 else None,
            "proton_mev": d9.SREM_CHANNELS[name].proton_mev,
            "electron_mev": d9.SREM_CHANNELS[name].electron_mev,
            "proton_only": name in d9.PROTON_ONLY,
        }
    return {
        "source_id": "D9", "dataset_id": d9.dataset_for(phase), "phase": phase,
        "event": event, "closest_approach_utc": closest,
        "altitude_km": SWINGBYS[event]["altitude_km"] if event else None,
        "interval_utc": [start, end], "parser_version": __version__,
        "status": "reference_instrument_count_rates",
        "interpretation": (
            "SREM count rates in 1/sec around a published closest approach, "
            "against a same-day background taken at least four hours away. "
            "Count rates are not flux: no unfolding is applied. The comparison "
            "with housekeeping counters this supports is temporal."
        ),
        "daily": daily_srem(samples),
        "l1_matched_channel": d9.L1_MATCHED_CHANNEL,
        "proton_only_channels": list(d9.PROTON_ONLY),
        "sample_count": len(samples), "samples_in_interval": len(inside),
        "background_sample_count": len(outside),
        "channels": channels, "products": receipts,
    }


E3_CONTRACT = {
    "exposure_channel": "TC2",
    "exposure_statistic": "max_per_s",
    "exposure_threshold_per_s": 100.0,
    "srem_phase": "cr2",
    "srem_first_day": "2005-04-05",
    "srem_last_day": "2006-06-30",
    "d2_channel": "aocs_and_dms_edac_cntr",
    "d2_quarters": ("2005_q2", "2005_q3", "2005_q4", "2006_q1", "2006_q2"),
    "counters": ("NACW0D0A", "NDMW0D0A"),
    "min_high_exposure_days": 5,
    "alpha": 0.01,
    "trials": 20000,
    "seed": 20260919,
}


def exposure_response(data_dir: Path, contract: dict = E3_CONTRACT) -> dict:
    phase = contract["srem_phase"]
    days = d9.days_between(contract["srem_first_day"], contract["srem_last_day"])
    checksums = d9.fetch_checksums(phase)
    absent = d9.missing_days(phase, days, checksums)
    products = d9.products_for(phase, days, checksums, allow_missing=True)
    directory = data_dir / "D9" / d9.dataset_for(phase)

    exposure: dict[str, float] = {}
    for product in products:
        d9.retrieve(phase, product.label_path, checksums, directory / product.label_path)
        d9.retrieve(phase, product.table_path, checksums, directory / product.table_path)
        parsed = parse_product(
            (directory / product.label_path).read_bytes().decode("ascii", "strict"),
            (directory / product.table_path).read_bytes())
        rates = d9.count_rates(parsed)
        if not rates:
            continue
        channel = contract["exposure_channel"]
        exposure[product.day] = max(r[channel] for _, r in rates)

    high_days = {d for d, peak in exposure.items()
                 if peak >= contract["exposure_threshold_per_s"]}

    counts: dict[str, int] = {}
    covered: dict[str, set[str]] = {}
    for quarter in contract["d2_quarters"]:
        for item in _d2_series(data_dir, quarter):
            if item.parameter not in contract["counters"]:
                continue
            for timestamp, _ in item.samples:
                covered.setdefault(timestamp[:10], set()).add(item.parameter)
            split = coincidence.transitions(item, max_gap_s=DAY_S)
            for step in split.increments:
                if coincidence.within_one_day(step):
                    day = step.observed_utc[:10]
                    counts[day] = counts.get(day, 0) + step.delta

    wanted = set(contract["counters"])
    usable = sorted(d for d in exposure
                    if covered.get(d, set()) >= wanted)
    treatment = [counts.get(d, 0) for d in usable if d in high_days]
    control = [counts.get(d, 0) for d in usable if d not in high_days]

    verdict, result = "not_evaluable", None
    if len(treatment) >= contract["min_high_exposure_days"] and control:
        result = stats.mann_whitney(treatment, control,
                                    trials=contract["trials"], seed=contract["seed"])
        if result.p_one_sided < contract["alpha"]:
            verdict = ("supported" if result.median_treatment > result.median_control
                       else "reversed")
        else:
            verdict = "not_supported"

    ranked = [(exposure[d], counts.get(d, 0)) for d in usable]
    return {
        "experiment_id": "E3", "contract": {k: list(v) if isinstance(v, tuple) else v
                                            for k, v in contract.items()},
        "parser_version": __version__, "status": "pre_registered_test",
        "interpretation": (
            "Exposure classified from SREM before any counter was read. Counter "
            "semantics remain unresolved: the response is a raw daily counter "
            "total, not an error, dose or flux."
        ),
        "time_system": "unresolved", "quantitative_channels": [],
        "requested_days": len(days), "days_absent_from_archive": absent,
        "srem_days": len(exposure), "usable_days": len(usable),
        "days_without_both_counters": len(exposure) - len(usable),
        "high_exposure_days": sorted(d for d in usable if d in high_days),
        "verdict": verdict,
        "primary": asdict(result) if result else None,
        "secondary_spearman_rho": (stats.spearman([e for e, _ in ranked],
                                                  [c for _, c in ranked])
                                   if len(ranked) > 1 else None),
        "counts_by_day": {d: counts.get(d, 0) for d in usable},
        "exposure_by_day": {d: exposure[d] for d in usable},
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SRMWSE research foundation (P0)")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    synthetic = commands.add_parser("demo", help="Run a deterministic synthetic counter contract demonstration")
    synthetic.add_argument("--output", type=Path, default=Path("results/demo.json"))
    metadata = commands.add_parser("inspect-source", help="Read and validate D1 v1 metadata (network)")
    metadata.add_argument("--output", type=Path, default=Path("results/d1-metadata.json"))
    for name, default_output, description in [
        ("fetch-d1", "d1-manifest", "Download and hash D1 v1, or verify its immutable cache"),
        ("audit-d1", "d1-audit", "Parse and inventory a downloaded D1 archive without physical inference"),
        ("channel-rates", "d1-channel-rates",
         "Summarise raw counter step scale per channel for literature cross-checks (exploration only)"),
        ("fetch-d2", "d2-manifest",
         "Retrieve and parse a named slice of the Rosetta EDAC archive (network)"),
        ("fetch-d5", "d5-manifest",
         "Retrieve the pinned Solar Orbiter EPD products and record provenance (network; no CDF decoding)"),
        ("fetch-d9", "d9-srem",
         "Retrieve Rosetta SREM count rates around a swing-by (network; reference instrument)"),
        ("exposure-response", "E3-exposure-response",
         "Run the pre-registered E3 test: does SREM-measured exposure predict daily counter totals (network)"),
        ("coincidence", "d2-coincidence",
         "Find windows where several independent units step together, against a re-timed null (exploration only)"),
    ]:
        command = commands.add_parser(name, help=description)
        command.add_argument("--data-dir", type=Path,
                             default=Path(os.environ.get("SRMWSE_DATA_DIR", "data/raw")),
                             help="Raw cache root (default: SRMWSE_DATA_DIR or data/raw)")
        command.add_argument("--output", type=Path, default=Path(f"results/{default_output}.json"))
        if name == "fetch-d2":
            command.add_argument("--channel", default=D2_DEFAULT_CHANNEL,
                                 choices=sorted(d2.D2_CHANNELS),
                                 help="Archive channel directory to retrieve")
            command.add_argument("--quarters", nargs="+", default=list(D2_DEFAULT_QUARTERS),
                                 metavar="YYYY_qN",
                                 help="Quarters to retrieve, e.g. 2006_q4 2007_q1")
        if name == "fetch-d9":
            command.add_argument("--event", choices=sorted(D9_EVENT_PHASE),
                                 help="Swing-by whose SREM coverage to retrieve")
            command.add_argument("--phase", choices=sorted(d9.D9_DATASETS),
                                 help="SREM mission-phase dataset, with --from and --to")
            command.add_argument("--from", dest="first", metavar="YYYY-MM-DD")
            command.add_argument("--to", dest="last", metavar="YYYY-MM-DD")
        if name == "coincidence":
            command.add_argument("--event", choices=sorted(SWINGBYS),
                                 help="Test the pre-registered interval around this "
                                      "swing-by's published closest approach")
            command.add_argument("--window", type=float, default=COINCIDENCE_WINDOW_S,
                                 metavar="SECONDS",
                                 help="Coincidence window; must not be narrower than the slowest cadence")
            command.add_argument("--min-devices", type=int, default=COINCIDENCE_MIN_DEVICES,
                                 help="Distinct physical units required for a reported window")
            command.add_argument("--trials", type=int, default=COINCIDENCE_TRIALS,
                                 help="Re-timed null trials")
            command.add_argument("--seed", type=int, default=COINCIDENCE_SEED)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.command == "demo":
            result = demo(args.output)
            print(f"Synthetic demonstration: {result['valid_increment_count']} valid increments; no scientific result.")
        elif args.command == "inspect-source":
            write_json(args.output, inspect_source())
            print("D1 v1 metadata identity, license and file inventory verified.")
        elif args.command == "fetch-d1":
            result = fetch_d1(args.data_dir)
            write_json(args.output, result)
            print(f"D1 verified: {result['byte_size']} bytes; SHA-256 {result['sha256']}")
        elif args.command == "fetch-d2":
            result = fetch_d2(args.data_dir, args.channel, tuple(args.quarters))
            write_json(args.output, result)
            print(f"D2 slice: {result['product_count']} products / "
                  f"{result['record_total']} records, verified against the publisher checksum "
                  "manifest. Quantitative channels admitted: 0.")
        elif args.command == "fetch-d5":
            result = fetch_d5(args.data_dir)
            write_json(args.output, result)
            coverage = result["declared_coverage"]
            print(f"D5 access check: {result['item_count']} CDF products, "
                  f"{coverage['first_begin_time']} to {coverage['last_end_time']}. "
                  "Container verified; no science data decoded.")
        elif args.command == "fetch-d9":
            if args.event is None and args.phase is None:
                args.event = "earth_1"
            result = fetch_d9(args.data_dir, event=args.event, phase=args.phase,
                              first=args.first, last=args.last)
            write_json(args.output, result)
            matched = result["channels"].get(result["l1_matched_channel"], {})
            ratio = matched.get("ratio")
            print(f"D9 {result['event'] or result['phase']}: "
                  f"{result['sample_count']} SREM samples over "
                  f"{len(result['daily'])} days, "
                  f"{result['samples_in_interval']} in the interval. "
                  f"{result['l1_matched_channel']} (protons >49 MeV) peaks at "
                  f"{matched.get('peak_in_interval_per_s', float('nan')):.4g}/s vs "
                  f"{matched.get('median_background_per_s', float('nan')):.4g}/s background"
                  + (f", ratio {ratio:.4g}." if ratio else ".")
                  + " Count rates only; no flux derived.")
        elif args.command == "exposure-response":
            result = exposure_response(args.data_dir)
            write_json(args.output, result)
            primary = result["primary"]
            print(f"E3: {result['usable_days']} usable days, "
                  f"{len(result['high_exposure_days'])} above the frozen "
                  f"{E3_CONTRACT['exposure_threshold_per_s']:g}/s threshold. "
                  f"Verdict: {result['verdict'].upper()}.")
            if primary:
                print(f"   Mann-Whitney U={primary['u']:.1f}, "
                      f"p(one-sided) = {primary['p_one_sided']:.5f}, "
                      f"rank-biserial = {primary['rank_biserial']:+.3f}; "
                      f"medians {primary['median_treatment']:g} vs "
                      f"{primary['median_control']:g}.")
            rho = result["secondary_spearman_rho"]
            if rho is not None:
                print(f"   Secondary: Spearman rho = {rho:+.3f} "
                      "(training set gave +0.510).")
            print("Exploration of a frozen hypothesis; quantitative channels admitted: 0.")
        elif args.command == "coincidence":
            result = coincidence_scan(args.data_dir, args.window, args.min_devices,
                                      args.trials, args.seed, args.event)
            write_json(args.output, result)
            null = result["null"]
            print(f"{result['coincidence_count']} window(s) with >= {args.min_devices} "
                  f"of {result['device_count']} units stepping within {args.window:g} s. "
                  f"Re-timed null: {null['mean_coincidences_per_trial']:.4f} per trial, "
                  f"p(>=1) = {null['p_at_least_one']:.4f}.")
            if result["prediction"]:
                pred = result["prediction"]
                restricted = pred["null_restricted"]
                verdict = "HIT" if pred["predicted_hit"] else "no hit"
                bound = (f"p(>=1) = {restricted['p_at_least_one']:.4f}"
                         if restricted["p_at_least_one"]
                         else f"p(>=1) < {restricted['p_upper_95']:.4f} (95% bound)")
                print(f"Pre-registered interval for {pred['event']} "
                      f"({pred['closest_approach_utc']} +/- {pred['half_width_s']:g} s): "
                      f"{verdict}, {pred['hits_inside_interval']} coincidence(s) inside. "
                      f"Re-timed null restricted to that interval: {bound}.")
            print("Exploration only; quantitative channels admitted: 0.")
        elif args.command == "channel-rates":
            result = channel_rates(args.data_dir)
            write_json(args.output, result)
            print(f"Summarised raw counter steps for {len(result['channels'])} channel series. "
                  "Exploration only; not an error, event or particle rate.")
        else:
            result = audit_d1(args.data_dir)
            write_json(args.output, result)
            print(f"Parsed {result['parsed_file_count']}/{result['source_file_count']} source files / {result['source_sample_count']} records. Quantitative channels admitted: 0.")
        print(f"Output: {args.output}")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"srmwse: {error}", file=sys.stderr)
        return 1
    return 0
