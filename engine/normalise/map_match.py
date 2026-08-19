"""Snap a vehicle's raw position onto its route shape.

Raw GPS wanders off the corridor and looks wrong on a physical model where a
train or bus should sit in a rail/street channel -- snapping is not optional,
per the brief. Also provides a nearest-shape fallback for vehicles whose
route didn't resolve via the static id join (the sydneytrains/metro/CBD
light rail quirk found in Phase 2): geometric matching doesn't care what the
realtime feed calls the route, only where the vehicle actually is.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from engine.normalise.projection import to_metric, to_wgs84


@dataclass
class MapMatch:
    matched_lat: float
    matched_lon: float
    distance_along_shape_m: float
    distance_from_shape_m: float


class ShapeIndex:
    def __init__(self, db_path: Path):
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            self._shapes: dict[str, LineString] = self._load_shapes(conn)
            self._shape_route: dict[str, str] = self._load_shape_routes(conn)
        finally:
            conn.close()

        self._shape_ids = list(self._shapes.keys())
        self._tree = STRtree([self._shapes[sid] for sid in self._shape_ids])

    @staticmethod
    def _load_shapes(conn: sqlite3.Connection) -> dict[str, LineString]:
        points: dict[str, list[tuple[int, float, float]]] = {}
        for shape_id, seq, lat, lon in conn.execute(
            "SELECT shape_id, shape_pt_sequence, shape_pt_lat, shape_pt_lon FROM shapes"
        ):
            points.setdefault(shape_id, []).append((seq, lat, lon))

        shapes: dict[str, LineString] = {}
        for shape_id, pts in points.items():
            pts.sort(key=lambda p: p[0])
            coords = [to_metric(lat, lon) for _, lat, lon in pts]
            if len(coords) >= 2:
                shapes[shape_id] = LineString(coords)
        return shapes

    @staticmethod
    def _load_shape_routes(conn: sqlite3.Connection) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for shape_id, route_id in conn.execute(
            "SELECT shape_id, route_id FROM trips WHERE shape_id IS NOT NULL AND route_id IS NOT NULL"
        ):
            mapping.setdefault(shape_id, route_id)  # first trip's route wins, ties are rare
        return mapping

    def route_for_shape(self, shape_id: str) -> str | None:
        return self._shape_route.get(shape_id)

    def snap_to_shape(self, shape_id: str, lat: float, lon: float) -> MapMatch | None:
        line = self._shapes.get(shape_id)
        if line is None:
            return None
        return self._snap(line, lat, lon)

    def nearest_shape(self, lat: float, lon: float, max_distance_m: float) -> tuple[str, MapMatch] | None:
        """Geometric fallback when the feed's route/trip id didn't resolve."""
        if not self._shape_ids:
            return None
        point = Point(to_metric(lat, lon))
        idx = self._tree.nearest(point)
        shape_id = self._shape_ids[idx]
        match = self._snap(self._shapes[shape_id], lat, lon)
        if match.distance_from_shape_m > max_distance_m:
            return None
        return shape_id, match

    @staticmethod
    def _snap(line: LineString, lat: float, lon: float) -> MapMatch:
        point = Point(to_metric(lat, lon))
        distance_along = line.project(point)
        nearest = line.interpolate(distance_along)
        matched_lat, matched_lon = to_wgs84(nearest.x, nearest.y)
        return MapMatch(
            matched_lat=matched_lat,
            matched_lon=matched_lon,
            distance_along_shape_m=distance_along,
            distance_from_shape_m=point.distance(nearest),
        )
