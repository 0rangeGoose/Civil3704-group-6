"""Demand-side ingestion: workplace employment by TZ21 travel zone.

TfNSW's Travel Zone Projections (TZP24) give "employed persons by place of
work" per travel zone - i.e. where people's JOBS are, not where they live.
That's the right demand signal for weekday peak bus congestion: it
concentrates in the CBD core, the same place congestion actually shows up,
unlike residential population which spreads across the model's wider
harbourside footprint.

Two files are needed from the TfNSW Open Data Hub (requires a free portal
login - this module can't fetch them automatically):

  1. Travel Zones 2021 (TZ21) boundaries, GeoJSON format
     https://opendata.transport.nsw.gov.au/dataset/travel-zones-2021
     -> save as data/demand/travel_zones_2021.geojson

  2. TZP24 employment projections (place-of-work), CSV format
     https://opendata.transport.nsw.gov.au/dataset/employment-projections
     -> save as data/demand/tzp24_employment.csv

The exact column names in TfNSW's files weren't confirmed when this was
written (the technical guide PDF and dataset pages require a logged-in
session to view). _find_column() below searches a short list of likely
names/substrings rather than hardcoding one guess, and raises a clear error
naming the columns it actually found if none match - so a mismatch is a
one-line fix (add the real name to the candidate list) rather than a silent
wrong answer.

If either file is missing, load_demand_zones() returns None and the rest of
the pipeline runs exactly as before - this is an optional enrichment, not a
hard dependency.
"""

from __future__ import annotations

import csv
import datetime
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .. import config, gridmath

logger = logging.getLogger(__name__)

DEMAND_DIR = config.REPO_ROOT / "data" / "demand"
ZONES_PATH = DEMAND_DIR / "travel_zones_2021.geojson"
EMPLOYMENT_PATH = DEMAND_DIR / "tzp24_employment.csv"

# Candidate column/property names, checked in order, case-insensitive.
ZONE_ID_CANDIDATES = ["tz21_code", "tz_code", "zone_id", "tz21", "tz", "objectid"]
EMPLOYMENT_CANDIDATES = [
    "employed_persons_place_of_work",
    "employment_place_of_work",
    "total_employment",
    "employed_persons",
    "employment",
    "jobs",
]
YEAR_CANDIDATES = ["year", "tzp_year", "projection_year"]

# TZP24's actual export is WIDE: one row per zone, one column per projection
# year (EMP_2021, EMP_2022, ..., EMP_2066 - annual to 2031, then 5-yearly).
# Matched separately from EMPLOYMENT_CANDIDATES/YEAR_CANDIDATES, which cover
# the LONG-format alternative (one row per zone *per year*, single value
# column) some exports use instead.
EMP_YEAR_COLUMN_PATTERN = re.compile(r"^emp(?:loyment)?[_\s]?(\d{4})$", re.IGNORECASE)


@dataclass
class DemandZone:
    zone_id: str
    employment: float
    bbox: Tuple[float, float, float, float]  # min_lat, max_lat, min_lon, max_lon
    rings: List[List[Tuple[float, float]]]  # each ring: [(lat, lon), ...]; rings[0] is the exterior


@dataclass
class DemandZones:
    zones: List[DemandZone] = field(default_factory=list)
    max_employment: float = 0.0

    def demand_score(self, lat: float, lon: float) -> Optional[float]:
        """Normalised [0, 1] job-density score for the zone containing this
        point (1.0 = the busiest zone in the loaded set), or None if the
        point falls outside every loaded zone."""
        if not self.zones or self.max_employment <= 0:
            return None
        for zone in self.zones:
            min_lat, max_lat, min_lon, max_lon = zone.bbox
            if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                continue
            if _point_in_rings(lat, lon, zone.rings):
                return round(zone.employment / self.max_employment, 3)
        return None

    def build_grid(self, cell_metres: float) -> List[dict]:
        """Rasterize every loaded zone onto the fixed lat/lon grid shared
        with analytics.congestion_grid (via pipeline.gridmath) - independent
        of any vehicle/bus data, so demand can be shown even where no bus is
        currently running (a genuine service-coverage gap, not a blank spot).

        This is a one-time computation, not something to redo every poll
        cycle like congestion: demand data doesn't change during a run, and
        rasterizing every zone is meaningfully more work than bucketing a
        few hundred live bus positions.

        Returns a list of {cell_id, bounds, center, demand_score} dicts.
        """
        if not self.zones or self.max_employment <= 0:
            return []

        lat_step, lon_step = gridmath.grid_steps(cell_metres)
        seen: Dict[Tuple[int, int], float] = {}

        for zone in self.zones:
            min_lat, max_lat, min_lon, max_lon = zone.bbox
            row_start = int(math.floor(min_lat / lat_step))
            row_end = int(math.floor(max_lat / lat_step))
            col_start = int(math.floor(min_lon / lon_step))
            col_end = int(math.floor(max_lon / lon_step))

            for row in range(row_start, row_end + 1):
                for col in range(col_start, col_end + 1):
                    if (row, col) in seen:
                        continue  # already claimed by an earlier (denser) zone
                    bounds = gridmath.cell_bounds(row, col, cell_metres)
                    center_lat = (bounds["min_lat"] + bounds["max_lat"]) / 2
                    center_lon = (bounds["min_lon"] + bounds["max_lon"]) / 2
                    if _point_in_rings(center_lat, center_lon, zone.rings):
                        seen[(row, col)] = round(zone.employment / self.max_employment, 3)

        cells = []
        for (row, col), score in seen.items():
            bounds = gridmath.cell_bounds(row, col, cell_metres)
            cells.append(
                {
                    "cell_id": f"r{row}c{col}",
                    "bounds": bounds,
                    "center": {
                        "lat": round((bounds["min_lat"] + bounds["max_lat"]) / 2, 6),
                        "lon": round((bounds["min_lon"] + bounds["max_lon"]) / 2, 6),
                    },
                    "demand_score": score,
                }
            )
        return cells


def _find_column(fieldnames: Sequence[str], candidates: List[str], label: str) -> str:
    lowered = {name.lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    for candidate in candidates:
        for lower_name, original in lowered.items():
            if candidate in lower_name:
                return original
    raise ValueError(
        f"Could not find a {label} column among {list(fieldnames)}. "
        f"Tried: {candidates}. If this is the real TZP24/TZ21 file, add its "
        f"actual column name to the candidates list in pipeline/ingestion/demand.py."
    )


def _find_year_columns(fieldnames: Sequence[str]) -> Dict[int, str]:
    """Wide-format files have one column per year, e.g. EMP_2024. Returns
    {year: column_name} for every such column found (empty dict if none)."""
    years: Dict[int, str] = {}
    for name in fieldnames:
        match = EMP_YEAR_COLUMN_PATTERN.match(name.strip())
        if match:
            years[int(match.group(1))] = name
    return years


def _pick_nearest_year(available_years, reference_year: Optional[int]) -> Optional[int]:
    """Pick whichever of `available_years` (ints) is closest to `reference_year`
    (default: this calendar year) - used when no explicit year is requested,
    so rows/columns for every projected year don't get silently combined."""
    if not available_years:
        return None
    reference = reference_year if reference_year is not None else datetime.date.today().year
    return min(available_years, key=lambda y: abs(y - reference))


def _ring_bbox(ring: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    lats = [p[0] for p in ring]
    lons = [p[1] for p in ring]
    return min(lats), max(lats), min(lons), max(lons)


def _point_in_ring(lat: float, lon: float, ring: List[Tuple[float, float]]) -> bool:
    """Standard ray-casting point-in-polygon test."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]
        if (lon_i > lon) != (lon_j > lon):
            x_intersect = (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i + 1e-15) + lat_i
            if lat < x_intersect:
                inside = not inside
        j = i
    return inside


def _point_in_rings(lat: float, lon: float, rings: List[List[Tuple[float, float]]]) -> bool:
    """rings[0] is the exterior boundary; any further rings are holes cut out of it."""
    if not rings or not _point_in_ring(lat, lon, rings[0]):
        return False
    return not any(_point_in_ring(lat, lon, hole) for hole in rings[1:])


def _extract_rings(geometry: dict) -> List[List[Tuple[float, float]]]:
    """GeoJSON coordinates are [lon, lat] - flip to (lat, lon) here so the
    rest of the pipeline never has to think about GeoJSON's axis order."""
    coords = geometry.get("coordinates", [])
    gtype = geometry.get("type")

    def flip(ring):
        return [(point[1], point[0]) for point in ring]

    if gtype == "Polygon":
        return [flip(ring) for ring in coords]
    if gtype == "MultiPolygon":
        return [flip(ring) for polygon in coords for ring in polygon]
    return []


def load_demand_zones(
    zones_path: Path = ZONES_PATH,
    employment_path: Path = EMPLOYMENT_PATH,
    bbox: dict = config.CBD_BBOX,
    year: Optional[str] = None,
    reference_year: Optional[int] = None,
) -> Optional[DemandZones]:
    """Load TZ21 boundaries + TZP24 employment, pre-filtered to `bbox`.

    `year`: which projection year's rows to use. If omitted and the file has
    a year column, the year closest to `reference_year` (default: today's
    calendar year) is picked automatically - without this, employment would
    get summed across every projected year in the file (TZP24 has ~20 of
    them per zone), wildly inflating every total.

    Returns None (not an error) if either file is missing - demand is an
    optional layer; everything else keeps working without it.
    """
    if not zones_path.exists() or not employment_path.exists():
        logger.info(
            f"Demand data not found ({zones_path.name} / {employment_path.name} "
            f"expected in {zones_path.parent}) - running without a demand layer."
        )
        return None

    with open(zones_path, "r", encoding="utf-8") as f:
        geojson = json.load(f)

    employment_by_zone: Dict[str, float] = {}
    # utf-8-sig: TfNSW's CSV export starts with a UTF-8 BOM, which a plain
    # "utf-8" decode leaves stuck to the first column name (only worked
    # before by luck, since the BOM sat in front of a substring match).
    with open(employment_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        zone_col = _find_column(fieldnames, ZONE_ID_CANDIDATES, "zone id")
        rows = list(reader)
        year_columns = _find_year_columns(fieldnames)

        if year_columns:
            # Wide format (TZP24's actual export): one row per zone, one
            # column per projection year - EMP_2021 .. EMP_2066.
            requested_year = int(year) if year is not None else None
            selected_year = requested_year or _pick_nearest_year(set(year_columns), reference_year)
            if selected_year not in year_columns:
                raise ValueError(
                    f"Employment year {selected_year} not in this file. "
                    f"Available years: {sorted(year_columns)}"
                )
            if requested_year is None:
                logger.info(f"No year specified for demand data; using {selected_year} (closest to today)")
            employment_col = year_columns[selected_year]

            for row in rows:
                try:
                    value = float(row[employment_col])
                except (TypeError, ValueError):
                    continue
                zone_id = row[zone_col]
                employment_by_zone[zone_id] = employment_by_zone.get(zone_id, 0.0) + value
        else:
            # Long format fallback: one row per zone per YEAR (and possibly
            # per industry within a year, which should be summed) with a
            # single employment column. Without picking one year first,
            # summing every row for a zone would add employment across every
            # projected year in the file, wildly inflating the total.
            employment_col = _find_column(fieldnames, EMPLOYMENT_CANDIDATES, "employment")
            try:
                year_col = _find_column(fieldnames, YEAR_CANDIDATES, "year")
            except ValueError:
                year_col = None
                if year is not None:
                    logger.warning("No year column found in employment CSV; using all rows")

            selected_year = year
            if year_col and selected_year is None:
                years_present = {row.get(year_col) for row in rows if row.get(year_col)}
                try:
                    selected_year = _pick_nearest_year(
                        {int(y) for y in years_present}, reference_year
                    )
                except ValueError:
                    selected_year = None  # year values weren't numeric; fall back to using all rows
                if selected_year is not None:
                    logger.info(f"No year specified for demand data; using {selected_year} (closest to today)")

            for row in rows:
                if year_col and selected_year is not None and row.get(year_col) != str(selected_year):
                    continue
                try:
                    value = float(row[employment_col])
                except (TypeError, ValueError):
                    continue
                zone_id = row[zone_col]
                employment_by_zone[zone_id] = employment_by_zone.get(zone_id, 0.0) + value

    zones: List[DemandZone] = []
    for feature in geojson.get("features", []):
        properties = feature.get("properties", {})
        zone_id = next(
            (str(v) for k, v in properties.items() if k.lower() in ZONE_ID_CANDIDATES),
            None,
        )
        if zone_id is None or zone_id not in employment_by_zone:
            continue

        rings = _extract_rings(feature.get("geometry", {}))
        if not rings:
            continue
        ring_bbox = _ring_bbox(rings[0])

        # Skip zones nowhere near the model footprint before doing anything
        # more expensive with them - TZ21 covers the whole state.
        min_lat, max_lat, min_lon, max_lon = ring_bbox
        if max_lat < bbox["min_lat"] or min_lat > bbox["max_lat"]:
            continue
        if max_lon < bbox["min_lon"] or min_lon > bbox["max_lon"]:
            continue

        zones.append(
            DemandZone(
                zone_id=zone_id,
                employment=employment_by_zone[zone_id],
                bbox=ring_bbox,
                rings=rings,
            )
        )

    max_employment = max((z.employment for z in zones), default=0.0)
    logger.info(
        f"Demand data loaded: {len(zones)} travel zones overlapping the model footprint "
        f"(max employment in one zone: {max_employment:.0f})"
    )
    return DemandZones(zones=zones, max_employment=max_employment)
