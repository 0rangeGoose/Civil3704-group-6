"""The rolling aggregate layer: adherence mix by mode, interchange activity,
active service alerts intersecting the CBD.

Per-vehicle classification (adherence.py) is instantaneous, one poll at a
time. This module is what turns a stream of those into "percentage of
tracked services on time, by mode, over the last 5/15/60 minutes" -- the
brief's aggregate layer.
"""
from __future__ import annotations

import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import Point

from engine.normalise.decode import DecodeError, parse_feed_message
from engine.normalise.projection import to_metric
from engine.normalise.proto import pb

DEFAULT_WINDOWS_SECONDS = (300, 900, 3600)  # 5, 15, 60 minutes


@dataclass
class _Observation:
    at: float
    mode: str
    adherence_state: str


class RollingAdherence:
    """Percentage of tracked services in each adherence state, by mode,
    over trailing windows (default 5/15/60 min)."""

    def __init__(self, windows_seconds: tuple[int, ...] = DEFAULT_WINDOWS_SECONDS):
        self._windows = windows_seconds
        self._obs: deque[_Observation] = deque()

    def record(self, mode: str, adherence_state: str, at: float | None = None) -> None:
        ts = at if at is not None else time.time()
        self._obs.append(_Observation(at=ts, mode=mode, adherence_state=adherence_state))
        # Pruned relative to the observation's own timestamp (not wall clock)
        # so a caller feeding synthetic/replayed timestamps still gets
        # correct, boundedly-sized state.
        self._prune(ts)

    def _prune(self, now: float) -> None:
        cutoff = now - max(self._windows)
        while self._obs and self._obs[0].at < cutoff:
            self._obs.popleft()

    def breakdown(self, window_seconds: int, now: float | None = None) -> dict[str, dict[str, float]]:
        """{mode: {state: percentage}} over the trailing window."""
        now = now if now is not None else time.time()
        cutoff = now - window_seconds
        counts: dict[str, dict[str, int]] = {}
        for obs in self._obs:
            if obs.at < cutoff:
                continue
            counts.setdefault(obs.mode, {}).setdefault(obs.adherence_state, 0)
            counts[obs.mode][obs.adherence_state] += 1

        result: dict[str, dict[str, float]] = {}
        for mode, state_counts in counts.items():
            total = sum(state_counts.values())
            result[mode] = {state: round(100 * n / total, 1) for state, n in state_counts.items()}
        return result

    def breakdown_all_windows(self, now: float | None = None) -> dict[int, dict[str, dict[str, float]]]:
        now = now if now is not None else time.time()
        return {w: self.breakdown(w, now) for w in self._windows}


@dataclass
class Interchange:
    name: str
    lat: float
    lon: float


def load_interchanges(db_path: Path, names: list[str]) -> list[Interchange]:
    """Centroid of every stop whose name contains the interchange name --
    the static cache has no single "station" row, just per-platform/stand
    stops (e.g. "Central Station, Railway Square, Stand J")."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = []
        for name in names:
            rows = conn.execute(
                "SELECT stop_lat, stop_lon FROM stops WHERE stop_name LIKE ?", (f"%{name}%",)
            ).fetchall()
            if not rows:
                continue
            lat = sum(r[0] for r in rows) / len(rows)
            lon = sum(r[1] for r in rows) / len(rows)
            result.append(Interchange(name=name, lat=lat, lon=lon))
        return result
    finally:
        conn.close()


def count_services_near_interchanges(
    interchanges: list[Interchange],
    vehicle_positions: list[tuple[float, float]],
    radius_m: float = 300,
) -> dict[str, int]:
    interchange_points = [(ic.name, Point(to_metric(ic.lat, ic.lon))) for ic in interchanges]
    counts = {name: 0 for name, _ in interchange_points}
    for lat, lon in vehicle_positions:
        p = Point(to_metric(lat, lon))
        for name, ic_point in interchange_points:
            if p.distance(ic_point) <= radius_m:
                counts[name] += 1
    return counts


def load_cbd_ids(db_path: Path) -> tuple[set[str], set[str]]:
    """(route_ids, stop_ids) already retained in the Phase 1 CBD-filtered cache."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        route_ids = {r[0] for r in conn.execute("SELECT route_id FROM routes")}
        stop_ids = {r[0] for r in conn.execute("SELECT stop_id FROM stops")}
        return route_ids, stop_ids
    finally:
        conn.close()


def _alert_intersects_cbd(entity, cbd_route_ids: set[str], cbd_stop_ids: set[str]) -> bool:
    for sel in entity.alert.informed_entity:
        if sel.HasField("route_id") and sel.route_id in cbd_route_ids:
            return True
        if sel.HasField("stop_id") and sel.stop_id in cbd_stop_ids:
            return True
        if sel.HasField("trip") and sel.trip.HasField("route_id") and sel.trip.route_id in cbd_route_ids:
            return True
    return False


def count_active_alerts(payload: bytes, cbd_route_ids: set[str], cbd_stop_ids: set[str]) -> dict:
    """Count of active Service Alerts intersecting the bounding box's routes/stops,
    with severity and affected routes -- per the brief's aggregate layer."""
    try:
        feed = parse_feed_message(payload)
    except DecodeError:
        return {"count": 0, "by_severity": {}, "route_ids": []}

    matched = [
        e for e in feed.entity if e.HasField("alert") and _alert_intersects_cbd(e, cbd_route_ids, cbd_stop_ids)
    ]
    by_severity: dict[str, int] = {}
    route_ids: set[str] = set()
    for e in matched:
        severity = (
            pb.Alert.SeverityLevel.Name(e.alert.severity_level)
            if e.alert.HasField("severity_level")
            else "UNKNOWN_SEVERITY"
        )
        by_severity[severity] = by_severity.get(severity, 0) + 1
        for sel in e.alert.informed_entity:
            if sel.HasField("route_id"):
                route_ids.add(sel.route_id)

    return {"count": len(matched), "by_severity": by_severity, "route_ids": sorted(route_ids)}
