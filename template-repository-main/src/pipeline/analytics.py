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

from . import config, gridmath
from .ingestion.demand import DemandZones
from .ingestion.static_gtfs import StaticGTFS
from .normalise import VehicleRecord

EARTH_RADIUS_M = 6_371_000


def _to_local_xy(lat: float, lon: float, origin_lat: float) -> Tuple[float, float]:
    """Equirectangular projection: good enough over a few km, much cheaper than geodesics."""
    x = math.radians(lon) * math.cos(math.radians(origin_lat)) * EARTH_RADIUS_M
    y = math.radians(lat) * EARTH_RADIUS_M
    return x, y


def _project_onto_shape(
    lat: float, lon: float, shape_points: List[Tuple[float, float]]
) -> Optional[Tuple[float, float, float, float]]:
    """Project (lat, lon) onto the nearest segment of a shape.

    Returns (snapped_lat, snapped_lon, distance_from_point_metres,
    progress_along_shape_metres), or None if the shape has fewer than two
    points to form a segment. `progress` is the cumulative distance from the
    start of the shape to the snapped point - what lets two vehicles on the
    same shape be ordered and have a gap measured between them.
    """
    if len(shape_points) < 2:
        return None

    origin_lat = lat
    px, py = _to_local_xy(lat, lon, origin_lat)

    best_dist_sq = math.inf
    best_point: Optional[Tuple[float, float]] = None
    best_progress = 0.0
    cumulative = 0.0

    for (lat1, lon1), (lat2, lon2) in zip(shape_points, shape_points[1:]):
        x1, y1 = _to_local_xy(lat1, lon1, origin_lat)
        x2, y2 = _to_local_xy(lat2, lon2, origin_lat)

        dx, dy = x2 - x1, y2 - y1
        seg_len = math.hypot(dx, dy)
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
            best_progress = cumulative + t * seg_len

        cumulative += seg_len

    if best_point is None:
        return None
    return best_point[0], best_point[1], math.sqrt(best_dist_sq), best_progress


def snap_to_shape(
    lat: float, lon: float, shape_points: List[Tuple[float, float]]
) -> Optional[Tuple[float, float, float]]:
    """Project (lat, lon) onto the nearest segment of a route shape.

    Returns (snapped_lat, snapped_lon, distance_metres), or None if the
    shape has fewer than two points to form a segment.
    """
    projection = _project_onto_shape(lat, lon, shape_points)
    if projection is None:
        return None
    snapped_lat, snapped_lon, distance_metres, _progress_metres = projection
    return snapped_lat, snapped_lon, distance_metres


def route_bunching_scores(
    records: List[VehicleRecord],
    static: StaticGTFS,
    far_metres: Optional[float] = None,
    close_metres: Optional[float] = None,
) -> Dict[str, float]:
    """Bus bunching: how close each vehicle is to its nearest same-shape
    neighbour, measured along the route (not straight-line) - the classic
    congestion signature transit agencies watch for.

    Vehicles are grouped by shape_id (which already encodes direction in
    GTFS, so inbound/outbound buses on the same route aren't compared to
    each other), projected onto that shape to get their progress along it,
    then scored by the gap to their nearest neighbour in that ordering.

    Returns {vehicle_id: score in [0, 1]} only for vehicles this could be
    computed for - a lone bus on its shape, or a shape with no static data,
    is simply left out (no false "not bunched" signal from an empty gap).
    """
    far_metres = config.BUNCHING_FAR_METRES if far_metres is None else far_metres
    close_metres = config.BUNCHING_CLOSE_METRES if close_metres is None else close_metres
    span = max(1.0, far_metres - close_metres)

    by_shape: Dict[str, list] = defaultdict(list)
    for record in records:
        if record.shape_id:
            by_shape[record.shape_id].append(record)

    scores: Dict[str, float] = {}
    for shape_id, shape_records in by_shape.items():
        if len(shape_records) < 2:
            continue
        shape_points = static.shapes.get(shape_id)
        if not shape_points:
            continue

        positioned = []
        for record in shape_records:
            projection = _project_onto_shape(record.latitude, record.longitude, shape_points)
            if projection is not None:
                positioned.append((projection[3], record))

        if len(positioned) < 2:
            continue

        positioned.sort(key=lambda p: p[0])
        progresses = [p[0] for p in positioned]

        for i, (progress, record) in enumerate(positioned):
            gaps = []
            if i > 0:
                gaps.append(progress - progresses[i - 1])
            if i < len(positioned) - 1:
                gaps.append(progresses[i + 1] - progress)
            nearest_gap = min(gaps)
            bunching = max(0.0, min(1.0, (far_metres - nearest_gap) / span))
            scores[record.vehicle_id] = round(bunching, 3)

    return scores


def vehicle_congestion_score(
    speed_mps: Optional[float],
    delay_seconds: Optional[int],
    bunching_score: Optional[float] = None,
) -> Optional[float]:
    """A [0, 1] congestion proxy for one bus, blending up to three signals:
      - "moving slowly": how far below free-flow speed the bus is
      - "behind schedule": how large its delay is (delays cascade in congestion)
      - "bunched": how close it is to its nearest same-shape neighbour

    Any signal that isn't available is simply left out, and the remaining
    ones are re-weighted proportionally - e.g. with only delay available,
    the score is just the delay term. Returns None if none are available.
    """
    components: List[Tuple[float, float]] = []  # (value, weight)

    if speed_mps is not None and speed_mps >= 0:
        slowness = max(0.0, min(1.0, (config.FREE_FLOW_SPEED_MPS - speed_mps) / config.FREE_FLOW_SPEED_MPS))
        components.append((slowness, config.CONGESTION_SPEED_WEIGHT))

    if delay_seconds is not None:
        delay_term = max(0.0, min(1.0, delay_seconds / config.CONGESTION_DELAY_CAP_SECONDS))
        components.append((delay_term, config.CONGESTION_DELAY_WEIGHT))

    if bunching_score is not None:
        components.append((bunching_score, config.CONGESTION_BUNCHING_WEIGHT))

    if not components:
        return None

    total_weight = sum(weight for _, weight in components)
    score = sum(value * weight for value, weight in components) / total_weight
    return round(score, 3)


def score_vehicles(records: List[VehicleRecord], static: StaticGTFS) -> List[VehicleRecord]:
    """Set `congestion_score` (and `bunching_score`) on each record in place."""
    bunching = route_bunching_scores(records, static)
    for record in records:
        record.bunching_score = bunching.get(record.vehicle_id)
        record.congestion_score = vehicle_congestion_score(
            record.speed, record.delay_seconds, record.bunching_score
        )
    return records


# Grid cell math (steps/key/bounds) lives in pipeline.gridmath, shared with
# ingestion.demand's comprehensive demand grid so the two align on the map.
_cell_key = gridmath.cell_key
_cell_bounds = gridmath.cell_bounds


def congestion_grid(
    records: List[VehicleRecord],
    cell_metres: float = config.CONGESTION_GRID_CELL_METRES,
    hotspot_threshold: float = config.CONGESTION_HOTSPOT_THRESHOLD,
    demand: Optional[DemandZones] = None,
    priority_threshold: float = config.PRIORITY_THRESHOLD,
) -> dict:
    """Aggregate per-vehicle congestion scores onto a fixed grid over the area.

    Each returned cell is a real ~`cell_metres` square with a stable id and
    explicit bounds, plus what it currently holds (bus count and the route
    codes present), so it's clear on the map exactly which patch of the CBD
    a reading covers.

    If `demand` (see pipeline.ingestion.demand) is supplied, each cell also
    gets demand_score (job density where the cell sits, from TZP24) and
    priority_score = congestion x demand - the supply-meets-demand signal
    the proposal is actually about, not just raw congestion. Both are None
    per cell (and priority_count is 0) when demand isn't supplied, e.g.
    because data/demand/ hasn't been populated yet.

    Returns {"cells": [{cell_id, bounds, center, score, vehicle_count,
                        routes, hotspot, demand_score, priority_score,
                        priority}, ...],
             "hotspot_count": int, "priority_count": int, "cell_metres": float}
    """
    buckets: Dict[Tuple[int, int], list] = defaultdict(list)
    for record in records:
        if record.congestion_score is None:
            continue
        buckets[_cell_key(record.latitude, record.longitude, cell_metres)].append(record)

    cells = []
    hotspots = 0
    priority_zones = 0
    for (row, col), members in buckets.items():
        scores = [m.congestion_score for m in members]
        mean_score = round(sum(scores) / len(scores), 3)
        bounds = _cell_bounds(row, col, cell_metres)
        center = {
            "lat": round((bounds["min_lat"] + bounds["max_lat"]) / 2, 6),
            "lon": round((bounds["min_lon"] + bounds["max_lon"]) / 2, 6),
        }
        routes = sorted({(m.route_short_name or m.route_id or "?") for m in members})
        is_hotspot = (
            mean_score >= hotspot_threshold
            and len(members) >= config.CONGESTION_HOTSPOT_MIN_VEHICLES
        )
        if is_hotspot:
            hotspots += 1

        demand_score = demand.demand_score(center["lat"], center["lon"]) if demand else None
        priority_score = round(mean_score * demand_score, 3) if demand_score is not None else None
        is_priority = priority_score is not None and priority_score >= priority_threshold
        if is_priority:
            priority_zones += 1

        cells.append(
            {
                "cell_id": f"r{row}c{col}",
                "bounds": bounds,
                "center": center,
                "score": mean_score,
                "vehicle_count": len(members),
                "routes": routes,
                "hotspot": is_hotspot,
                "demand_score": demand_score,
                "priority_score": priority_score,
                "priority": is_priority,
            }
        )

    cells.sort(key=lambda c: c["score"], reverse=True)
    return {
        "cells": cells,
        "hotspot_count": hotspots,
        "priority_count": priority_zones,
        "cell_metres": cell_metres,
    }


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
