"""Static area layer: jobs, on travel zones or a grid.

These describe *places*, not vehicles, so they're computed once at startup
(and cached to disk) rather than every poll. Two spatial units, switchable on
the tablet, so the team can compare them on the model:

- **zones**: TfNSW Travel Zones 2021 (TZ21) intersecting the footprint;
- **grid**: 150m squares over the footprint (in UTM 56S, so they're square on
  the model).

Per unit:

- **jobs** -- TZP24 employment by place of work (EMP_2026). Zones take their
  own figure; grid cells take an area-weighted share of the zones they overlap.
Each unit also gets a 0-4 colour bin: quintiles of jobs among units with any.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
from pathlib import Path

from shapely.geometry import box, shape
from shapely.ops import transform
from shapely.strtree import STRtree

from engine.config import Config
from engine.normalise.projection import _to_metric, to_wgs84

logger = logging.getLogger("pulse_cbd.areas")

GRID_CELL_M = 150.0
JOBS_YEAR_COLUMN = "EMP_2026"
SIMPLIFY_M = 4.0
CACHE_VERSION = 5  # 3: Opal 2016 removed; 4: pressure layer removed; 5: service gap removed


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



# --- build ----------------------------------------------------------------------

def _signature(cfg: Config, files: list[Path]) -> str:
    bb = cfg.bounding_box
    parts = [str(CACHE_VERSION), f"{bb.north},{bb.south},{bb.west},{bb.east}"]
    parts += [f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime}" for p in files if p.exists()]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def build_area_layers(cfg: Config) -> dict:
    """{"available": {...}, "zones": [...], "grid": [...], "meta": {...}}, cached
    to data/demand/area_layers.json until any input changes."""
    demand_dir = cfg.root / "data" / "demand"
    zones_path = demand_dir / "zones_footprint.geojson"
    jobs_path = demand_dir / "tzp24_employment.csv"
    cache_path = demand_dir / "area_layers.json"

    if not zones_path.exists():
        logger.warning("no %s -- area layers unavailable (see README, Demand layers)", zones_path)
        return {"available": {}, "zones": [], "grid": [], "meta": {}}

    signature = _signature(cfg, [zones_path, jobs_path])
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("meta", {}).get("signature") == signature:
            return cached
    logger.info("building area layers ...")
    result = _build(cfg, zones_path, jobs_path)
    result["meta"]["signature"] = signature
    cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


def _build(cfg, zones_path, jobs_path) -> dict:
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

    available = {"jobs": bool(zone_jobs)}

    def assemble(geoms, jobs, names=None, simplify=True):
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
                "bins": {"jobs": jobs_bins.get(uid)},
            }
            if names:
                unit["name"] = names[uid]
            if unit["rings"] and any(v is not None for v in unit["bins"].values()):
                out.append(unit)
        return out

    return {
        "available": available,
        "zones": assemble(zone_geoms, zone_jobs, zone_names),
        "grid": assemble(cells, cell_jobs, simplify=False),
        "meta": {
            "jobs_year": JOBS_YEAR_COLUMN[-4:],
            "grid_cell_m": GRID_CELL_M,
        },
    }
