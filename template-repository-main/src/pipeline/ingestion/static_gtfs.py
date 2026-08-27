"""Static GTFS ingestion: routes, stops, trips and shapes.

Static GTFS changes rarely (TfNSW refreshes it roughly daily), so this
module downloads/extracts it once, filters it down to the model's bounding
box, and caches the result in SQLite. Every other pipeline stage reads from
that cache instead of re-parsing the raw CSVs on every cycle.

Fully testable without a network connection: if `data/gtfs/*.txt` is already
present (downloaded manually per src/README.md), no download happens at all.
"""

from __future__ import annotations

import io
import logging
import sqlite3
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

from .. import config

logger = logging.getLogger(__name__)

REQUIRED_FILES = ["routes.txt", "stops.txt", "trips.txt"]
OPTIONAL_FILES = ["shapes.txt", "stop_times.txt"]


@dataclass
class TripInfo:
    route_id: str
    route_short_name: str
    shape_id: Optional[str]


@dataclass
class StaticGTFS:
    """Everything downstream pipeline stages need from static GTFS."""

    trips: Dict[str, TripInfo] = field(default_factory=dict)
    shapes: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    stop_ids_in_bbox: set = field(default_factory=set)

    def lookup_trip(self, trip_id: str) -> Optional[TripInfo]:
        return self.trips.get(trip_id)


def ensure_downloaded(data_dir: Path, api_key: str) -> None:
    """Download + extract the static GTFS bundle if it isn't present locally."""
    data_dir.mkdir(parents=True, exist_ok=True)
    missing = [f for f in REQUIRED_FILES if not (data_dir / f).exists()]
    if not missing:
        return

    logger.warning(f"Missing {missing}; downloading static GTFS from TfNSW")
    response = requests.get(
        config.STATIC_GTFS_DOWNLOAD_URL,
        headers={"Authorization": f"apikey {api_key}"},
        timeout=120,
    )
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(data_dir)
    logger.info(f"Static GTFS extracted to {data_dir}")


def _read_csv(data_dir: Path, filename: str) -> Optional[pd.DataFrame]:
    path = data_dir / filename
    if not path.exists():
        return None
    return pd.read_csv(path, dtype=str, low_memory=False)


def load_and_filter(data_dir: Path = config.STATIC_GTFS_DIR, bbox: dict = config.CBD_BBOX) -> StaticGTFS:
    """Read local GTFS CSVs, filter spatially, and return lookup tables."""
    stops = _read_csv(data_dir, "stops.txt")
    routes = _read_csv(data_dir, "routes.txt")
    trips = _read_csv(data_dir, "trips.txt")
    shapes = _read_csv(data_dir, "shapes.txt")

    if stops is None or routes is None or trips is None:
        logger.warning("Static GTFS files missing; returning empty lookup tables")
        return StaticGTFS()

    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    in_bbox = stops[
        (stops["stop_lat"] >= bbox["min_lat"])
        & (stops["stop_lat"] <= bbox["max_lat"])
        & (stops["stop_lon"] >= bbox["min_lon"])
        & (stops["stop_lon"] <= bbox["max_lon"])
    ]
    stop_ids_in_bbox = set(in_bbox["stop_id"])

    route_names = routes.set_index("route_id")["route_short_name"].to_dict()

    trip_lookup: Dict[str, TripInfo] = {}
    for row in trips.itertuples(index=False):
        trip_lookup[row.trip_id] = TripInfo(
            route_id=row.route_id,
            route_short_name=route_names.get(row.route_id, row.route_id),
            shape_id=getattr(row, "shape_id", None),
        )

    shape_lookup: Dict[str, List[Tuple[float, float]]] = {}
    if shapes is not None:
        shapes["shape_pt_lat"] = pd.to_numeric(shapes["shape_pt_lat"], errors="coerce")
        shapes["shape_pt_lon"] = pd.to_numeric(shapes["shape_pt_lon"], errors="coerce")
        shapes["shape_pt_sequence"] = pd.to_numeric(shapes["shape_pt_sequence"], errors="coerce")
        shapes = shapes.sort_values(["shape_id", "shape_pt_sequence"])
        for shape_id, group in shapes.groupby("shape_id"):
            shape_lookup[shape_id] = list(zip(group["shape_pt_lat"], group["shape_pt_lon"]))

    logger.info(
        f"Static GTFS loaded: {len(trip_lookup)} trips, {len(shape_lookup)} shapes, "
        f"{len(stop_ids_in_bbox)} stops in bbox"
    )
    return StaticGTFS(trips=trip_lookup, shapes=shape_lookup, stop_ids_in_bbox=stop_ids_in_bbox)


def cache_to_sqlite(static: StaticGTFS, db_path: Path = config.STATIC_GTFS_CACHE_DB) -> None:
    """Persist the filtered lookup tables so a restart doesn't need the raw CSVs re-parsed."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS trips")
        conn.execute(
            "CREATE TABLE trips (trip_id TEXT PRIMARY KEY, route_id TEXT, "
            "route_short_name TEXT, shape_id TEXT)"
        )
        conn.executemany(
            "INSERT INTO trips VALUES (?, ?, ?, ?)",
            [
                (trip_id, info.route_id, info.route_short_name, info.shape_id)
                for trip_id, info in static.trips.items()
            ],
        )
        conn.execute("DROP TABLE IF EXISTS meta")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES ('cached_at', ?)", (str(int(time.time())),))
        conn.commit()
    finally:
        conn.close()


def cache_is_fresh(db_path: Path = config.STATIC_GTFS_CACHE_DB, max_age_hours: int = config.STATIC_REFRESH_HOURS) -> bool:
    if not db_path.exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'cached_at'").fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    if row is None:
        return False
    cached_at = int(row[0])
    return (time.time() - cached_at) < max_age_hours * 3600


def load_static_gtfs(api_key: str) -> StaticGTFS:
    """Top-level entry point: ensure data is on disk, load it, refresh the cache."""
    ensure_downloaded(config.STATIC_GTFS_DIR, api_key)
    static = load_and_filter()
    cache_to_sqlite(static)
    return static
