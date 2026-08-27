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
