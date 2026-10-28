"""Stop-level ingestion: real bus occupancy by stop, from TfNSW's BOAM
(Bus Opal Assignment Model) export.

Unlike pipeline.ingestion.opal_importance (travel-zone-level, a 2020 Opal
tap on/off snapshot), this is the real thing at the granularity planners
actually want: individual bus stops, each with its own lat/lon straight off
the file's own columns (no polygon join needed), drawn from a day of actual
trip operations rather than a tap-count aggregate.

  https://opendata.transport.nsw.gov.au/data/dataset/boam-bus-opal-assignment-model
  -> save a day's export as data/boam/BOAM_<YYYYMMDD>.txt (pipe-delimited;
     requires a free portal login - this module can't fetch it
     automatically). If more than one day's file is present,
     load_boam_stops() uses whichever sorts last (the most recent date).

A stop's "importance" here is the PEAK occupancy bucket reached across
every visit logged that day (OCCUPANCY_RANGE), not an average. Averaging
would wash out to near-nothing: in a real statewide export, 94.7% of every
stop-visit sits in the lowest bucket (0-20% of capacity), since most of a
bus's time on most routes is spent well under capacity. Peak answers "does
this stop ever get properly crowded", which is the planning-relevant
question - a stop that's crush-loaded once during the school run is not
the same as one that never is, even if their daily averages would look
identical.

If no BOAM file is found, load_boam_stops() returns None - this is an
optional layer, not a hard dependency.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .. import config

logger = logging.getLogger(__name__)

BOAM_DIR = config.REPO_ROOT / "data" / "boam"

# OCCUPANCY_RANGE's buckets, in the order the file actually uses them -
# "101-120" is standing beyond rated capacity, still the single busiest tier.
OCCUPANCY_RANKS: Dict[str, int] = {
    "0-20": 0,
    "21-40": 1,
    "41-60": 2,
    "61-80": 3,
    "81-100": 4,
    "101-120": 5,
}
OCCUPANCY_LABELS: Dict[int, str] = {rank: label for label, rank in OCCUPANCY_RANKS.items()}
MAX_OCCUPANCY_RANK = max(OCCUPANCY_RANKS.values())


@dataclass
class BoamStop:
    stop_id: str
    description: str
    suburb: str
    lat: float
    lon: float
    peak_rank: int
    visit_count: int


@dataclass
class BoamStops:
    stops: List[BoamStop] = field(default_factory=list)

    def to_markers(self) -> List[dict]:
        """One marker per stop - unlike demand/opal_importance there's no
        grid to rasterize onto, since every stop already has an exact
        coordinate of its own."""
        return [
            {
                "stop_id": s.stop_id,
                "lat": s.lat,
                "lon": s.lon,
                "description": s.description,
                "suburb": s.suburb,
                "peak_rank": s.peak_rank,
                "peak_label": OCCUPANCY_LABELS[s.peak_rank],
                "visit_count": s.visit_count,
            }
            for s in self.stops
        ]


def _find_latest_file(data_dir: Path) -> Optional[Path]:
    if not data_dir.exists():
        return None
    candidates = sorted(data_dir.glob("BOAM_*.txt"))  # YYYYMMDD in the name sorts chronologically
    return candidates[-1] if candidates else None


def load_boam_stops(
    path: Optional[Path] = None,
    data_dir: Path = BOAM_DIR,
    bbox: dict = config.CBD_BBOX,
) -> Optional[BoamStops]:
    """Load a day's BOAM export, pre-filtered to `bbox`, aggregated to one
    peak-occupancy reading per stop.

    `path`: an exact file to load. If omitted, the most recent
    `BOAM_*.txt` in `data_dir` is used.

    Streams the file row-by-row rather than loading it into memory -
    a full statewide day's export is on the order of a million rows and a
    few hundred MB, but bbox-filtering keeps only ~1,000 stops' worth of
    aggregated state in memory regardless of the file's size.

    Returns None (not an error) if no file is found - this is an optional
    layer; everything else keeps working without it.
    """
    if path is None:
        path = _find_latest_file(data_dir)
        if path is None:
            logger.info(f"No BOAM_*.txt export found in {data_dir} - running without a stop-crowding layer.")
            return None
    elif not path.exists():
        logger.info(f"BOAM export not found at {path} - running without a stop-crowding layer.")
        return None

    stops: Dict[str, BoamStop] = {}
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            try:
                lat = float(row["LATITUDE"])
                lon = float(row["LONGITUDE"])
            except (TypeError, ValueError, KeyError):
                continue
            if not (bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lon"] <= lon <= bbox["max_lon"]):
                continue
            rank = OCCUPANCY_RANKS.get(row.get("OCCUPANCY_RANGE", ""))
            if rank is None:
                continue

            stop_id = row["TRANSIT_STOP"]
            existing = stops.get(stop_id)
            if existing is None:
                stops[stop_id] = BoamStop(
                    stop_id=stop_id,
                    description=row.get("TRANSIT_STOP_DESCRIPTION", ""),
                    suburb=row.get("SUBURB", ""),
                    lat=lat,
                    lon=lon,
                    peak_rank=rank,
                    visit_count=1,
                )
            else:
                existing.visit_count += 1
                if rank > existing.peak_rank:
                    existing.peak_rank = rank

    result = BoamStops(stops=list(stops.values()))
    logger.info(f"BOAM stops loaded from {path.name}: {len(result.stops)} stops in bbox")
    return result
