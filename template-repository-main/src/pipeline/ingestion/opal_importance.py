"""Demand-side ingestion: Opal tap on/off activity by TZ21 travel zone.

Where pipeline.ingestion.demand answers "where are the jobs" (a structural,
slow-moving signal), this module answers "where do people actually tap on
and off the network" - real observed trip volume, from TfNSW's Opal Tap On
and Tap Off Release 3 (2020) dataset:

  https://opendata.transport.nsw.gov.au/data/dataset/opal-tap-on-and-tap-off-release-3-2020
  -> save the "origin and destination by travel zone" CSV for one week as
     data/opal/origin_destination_<week>.csv

That file's TZcode_on/TZcode_off columns use the same TZ21 codes as
pipeline.ingestion.demand's employment data (confirmed by cross-checking:
~99.5% of this file's codes match data/demand/tzp24_employment.csv's
TZ_CODE21), so it re-uses the same travel_zones_2021.geojson boundaries
rather than needing its own geometry file.

A zone's "importance" here is a z-score, not a [0, 1] ratio like
DemandZones.demand_score: (zone_taps - mean_taps) / std_taps across every
zone loaded (i.e. already filtered to the model's bbox). A ratio against
the single busiest zone would always paint one zone as "the max" and
everywhere else as some fraction of it; a z-score instead answers "is this
zone unusual relative to the others in view", which is what makes a handful
of genuine hotspots (interchanges, stadiums on event days) stand out instead
of being flattened by Central/Town Hall-sized outliers at the top of a
linear scale.

If the OD file or the TZ21 boundaries are missing, load_opal_importance()
returns None - this is an optional enrichment layered on top of the vehicle
feed, not a hard dependency.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import config, gridmath
from .demand import ZONES_PATH
from .zone_geometry import Ring, extract_rings, point_in_rings, ring_bbox

logger = logging.getLogger(__name__)

OPAL_DIR = config.REPO_ROOT / "data" / "opal"
ORIGIN_DESTINATION_PATH = OPAL_DIR / "origin_destination_24feb.csv"

# Same property-name search as demand.ZONE_ID_CANDIDATES - duplicated rather
# than imported since it's a one-line list and the two modules otherwise
# have no reason to stay coupled.
ZONE_ID_CANDIDATES = ["tz21_code", "tz_code", "zone_id", "tz21", "tz", "objectid"]

ALL_MODES = {"bus", "train", "ferry", "light_rail"}


@dataclass
class OpalZone:
    zone_id: str
    taps: float  # total boardings + alightings this zone recorded, for the loaded mode(s)
    bbox: Tuple[float, float, float, float]  # min_lat, max_lat, min_lon, max_lon
    rings: List[Ring]  # rings[0] is the exterior


@dataclass
class OpalZones:
    zones: List[OpalZone] = field(default_factory=list)
    mean_taps: float = 0.0
    std_taps: float = 0.0

    def importance_score(self, lat: float, lon: float) -> Optional[float]:
        """Z-score of the zone containing this point, relative to every
        other zone loaded - positive means busier than the model-area
        average, negative means quieter. None if the point falls outside
        every loaded zone, or if there's no spread to score against (e.g.
        only one zone loaded)."""
        if not self.zones or self.std_taps <= 0:
            return None
        for zone in self.zones:
            min_lat, max_lat, min_lon, max_lon = zone.bbox
            if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                continue
            if point_in_rings(lat, lon, zone.rings):
                return round((zone.taps - self.mean_taps) / self.std_taps, 3)
        return None

    def build_grid(self, cell_metres: float) -> List[dict]:
        """Rasterize every loaded zone onto the same fixed lat/lon grid as
        DemandZones.build_grid (via pipeline.gridmath), so the two layers
        line up on the map. A one-time computation, like the demand grid -
        this data doesn't change during a run.

        Returns a list of {cell_id, bounds, center, importance_score} dicts.
        """
        if not self.zones or self.std_taps <= 0:
            return []

        lat_step, lon_step = gridmath.grid_steps(cell_metres)
        seen: Dict[Tuple[int, int], float] = {}

        for zone in self.zones:
            min_lat, max_lat, min_lon, max_lon = zone.bbox
            row_start = int(math.floor(min_lat / lat_step))
            row_end = int(math.floor(max_lat / lat_step))
            col_start = int(math.floor(min_lon / lon_step))
            col_end = int(math.floor(max_lon / lon_step))
            score = round((zone.taps - self.mean_taps) / self.std_taps, 3)

            for row in range(row_start, row_end + 1):
                for col in range(col_start, col_end + 1):
                    if (row, col) in seen:
                        continue  # already claimed by an earlier zone
                    bounds = gridmath.cell_bounds(row, col, cell_metres)
                    center_lat = (bounds["min_lat"] + bounds["max_lat"]) / 2
                    center_lon = (bounds["min_lon"] + bounds["max_lon"]) / 2
                    if point_in_rings(center_lat, center_lon, zone.rings):
                        seen[(row, col)] = score

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
                    "importance_score": score,
                }
            )
        return cells


def _sum_taps_by_zone(od_path: Path, modes: Optional[set]) -> Dict[str, float]:
    """A zone's tap total is its activity at EITHER end of a trip - boardings
    where it's the origin (TZcode_on) plus alightings where it's the
    destination (TZcode_off) - summed across every row in the file,
    regardless of where the *other* end of each trip falls. That mirrors
    demand.py's employment_by_zone: a per-zone property, not a property of
    the bbox, so a zone's score doesn't depend on which other zones happen
    to be in view.
    """
    taps: Dict[str, float] = {}
    with open(od_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if modes is not None and row.get("mode") not in modes:
                continue
            try:
                count = float(row["count"])
            except (TypeError, ValueError, KeyError):
                continue
            for zone_col in ("TZcode_on", "TZcode_off"):
                zone_id = row.get(zone_col)
                if zone_id:
                    taps[zone_id] = taps.get(zone_id, 0.0) + count
    return taps


def load_opal_importance(
    od_path: Path = ORIGIN_DESTINATION_PATH,
    zones_path: Path = ZONES_PATH,
    bbox: dict = config.CBD_BBOX,
    modes: Optional[set] = None,
) -> Optional[OpalZones]:
    """Load TZ21 boundaries + Opal tap on/off activity, pre-filtered to `bbox`.

    `modes`: restrict to a subset of {"bus", "train", "ferry", "light_rail"}
    (the OD file's `mode` column). Omit for every mode combined.

    Returns None (not an error) if either file is missing - this is an
    optional layer; everything else keeps working without it.
    """
    if not od_path.exists() or not zones_path.exists():
        logger.info(
            f"Opal tap data not found ({od_path.name} / {zones_path.name} "
            f"expected in {od_path.parent} / {zones_path.parent}) - running without an importance layer."
        )
        return None

    taps_by_zone = _sum_taps_by_zone(od_path, modes)

    with open(zones_path, "r", encoding="utf-8") as f:
        geojson = json.load(f)

    zones: List[OpalZone] = []
    for feature in geojson.get("features", []):
        properties = feature.get("properties", {})
        zone_id = next(
            (str(v) for k, v in properties.items() if k.lower() in ZONE_ID_CANDIDATES),
            None,
        )
        if zone_id is None or zone_id not in taps_by_zone:
            continue

        rings = extract_rings(feature.get("geometry", {}))
        if not rings:
            continue
        zone_bbox = ring_bbox(rings[0])

        # Skip zones nowhere near the model footprint before doing anything
        # more expensive with them - TZ21 covers the whole state.
        min_lat, max_lat, min_lon, max_lon = zone_bbox
        if max_lat < bbox["min_lat"] or min_lat > bbox["max_lat"]:
            continue
        if max_lon < bbox["min_lon"] or min_lon > bbox["max_lon"]:
            continue

        zones.append(
            OpalZone(
                zone_id=zone_id,
                taps=taps_by_zone[zone_id],
                bbox=zone_bbox,
                rings=rings,
            )
        )

    mean_taps = statistics.fmean(z.taps for z in zones) if zones else 0.0
    std_taps = statistics.pstdev(z.taps for z in zones) if len(zones) > 1 else 0.0
    logger.info(
        f"Opal importance data loaded: {len(zones)} travel zones overlapping the model "
        f"footprint (mean taps/zone: {mean_taps:.0f}, std: {std_taps:.0f})"
    )
    return OpalZones(zones=zones, mean_taps=mean_taps, std_taps=std_taps)
