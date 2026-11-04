"""Congestion and crowding aggregated onto route segments.

Each route shape is cut into fixed-length sections (SEGMENT_M) by distance
along the shape. A map-matched vehicle's reading lands on the section it's
in, so road data follows the streets on the model instead of a square grid.

A section's value is the mean level across the vehicles that reported on it
within the last WINDOW_S seconds -- each vehicle counted once, at its most
recent reading there, so a bus sitting at a red light for four polls doesn't
outvote the three that drove past. Levels are ordinal categories (see
levels.py); the renderer rounds the mean back to a category for colour.

**Pooled along shared streets.** Sections are per *shape*, and several routes
often share one street, each with its own sections there. On their own, most
sections hold a single bus (about 80% at the 4 Nov AM peak, in the CBD as much
as the suburbs), and drawn worst-on-top the street showed its worst single
bus. So each section's value pools every vehicle on sections of the same
street heading the same way: midpoints within POOL_ALONG_M along it and
POOL_ACROSS_M across it (parallel CBD streets are ~100 m apart), headings
within POOL_ANGLE_DEG. The two directions stay separate: inbound and outbound
traffic genuinely differ, and the renderer still draws the worse on top.
Each layer's value comes with how many vehicles it rests on (``*_n``), so the
renderer can show one-vehicle streets as tentative.

Each section also carries a **both-directions** value (``*_all``, ``*_all_n``):
the same pool plus the sections running the other way along the street, for
the renderer's default "one colour per street"; and whether it heads towards
the city centre (``inbound``), for showing one direction only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from engine.normalise.travel_time import Interval
from engine.normalise.levels import congestion_level, crowding_level
from engine.normalise.map_match import ShapeIndex
from engine.normalise.vehicle import VehicleRecord

SEGMENT_M = 200.0
WINDOW_S = 600.0
POOL_ALONG_M = 100.0
POOL_ACROSS_M = 25.0
POOL_ANGLE_DEG = 30.0
_POOL_COS = math.cos(math.radians(POOL_ANGLE_DEG))
CITY_CENTRE = (-33.8732, 151.2068)  # Town Hall: "towards the city" means towards here
_M_PER_DEG = 111_320.0
_KX = _M_PER_DEG * math.cos(math.radians(-33.87))  # one flat plane for the whole footprint

# What each layer measures, and for which modes. Congestion is a road
# measure: TfNSW reports it for Metro too, but a metro line isn't in traffic,
# so only road modes count.
CONGESTION_MODES = {"buses"}


@dataclass
class _Reading:
    at: float
    congestion: int | None
    crowding: int | None


def _same_street(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    """Same street, same way: headings within POOL_ANGLE_DEG, and midpoints
    within POOL_ALONG_M along and POOL_ACROSS_M across it -- measured along
    *both* sections, so the answer doesn't depend on which is asked first."""
    ax, ay, aux, auy = a
    bx, by, bux, buy = b
    if aux * bux + auy * buy < _POOL_COS:
        return False  # crossing street, or the other direction
    dx, dy = bx - ax, by - ay
    return all(abs(dx * ux + dy * uy) <= POOL_ALONG_M and abs(dx * uy - dy * ux) <= POOL_ACROSS_M
               for ux, uy in ((aux, auy), (bux, buy)))


def _reversed(frame: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, ux, uy = frame
    return x, y, -ux, -uy


def heads_to_centre(frame: tuple[float, float, float, float]) -> bool:
    x, y, ux, uy = frame
    cx, cy = CITY_CENTRE[1] * _KX, CITY_CENTRE[0] * _M_PER_DEG
    return (cx - x) * ux + (cy - y) * uy > 0


def _summarise(live: dict, pool: list) -> dict:
    """Mean level and vehicle count per layer over a pool of sections, each
    vehicle once at its latest reading."""
    readings: dict = {}
    derived: dict = {}
    for other in pool:
        for vk, r in live[other][0].items():
            if vk not in readings or r.at > readings[vk].at:
                readings[vk] = r
        for vk, r in live[other][1].items():
            if vk not in derived or r[0] > derived[vk][0]:
                derived[vk] = r
    congestion = [r.congestion for r in readings.values() if r.congestion is not None]
    crowding = [r.crowding for r in readings.values() if r.crowding is not None]
    return {
        "congestion": round(sum(congestion) / len(congestion), 2) if congestion else None,
        "congestion_n": len(congestion),
        "crowding": round(sum(crowding) / len(crowding), 2) if crowding else None,
        "crowding_n": len(crowding),
        "derived": round(sum(r[1] for r in derived.values()) / len(derived), 2) if derived else None,
        "derived_tti": round(sum(r[2] for r in derived.values()) / len(derived), 2) if derived else None,
        "derived_n": len(derived),
        "vehicles": len(readings | derived),
    }


def _halfway(xy: list[tuple[float, float]]) -> tuple[float, float]:
    """The point half-way along a polyline (not its middle vertex: a straight
    section has only its two ends)."""
    steps = [math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(xy, xy[1:])]
    remaining = sum(steps) / 2
    for (x1, y1), (x2, y2), step in zip(xy, xy[1:], steps):
        if step >= remaining and step > 0:
            t = remaining / step
            return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        remaining -= step
    return xy[-1]


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
        # (shape, index) -> (mid_x, mid_y, unit_dx, unit_dy) in metres, or None if too short to have a direction
        self._frames: dict[tuple[str, int], tuple[float, float, float, float] | None] = {}
        # same-street relation between sections, built up as sections are first seen
        self._neighbours: dict[tuple[str, int], list[tuple[str, int]]] = {}
        self._opposite: dict[tuple[str, int], list[tuple[str, int]]] = {}  # same street, other way
        self._grid: dict[tuple[int, int], list[tuple[str, int]]] = {}

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
        [{id, pts, congestion, congestion_n, crowding, crowding_n, derived,
        derived_tti, derived_n, vehicles, <layer>_all, <layer>_all_n, inbound},
        ...] where each value is the mean level (or None) over the vehicles
        pooled along the street (this way; ``_all``: both ways) in the window,
        ``*_n`` how many vehicles it rests on, ``vehicles`` the distinct vehicles
        behind any of the one-way values, and ``inbound`` whether the section
        heads towards CITY_CENTRE."""
        cutoff = now - self._window_s
        live: dict[tuple[str, int], tuple[dict[str, _Reading], dict[str, tuple[float, int, float]]]] = {}
        for key in set(self._readings) | set(self._derived):
            by_vehicle = {k: r for k, r in self._readings.get(key, {}).items() if r.at >= cutoff}
            derived = {k: r for k, r in self._derived.get(key, {}).items() if r[0] >= cutoff}
            for store, kept in ((self._readings, by_vehicle), (self._derived, derived)):
                if kept:
                    store[key] = kept
                else:
                    store.pop(key, None)
            if by_vehicle or derived:
                live[key] = (by_vehicle, derived)

        result = []
        for key, (pool, opposite) in self._pools(live.keys()).items():
            same_way = _summarise(live, pool)
            both_ways = _summarise(live, pool + opposite)
            frame = self._frames.get(key)
            result.append({
                "id": f"{key[0]}:{key[1]}",
                "pts": self._section_geometry(key),
                **same_way,
                "congestion_all": both_ways["congestion"],
                "congestion_all_n": both_ways["congestion_n"],
                "crowding_all": both_ways["crowding"],
                "crowding_all_n": both_ways["crowding_n"],
                "derived_all": both_ways["derived"],
                "derived_all_n": both_ways["derived_n"],
                "inbound": heads_to_centre(frame) if frame else None,
            })
        return result

    def _pools(self, keys):
        """For each section with readings: (the sections, itself included, on
        the same street heading the same way; those heading the other way)."""
        for key in keys:
            self._register(key)
        return {key: ([key] + [o for o in self._neighbours[key] if o in keys],
                      [o for o in self._opposite[key] if o in keys]) for key in keys}

    def _register(self, key: tuple[str, int]) -> None:
        """Works out, once, which known sections share this one's street (street
        geometry never changes), through a grid of POOL_ALONG_M cells; the
        relation is stored both ways so sections seen later join earlier ones."""
        if key in self._neighbours:
            return
        self._neighbours[key] = []
        self._opposite[key] = []
        frame = self._frame(key)
        if frame is None:
            return
        cx, cy = int(frame[0] // POOL_ALONG_M), int(frame[1] // POOL_ALONG_M)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for other in self._grid.get((gx, gy), ()):
                    if _same_street(frame, self._frames[other]):
                        self._neighbours[key].append(other)
                        self._neighbours[other].append(key)
                    elif _same_street(frame, _reversed(self._frames[other])):
                        self._opposite[key].append(other)
                        self._opposite[other].append(key)
        self._grid.setdefault((cx, cy), []).append(key)

    def _frame(self, key: tuple[str, int]) -> tuple[float, float, float, float] | None:
        if key not in self._frames:
            pts = self._section_geometry(key)
            frame = None
            if len(pts) >= 2:
                xy = [(lon * _KX, lat * _M_PER_DEG) for lat, lon in pts]
                dx, dy = xy[-1][0] - xy[0][0], xy[-1][1] - xy[0][1]
                length = math.hypot(dx, dy)
                if length >= 20:
                    frame = (*_halfway(xy), dx / length, dy / length)
            self._frames[key] = frame
        return self._frames[key]

    def _section_geometry(self, key: tuple[str, int]) -> list[list[float]]:
        if key not in self._geometry:
            shape_id, index = key
            start = index * self._segment_m
            length = self._shapes.shape_length_m(shape_id) or start
            pts = self._shapes.section_latlon(shape_id, start, min(start + self._segment_m, length))
            self._geometry[key] = [[round(lat, 6), round(lon, 6)] for lat, lon in pts]
        return self._geometry[key]
