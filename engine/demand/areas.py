"""Static area layers: jobs and the service gap, on travel zones or a grid.

These describe *places*, not vehicles, so they're computed once at startup
(and cached to disk) rather than every poll. Two spatial units, switchable on
the tablet, so the team can compare them on the model:

- **zones**: TfNSW Travel Zones 2021 (TZ21) intersecting the footprint;
- **grid**: 150m squares over the footprint (in UTM 56S, so they're square on
  the model).

Per unit:

- **jobs** -- TZP24 employment by place of work (EMP_2026). Zones take their
  own figure; grid cells take an area-weighted share of the zones they overlap.
- **departures** -- scheduled departures (all modes) from stops within
  SERVICE_WALK_M of the unit, 7:00-9:00 on a reference weekday from the static
  timetable. A stop counts for every unit within walking distance, and a trip
  counts once per stop it serves there: it measures service a worker in that
  unit could reach, not distinct trips.
- **service gap** -- percentile rank of jobs minus percentile rank of
  departures, among units with jobs. -1..1: positive = more jobs than service
  relative to the rest of the footprint (underserved), negative = more service
  than jobs.
Each layer also gets a 0-4 colour bin: quintiles for jobs (among units with
any), fixed bands for the gap (see GAP_BANDS).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from shapely.geometry import Point, Polygon, box, shape
from shapely.ops import transform
from shapely.strtree import STRtree

from engine.config import Config
from engine.normalise.projection import _to_metric, to_wgs84

logger = logging.getLogger("pulse_cbd.areas")

GRID_CELL_M = 150.0
SERVICE_WALK_M = 200.0
PEAK_START, PEAK_END = "07:00:00", "09:00:00"
JOBS_YEAR_COLUMN = "EMP_2026"
GAP_BANDS = (-0.4, -0.15, 0.15, 0.4)  # -> bins 0 (well served) .. 4 (underserved)
SIMPLIFY_M = 4.0
CACHE_VERSION = 4  # 3: Opal 2016 removed; 4: pressure layer removed


def _metric(geom):
    return transform(lambda x, y, z=None: _to_metric.transform(x, y), geom)


def _ring_latlon(ring) -> list[list[float]]:
    return [[round(lat, 5), round(lon, 5)] for lat, lon in (to_wgs84(x, y) for x, y in ring.coords)]


def _rings(geom) -> list[list[list[float]]]:
    """Outer rings of a (multi)polygon in metric coords, as [[lat, lon], ...]."""
    polys = [geom] if geom.geom_type == "Polygon" else [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]
    return [_ring_latlon(p.exterior) for p in polys if not p.is_empty]


# --- pure helpers (tested directly) ------------------------------------------

def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """0..1 rank of each value among all of them; ties share their average rank."""
    if len(values) < 2:
        return {k: 0.5 for k in values}
    ordered = sorted(values.items(), key=lambda kv: kv[1])
    ranks: dict[str, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        avg = (i + j) / 2 / (len(ordered) - 1)
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = avg
        i = j + 1
    return ranks


def quintile_bins(values: dict[str, float]) -> dict[str, int]:
    """0-4 for units with a positive value; others get no bin."""
    positive = {k: v for k, v in values.items() if v > 0}
    return {k: min(4, int(r * 5)) for k, r in percentile_ranks(positive).items()}


def bin_ranges(units: list[dict], layer: str) -> list[list[int] | None]:
    """[lowest, highest] of a layer's value (jobs) in each of its five
    colour bins, for the tablet legend; None for a bin no unit falls in."""
    ranges: list[list[int] | None] = [None] * 5
    for u in units:
        b = u["bins"].get(layer)
        if b is None:
            continue
        v = u[layer]
        r = ranges[b]
        ranges[b] = [min(r[0], v), max(r[1], v)] if r else [v, v]
    return ranges


def service_gap(jobs: dict[str, float], departures: dict[str, float]) -> dict[str, float]:
    with_jobs = {k: v for k, v in jobs.items() if v > 0}
    job_rank = percentile_ranks(with_jobs)
    dep_rank = percentile_ranks({k: departures.get(k, 0.0) for k in with_jobs})
    return {k: round(job_rank[k] - dep_rank[k], 3) for k in with_jobs}


def gap_bin(gap: float) -> int:
    return sum(gap > band for band in GAP_BANDS)


# --- inputs -------------------------------------------------------------------

def _reference_weekday(conn: sqlite3.Connection, override: str | None) -> str:
    if override:
        return override
    lo, hi = conn.execute("SELECT min(start_date), max(end_date) FROM calendar").fetchone()
    day = max(date.today(), date(int(lo[:4]), int(lo[4:6]), int(lo[6:])))
    while day.weekday() != 1:  # Tuesday: a plain weekday, rarely a public holiday
        day += timedelta(days=1)
    end = date(int(hi[:4]), int(hi[4:6]), int(hi[6:]))
    if day > end:
        day = end - timedelta(days=(end.weekday() - 1) % 7)
    return day.strftime("%Y%m%d")


def _active_services(conn: sqlite3.Connection, day: str) -> set[str]:
    weekday = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][
        date(int(day[:4]), int(day[4:6]), int(day[6:])).weekday()]
    active = {r[0] for r in conn.execute(
        f"SELECT service_id FROM calendar WHERE {weekday} = '1' AND start_date <= ? AND end_date >= ?", (day, day))}
    for service_id, exception in conn.execute("SELECT service_id, exception_type FROM calendar_dates WHERE date = ?", (day,)):
        if exception == "1":
            active.add(service_id)
        elif exception == "2":
            active.discard(service_id)
    return active


def _stop_departures(conn: sqlite3.Connection, day: str) -> dict[str, int]:
    active = _active_services(conn, day)
    counts: dict[str, int] = defaultdict(int)
    rows = conn.execute(
        "SELECT st.stop_id, t.service_id FROM stop_times st JOIN trips t ON t.trip_id = st.trip_id "
        "WHERE st.departure_time >= ? AND st.departure_time < ? AND st.pickup_type != '1'",
        (PEAK_START, PEAK_END),
    )
    for stop_id, service_id in rows:
        if service_id in active:
            counts[stop_id] += 1
    return counts


# --- build ----------------------------------------------------------------------

def _signature(cfg: Config, files: list[Path], day: str) -> str:
    bb = cfg.bounding_box
    parts = [str(CACHE_VERSION), day, f"{bb.north},{bb.south},{bb.west},{bb.east}"]
    parts += [f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime}" for p in files if p.exists()]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def build_area_layers(cfg: Config) -> dict:
    """{"available": {...}, "zones": [...], "grid": [...], "meta": {...}}, cached
    to data/demand/area_layers.json until any input changes."""
    demand_dir = cfg.root / "data" / "demand"
    zones_path = demand_dir / "zones_footprint.geojson"
    jobs_path = demand_dir / "tzp24_employment.csv"
    db_path = cfg.path("db_path")
    cache_path = demand_dir / "area_layers.json"

    if not zones_path.exists():
        logger.warning("no %s -- area layers unavailable (see README, Demand layers)", zones_path)
        return {"available": {}, "zones": [], "grid": [], "meta": {}}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        day = _reference_weekday(conn, cfg.raw.get("demand", {}).get("service_date"))
        signature = _signature(cfg, [zones_path, jobs_path, db_path], day)
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("meta", {}).get("signature") == signature:
                return cached
        logger.info("building area layers (service reference day %s) ...", day)
        result = _build(cfg, conn, day, zones_path, jobs_path)
    finally:
        conn.close()
    result["meta"]["signature"] = signature
    cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _build(cfg, conn, day, zones_path, jobs_path) -> dict:
    bb = cfg.bounding_box
    footprint_m = _metric(box(bb.west, bb.south, bb.east, bb.north))

    # Units: zones (whole polygons for values, clipped for drawing) and grid cells.
    zone_geoms: dict[str, object] = {}
    zone_names: dict[str, str] = {}
    for feature in json.loads(zones_path.read_text(encoding="utf-8"))["features"]:
        code = str(feature["properties"]["tz21_code"])
        zone_geoms[code] = _metric(shape(feature["geometry"])).buffer(0)
        zone_names[code] = feature["properties"]["tz21_name"]

    minx, miny, maxx, maxy = footprint_m.bounds
    cells: dict[str, object] = {}
    y, row = miny, 0
    while y < maxy:
        x, col = minx, 0
        while x < maxx:
            cell = box(x, y, x + GRID_CELL_M, y + GRID_CELL_M)
            if cell.intersects(footprint_m):
                cells[f"r{row}c{col}"] = cell
            x += GRID_CELL_M
            col += 1
        y += GRID_CELL_M
        row += 1

    # Jobs
    zone_jobs: dict[str, float] = {}
    if jobs_path.exists():
        with open(jobs_path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code = str(int(float(r["TZ_CODE21"])))
                if code in zone_geoms:
                    zone_jobs[code] = float(r[JOBS_YEAR_COLUMN] or 0)
    zone_ids = list(zone_geoms)
    zone_tree = STRtree([zone_geoms[z] for z in zone_ids])
    cell_jobs: dict[str, float] = {}
    for cid, cell in cells.items():
        total = 0.0
        for i in zone_tree.query(cell):
            z = zone_ids[int(i)]
            zg = zone_geoms[z]
            if zone_jobs.get(z) and zg.area:
                total += zone_jobs[z] * cell.intersection(zg).area / zg.area
        cell_jobs[cid] = round(total, 1)

    # Departures within walking distance
    stops = {sid: (lat, lon, name) for sid, name, lat, lon in
             conn.execute("SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops")}
    departures = _stop_departures(conn, day)
    dep_ids = [sid for sid in departures if sid in stops]
    dep_points = [Point(_to_metric.transform(stops[s][1], stops[s][0])) for s in dep_ids]
    dep_tree = STRtree(dep_points)

    def departures_near(geom) -> float:
        reach = geom.buffer(SERVICE_WALK_M)
        return float(sum(departures[dep_ids[int(i)]] for i in dep_tree.query(reach, predicate="contains")))

    zone_dep = {z: departures_near(g) for z, g in zone_geoms.items()}
    cell_dep = {c: departures_near(g) for c, g in cells.items()}

    available = {"jobs": bool(zone_jobs), "service_gap": bool(zone_jobs)}

    def assemble(geoms, jobs, deps, names=None, simplify=True):
        gaps = service_gap(jobs, deps)
        jobs_bins = quintile_bins(jobs)
        out = []
        for uid, geom in geoms.items():
            shown = geom.intersection(footprint_m)
            if shown.is_empty:
                continue
            if simplify:
                shown = shown.simplify(SIMPLIFY_M)
            unit = {
                "id": uid,
                "rings": _rings(shown),
                "jobs": round(jobs.get(uid, 0.0)),
                "departures": round(deps.get(uid, 0.0)),
                "gap": gaps.get(uid),
                "bins": {
                    "jobs": jobs_bins.get(uid),
                    "service_gap": gap_bin(gaps[uid]) if uid in gaps else None,
                },
            }
            if names:
                unit["name"] = names[uid]
            if unit["rings"] and any(v is not None for v in unit["bins"].values()):
                out.append(unit)
        return out

    return {
        "available": available,
        "zones": assemble(zone_geoms, zone_jobs, zone_dep, zone_names),
        "grid": assemble(cells, cell_jobs, cell_dep, simplify=False),
        "meta": {
            "service_day": day,
            "peak": f"{PEAK_START[:5]}-{PEAK_END[:5]}",
            "jobs_year": JOBS_YEAR_COLUMN[-4:],
            "grid_cell_m": GRID_CELL_M,
            "walk_m": SERVICE_WALK_M,
        },
    }
