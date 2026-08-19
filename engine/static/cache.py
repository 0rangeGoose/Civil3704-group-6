"""Write filtered static GTFS data into a local SQLite cache.

One table per GTFS file, columns matching the GTFS spec column names
verbatim so downstream code (map matching, adherence) can query with the
names from the spec rather than a renamed schema. stop_times and shapes are
inserted in batches from a streaming generator rather than being materialised
as Python lists first.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Iterator

from engine.static.filter import FilteredStaticData, stream_filtered_rows

_BATCH_SIZE = 20_000

SCHEMA = """
CREATE TABLE agency (
    agency_id TEXT PRIMARY KEY,
    agency_name TEXT,
    agency_url TEXT,
    agency_timezone TEXT,
    agency_lang TEXT,
    agency_phone TEXT
);

CREATE TABLE routes (
    route_id TEXT PRIMARY KEY,
    agency_id TEXT,
    route_short_name TEXT,
    route_long_name TEXT,
    route_desc TEXT,
    route_type TEXT,
    route_color TEXT,
    route_text_color TEXT
);

CREATE TABLE stops (
    stop_id TEXT PRIMARY KEY,
    stop_code TEXT,
    stop_name TEXT,
    stop_lat REAL,
    stop_lon REAL,
    location_type TEXT,
    parent_station TEXT,
    wheelchair_boarding TEXT
);

CREATE TABLE trips (
    trip_id TEXT PRIMARY KEY,
    route_id TEXT,
    service_id TEXT,
    shape_id TEXT,
    trip_headsign TEXT,
    direction_id TEXT,
    block_id TEXT,
    wheelchair_accessible TEXT
);

CREATE TABLE stop_times (
    trip_id TEXT,
    arrival_time TEXT,
    departure_time TEXT,
    stop_id TEXT,
    stop_sequence INTEGER,
    stop_headsign TEXT,
    pickup_type TEXT,
    drop_off_type TEXT,
    shape_dist_traveled REAL
);

CREATE TABLE shapes (
    shape_id TEXT,
    shape_pt_lat REAL,
    shape_pt_lon REAL,
    shape_pt_sequence INTEGER,
    shape_dist_traveled REAL
);

CREATE TABLE calendar (
    service_id TEXT PRIMARY KEY,
    monday TEXT, tuesday TEXT, wednesday TEXT, thursday TEXT,
    friday TEXT, saturday TEXT, sunday TEXT,
    start_date TEXT, end_date TEXT
);

CREATE TABLE calendar_dates (
    service_id TEXT,
    date TEXT,
    exception_type TEXT
);

CREATE INDEX idx_stop_times_trip ON stop_times(trip_id);
CREATE INDEX idx_stop_times_stop ON stop_times(stop_id);
CREATE INDEX idx_shapes_id ON shapes(shape_id);
CREATE INDEX idx_trips_route ON trips(route_id);
CREATE INDEX idx_trips_shape ON trips(shape_id);
"""


def _get(row: dict, key: str, cast=str):
    val = row.get(key, "")
    if val == "":
        return None
    try:
        return cast(val)
    except (ValueError, TypeError):
        return None


def _insert_batches(conn: sqlite3.Connection, sql: str, rows: Iterable[tuple]) -> int:
    count = 0
    batch: list[tuple] = []
    for row in rows:
        batch.append(row)
        count += 1
        if len(batch) >= _BATCH_SIZE:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)
    return count


def build_cache(
    db_path: Path,
    bundle_dir: Path,
    filtered: FilteredStaticData,
) -> dict[str, int]:
    """Create a fresh SQLite cache from filtered small tables plus streamed large ones."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    counts: dict[str, int] = {}
    try:
        conn.executescript(SCHEMA)

        counts["agency"] = _insert_batches(
            conn,
            "INSERT INTO agency VALUES (?,?,?,?,?,?)",
            (
                (r["agency_id"], r.get("agency_name"), r.get("agency_url"),
                 r.get("agency_timezone"), r.get("agency_lang"), r.get("agency_phone"))
                for r in filtered.agency_rows
            ),
        )

        counts["routes"] = _insert_batches(
            conn,
            "INSERT INTO routes VALUES (?,?,?,?,?,?,?,?)",
            (
                (r["route_id"], r.get("agency_id"), r.get("route_short_name"),
                 r.get("route_long_name"), r.get("route_desc"), r.get("route_type"),
                 r.get("route_color"), r.get("route_text_color"))
                for r in filtered.routes_rows
            ),
        )

        counts["stops"] = _insert_batches(
            conn,
            "INSERT INTO stops VALUES (?,?,?,?,?,?,?,?)",
            (
                (r["stop_id"], r.get("stop_code"), r.get("stop_name"),
                 _get(r, "stop_lat", float), _get(r, "stop_lon", float),
                 r.get("location_type"), r.get("parent_station"),
                 r.get("wheelchair_boarding"))
                for r in filtered.stops_rows
            ),
        )

        counts["trips"] = _insert_batches(
            conn,
            "INSERT INTO trips VALUES (?,?,?,?,?,?,?,?)",
            (
                (r["trip_id"], r.get("route_id"), r.get("service_id"), r.get("shape_id"),
                 r.get("trip_headsign"), r.get("direction_id"), r.get("block_id"),
                 r.get("wheelchair_accessible"))
                for r in filtered.trips_rows
            ),
        )

        if filtered.calendar_rows:
            counts["calendar"] = _insert_batches(
                conn,
                "INSERT INTO calendar VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    (r["service_id"], r.get("monday"), r.get("tuesday"), r.get("wednesday"),
                     r.get("thursday"), r.get("friday"), r.get("saturday"), r.get("sunday"),
                     r.get("start_date"), r.get("end_date"))
                    for r in filtered.calendar_rows
                ),
            )
        else:
            counts["calendar"] = 0

        counts["calendar_dates"] = _insert_batches(
            conn,
            "INSERT INTO calendar_dates VALUES (?,?,?)",
            (
                (r["service_id"], r.get("date"), r.get("exception_type"))
                for r in filtered.calendar_dates_rows
            ),
        )

        def _stop_time_rows() -> Iterator[tuple]:
            for r in stream_filtered_rows(bundle_dir / "stop_times.txt", "trip_id", filtered.trip_ids):
                yield (
                    r["trip_id"], r.get("arrival_time"), r.get("departure_time"), r.get("stop_id"),
                    _get(r, "stop_sequence", int), r.get("stop_headsign"),
                    r.get("pickup_type"), r.get("drop_off_type"),
                    _get(r, "shape_dist_traveled", float),
                )

        counts["stop_times"] = _insert_batches(
            conn, "INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?,?)", _stop_time_rows()
        )

        def _shape_rows() -> Iterator[tuple]:
            for r in stream_filtered_rows(bundle_dir / "shapes.txt", "shape_id", filtered.shape_ids):
                yield (
                    r["shape_id"], _get(r, "shape_pt_lat", float), _get(r, "shape_pt_lon", float),
                    _get(r, "shape_pt_sequence", int), _get(r, "shape_dist_traveled", float),
                )

        counts["shapes"] = _insert_batches(
            conn, "INSERT INTO shapes VALUES (?,?,?,?,?)", _shape_rows()
        )

        conn.commit()
    finally:
        conn.close()

    return counts
