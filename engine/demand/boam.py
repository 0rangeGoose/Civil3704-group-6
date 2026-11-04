"""Typical bus load and time lost per stop-to-stop section, from BOAM.

BOAM (Bus Opal Assignment Model, TfNSW) gives, for every bus trip at every
stop: scheduled and actual arrival time, the bus's capacity, and an
occupancy band (0-20, 21-40, ... passengers). scripts/import_boam.py keeps the
footprint rows of each day file in data/boam/; this module averages them into
*sections* -- consecutive stops a -> b on a trip -- for two periods:

- **load**: occupancy band midpoint at a / the bus's total capacity, i.e. how
  full the bus is leaving a. (Assumes BOAM's occupancy at a stop is the load
  on departure; the documentation doesn't say.)
- **time lost**: delay at b minus delay at a (delay = actual - scheduled), per
  100m of route. Positive = the bus fell further behind on that section.
  Since March 2026 BOAM's "actual" arrival times are TfNSW's GTFS-Realtime
  predictions, so this is not fully independent of the live feed -- keep that
  in mind when comparing it with TfNSW's congestion labels (step 6).

Only weekdays are averaged ("typical weekday"); weekend files are imported but
left out. Each value also gets a 0-4 colour bin with fixed, absolute bands
(LOAD_BANDS, LOST_BANDS), so days added later stay comparable.

Section geometry comes from the static timetable: the shape of a trip on
which a and b are consecutive, between their shape distances. BOAM trip ids
don't match the timetable's, but its stop ids do (checked: 100%).
"""
from __future__ import annotations

import csv
import glob
import gzip
import hashlib
import json
import logging
import math
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from engine.config import Config
from engine.normalise.map_match import ShapeIndex
from engine.normalise.segments import _KX, _M_PER_DEG, _POOL_COS, heads_to_centre

logger = logging.getLogger("pulse_cbd.boam")

PERIODS = {"am": (7 * 3600, 9 * 3600), "pm": (16 * 3600 + 1800, 18 * 3600 + 1800)}
# Share of capacity. Averaged over a 2-hour peak, section loads rarely pass
# 50% (and the first days imported are school holidays), so 20%-wide bands
# left nearly every street in the bottom colour; these are narrower but still
# absolute, and the legend states them.
LOAD_BANDS = (0.15, 0.30, 0.45, 0.60)
LOST_BANDS = (0.0, 3.0, 8.0, 15.0)         # seconds lost per 100m; <= 0 means held or gained time
MAX_ABS_GAIN_S = 900                        # bigger jumps are data errors, not traffic
MIN_TRIPS = 3                               # sections with fewer trips in a period aren't shown
CACHE_VERSION = 3  # 3: both-directions values and the inbound flag
LONG_SECTION_M = 800  # time lost over a longer section can't say where; left out of that layer (and its pooling)
POOL_ACROSS_M = 25.0  # a section's midpoint this close to another's line is on the same street


def _secs(t: str) -> int | None:
    try:
        h, m, s = (int(x) for x in t.split(":"))
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


def occupancy_midpoint(band: str) -> float | None:
    try:
        lo, hi = (int(x) for x in band.split("-"))
    except ValueError:
        return None
    return (lo + hi) / 2


def band(value: float, bands: tuple[float, ...]) -> int:
    return sum(value > b for b in bands)


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    return 2 * r * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


def _is_weekday(path: str) -> bool:
    stamp = Path(path).name[5:13]
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:])).weekday() < 5


def section_samples(paths: list[str]) -> tuple[dict, dict[str, tuple[float, float]], list[str]]:
    """{(a, b, period): {"load": [...], "lost_s": [...]}}, stop coordinates, days used."""
    samples: dict = defaultdict(lambda: {"load": [], "lost_s": []})
    stops: dict[str, tuple[float, float]] = {}
    days = []
    for path in paths:
        if not _is_weekday(path):
            continue
        days.append(Path(path).name[5:13])
        trips: dict = defaultdict(list)
        with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                trips[(r["TRIP_ID"], r["ROUTE_VARIANT"])].append(r)
                stops.setdefault(r["TRANSIT_STOP"], (float(r["LATITUDE"]), float(r["LONGITUDE"])))
        for rows in trips.values():
            rows.sort(key=lambda r: int(r["TRANSIT_STOP_SEQUENCE"]))
            for a, b in zip(rows, rows[1:]):
                if int(b["TRANSIT_STOP_SEQUENCE"]) != int(a["TRANSIT_STOP_SEQUENCE"]) + 1:
                    continue
                sched_a = _secs(a["SCHD_ARRIVE_TIME"])
                period = next((p for p, (lo, hi) in PERIODS.items() if sched_a is not None and lo <= sched_a < hi), None)
                if period is None:
                    continue
                s = samples[(a["TRANSIT_STOP"], b["TRANSIT_STOP"], period)]
                capacity = float(a["TOTAL_CAPACITY"] or 0)
                onboard = occupancy_midpoint(a["OCCUPANCY_RANGE"])
                if capacity > 0 and onboard is not None:
                    s["load"].append(onboard / capacity)
                times = [_secs(x) for x in (a["ACTUAL_ARRIVE_TIME"], a["SCHD_ARRIVE_TIME"],
                                            b["ACTUAL_ARRIVE_TIME"], b["SCHD_ARRIVE_TIME"])]
                if None not in times:
                    gain = (times[2] - times[3]) - (times[0] - times[1])
                    if abs(gain) <= MAX_ABS_GAIN_S:
                        s["lost_s"].append(gain)
    return samples, stops, days


def _pair_geometry(conn: sqlite3.Connection, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], tuple[str, float, float]]:
    """(a, b) -> (shape_id, dist_a, dist_b) from a static trip where b follows a."""
    trip_shape = dict(conn.execute("SELECT trip_id, shape_id FROM trips"))
    found: dict[tuple[str, str], tuple[str, float, float]] = {}
    prev = None
    for trip_id, stop_id, dist in conn.execute(
        "SELECT trip_id, stop_id, shape_dist_traveled FROM stop_times ORDER BY trip_id, stop_sequence"
    ):
        if prev and prev[0] == trip_id:
            pair = (prev[1], stop_id)
            if pair in pairs and pair not in found and prev[2] is not None and dist is not None and dist > prev[2]:
                shape_id = trip_shape.get(trip_id)
                if shape_id:
                    found[pair] = (shape_id, float(prev[2]), float(dist))
        prev = (trip_id, stop_id, dist)
    return found


def _xy(pts: list[list[float]]) -> list[tuple[float, float]]:
    return [(lon * _KX, lat * _M_PER_DEG) for lat, lon in pts]


def _midpoint_frame(xy: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Half-way point along a line and the line's direction there."""
    steps = [math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(xy, xy[1:])]
    remaining = sum(steps) / 2
    for (x1, y1), (x2, y2), step in zip(xy, xy[1:], steps):
        if step >= remaining and step > 0:
            t = remaining / step
            return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, (x2 - x1) / step, (y2 - y1) / step
        remaining -= step
    return None


def add_both_directions(sections: list[dict]) -> None:
    """For the renderer's "one colour per street": each section's load and
    time lost pooled (trip-weighted) with every section whose midpoint lies on
    its line, heading either way -- the other direction, and shorter or longer
    stop pairs of other routes along the same street. Also whether it heads
    towards the city centre. Sections over LONG_SECTION_M stay out of the
    time-lost pool, as they're out of that layer."""
    lines = [_xy(sec["pts"]) for sec in sections]
    frames = [_midpoint_frame(xy) for xy in lines]
    cell = 100.0
    grid: dict[tuple[int, int], list[int]] = {}
    for i, f in enumerate(frames):
        if f:
            grid.setdefault((int(f[0] // cell), int(f[1] // cell)), []).append(i)
    for sec, xy, frame in zip(sections, lines, frames):
        sec["inbound"] = heads_to_centre(frame) if frame else None
        members = set()
        for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
            length = math.hypot(x2 - x1, y2 - y1)
            if length == 0:
                continue
            ux, uy = (x2 - x1) / length, (y2 - y1) / length
            for gx in range(int((min(x1, x2) - POOL_ACROSS_M) // cell), int((max(x1, x2) + POOL_ACROSS_M) // cell) + 1):
                for gy in range(int((min(y1, y2) - POOL_ACROSS_M) // cell), int((max(y1, y2) + POOL_ACROSS_M) // cell) + 1):
                    for j in grid.get((gx, gy), ()):
                        bx, by, bux, buy = frames[j]
                        along = (bx - x1) * ux + (by - y1) * uy
                        across = abs((bx - x1) * uy - (by - y1) * ux)
                        if 0 <= along <= length and across <= POOL_ACROSS_M and abs(bux * ux + buy * uy) >= _POOL_COS:
                            members.add(j)
        for period in PERIODS:
            mine = sec.get(period)
            if not mine:
                continue
            pool = [sections[j][period] | {"length_m": sections[j]["length_m"]} for j in members if sections[j].get(period)]
            load = [(m["trips"], m["load"]) for m in pool if m["load"] is not None]
            lost = [(m["trips"], m["lost_s_per_100m"]) for m in pool
                    if m["lost_s_per_100m"] is not None and m["length_m"] <= LONG_SECTION_M]
            load_all = sum(t * v for t, v in load) / sum(t for t, _ in load) if load else None
            lost_all = sum(t * v for t, v in lost) / sum(t for t, _ in lost) if lost else None
            mine["load_all_bin"] = band(load_all, LOAD_BANDS) if load_all is not None else None
            mine["lost_all_bin"] = band(lost_all, LOST_BANDS) if lost_all is not None else None


def build_boam_layers(cfg: Config, shapes: ShapeIndex) -> dict:
    """{"sections": [...], "meta": {...}}, cached in data/boam/boam_layers.json."""
    boam_dir = cfg.root / "data" / "boam"
    paths = sorted(glob.glob(str(boam_dir / "BOAM_*.csv.gz")))
    empty = {"sections": [], "meta": {"days": []}}
    if not paths:
        return empty
    db_path = cfg.path("db_path")
    signature = hashlib.sha1("|".join(
        [str(CACHE_VERSION)] + [f"{p}:{Path(p).stat().st_size}" for p in paths]
        + [f"{db_path.stat().st_size}:{db_path.stat().st_mtime}"]).encode()).hexdigest()
    cache_path = boam_dir / "boam_layers.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("meta", {}).get("signature") == signature:
            return cached

    samples, stops, days = section_samples(paths)
    if not days:
        return empty
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        geometry = _pair_geometry(conn, {(a, b) for a, b, _ in samples})
    finally:
        conn.close()

    by_pair: dict[tuple[str, str], dict] = {}
    for (a, b, period), s in samples.items():
        n = max(len(s["load"]), len(s["lost_s"]))
        if n < MIN_TRIPS:
            continue
        geom = geometry.get((a, b))
        if geom:
            shape_id, da, db = geom
            length_m = db - da
            pts = shapes.section_latlon(shape_id, da, db)
        else:  # no timetable trip with a then b: straight line between the stops
            if a not in stops or b not in stops:
                continue
            length_m = _haversine_m(*stops[a], *stops[b])
            pts = [list(stops[a]), list(stops[b])]
        if length_m < 20 or len(pts) < 2:
            continue
        entry = by_pair.setdefault((a, b), {
            "id": f"{a}>{b}",
            "pts": [[round(lat, 6), round(lon, 6)] for lat, lon in pts],
            "length_m": round(length_m),
        })
        load = sum(s["load"]) / len(s["load"]) if s["load"] else None
        lost = (sum(s["lost_s"]) / len(s["lost_s"])) / length_m * 100 if s["lost_s"] else None
        entry[period] = {
            "trips": n,
            "load": round(load, 3) if load is not None else None,
            "load_bin": band(load, LOAD_BANDS) if load is not None else None,
            "lost_s_per_100m": round(lost, 2) if lost is not None else None,
            "lost_bin": band(lost, LOST_BANDS) if lost is not None else None,
        }

    sections = list(by_pair.values())
    add_both_directions(sections)
    result = {
        "sections": sections,
        "meta": {
            "days": days,
            "periods": {"am": "07:00-09:00", "pm": "16:30-18:30"},
            "note": "Weekdays only. 28-30 Sep 2026 fall in NSW school holidays.",
            "signature": signature,
        },
    }
    cache_path.write_text(json.dumps(result), encoding="utf-8")
    logger.info("BOAM: %d sections from %d weekdays", len(result["sections"]), len(days))
    return result
