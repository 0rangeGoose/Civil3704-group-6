#!/usr/bin/env python3
"""Phase 3: map matching and adherence.

Same polling loop as Phase 2, plus: snap each vehicle to its route shape,
compute schedule deviation from Trip Updates (stop-anchored, per the brief),
classify adherence state, and maintain the rolling aggregate layer
(adherence mix by mode over 5/15/60 min, interchange activity, active
service alerts intersecting the CBD). Still headless -- no rendering.

Requires TFNSW_API_KEY in .env and the Phase 1 SQLite cache.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import load_config
from engine.normalise.aggregate import (
    RollingAdherence,
    count_active_alerts,
    count_services_near_interchanges,
    load_cbd_ids,
    load_interchanges,
)
from engine.normalise.build import build_vehicle_records
from engine.normalise.enrich import enrich_vehicle_record
from engine.normalise.map_match import ShapeIndex
from engine.normalise.static_lookup import StaticLookup
from engine.normalise.trip_updates import build_trip_update_index
from engine.sources.live import LiveSource
from engine.sources.recorder import RecordingSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pulse_cbd.run_phase3")


def _print_aggregate(roll: RollingAdherence, interchanges, positions, alerts_summary) -> None:
    print("\n--- aggregate ---", file=sys.stderr)
    for window_s, by_mode in roll.breakdown_all_windows().items():
        label = {300: "5min", 900: "15min", 3600: "60min"}.get(window_s, f"{window_s}s")
        print(f"  adherence ({label}):", file=sys.stderr)
        for mode, states in sorted(by_mode.items()):
            print(f"    {mode:28s} {states}", file=sys.stderr)

    interchange_counts = count_services_near_interchanges(interchanges, positions)
    print(f"  interchange activity: {interchange_counts}", file=sys.stderr)
    print(f"  active alerts in CBD: {alerts_summary}", file=sys.stderr)


async def run(max_cycles: int | None, out_path: Path | None, aggregate_every: int) -> None:
    cfg = load_config()
    db_path = cfg.path("db_path")
    if not db_path.exists():
        print(f"error: {db_path} not found -- run scripts/run_phase1.py first", file=sys.stderr)
        raise SystemExit(1)

    logger.info("Building shape index from %s ...", db_path)
    t0 = time.monotonic()
    shapes = ShapeIndex(db_path)
    logger.info("Shape index built in %.1fs", time.monotonic() - t0)

    cbd_route_ids, cbd_stop_ids = load_cbd_ids(db_path)
    interchanges = load_interchanges(db_path, cfg.raw["interchanges"])
    thresholds = cfg.raw["adherence_thresholds_seconds"]
    very_late_multiplier = thresholds["very_late_multiplier"]

    recordings_dir = cfg.root / "data" / "recordings"
    source = RecordingSource(LiveSource(cfg), recordings_dir)

    out_file = open(out_path, "a", encoding="utf-8") if out_path else None
    bbox = cfg.bounding_box
    roll = RollingAdherence()
    trip_update_index_by_mode: dict[str, dict] = {}
    latest_positions: dict[str, tuple[float, float]] = {}  # vehicle_key -> (lat, lon)
    latest_alerts_summary: dict = {"count": 0, "by_severity": {}, "route_ids": []}

    cycles = 0

    with StaticLookup(db_path) as lookup:
        try:
            async for raw in source.stream():
                if raw.feed_type == "trip_updates":
                    trip_update_index_by_mode[raw.mode] = build_trip_update_index(raw.payload)
                    continue

                if raw.feed_type == "service_alerts":
                    latest_alerts_summary = count_active_alerts(raw.payload, cbd_route_ids, cbd_stop_ids)
                    continue

                if raw.feed_type != "vehicle_positions":
                    continue

                records = build_vehicle_records(raw, bbox, lookup)
                trip_updates = trip_update_index_by_mode.get(raw.mode, {})

                enriched = [
                    enrich_vehicle_record(r, trip_updates, shapes, lookup, thresholds, very_late_multiplier)
                    for r in records
                ]

                for r in enriched:
                    latest_positions[r.vehicle_key] = (
                        (r.matched_lat, r.matched_lon) if r.matched_lat is not None else (r.lat, r.lon)
                    )
                    line = json.dumps(r.to_json())
                    if out_file:
                        out_file.write(line + "\n")
                        out_file.flush()
                    else:
                        print(line)

                adherence_counts: dict[str, int] = {}
                for r in enriched:
                    roll.record(r.mode, r.adherence_state)
                    adherence_counts[r.adherence_state] = adherence_counts.get(r.adherence_state, 0) + 1
                logger.info(
                    "vehicle_positions:%s: %d vehicles in box, adherence %s",
                    raw.mode, len(enriched), adherence_counts or "{}",
                )

                cycles += 1
                if aggregate_every and cycles % aggregate_every == 0:
                    _print_aggregate(roll, interchanges, list(latest_positions.values()), latest_alerts_summary)

                if max_cycles is not None and cycles >= max_cycles:
                    break
        finally:
            if out_file:
                out_file.close()

    _print_aggregate(roll, interchanges, list(latest_positions.values()), latest_alerts_summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cycles", type=int, default=None,
        help="Stop after this many vehicle_positions poll cycles (each mode counts "
        "separately). Omit to run forever.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Append enriched vehicle records here as JSON lines, instead of stdout.",
    )
    parser.add_argument(
        "--aggregate-every", type=int, default=7,
        help="Print the rolling aggregate every N vehicle_positions polls (default 7, "
        "one full pass across all modes).",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args.cycles, args.out, args.aggregate_every))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
