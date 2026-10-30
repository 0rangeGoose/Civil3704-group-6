#!/usr/bin/env python3
"""Builds each street's free-flow bus speed from our night-time recordings.

Replays every recorded night (22:00-05:00 Sydney time) through the engine and
takes, per 200m route segment, the 85th-percentile speed of buses over
300m+ stretches (stops included) -- the baseline the travel-time index
divides by (engine/normalise/travel_time.py). Segments with fewer than
MIN_SEGMENT readings fall back to their whole route shape's 85th percentile.

Rerun as more nights are recorded; the server reads the result at startup.

    python scripts/build_freeflow.py
"""
from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.analysis.collect import replay_window
from engine.config import load_config
from engine.publish.feed import LiveVehicleFeed
from engine.sources.replay import SYDNEY, RecordingIndex

NIGHT_START, NIGHT_END = time(22, 0), time(5, 0)
PERCENTILE = 0.85
MIN_SEGMENT, MIN_SHAPE = 3, 5


def p85(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[int(PERCENTILE * (len(ordered) - 1))]


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    cfg = load_config()
    feed = LiveVehicleFeed(cfg)
    feed.travel_time.freeflow = None  # speeds only while building the baseline
    index = RecordingIndex(feed.recordings_dir)
    times = index._bus_update_times()
    if not times:
        raise SystemExit("no recordings")
    first = times[0].astimezone(SYDNEY).date() - timedelta(days=1)
    last = times[-1].astimezone(SYDNEY).date()

    by_segment: dict[str, list[float]] = defaultdict(list)
    by_shape: dict[str, list[float]] = defaultdict(list)
    nights = []
    day = first
    while day <= last:
        start = datetime.combine(day, NIGHT_START, SYDNEY).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), NIGHT_END, SYDNEY).astimezone(timezone.utc)
        if any(start <= t < end for t in times):
            before = sum(len(v) for v in by_shape.values())

            def collect(interval, _vehicle):
                shape, index_ = interval.segment
                by_segment[f"{shape}:{index_}"].append(interval.speed_mps)
                by_shape[shape].append(interval.speed_mps)

            replay_window(feed, index, start, end, collect)
            n = sum(len(v) for v in by_shape.values()) - before
            nights.append(day.isoformat())
            print(f"night of {day}: {n} readings")
        day += timedelta(days=1)

    segments = {k: [round(p85(v), 2), len(v)] for k, v in by_segment.items() if len(v) >= MIN_SEGMENT}
    shapes = {k: [round(p85(v), 2), len(v)] for k, v in by_shape.items() if len(v) >= MIN_SHAPE}
    out = cfg.root / cfg.raw.get("derived_congestion", {}).get("freeflow_path", "data/analysis/freeflow.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "segments": segments,
        "shapes": shapes,
        "meta": {"nights": nights, "percentile": PERCENTILE, "built": datetime.now(SYDNEY).isoformat(timespec="minutes"),
                 "window": f"{NIGHT_START:%H:%M}-{NIGHT_END:%H:%M}"},
    }))
    print(f"wrote {out}: {len(segments)} segments, {len(shapes)} route shapes, from {len(nights)} nights")
    return 0


if __name__ == "__main__":
    sys.exit(main())
