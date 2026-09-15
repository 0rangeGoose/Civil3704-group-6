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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .. import config

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
    with open(employment_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        zone_col = _find_column(fieldnames, ZONE_ID_CANDIDATES, "zone id")
        employment_col = _find_column(fieldnames, EMPLOYMENT_CANDIDATES, "employment")
        try:
            year_col = _find_column(fieldnames, YEAR_CANDIDATES, "year")
        except ValueError:
            year_col = None
            if year is not None:
                logger.warning("No year column found in employment CSV; using all rows")

        rows = list(reader)

        # TZP24 has one row per zone per PROJECTION YEAR (annually out to
        # 2031, then every 5 years) - and, within a year, possibly several
        # rows per zone broken down by industry (which should be summed).
        # Without picking a single year first, summing every row for a zone
        # would add its employment across ~20 different years, wildly
        # inflating the total. If no year was requested, auto-pick whichever
        # year present in the file is closest to "now" instead.
        selected_year = year
        if year_col and selected_year is None:
            years_present = {row.get(year_col) for row in rows if row.get(year_col)}
            reference_year = reference_year if reference_year is not None else datetime.date.today().year
            try:
                selected_year = min(years_present, key=lambda y: abs(int(y) - reference_year))
            except ValueError:
                selected_year = None  # year column values weren't numeric; fall back to using all rows
            if selected_year is not None:
                logger.info(f"No year specified for demand data; using {selected_year} (closest to {reference_year})")

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
