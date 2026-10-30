#!/usr/bin/env python3
"""Step 6: how well does our travel-time index agree with TfNSW's congestion labels?

Replays every recorded weekday peak (at least MIN_COVERAGE complete) through
the engine and compares, for buses:

1. **Per reading** -- each 300m+ stretch: our level (from the travel-time index)
   vs TfNSW's ``congestion_level`` reported by the same bus at the end of it.
2. **Per segment and 10 minutes** -- the mean of each, per 200m segment and
   10-minute slot, rounded to a level: what the street layers actually show.

For both: a 4x4 agreement table, exact agreement, agreement within one level,
and quadratically weighted kappa (agreement beyond chance, counting near-misses
as part-agreement). Also: median TTI per TfNSW level, Spearman correlation,
how much of the data had a free-flow baseline, and -- as an independent
reference -- the correlation per segment between our mean TTI and BOAM's
"time lost" for the matching stop-to-stop section and peak.

Writes data/analysis/congestion_agreement.json (everything),
congestion_disagreements.geojson (segments where the two differ by 2+ levels
on average, for a map) and prints a summary.

    python scripts/analyse_congestion.py
"""
from __future__ import annotations

import json
import logging
import math
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.analysis.collect import replay_window
from engine.config import load_config
from engine.demand.boam import build_boam_layers
from engine.normalise.levels import CONGESTION_LABELS, congestion_level
from engine.publish.feed import LiveVehicleFeed
from engine.sources.replay import SYDNEY, RecordingIndex

MIN_COVERAGE = 0.5
SLOT_S = 600
BOAM_MATCH_M = 40.0
LEVELS = 4


# --- statistics ------------------------------------------------------------------

def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return round(cov / (vx * vy), 3) if vx and vy else None


def agreement(pairs: list[tuple[int, int]]) -> dict:
    """pairs of (ours, tfnsw) levels 0-3 -> table, exact, within-one, weighted kappa."""
    table = [[0] * LEVELS for _ in range(LEVELS)]
    for ours, theirs in pairs:
        table[ours][theirs] += 1
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    exact = sum(table[i][i] for i in range(LEVELS)) / n
    within = sum(table[i][j] for i in range(LEVELS) for j in range(LEVELS) if abs(i - j) <= 1) / n
    row = [sum(table[i]) / n for i in range(LEVELS)]
    col = [sum(table[i][j] for i in range(LEVELS)) / n for j in range(LEVELS)]
    w = lambda i, j: ((i - j) ** 2) / ((LEVELS - 1) ** 2)
    observed = sum(w(i, j) * table[i][j] / n for i in range(LEVELS) for j in range(LEVELS))
    expected = sum(w(i, j) * row[i] * col[j] for i in range(LEVELS) for j in range(LEVELS))
    kappa = 1 - observed / expected if expected else None
    return {
        "n": n,
        "table_rows_ours_cols_tfnsw": table,
        "exact": round(exact, 3),
        "within_one_level": round(within, 3),
        "weighted_kappa": round(kappa, 3) if kappa is not None else None,
    }


def _metres(lat1, lon1, lat2, lon2) -> float:
    dy = (lat2 - lat1) * 111320.0
    dx = (lon2 - lon1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


# --- main ----------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    cfg = load_config()
    feed = LiveVehicleFeed(cfg)
    # Compare levels using thresholds fitted to TfNSW's level shares, not the
    # display thresholds (config derived_congestion.analysis_thresholds_tti).
    analysis_th = cfg.raw.get("derived_congestion", {}).get("analysis_thresholds_tti")
    if analysis_th:
        feed.travel_time.thresholds = tuple(analysis_th)
    if feed.travel_time.freeflow is None:
        raise SystemExit("no free-flow baseline -- run scripts/build_freeflow.py first")
    index = RecordingIndex(feed.recordings_dir)
    sessions = [s for s in index.sessions()
                if s.id != "latest" and s.coverage >= MIN_COVERAGE
                and date.fromisoformat(s.id[:10]).weekday() < 5]
    if not sessions:
        raise SystemExit("no weekday peak recordings yet")

    readings = []          # (session, period, segment, slot, tti, ours, tfnsw|None)
    total_intervals = 0
    for s in sessions:
        period = s.id[-2:]

        def collect(interval, vehicle, s=s, period=period):
            nonlocal total_intervals
            total_intervals += 1
            if interval.tti is None:
                return
            theirs = congestion_level(vehicle.congestion_level)
            readings.append((s.id, period, interval.segment, int(interval.at // SLOT_S), interval.tti, interval.level, theirs))

        before = len(readings)
        replay_window(feed, index, s.start, s.end, collect)
        print(f"{s.label}: {len(readings) - before} readings with a baseline")

    with_tfnsw = [r for r in readings if r[6] is not None]

    # 1. per reading
    per_reading = agreement([(r[5], r[6]) for r in with_tfnsw])
    tti_by_level = defaultdict(list)
    for r in with_tfnsw:
        tti_by_level[r[6]].append(r[4])
    median = lambda v: sorted(v)[len(v) // 2]

    # 2. per segment and 10-minute slot
    slots = defaultdict(lambda: {"ours": [], "theirs": []})
    for r in with_tfnsw:
        key = (r[0], r[2], r[3])
        slots[key]["ours"].append(r[5])
        slots[key]["theirs"].append(r[6])
    per_slot = agreement([(round(sum(v["ours"]) / len(v["ours"])), round(sum(v["theirs"]) / len(v["theirs"])))
                          for v in slots.values()])

    # disagreement map: segments whose mean levels differ by 2+
    seg_levels = defaultdict(lambda: {"ours": [], "theirs": []})
    for r in with_tfnsw:
        seg_levels[r[2]]["ours"].append(r[5])
        seg_levels[r[2]]["theirs"].append(r[6])
    features = []
    for (shape_id, idx), v in seg_levels.items():
        if len(v["ours"]) < 5:
            continue
        ours, theirs = sum(v["ours"]) / len(v["ours"]), sum(v["theirs"]) / len(v["theirs"])
        if abs(ours - theirs) >= 2:
            pts = feed.shapes.section_latlon(shape_id, idx * 200.0, idx * 200.0 + 200.0)
            features.append({"type": "Feature",
                             "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in pts]},
                             "properties": {"segment": f"{shape_id}:{idx}", "ours": round(ours, 2),
                                            "tfnsw": round(theirs, 2), "readings": len(v["ours"])}})

    # 2b. per street over a whole peak: mean TTI vs mean TfNSW level (Spearman),
    # streets with 10+ readings -- where the two measures do relate (30 Oct experiment)
    street_peak = defaultdict(lambda: ([], []))
    for r in with_tfnsw:
        street_peak[(r[0], r[2])][0].append(r[4])
        street_peak[(r[0], r[2])][1].append(r[6])
    big = [(sum(a) / len(a), sum(b) / len(b)) for a, b in street_peak.values() if len(a) >= 10]
    street_rho = spearman([a for a, _ in big], [b for _, b in big])

    # 3. against BOAM time lost, per segment and peak
    boam = build_boam_layers(cfg, feed.shapes)
    boam_mid = []
    for sec in boam.get("sections", []):
        lat, lon = sec["pts"][len(sec["pts"]) // 2]
        boam_mid.append((lat, lon, sec))
    seg_tti = defaultdict(list)
    for r in readings:
        seg_tti[(r[2], r[1])].append(r[4])
    xs, ys = [], []
    for ((shape_id, idx), period), values in seg_tti.items():
        if len(values) < 5:
            continue
        pts = feed.shapes.section_latlon(shape_id, idx * 200.0 + 90, idx * 200.0 + 110)
        if not pts:
            continue
        lat, lon = pts[0]
        best = min(boam_mid, key=lambda b: _metres(lat, lon, b[0], b[1]), default=None)
        if best and _metres(lat, lon, best[0], best[1]) <= BOAM_MATCH_M:
            lost = (best[2].get(period) or {}).get("lost_s_per_100m")
            if lost is not None:
                xs.append(median(values))
                ys.append(lost)

    result = {
        "built": datetime.now(SYDNEY).isoformat(timespec="minutes"),
        "sessions": [s.id for s in sessions],
        "thresholds_tti": list(feed.travel_time.thresholds),
        "levels": CONGESTION_LABELS,
        "intervals_total": total_intervals,
        "intervals_with_baseline": len(readings),
        "baseline_coverage": round(len(readings) / total_intervals, 3) if total_intervals else 0,
        "readings_with_tfnsw_level": len(with_tfnsw),
        "per_reading": per_reading,
        "per_segment_10min": per_slot,
        "median_tti_by_tfnsw_level": {CONGESTION_LABELS[k]: round(median(v), 2) for k, v in sorted(tti_by_level.items())},
        "spearman_tti_vs_tfnsw_level": spearman([r[4] for r in with_tfnsw], [r[6] for r in with_tfnsw]),
        "street_whole_peak": {"streets": len(big), "spearman_mean_tti_vs_mean_tfnsw_level": street_rho},
        "boam": {"segments_matched": len(xs), "spearman_tti_vs_time_lost": spearman(xs, ys),
                 "note": "BOAM days are 28-30 Sep 2026 (school holidays); its actual times are GTFS-R predictions."},
        "disagreement_segments": len(features),
    }
    out_dir = cfg.root / "data" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "congestion_agreement.json").write_text(json.dumps(result, indent=2))
    (out_dir / "congestion_disagreements.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))

    print(f"\nPeaks: {len(sessions)}  stretches: {total_intervals:,}  with a free-flow baseline: {result['baseline_coverage']:.0%}")
    print("Median travel-time index by TfNSW level:", result["median_tti_by_tfnsw_level"])
    print("Spearman (TTI vs TfNSW level):", result["spearman_tti_vs_tfnsw_level"])
    print(f"Per street, whole peak: {len(big)} streets, Spearman {street_rho}")
    for name, a in (("Per reading", per_reading), ("Per segment, 10 min", per_slot)):
        if not a.get("n"):
            continue
        print(f"\n{name}: n={a['n']:,}  exact {a['exact']:.0%}  within one level {a['within_one_level']:.0%}  weighted kappa {a['weighted_kappa']}")
        print("            TfNSW: " + "  ".join(f"{l[:12]:>12}" for l in CONGESTION_LABELS))
        for i, row in enumerate(a["table_rows_ours_cols_tfnsw"]):
            print(f"  ours {CONGESTION_LABELS[i][:12]:>12}: " + "  ".join(f"{c:>12,}" for c in row))
    print(f"\nBOAM: {len(xs)} segments matched; Spearman TTI vs time lost = {result['boam']['spearman_tti_vs_time_lost']}")
    print(f"Disagreement map: {len(features)} segments differ by 2+ levels -> data/analysis/congestion_disagreements.geojson")
    return 0


if __name__ == "__main__":
    sys.exit(main())
