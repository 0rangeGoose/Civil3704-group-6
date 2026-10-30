"""Static area layers: jobs, service gap and Opal 2016, on travel zones or a grid.

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
- **opal** -- Opal tap-ons + tap-offs, 7:00-9:00, average weekday of
  21-25 Nov 2016 (Opal Tap On and Tap Off Release 2). Trains, light rail and
  ferries are given by station and placed at that station; buses are only
  given by postcode, so a postcode's bus taps are scaled by the share of its
  area inside the footprint and split evenly across today's bus stops in it.
  2016 predates the Metro and the CBD light rail, so those show nothing.

Each layer also gets a 0-4 colour bin: quintiles for jobs and Opal (among
units with any), fixed bands for the gap (see GAP_BANDS).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
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
OPAL_DAYS = {"20161121", "20161122", "20161123", "20161124", "20161125"}  # Mon-Fri
GAP_BANDS = (-0.4, -0.15, 0.15, 0.4)  # -> bins 0 (well served) .. 4 (underserved)
SIMPLIFY_M = 4.0
CACHE_VERSION = 2


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


def service_gap(jobs: dict[str, float], departures: dict[str, float]) -> dict[str, float]:
    with_jobs = {k: v for k, v in jobs.items() if v > 0}
    job_rank = percentile_ranks(with_jobs)
    dep_rank = percentile_ranks({k: departures.get(k, 0.0) for k in with_jobs})
    return {k: round(job_rank[k] - dep_rank[k], 3) for k in with_jobs}


def gap_bin(gap: float) -> int:
    return sum(gap > band for band in GAP_BANDS)


_STOP_KIND = {"train": "Station", "lightrail": "Light Rail", "ferry": "Wharf"}


def opal_base_name(mode: str, loc: str) -> tuple[str, str]:
    """('Circular Quay', 'Wharf') from ('ferry', 'Circular Quay, No. 3 Wharf'):
    the place name before any comma or mode word, and the word the timetable's
    stop names use for that mode."""
    kind = _STOP_KIND[mode]
    base = loc.split(",")[0]
    base = re.sub(r"\s+(Station|Light Rail|Wharf)(\s+\d+)?$", "", base).strip()
    base = re.sub(r"\s+Station$", "", base).strip()
    return base, kind


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


def _bus_stop_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT st.stop_id FROM stop_times st JOIN trips t ON t.trip_id = st.trip_id "
        "JOIN routes r ON r.route_id = t.route_id WHERE r.route_type IN ('3', '700', '712', '714')")}


def _opal_points(opal_csv: Path, stops: dict[str, tuple[float, float, str]],
                 bus_stops: set[str], postcodes: list[tuple[str, object]], footprint_m) -> list[tuple[Point, float]]:
    """[(metric point, average weekday AM-peak taps), ...]"""
    station_taps: dict[tuple[str, str], float] = defaultdict(float)
    postcode_taps: dict[str, float] = defaultdict(float)
    with open(opal_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"] not in OPAL_DAYS or not (PEAK_START[:5] <= row["time"] < PEAK_END[:5]):
                continue
            taps = int(row["count"]) / len(OPAL_DAYS)
            if row["mode"] == "bus":
                postcode_taps[row["loc"]] += taps
            elif row["mode"] in _STOP_KIND and row["loc"] != "UNKNOWN":
                station_taps[(row["mode"], row["loc"])] += taps

    by_name: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for lat, lon, name in stops.values():
        by_name[name].append((lat, lon))

    points: list[tuple[Point, float]] = []
    unplaced = 0.0
    for (mode, loc), taps in station_taps.items():
        coords = by_name.get(loc)
        if not coords:
            base, kind = opal_base_name(mode, loc)
            coords = [c for name, cs in by_name.items()
                      if name.startswith(base) and kind in name for c in cs]
        if not coords:
            unplaced += taps  # outside the footprint, or renamed beyond recognition
            continue
        lat = sum(c[0] for c in coords) / len(coords)
        lon = sum(c[1] for c in coords) / len(coords)
        points.append((Point(_to_metric.transform(lon, lat)), taps))

    stop_points = {sid: Point(_to_metric.transform(lon, lat)) for sid, (lat, lon, _) in stops.items() if sid in bus_stops}
    for postcode, poly_m in postcodes:
        taps = postcode_taps.get(postcode, 0.0)
        if not taps:
            continue
        inside = [p for p in stop_points.values() if poly_m.contains(p)]
        share = poly_m.intersection(footprint_m).area / poly_m.area if poly_m.area else 0
        if inside and share:
            each = taps * share / len(inside)
            points.extend((p, each) for p in inside)
    logger.info("opal 2016: %d located points; %.0f station taps/day not placed (outside footprint)",
                len(points), unplaced)
    return points


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
    postcodes_path = demand_dir / "postcodes_footprint.geojson"
    opal_path = cfg.root / "data" / "opal" / "time_loc_20161121-27.csv"
    db_path = cfg.path("db_path")
    cache_path = demand_dir / "area_layers.json"

    if not zones_path.exists():
        logger.warning("no %s -- area layers unavailable (see README, Demand layers)", zones_path)
        return {"available": {}, "zones": [], "grid": [], "meta": {}}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        day = _reference_weekday(conn, cfg.raw.get("demand", {}).get("service_date"))
        signature = _signature(cfg, [zones_path, jobs_path, postcodes_path, opal_path, db_path], day)
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("meta", {}).get("signature") == signature:
                return cached
        logger.info("building area layers (service reference day %s) ...", day)
        result = _build(cfg, conn, day, zones_path, jobs_path, postcodes_path, opal_path)
    finally:
        conn.close()
    result["meta"]["signature"] = signature
    cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _build(cfg, conn, day, zones_path, jobs_path, postcodes_path, opal_path) -> dict:
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

    # Opal 2016
    opal_points: list[tuple[Point, float]] = []
    if opal_path.exists():
        postcodes = []
        if postcodes_path.exists():
            postcodes = [(str(f["properties"]["postcode"]), _metric(shape(f["geometry"])).buffer(0))
                         for f in json.loads(postcodes_path.read_text(encoding="utf-8"))["features"]]
        opal_points = _opal_points(opal_path, stops, _bus_stop_ids(conn), postcodes, footprint_m)
    opal_tree = STRtree([p for p, _ in opal_points]) if opal_points else None

    def opal_in(geom) -> float:
        if opal_tree is None:
            return 0.0
        return round(sum(opal_points[int(i)][1] for i in opal_tree.query(geom, predicate="contains")), 1)

    zone_opal = {z: opal_in(g) for z, g in zone_geoms.items()}
    cell_opal = {c: opal_in(g) for c, g in cells.items()}

    # The combined layer (engine/demand/pressure.py) adds live inputs on top,
    # so it only needs one of these to be meaningful.
    available = {"jobs": bool(zone_jobs), "service_gap": bool(zone_jobs), "opal": bool(opal_points),
                 "pressure": bool(zone_jobs or opal_points)}

    def assemble(geoms, jobs, deps, opal, names=None, simplify=True):
        gaps = service_gap(jobs, deps)
        jobs_bins, opal_bins = quintile_bins(jobs), quintile_bins(opal)
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
                "opal": round(opal.get(uid, 0.0)),
                "bins": {
                    "jobs": jobs_bins.get(uid),
                    "service_gap": gap_bin(gaps[uid]) if uid in gaps else None,
                    "opal": opal_bins.get(uid),
                },
            }
            if names:
                unit["name"] = names[uid]
            if unit["rings"] and any(v is not None for v in unit["bins"].values()):
                out.append(unit)
        return out

    return {
        "available": available,
        "zones": assemble(zone_geoms, zone_jobs, zone_dep, zone_opal, zone_names),
        "grid": assemble(cells, cell_jobs, cell_dep, cell_opal, simplify=False),
        "meta": {
            "service_day": day,
            "peak": f"{PEAK_START[:5]}-{PEAK_END[:5]}",
            "jobs_year": JOBS_YEAR_COLUMN[-4:],
            "grid_cell_m": GRID_CELL_M,
            "walk_m": SERVICE_WALK_M,
            "opal_points": len(opal_points),
        },
    }
