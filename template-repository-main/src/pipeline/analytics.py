"""Analytics layer: map matching, adherence scoring, rolling aggregates.

Kept dependency-light on purpose (no shapely) - the CBD bounding box is
small enough that an equirectangular projection is accurate to a few
centimetres, which is well within GPS noise, so plain trigonometry is fine
for snapping vehicles onto their route shape.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import config
from .normalise import VehicleRecord

EARTH_RADIUS_M = 6_371_000


def _to_local_xy(lat: float, lon: float, origin_lat: float) -> Tuple[float, float]:
    """Equirectangular projection: good enough over a few km, much cheaper than geodesics."""
    x = math.radians(lon) * math.cos(math.radians(origin_lat)) * EARTH_RADIUS_M
    y = math.radians(lat) * EARTH_RADIUS_M
    return x, y


def snap_to_shape(
    lat: float, lon: float, shape_points: List[Tuple[float, float]]
) -> Optional[Tuple[float, float, float]]:
    """Project (lat, lon) onto the nearest segment of a route shape.

    Returns (snapped_lat, snapped_lon, distance_metres), or None if the
    shape has fewer than two points to form a segment.
    """
    if len(shape_points) < 2:
        return None

    origin_lat = lat
    px, py = _to_local_xy(lat, lon, origin_lat)

    best_dist_sq = math.inf
    best_point: Optional[Tuple[float, float]] = None

    for (lat1, lon1), (lat2, lon2) in zip(shape_points, shape_points[1:]):
        x1, y1 = _to_local_xy(lat1, lon1, origin_lat)
        x2, y2 = _to_local_xy(lat2, lon2, origin_lat)

        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg_len_sq))

        proj_x, proj_y = x1 + t * dx, y1 + t * dy
        dist_sq = (px - proj_x) ** 2 + (py - proj_y) ** 2
        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            # Convert the projected point back to lat/lon via linear
            # interpolation - accurate enough given the local projection.
            best_point = (lat1 + t * (lat2 - lat1), lon1 + t * (lon2 - lon1))

    if best_point is None:
        return None
    return best_point[0], best_point[1], math.sqrt(best_dist_sq)


def vehicle_congestion_score(
    speed_mps: Optional[float], delay_seconds: Optional[int]
) -> Optional[float]:
    """A [0, 1] congestion proxy for one bus.

    Combines two signals:
      - "moving slowly": how far below free-flow speed the bus is
      - "behind schedule": how large its delay is (delays cascade in congestion)

    Returns None when neither signal is available (no speed and no delay).
    """
    slowness = None
    if speed_mps is not None and speed_mps >= 0:
        slowness = max(0.0, min(1.0, (config.FREE_FLOW_SPEED_MPS - speed_mps) / config.FREE_FLOW_SPEED_MPS))

    delay_term = None
    if delay_seconds is not None:
        delay_term = max(0.0, min(1.0, delay_seconds / config.CONGESTION_DELAY_CAP_SECONDS))

    if slowness is None and delay_term is None:
        return None
    if slowness is None:
        return round(delay_term, 3)
    if delay_term is None:
        return round(slowness, 3)

    score = (
        config.CONGESTION_SPEED_WEIGHT * slowness
        + config.CONGESTION_DELAY_WEIGHT * delay_term
    )
    return round(score, 3)


def score_vehicles(records: List[VehicleRecord]) -> List[VehicleRecord]:
    """Set `congestion_score` on each record in place, and return the list."""
    for record in records:
        record.congestion_score = vehicle_congestion_score(record.speed, record.delay_seconds)
    return records


# Reference latitude for the grid's longitude spacing, so every cell is the
# same real-world size and a (row, col) key maps back to a fixed rectangle.
_GRID_REF_LAT = (config.CBD_BBOX["min_lat"] + config.CBD_BBOX["max_lat"]) / 2


def _grid_steps(cell_metres: float) -> Tuple[float, float]:
    lat_step = cell_metres / 111_320.0
    lon_step = cell_metres / (111_320.0 * math.cos(math.radians(_GRID_REF_LAT)))
    return lat_step, lon_step


def _cell_key(lat: float, lon: float, cell_metres: float) -> Tuple[int, int]:
    lat_step, lon_step = _grid_steps(cell_metres)
    return (int(math.floor(lat / lat_step)), int(math.floor(lon / lon_step)))


def _cell_bounds(row: int, col: int, cell_metres: float) -> dict:
    lat_step, lon_step = _grid_steps(cell_metres)
    return {
        "min_lat": round(row * lat_step, 6),
        "max_lat": round((row + 1) * lat_step, 6),
        "min_lon": round(col * lon_step, 6),
        "max_lon": round((col + 1) * lon_step, 6),
    }


def congestion_grid(
    records: List[VehicleRecord],
    cell_metres: float = config.CONGESTION_GRID_CELL_METRES,
    hotspot_threshold: float = config.CONGESTION_HOTSPOT_THRESHOLD,
) -> dict:
    """Aggregate per-vehicle congestion scores onto a fixed grid over the area.

    Each returned cell is a real ~`cell_metres` square with a stable id and
    explicit bounds, plus what it currently holds (bus count and the route
    codes present), so it's clear on the map exactly which patch of the CBD
    a reading covers.

    Returns {"cells": [{cell_id, bounds, center, score, vehicle_count,
                        routes, hotspot}, ...],
             "hotspot_count": int, "cell_metres": float}
    """
    buckets: Dict[Tuple[int, int], list] = defaultdict(list)
    for record in records:
        if record.congestion_score is None:
            continue
        buckets[_cell_key(record.latitude, record.longitude, cell_metres)].append(record)

    cells = []
    hotspots = 0
    for (row, col), members in buckets.items():
        scores = [m.congestion_score for m in members]
        mean_score = round(sum(scores) / len(scores), 3)
        bounds = _cell_bounds(row, col, cell_metres)
        routes = sorted({(m.route_short_name or m.route_id or "?") for m in members})
        is_hotspot = (
            mean_score >= hotspot_threshold
            and len(members) >= config.CONGESTION_HOTSPOT_MIN_VEHICLES
        )
        if is_hotspot:
            hotspots += 1

        cells.append(
            {
                "cell_id": f"r{row}c{col}",
                "bounds": bounds,
                "center": {
                    "lat": round((bounds["min_lat"] + bounds["max_lat"]) / 2, 6),
                    "lon": round((bounds["min_lon"] + bounds["max_lon"]) / 2, 6),
                },
                "score": mean_score,
                "vehicle_count": len(members),
                "routes": routes,
                "hotspot": is_hotspot,
            }
        )

    cells.sort(key=lambda c: c["score"], reverse=True)
    return {"cells": cells, "hotspot_count": hotspots, "cell_metres": cell_metres}


def classify_adherence(delay_seconds: Optional[int], cancelled: bool) -> str:
    """on_time / late / very_late / cancelled / unknown"""
    if cancelled:
        return "cancelled"
    if delay_seconds is None:
        return "unknown"
    if delay_seconds <= config.ADHERENCE_THRESHOLDS["on_time"]:
        return "on_time"
    if delay_seconds <= config.ADHERENCE_THRESHOLDS["late"]:
        return "late"
    return "very_late"


@dataclass
class RouteAggregate:
    sample_count: int
    on_time_pct: float
    late_pct: float
    very_late_pct: float
    cancelled_count: int


class RollingAggregator:
    """Maintains a rolling window of adherence classifications per route."""

    def __init__(self, window_size: int = config.ROLLING_WINDOW_SIZE):
        self.window_size = window_size
        self._history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))

    def update(self, records: List[VehicleRecord]) -> None:
        for record in records:
            route_key = record.route_id or "unknown_route"
            status = classify_adherence(record.delay_seconds, record.cancelled)
            self._history[route_key].append(status)

    def snapshot(self) -> Dict[str, RouteAggregate]:
        result = {}
        for route_id, history in self._history.items():
            if not history:
                continue
            n = len(history)
            on_time = sum(1 for s in history if s == "on_time")
            late = sum(1 for s in history if s == "late")
            very_late = sum(1 for s in history if s == "very_late")
            cancelled = sum(1 for s in history if s == "cancelled")
            result[route_id] = RouteAggregate(
                sample_count=n,
                on_time_pct=round(100 * on_time / n, 1),
                late_pct=round(100 * late / n, 1),
                very_late_pct=round(100 * very_late / n, 1),
                cancelled_count=cancelled,
            )
        return result
