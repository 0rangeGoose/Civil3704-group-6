#!/usr/bin/env python3
"""Exports one recorded peak as three CSV files (engine/analysis/export.py):
vehicles.csv, streets.csv and stop_visits.csv, in data/exports/<session>/.

Replays the peak through the engine as fast as it goes (a two-hour peak takes
a minute or two). The tablet runs this too (Display → Time), through /export.

    python scripts/export_peak.py 2026-11-04-am
    python scripts/export_peak.py 2026-11-04-pm --out ~/Desktop/pm-peak
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.analysis.export import export_peak
from engine.config import load_config
from engine.publish.feed import LiveVehicleFeed
from engine.sources.replay import RecordingIndex


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", help="a recorded peak, e.g. 2026-11-04-am (see /recordings)")
    parser.add_argument("--out", type=Path, help="folder to write to (default data/exports/<session>)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    cfg = load_config()
    feed = LiveVehicleFeed(cfg)
    index = RecordingIndex(feed.recordings_dir)
    out = args.out or cfg.root / "data" / "exports" / args.session
    t0 = time.time()
    try:
        paths = export_peak(feed, index, args.session, out)
    except ValueError as exc:
        raise SystemExit(str(exc))
    for kind, path in paths.items():
        rows = sum(1 for _ in path.open(encoding="utf-8")) - 1
        print(f"{kind}: {rows:,} rows -> {path}")
    print(f"done in {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
