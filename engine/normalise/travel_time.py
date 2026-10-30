"""Our own congestion measure: a travel-time index against each street's free flow.

TfNSW's ``congestion_level`` is shown as published but its method isn't
public. This is a measure we can explain, and step 6 compares the two.

For each bus (any bus placed on a route shape -- no timetable needed):

1. Its position is a distance along the shape (map matching).
2. Once it has moved at least MIN_INTERVAL_M since the interval started, its
   average speed over that stretch is one reading. Stops and lights are
   included on purpose: the free-flow speed below includes them too, so they
   cancel out.
3. Travel-time index (TTI) = free-flow speed / this speed. 1.0 = as fast as
   the street at night; 2.0 = taking twice as long.
4. The reading is attributed to the 200m segment at the stretch's midpoint.

**Free-flow speed** is the 85th-percentile speed of the same kind of reading,
from our own recordings at night (22:00-05:00), per 200m segment -- or, where
a segment has too few night readings, per route shape. Built by
scripts/build_freeflow.py into data/analysis/freeflow.json. A street with no
baseline gets no TTI (shown grey).

Why not delay against the timetable (the first method tried)? Bus timetables
are padded: buses run ahead and wait at timing points, so on a recorded AM
peak the median bus "gained" 3-5 s per 100m even where TfNSW reported severe
congestion, and the measure barely separated smooth from congested streets.
See docs/DECISIONS.md, 30 Oct.

Readings become levels 0-3 with config thresholds
(``derived_congestion.thresholds_tti``) so they can be compared with TfNSW's
four levels.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from engine.normalise.vehicle import VehicleRecord

MIN_INTERVAL_M = 300.0
SEGMENT_M = 200.0
STILL_M = 5.0
MAX_GAP_S = 180.0
MIN_SPEED_MPS = 0.3       # below this a reading is a parked bus, not traffic
MAX_TTI = 10.0
DEFAULT_THRESHOLDS = (1.5, 2.0, 3.0)   # TTI: <=1.5 smooth, <=2 slow, <=3 congested, else severe


@dataclass
class Interval:
    vehicle_key: str
    shape_id: str
    start_m: float
    end_m: float
    speed_mps: float
    tti: float | None
    level: int | None
    at: float

    @property
    def mid_m(self) -> float:
        return (self.start_m + self.end_m) / 2

    @property
    def segment(self) -> tuple[str, int]:
        return (self.shape_id, int(self.mid_m // SEGMENT_M))


@dataclass
class _Anchor:
    shape_id: str
    distance_m: float
    ts: float
    last_distance_m: float
    last_ts: float


def level_for(tti: float, thresholds=DEFAULT_THRESHOLDS) -> int:
    return sum(tti > t for t in thresholds)


class FreeFlow:
    """Night-time 85th-percentile speeds per segment, with a per-shape fallback."""

    def __init__(self, segments: dict[str, float], shapes: dict[str, float], meta: dict | None = None):
        self.segments = segments
        self.shapes = shapes
        self.meta = meta or {}

    @classmethod
    def load(cls, path: Path) -> "FreeFlow | None":
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return cls({k: v[0] for k, v in data["segments"].items()},
                   {k: v[0] for k, v in data["shapes"].items()}, data.get("meta"))

    def speed(self, shape_id: str, segment_index: int) -> float | None:
        return self.segments.get(f"{shape_id}:{segment_index}") or self.shapes.get(shape_id)


class TravelTimeTracker:
    def __init__(self, freeflow: FreeFlow | None = None, thresholds=DEFAULT_THRESHOLDS,
                 min_interval_m: float = MIN_INTERVAL_M):
        self.freeflow = freeflow
        self.thresholds = tuple(thresholds)
        self.min_interval_m = min_interval_m
        self._anchors: dict[str, _Anchor] = {}

    def reset(self) -> None:
        self._anchors.clear()

    def observe(self, v: VehicleRecord) -> Interval | None:
        """Feed every enriched record; returns an Interval when one closes."""
        if v.mode != "buses" or not v.shape_id or v.distance_along_shape_m is None or not v.position_timestamp:
            return None
        d, ts = v.distance_along_shape_m, float(v.position_timestamp)
        a = self._anchors.get(v.vehicle_key)
        if a is None or a.shape_id != v.shape_id or ts - a.last_ts > MAX_GAP_S or d < a.last_distance_m - STILL_M:
            self._anchors[v.vehicle_key] = _Anchor(v.shape_id, d, ts, d, ts)
            return None
        if ts <= a.last_ts:
            return None  # the same report seen again
        a.last_distance_m, a.last_ts = max(a.last_distance_m, d), ts
        if d - a.distance_m < self.min_interval_m:
            return None
        self._anchors[v.vehicle_key] = _Anchor(v.shape_id, d, ts, d, ts)
        speed = (d - a.distance_m) / (ts - a.ts)
        if speed < MIN_SPEED_MPS:
            return None
        interval = Interval(v.vehicle_key, v.shape_id, a.distance_m, d, round(speed, 2), None, None, ts)
        free = self.freeflow.speed(*interval.segment) if self.freeflow else None
        if free:
            interval.tti = round(min(MAX_TTI, free / speed), 2)
            interval.level = level_for(interval.tti, self.thresholds)
        return interval
