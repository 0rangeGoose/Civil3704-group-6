#!/usr/bin/env python3
"""Step 6 experiment, part 1 of 2: replay peaks, nights and middays once, measuring
bus speeds over 300 / 600 / 1000m stretches -> data/analysis/experiment_raw.json.
Part 2 (scripts/experiment_congestion_evaluate.py) scores each setup. Run from the repo root."""
import json, logging, sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
sys.path.insert(0, ".")
logging.basicConfig(level=logging.WARNING)
from engine.analysis.collect import replay_window
from engine.config import load_config
from engine.normalise.levels import congestion_level
from engine.normalise.travel_time import TravelTimeTracker
from engine.publish.feed import LiveVehicleFeed
from engine.sources.replay import SYDNEY, RecordingIndex

LENGTHS = [300, 600, 1000]
cfg = load_config(); feed = LiveVehicleFeed(cfg); idx = RecordingIndex(feed.recordings_dir)
feed.travel_time.freeflow = None
extra = {L: TravelTimeTracker(None, min_interval_m=L) for L in LENGTHS}
rows = []  # [kind, window, L, shape, segidx, at, speed, tfnsw]

def run(kind, wid, start, end):
    for t in extra.values(): t.reset()
    n0 = len(rows)
    def on(interval, v):
        pass
    orig = feed.travel_time.observe
    def spy(v):
        lvl = congestion_level(v.congestion_level)
        for L, tr in extra.items():
            iv = tr.observe(v)
            if iv is not None:
                rows.append([kind, wid, L, iv.shape_id, iv.segment[1], int(iv.at), iv.speed_mps, lvl])
        return orig(v)
    feed.travel_time.observe = spy
    try:
        replay_window(feed, idx, start, end, on)
    finally:
        feed.travel_time.observe = orig
    print(f"{kind} {wid}: {len(rows) - n0} intervals", flush=True)

utc = lambda d, t: datetime.combine(d, t, SYDNEY).astimezone(timezone.utc)
for s in idx.sessions():
    if s.id != "latest" and s.coverage >= 0.5 and date.fromisoformat(s.id[:10]).weekday() < 5:
        run("peak", s.id, s.start, s.end)
for d in [date(2026, 10, 23) + timedelta(days=i) for i in range(8)]:
    run("night", d.isoformat(), utc(d, time(22)), utc(d + timedelta(days=1), time(5)))
    if d.weekday() < 5:
        run("midday", d.isoformat(), utc(d, time(10)), utc(d, time(15)))
Path("data/analysis/experiment_raw.json").write_text(json.dumps(rows))
print("rows", len(rows))
