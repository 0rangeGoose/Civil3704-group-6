"""Historical trend storage.

The live view only shows the current instant - to compare "AM Peak" against
"Now" (an explicit proposal goal: congestion "across different periods of
the day"), something has to persist across restarts and accumulate over
real elapsed time. This module logs the already-computed per-cell and
per-route scores (not raw per-bus records - the live view has already done
the expensive work) to a small local SQLite database on a slow interval,
then averages by hour-of-day on request.

The grid cell IDs and route shape IDs are stable across time (same
pipeline.gridmath scheme, same static GTFS shapes), which is what makes
averaging old rows back onto today's live map meaningful - a historical
query returns cells/routes in exactly the shape analytics.congestion_grid()
and analytics.route_line_scores() already produce, so the frontend can feed
either through the same renderer.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from . import config, gridmath

logger = logging.getLogger(__name__)

DB_PATH = config.HISTORY_DB_PATH


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cell_history ("
        "cell_id TEXT, hour INTEGER, day TEXT, score REAL, "
        "vehicle_count INTEGER, recorded_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS route_history ("
        "shape_id TEXT, route_short_name TEXT, hour INTEGER, day TEXT, "
        "score REAL, vehicle_count INTEGER, recorded_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stop_occupancy_history ("
        "stop_id TEXT, vehicle_id TEXT, route_short_name TEXT, mode TEXT, "
        "occupancy TEXT, delay_seconds INTEGER, hour INTEGER, day TEXT, recorded_at REAL)"
    )
    # Migration for a database created before delay_seconds existed on this
    # table - ALTER TABLE ADD COLUMN has no "IF NOT EXISTS" in SQLite, so
    # the usual idiom is to just try it and ignore "duplicate column".
    try:
        conn.execute("ALTER TABLE stop_occupancy_history ADD COLUMN delay_seconds INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cell_hour ON cell_history(hour)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_route_hour ON route_history(hour)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stop_occ_stop ON stop_occupancy_history(stop_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stop_occ_day ON stop_occupancy_history(day)")
    return conn


def log_snapshot(
    cells: List[dict],
    route_lines: Dict[str, dict],
    now: Optional[float] = None,
) -> None:
    """Record the current per-cell and per-route scores as one historical
    reading. Call this on a slow interval (config.HISTORY_LOG_INTERVAL_SECONDS),
    not every live poll cycle."""
    now = now if now is not None else time.time()
    dt = datetime.fromtimestamp(now)
    hour = dt.hour
    day = dt.strftime("%Y-%m-%d")

    conn = _connect()
    try:
        conn.executemany(
            "INSERT INTO cell_history VALUES (?, ?, ?, ?, ?, ?)",
            [
                (c["cell_id"], hour, day, c["score"], c["vehicle_count"], now)
                for c in cells
                if c.get("score") is not None
            ],
        )
        conn.executemany(
            "INSERT INTO route_history VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    shape_id,
                    info.get("route_short_name"),
                    hour,
                    day,
                    info["score"],
                    info["vehicle_count"],
                    now,
                )
                for shape_id, info in route_lines.items()
            ],
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(
        f"History logged: {len(cells)} cell reading(s), {len(route_lines)} "
        f"route reading(s) (hour={hour}, day={day})"
    )


def log_stop_occupancy(visits: List[dict], now: Optional[float] = None) -> None:
    """Record one row per NEW vehicle-at-stop occupancy reading.

    Each dict in `visits` is {stop_id, vehicle_id, route_short_name, mode,
    occupancy, delay_seconds}. `delay_seconds` is the SAME vehicle's lateness
    at the SAME moment - logged alongside occupancy (not derived from it) so
    query_stop_lateness_by_occupancy() can ask "does crowding at this stop
    coincide with lateness" without needing occupancy to change at all. It's
    optional (None when the matching trip_update had no delay) and simply
    excluded from that comparison rather than counted as on-time.

    Unlike log_snapshot (sampled on a slow interval, since a grid/route score
    barely changes cycle to cycle), this is meant to be called every poll
    cycle - but the CALLER (pipeline.run) is responsible for only including a
    vehicle here on the cycle it first becomes near a given stop, not every
    cycle it happens to still be sitting there (a bus can dwell at a stop for
    several poll cycles). Logging every cycle instead of every arrival would
    make "visits" meaningless - a bus that sits for 30s at 5s polling would
    log 6 near-identical rows for what is really one event.

    This is this project's own live-sourced equivalent of TfNSW's day-after
    BOAM export (see pipeline.ingestion.boam_stops): real occupancy, tied to
    a real stop, logged as it actually happens, rather than waiting for a
    batch file someone has to manually download the next day.
    """
    if not visits:
        return
    now = now if now is not None else time.time()
    dt = datetime.fromtimestamp(now)
    hour = dt.hour
    day = dt.strftime("%Y-%m-%d")

    conn = _connect()
    try:
        conn.executemany(
            "INSERT INTO stop_occupancy_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    v["stop_id"],
                    v["vehicle_id"],
                    v.get("route_short_name"),
                    v.get("mode"),
                    v["occupancy"],
                    v.get("delay_seconds"),
                    hour,
                    day,
                    now,
                )
                for v in visits
            ],
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"Stop occupancy logged: {len(visits)} new stop visit(s) (hour={hour}, day={day})")


def query_stop_occupancy_peaks(day: Optional[str] = None) -> Dict[str, dict]:
    """Peak occupancy rank + visit count per stop, for one day - the live,
    self-logged analogue of pipeline.ingestion.boam_stops.load_boam_stops(),
    built from log_stop_occupancy() instead of a downloaded TfNSW file.

    `day`: "YYYY-MM-DD"; defaults to the most recent day logged.

    Returns {stop_id: {"peak_rank": int, "visit_count": int}} - no lat/lon
    (this module doesn't carry stop geometry); join against
    pipeline.ingestion.static_gtfs.StaticGTFS.stops for that, the same way
    callers already have to for nearest_stop_id in the first place.
    """
    if not DB_PATH.exists():
        return {}

    # Imported here, not at module level, to avoid a hard dependency from
    # this module (otherwise dependency-light) on normalise's occupancy
    # vocabulary for every caller that doesn't need stop-occupancy queries.
    from .normalise import OCCUPANCY_RANK

    conn = _connect()
    try:
        if day is None:
            day = conn.execute("SELECT MAX(day) FROM stop_occupancy_history").fetchone()[0]
            if day is None:
                return {}

        rows = conn.execute(
            "SELECT stop_id, occupancy FROM stop_occupancy_history WHERE day = ?", (day,)
        ).fetchall()
    finally:
        conn.close()

    peaks: Dict[str, dict] = {}
    for stop_id, occupancy in rows:
        rank = OCCUPANCY_RANK.get(occupancy)
        if rank is None:
            continue
        entry = peaks.setdefault(stop_id, {"peak_rank": rank, "visit_count": 0})
        entry["visit_count"] += 1
        if rank > entry["peak_rank"]:
            entry["peak_rank"] = rank
    return peaks


def query_stop_lateness_by_occupancy(stop_id: Optional[str] = None, day: Optional[str] = None) -> Dict[str, Dict[int, dict]]:
    """Average lateness at each occupancy level, per stop - the per-stop
    lateness/occupancy comparison a transport planner would actually want:
    does THIS stop's crowding tend to coincide with lateness (a genuine
    bottleneck there - worth a bus lane, signal priority, a timetable
    fix) or are the two decoupled (crowding from demand that has nothing
    to do with how late the bus is)?

    Deliberately NOT a single correlation number - a plain breakdown lets
    you see e.g. "visits at rank 3 here average 240s late, rank 1 visits
    average 20s late" directly, without presupposing a linear relationship
    a real bus stop has no reason to actually have.

    `stop_id`: restrict to one stop; omit for every stop with data.
    `day`: "YYYY-MM-DD"; defaults to the most recent day logged.

    Returns {stop_id: {occupancy_rank: {"avg_delay_seconds": float,
    "visit_count": int}}}. Visits with no delay_seconds logged (the
    matching trip_update had none) are excluded, not counted as on-time.
    """
    if not DB_PATH.exists():
        return {}

    from .normalise import OCCUPANCY_RANK

    conn = _connect()
    try:
        if day is None:
            day = conn.execute("SELECT MAX(day) FROM stop_occupancy_history").fetchone()[0]
            if day is None:
                return {}

        query = (
            "SELECT stop_id, occupancy, delay_seconds FROM stop_occupancy_history "
            "WHERE day = ? AND delay_seconds IS NOT NULL"
        )
        params: Tuple = (day,)
        if stop_id is not None:
            query += " AND stop_id = ?"
            params = (day, stop_id)
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    # stop_id -> rank -> running (sum_delay, count), reduced to an average below
    totals: Dict[str, Dict[int, List[float]]] = {}
    for row_stop_id, occupancy, delay_seconds in rows:
        rank = OCCUPANCY_RANK.get(occupancy)
        if rank is None:
            continue
        by_rank = totals.setdefault(row_stop_id, {})
        sum_count = by_rank.setdefault(rank, [0.0, 0])
        sum_count[0] += delay_seconds
        sum_count[1] += 1

    return {
        row_stop_id: {
            rank: {"avg_delay_seconds": round(sum_delay / count, 1), "visit_count": count}
            for rank, (sum_delay, count) in by_rank.items()
        }
        for row_stop_id, by_rank in totals.items()
    }


def _parse_cell_id(cell_id: str) -> Tuple[int, int]:
    """"r{row}c{col}" -> (row, col) - inverse of the f-string in analytics.congestion_grid."""
    row_part, col_part = cell_id[1:].split("c", 1)
    return int(row_part), int(col_part)


def query_period(
    hours: List[int],
    cell_metres: float = config.CONGESTION_GRID_CELL_METRES,
) -> dict:
    """The most recent day's reading for the given hour-of-day window - not
    blended across every day ever logged. While the database is only a few
    days deep, an average across 2-3 days isn't meaningfully different from
    "the most recent one", and "here's what AM Peak looked like yesterday"
    is a much simpler thing to defend than an averaged blend of days.

    Returns {"congestion": {...same shape as /snapshot's congestion...},
             "route_lines": {...same shape as /snapshot's route_lines...},
             "date": "YYYY-MM-DD", or None if nothing's been logged yet for
             this window} so the frontend can feed the result through the
    exact same renderer it already uses for live data.
    """
    empty = {
        "congestion": {"cells": [], "hotspot_count": 0, "cell_metres": cell_metres},
        "route_lines": {},
        "date": None,
    }
    if not hours or not DB_PATH.exists():
        return empty

    conn = _connect()
    try:
        placeholders = ",".join("?" * len(hours))

        # Determined independently per table: a cycle can legitimately log
        # route readings with zero scored cells (or vice versa), so tying
        # both to cell_history's latest day alone would silently drop a
        # route-only reading.
        latest_cell_day = conn.execute(
            f"SELECT MAX(day) FROM cell_history WHERE hour IN ({placeholders})", hours
        ).fetchone()[0]
        latest_route_day = conn.execute(
            f"SELECT MAX(day) FROM route_history WHERE hour IN ({placeholders})", hours
        ).fetchone()[0]

        if latest_cell_day is None and latest_route_day is None:
            return empty

        cell_rows = []
        if latest_cell_day is not None:
            cell_rows = conn.execute(
                f"SELECT cell_id, AVG(score), SUM(vehicle_count), COUNT(*) "
                f"FROM cell_history WHERE hour IN ({placeholders}) AND day = ? GROUP BY cell_id",
                hours + [latest_cell_day],
            ).fetchall()

        route_rows = []
        if latest_route_day is not None:
            route_rows = conn.execute(
                f"SELECT shape_id, route_short_name, AVG(score), SUM(vehicle_count) "
                f"FROM route_history WHERE hour IN ({placeholders}) AND day = ? GROUP BY shape_id",
                hours + [latest_route_day],
            ).fetchall()
    finally:
        conn.close()

    # Report whichever is more recent as "the" date - if they diverge, the
    # newer one is what the UI should say it's showing.
    latest_day = max(d for d in (latest_cell_day, latest_route_day) if d is not None)

    cells = []
    for cell_id, avg_score, total_vehicles, n_readings in cell_rows:
        row, col = _parse_cell_id(cell_id)
        bounds = gridmath.cell_bounds(row, col, cell_metres)
        is_hotspot = avg_score >= config.CONGESTION_HOTSPOT_THRESHOLD
        cells.append(
            {
                "cell_id": cell_id,
                "bounds": bounds,
                "center": {
                    "lat": round((bounds["min_lat"] + bounds["max_lat"]) / 2, 6),
                    "lon": round((bounds["min_lon"] + bounds["max_lon"]) / 2, 6),
                },
                "score": round(avg_score, 3),
                "vehicle_count": round((total_vehicles or 0) / max(1, n_readings)),
                "routes": [],
                "hotspot": is_hotspot,
                "demand_score": None,
            }
        )
    cells.sort(key=lambda c: c["score"], reverse=True)

    route_lines = {
        shape_id: {
            "route_id": None,
            "route_short_name": route_short_name,
            "score": round(avg_score, 3),
            "vehicle_count": round(total_vehicles or 0),
        }
        for shape_id, route_short_name, avg_score, total_vehicles in route_rows
    }

    return {
        "congestion": {
            "cells": cells,
            "hotspot_count": sum(1 for c in cells if c["hotspot"]),
            "cell_metres": cell_metres,
        },
        "route_lines": route_lines,
        "date": latest_day,
    }
