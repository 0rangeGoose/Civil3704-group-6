"""Bounding-box spatial filter for the static GTFS bundle.

Order, matching the project brief:

1. ``stops.txt`` and ``shapes.txt`` are filtered spatially against the CBD
   bounding box, independently of each other.
2. ``trips.txt`` and ``stop_times.txt`` are filtered down to trips that touch
   a retained stop -- keeping *every* stop_time row for those trips, not just
   the ones inside the box, so a vehicle has scheduling history before it
   enters frame rather than popping into existence.
3. ``routes.txt``, ``agency.txt``, ``calendar.txt`` and ``calendar_dates.txt``
   are trimmed to what the retained trips/routes actually reference.

``shapes.txt`` and ``stop_times.txt`` are large enough (~1GB / ~400MB in the
Greater Sydney bundle) that this module never loads them fully into memory:
each is read in two streaming passes -- one to decide what's retained, one to
yield the retained rows -- rather than being parsed with csv.DictReader or
pandas.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from engine.config import BoundingBox

csv.field_size_limit(10_000_000)


def _in_box(lat: float, lon: float, bbox: BoundingBox) -> bool:
    return bbox.south <= lat <= bbox.north and bbox.west <= lon <= bbox.east


def _open_rows(path: Path):
    f = open(path, newline="", encoding="utf-8-sig")
    reader = csv.reader(f)
    header = next(reader)
    return f, reader, {name: i for i, name in enumerate(header)}


def filter_stops(stops_path: Path, bbox: BoundingBox) -> set[str]:
    """Stop ids whose lat/lon fall inside the bounding box."""
    retained: set[str] = set()
    f, reader, idx = _open_rows(stops_path)
    try:
        i_id, i_lat, i_lon = idx["stop_id"], idx["stop_lat"], idx["stop_lon"]
        for row in reader:
            try:
                lat, lon = float(row[i_lat]), float(row[i_lon])
            except (ValueError, IndexError):
                continue
            if _in_box(lat, lon, bbox):
                retained.add(row[i_id])
    finally:
        f.close()
    return retained


def filter_shapes(shapes_path: Path, bbox: BoundingBox) -> set[str]:
    """Shape ids with at least one point inside the bounding box."""
    retained: set[str] = set()
    f, reader, idx = _open_rows(shapes_path)
    try:
        i_id, i_lat, i_lon = idx["shape_id"], idx["shape_pt_lat"], idx["shape_pt_lon"]
        for row in reader:
            shape_id = row[i_id]
            if shape_id in retained:
                continue
            try:
                lat, lon = float(row[i_lat]), float(row[i_lon])
            except (ValueError, IndexError):
                continue
            if _in_box(lat, lon, bbox):
                retained.add(shape_id)
    finally:
        f.close()
    return retained


def trips_touching_stops(stop_times_path: Path, retained_stop_ids: set[str]) -> set[str]:
    """Trip ids with at least one stop_time at a retained stop."""
    retained: set[str] = set()
    f, reader, idx = _open_rows(stop_times_path)
    try:
        i_trip, i_stop = idx["trip_id"], idx["stop_id"]
        for row in reader:
            if row[i_stop] in retained_stop_ids:
                retained.add(row[i_trip])
    finally:
        f.close()
    return retained


def stream_filtered_rows(path: Path, key_column: str, retained_keys: set[str]) -> Iterator[dict[str, str]]:
    """Yield full rows (as dicts) from a large file whose key column is retained.

    Used for the second pass over stop_times.txt (key=trip_id) and
    shapes.txt (key=shape_id), after the retained-key set is already known.
    """
    f, reader, idx = _open_rows(path)
    try:
        i_key = idx[key_column]
        columns = list(idx.keys())
        for row in reader:
            if row[i_key] in retained_keys:
                yield dict(zip(columns, row))
    finally:
        f.close()


def read_small_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load an entire small GTFS file (agency/routes/trips/calendar*) into memory."""
    f, reader, idx = _open_rows(path)
    try:
        columns = list(idx.keys())
        rows = [dict(zip(columns, row)) for row in reader]
    finally:
        f.close()
    return columns, rows


@dataclass
class FilteredStaticData:
    bbox: BoundingBox
    stop_ids: set[str]
    shape_ids: set[str]
    trip_ids: set[str]
    route_ids: set[str] = field(default_factory=set)
    service_ids: set[str] = field(default_factory=set)
    agency_ids: set[str] = field(default_factory=set)

    stops_rows: list[dict[str, str]] = field(default_factory=list)
    routes_rows: list[dict[str, str]] = field(default_factory=list)
    trips_rows: list[dict[str, str]] = field(default_factory=list)
    agency_rows: list[dict[str, str]] = field(default_factory=list)
    calendar_rows: list[dict[str, str]] = field(default_factory=list)
    calendar_dates_rows: list[dict[str, str]] = field(default_factory=list)


def build_filtered_static_data(bundle_dir: Path, bbox: BoundingBox) -> FilteredStaticData:
    """Run the full spatial + relational filter over a raw GTFS extract.

    Returns everything needed to populate the cache except the two large
    streamed tables (stop_times, shapes) -- callers should stream those
    straight into storage via ``stream_filtered_rows`` to avoid holding
    hundreds of thousands of rows in memory at once.
    """
    stop_ids = filter_stops(bundle_dir / "stops.txt", bbox)
    shape_ids = filter_shapes(bundle_dir / "shapes.txt", bbox)
    trip_ids = trips_touching_stops(bundle_dir / "stop_times.txt", stop_ids)

    _, all_trip_rows = read_small_table(bundle_dir / "trips.txt")
    trips_rows = [r for r in all_trip_rows if r["trip_id"] in trip_ids]
    route_ids = {r["route_id"] for r in trips_rows}
    service_ids = {r["service_id"] for r in trips_rows}
    shape_ids |= {r["shape_id"] for r in trips_rows if r.get("shape_id")}

    _, all_route_rows = read_small_table(bundle_dir / "routes.txt")
    routes_rows = [r for r in all_route_rows if r["route_id"] in route_ids]
    agency_ids = {r["agency_id"] for r in routes_rows if r.get("agency_id")}

    _, all_agency_rows = read_small_table(bundle_dir / "agency.txt")
    agency_rows = [r for r in all_agency_rows if r["agency_id"] in agency_ids]

    _, all_stops_rows = read_small_table(bundle_dir / "stops.txt")
    stops_rows = [r for r in all_stops_rows if r["stop_id"] in stop_ids]

    calendar_rows: list[dict[str, str]] = []
    calendar_path = bundle_dir / "calendar.txt"
    if calendar_path.exists():
        _, all_calendar_rows = read_small_table(calendar_path)
        calendar_rows = [r for r in all_calendar_rows if r["service_id"] in service_ids]

    calendar_dates_rows: list[dict[str, str]] = []
    calendar_dates_path = bundle_dir / "calendar_dates.txt"
    if calendar_dates_path.exists():
        _, all_cd_rows = read_small_table(calendar_dates_path)
        calendar_dates_rows = [r for r in all_cd_rows if r["service_id"] in service_ids]

    return FilteredStaticData(
        bbox=bbox,
        stop_ids=stop_ids,
        shape_ids=shape_ids,
        trip_ids=trip_ids,
        route_ids=route_ids,
        service_ids=service_ids,
        agency_ids=agency_ids,
        stops_rows=stops_rows,
        routes_rows=routes_rows,
        trips_rows=trips_rows,
        agency_rows=agency_rows,
        calendar_rows=calendar_rows,
        calendar_dates_rows=calendar_dates_rows,
    )
