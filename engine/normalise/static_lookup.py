"""Join realtime trip/route ids against the Phase 1 static cache.

Implements the "bus trip ids mutate" quirk from the brief: try an exact
trip_id match first; if that misses, fall back to route_id and mark the
record's confidence as degraded rather than dropping the vehicle. If even
the route can't be resolved -- a trip that's in realtime but not yet in the
static bundle, documented as normal for light rail -- the vehicle is still
forwarded with whatever raw fields the feed gave us, confidence "unmatched".
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RouteInfo:
    route_id: str
    route_short_name: str | None
    route_color: str | None


@dataclass
class JoinResult:
    confidence: str  # "static_join" | "degraded_route_fallback" | "unmatched"
    route: RouteInfo | None
    headsign: str | None


class StaticLookup:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StaticLookup":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def route_by_id(self, route_id: str) -> RouteInfo | None:
        row = self._conn.execute(
            "SELECT route_id, route_short_name, route_color FROM routes WHERE route_id = ?",
            (route_id,),
        ).fetchone()
        if row is None:
            return None
        return RouteInfo(row["route_id"], row["route_short_name"], row["route_color"])

    def shape_for_trip(self, trip_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT shape_id FROM trips WHERE trip_id = ?", (trip_id,)
        ).fetchone()
        return row["shape_id"] if row and row["shape_id"] else None

    def join(self, trip_id: str | None, route_id: str | None) -> JoinResult:
        if trip_id:
            row = self._conn.execute(
                "SELECT trip_id, route_id, trip_headsign FROM trips WHERE trip_id = ?",
                (trip_id,),
            ).fetchone()
            if row is not None:
                route = self.route_by_id(row["route_id"]) if row["route_id"] else None
                return JoinResult("static_join", route, row["trip_headsign"])

        if route_id:
            route = self.route_by_id(route_id)
            if route is not None:
                return JoinResult("degraded_route_fallback", route, None)

        return JoinResult("unmatched", None, None)
