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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cell_hour ON cell_history(hour)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_route_hour ON route_history(hour)")
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


def _parse_cell_id(cell_id: str) -> Tuple[int, int]:
    """"r{row}c{col}" -> (row, col) - inverse of the f-string in analytics.congestion_grid."""
    row_part, col_part = cell_id[1:].split("c", 1)
    return int(row_part), int(col_part)


def query_period(
    hours: List[int],
    cell_metres: float = config.CONGESTION_GRID_CELL_METRES,
) -> dict:
    """Average every logged reading whose hour-of-day is in `hours`, grouped
    back onto the cells/route shapes the live view uses.

    Returns {"congestion": {...same shape as /snapshot's congestion...},
             "route_lines": {...same shape as /snapshot's route_lines...},
             "sample_days": int}
    so the frontend can feed the result through the exact same renderer it
    already uses for live data. sample_days lets the UI show how much
    history a period is actually based on (honest about a "3 days" average
    vs. a "3 months" one).
    """
    empty = {
        "congestion": {"cells": [], "hotspot_count": 0, "priority_count": 0, "cell_metres": cell_metres},
        "route_lines": {},
        "sample_days": 0,
    }
    if not hours or not DB_PATH.exists():
        return empty

    conn = _connect()
    try:
        placeholders = ",".join("?" * len(hours))
        cell_rows = conn.execute(
            f"SELECT cell_id, AVG(score), SUM(vehicle_count), COUNT(*) "
            f"FROM cell_history WHERE hour IN ({placeholders}) GROUP BY cell_id",
            hours,
        ).fetchall()
        route_rows = conn.execute(
            f"SELECT shape_id, route_short_name, AVG(score), SUM(vehicle_count) "
            f"FROM route_history WHERE hour IN ({placeholders}) GROUP BY shape_id",
            hours,
        ).fetchall()
        sample_days_row = conn.execute(
            f"SELECT COUNT(DISTINCT day) FROM cell_history WHERE hour IN ({placeholders})",
            hours,
        ).fetchone()
    finally:
        conn.close()

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
                "priority_score": None,
                "priority": False,
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
            "priority_count": 0,
            "cell_metres": cell_metres,
        },
        "route_lines": route_lines,
        "sample_days": (sample_days_row[0] if sample_days_row else 0) or 0,
    }
