"""Routes a viewer can single out on the model, keyed by route_short_name.

The short name ("333", "T1", "L2") is what the vehicle frames already carry
and what people know a route by -- several GTFS route_ids can share one (T1
has two), so they're grouped. A route's detail is every shape its trips use
(to draw the line) and every stop its trips serve (to mark them).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def load_route_catalog(db_path: Path) -> list[dict]:
    """[{"name", "long_name", "color"}] sorted by name, one per short name."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT route_short_name, route_long_name, route_color FROM routes "
            "WHERE route_short_name IS NOT NULL AND route_short_name != '' ORDER BY route_short_name, route_id"
        ).fetchall()
    finally:
        conn.close()
    catalog: dict[str, dict] = {}
    for name, long_name, color in rows:
        catalog.setdefault(name, {"name": name, "long_name": long_name or "", "color": color or None})
    return list(catalog.values())


def route_detail(db_path: Path, name: str) -> dict | None:
    """{"name", "color", "shape_ids", "stop_ids"} for one short name, or None."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        routes = conn.execute(
            "SELECT route_id, route_color FROM routes WHERE route_short_name = ?", (name,)
        ).fetchall()
        if not routes:
            return None
        route_ids = [r[0] for r in routes]
        marks = ",".join("?" * len(route_ids))
        shape_ids = [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT shape_id FROM trips WHERE route_id IN ({marks}) AND shape_id IS NOT NULL", route_ids
            )
        ]
        stop_ids = [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT st.stop_id FROM trips t JOIN stop_times st ON st.trip_id = t.trip_id "
                f"WHERE t.route_id IN ({marks})", route_ids
            )
        ]
    finally:
        conn.close()
    color = next((c for _, c in routes if c), None)
    return {"name": name, "color": color, "shape_ids": shape_ids, "stop_ids": stop_ids}


def stop_routes(db_path: Path) -> dict[str, list[str]]:
    """{stop_id: [route short names serving it]} for every stop in the footprint,
    for highlighting a stop's routes on hover. One pass over the timetable (~2.5 s)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT st.stop_id, r.route_short_name FROM stop_times st "
            "JOIN trips t ON t.trip_id = st.trip_id JOIN routes r ON r.route_id = t.route_id "
            "WHERE st.stop_id IN (SELECT stop_id FROM stops) AND r.route_short_name != ''"
        ).fetchall()
    finally:
        conn.close()
    result: dict[str, list[str]] = {}
    for stop_id, name in rows:
        result.setdefault(stop_id, []).append(name)
    return {stop_id: sorted(names) for stop_id, names in result.items()}
