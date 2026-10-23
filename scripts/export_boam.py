#!/usr/bin/env python3
"""Exports the BOAM section averages as CSV, for report tables and charts --
and as the measured-delay reference step 6 compares against.

Writes data/boam/sections.csv: one row per stop-to-stop section and peak, with
typical load (share of capacity), seconds lost per 100m, trip count, length
and both stop ids. Built from the same cache the server uses
(engine/demand/boam.py), so the numbers match what's on the model.

    python scripts/export_boam.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import load_config
from engine.demand.boam import build_boam_layers
from engine.normalise.map_match import ShapeIndex


def main() -> int:
    cfg = load_config()
    layers = build_boam_layers(cfg, ShapeIndex(cfg.path("db_path")))
    out = cfg.root / "data" / "boam" / "sections.csv"
    rows = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["from_stop", "to_stop", "period", "trips", "length_m", "load_share",
                    "lost_s_per_100m", "mid_lat", "mid_lon"])
        for s in layers["sections"]:
            a, b = s["id"].split(">")
            mid = s["pts"][len(s["pts"]) // 2]
            for period in ("am", "pm"):
                if period in s:
                    p = s[period]
                    w.writerow([a, b, period, p["trips"], s["length_m"], p["load"], p["lost_s_per_100m"], *mid])
                    rows += 1
    print(f"wrote {out} ({rows} rows; weekdays {', '.join(layers['meta']['days'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
