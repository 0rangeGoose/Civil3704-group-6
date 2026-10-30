"""Congestion and crowding aggregated onto route segments.

Each route shape is cut into fixed-length sections (SEGMENT_M) by distance
along the shape. A map-matched vehicle's reading lands on the section it's
in, so road data follows the streets on the model instead of a square grid.

A section's value is the mean level across the vehicles that reported on it
within the last WINDOW_S seconds -- each vehicle counted once, at its most
recent reading there, so a bus sitting at a red light for four polls doesn't
outvote the three that drove past. Levels are ordinal categories (see
levels.py); the renderer rounds the mean back to a category for colour.

Known limitation: sections are per *shape*, and several bus routes (and both
directions) often share one street, so their sections are drawn on top of
each other. The renderer draws worst-on-top, so a congested street still
reads as congested.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.normalise.travel_time import Interval
from engine.normalise.levels import congestion_level, crowding_level
from engine.normalise.map_match import ShapeIndex
from engine.normalise.vehicle import VehicleRecord

SEGMENT_M = 200.0
WINDOW_S = 600.0

# What each layer measures, and for which modes. Congestion is a road
# measure: TfNSW reports it for Metro too, but a metro line isn't in traffic,
# so only road modes count.
CONGESTION_MODES = {"buses"}


@dataclass
class _Reading:
    at: float
    congestion: int | None
    crowding: int | None


class SegmentAggregator:
    def __init__(self, shapes: ShapeIndex, segment_m: float = SEGMENT_M, window_s: float = WINDOW_S):
        self._shapes = shapes
        self._segment_m = segment_m
        self._window_s = window_s
        # (shape_id, index) -> vehicle_key -> latest reading on that section
        self._readings: dict[tuple[str, int], dict[str, _Reading]] = {}
        # Our derived measure (engine/normalise/travel_time.py): (shape, index) ->
        # vehicle -> (at, level, travel-time index), attributed at each interval's midpoint
        self._derived: dict[tuple[str, int], dict[str, tuple[float, int, float]]] = {}
        self._geometry: dict[tuple[str, int], list[list[float]]] = {}

    def record(self, vehicle: VehicleRecord, at: float) -> None:
        if vehicle.shape_id is None or vehicle.distance_along_shape_m is None:
            return
        congestion = congestion_level(vehicle.congestion_level) if vehicle.mode in CONGESTION_MODES else None
        crowding = crowding_level(vehicle.occupancy_status)
        if congestion is None and crowding is None:
            return
        key = (vehicle.shape_id, int(vehicle.distance_along_shape_m // self._segment_m))
        self._readings.setdefault(key, {})[vehicle.vehicle_key] = _Reading(at, congestion, crowding)

    def record_derived(self, interval: Interval) -> None:
        if interval.level is None:
            return  # no free-flow baseline for this street
        key = (interval.shape_id, int(interval.mid_m // self._segment_m))
        self._derived.setdefault(key, {})[interval.vehicle_key] = (interval.at, interval.level, interval.tti)

    def snapshot(self, now: float) -> list[dict]:
        """Sections with at least one current reading:
        [{id, pts, congestion, crowding, vehicles}, ...] where
        congestion/crowding are mean levels (or None) over the window."""
        cutoff = now - self._window_s
        result = []
        for key in set(self._readings) | set(self._derived):
            by_vehicle = {k: r for k, r in self._readings.get(key, {}).items() if r.at >= cutoff}
            derived = {k: r for k, r in self._derived.get(key, {}).items() if r[0] >= cutoff}
            for store, kept in ((self._readings, by_vehicle), (self._derived, derived)):
                if kept:
                    store[key] = kept
                else:
                    store.pop(key, None)
            if not by_vehicle and not derived:
                continue
            congestion = [r.congestion for r in by_vehicle.values() if r.congestion is not None]
            crowding = [r.crowding for r in by_vehicle.values() if r.crowding is not None]
            result.append({
                "id": f"{key[0]}:{key[1]}",
                "pts": self._section_geometry(key),
                "congestion": round(sum(congestion) / len(congestion), 2) if congestion else None,
                "crowding": round(sum(crowding) / len(crowding), 2) if crowding else None,
                "derived": round(sum(r[1] for r in derived.values()) / len(derived), 2) if derived else None,
                "derived_tti": round(sum(r[2] for r in derived.values()) / len(derived), 2) if derived else None,
                "vehicles": len(by_vehicle | derived),
            })
        return result

    def _section_geometry(self, key: tuple[str, int]) -> list[list[float]]:
        if key not in self._geometry:
            shape_id, index = key
            start = index * self._segment_m
            length = self._shapes.shape_length_m(shape_id) or start
            pts = self._shapes.section_latlon(shape_id, start, min(start + self._segment_m, length))
            self._geometry[key] = [[round(lat, 6), round(lon, 6)] for lat, lon in pts]
        return self._geometry[key]
