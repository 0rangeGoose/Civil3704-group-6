"""Persists occupancy + lateness readings at the moment a vehicle passes a stop.

Per the brief ("Anthony notes" in the team's CIVL3704 group notes): keep
occupancy and lateness as two separate recorded numbers per stop visit, not
blended into a single congestion score -- the comparison itself (does high
occupancy coincide with high lateness here, or not) is the point, and
blending them into one number throws that question away.

One SQLite table, one row per stop visit -- see stop_tracker.py for what
counts as a visit (the trip's next_stop_id changing, i.e. it was just
passed) and what "the reading at that stop" means (the last occupancy/delay
seen while that stop was still next).

`db_path` is passed explicitly rather than read from Config on every call,
since this is written to far more often than config is reloaded (once per
real stop visit, not once per poll) and it keeps tests simple (pass a
tmp_path, no Config object needed) -- `default_db_path()` resolves the real
path once, the same way callers already do for engine.demand.boam's cache.
"""
from __future__ import annotations

import sqlite3
import statistics
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.config import Config
from engine.normalise.levels import crowding_level

SYDNEY = ZoneInfo("Australia/Sydney")


def default_db_path(cfg: Config) -> Path:
    return cfg.root / "data" / "history" / "stop_visits.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stop_visits ("
        "stop_id TEXT, vehicle_key TEXT, route_short_name TEXT, mode TEXT, "
        "occupancy_status TEXT, delay_seconds INTEGER, day TEXT, recorded_at REAL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stop_visits_stop ON stop_visits(stop_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stop_visits_day ON stop_visits(day)")
    return conn


def record_stop_visit(
    db_path: Path,
    stop_id: str,
    vehicle_key: str,
    route_short_name: str | None,
    mode: str,
    occupancy_status: str | None,
    delay_seconds: int | None,
    recorded_at: float | None = None,
) -> None:
    recorded_at = recorded_at if recorded_at is not None else time.time()
    day = datetime.fromtimestamp(recorded_at, tz=SYDNEY).strftime("%Y-%m-%d")
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO stop_visits VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (stop_id, vehicle_key, route_short_name, mode, occupancy_status, delay_seconds, day, recorded_at),
        )
        conn.commit()
    finally:
        conn.close()


def stop_breakdown(db_path: Path, stop_id: str | None = None, day: str | None = None) -> dict[str, dict]:
    """Average delay by occupancy level, per stop -- deliberately a plain
    breakdown, not a blended score (see module docstring).

    `day`: "YYYY-MM-DD" (Sydney local date); omit for every day recorded.

    Returns {stop_id: {occupancy_status: {"avg_delay_seconds": float,
    "visit_count": int}}}. Visits with no delay recorded are excluded from
    the average, not counted as zero -- a missing Trip Update isn't "on
    time", it's no reading at all.
    """
    if not db_path.exists():
        return {}

    conn = _connect(db_path)
    try:
        query = "SELECT stop_id, occupancy_status, delay_seconds FROM stop_visits WHERE delay_seconds IS NOT NULL"
        params: list[str] = []
        if stop_id is not None:
            query += " AND stop_id = ?"
            params.append(stop_id)
        if day is not None:
            query += " AND day = ?"
            params.append(day)
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    totals: dict[str, dict[str, list[float]]] = {}
    for row_stop_id, occupancy_status, delay_seconds in rows:
        if occupancy_status is None:
            continue
        by_status = totals.setdefault(row_stop_id, {})
        sum_count = by_status.setdefault(occupancy_status, [0.0, 0])
        sum_count[0] += delay_seconds
        sum_count[1] += 1

    return {
        s: {
            status: {"avg_delay_seconds": round(sd[0] / sd[1], 1), "visit_count": sd[1]}
            for status, sd in by_status.items()
        }
        for s, by_status in totals.items()
    }


def stop_summaries(db_path: Path, min_split_visits: int = 5) -> dict[str, dict]:
    """Per stop, over everything recorded: typical delay on arrival, how often
    vehicles arrive with few seats or worse, and typical delay split by
    whether they arrived seated or crowded -- kept as separate numbers, per the
    module docstring.

    Medians, not means: a handful of feed glitches (trips "75 min late")
    would drag a mean. A seated/crowded split needs `min_split_visits` on
    each side, else it's None rather than a median of two readings.
    """
    if not db_path.exists():
        return {}

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT stop_id, occupancy_status, delay_seconds FROM stop_visits WHERE delay_seconds IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    delays: dict[str, list[int]] = {}
    seated: dict[str, list[int]] = {}
    crowded: dict[str, list[int]] = {}
    for stop_id, occupancy_status, delay_seconds in rows:
        delays.setdefault(stop_id, []).append(delay_seconds)
        level = crowding_level(occupancy_status)
        if level is None:
            continue
        (crowded if level >= 1 else seated).setdefault(stop_id, []).append(delay_seconds)

    def split_median(values: list[int] | None) -> int | None:
        return round(statistics.median(values)) if values and len(values) >= min_split_visits else None

    result = {}
    for stop_id, stop_delays in delays.items():
        n_seated = len(seated.get(stop_id, []))
        n_crowded = len(crowded.get(stop_id, []))
        n_known = n_seated + n_crowded
        result[stop_id] = {
            "visits": len(stop_delays),
            "median_delay_s": round(statistics.median(stop_delays)),
            "crowded_share": round(n_crowded / n_known, 3) if n_known else None,
            "median_delay_seated_s": split_median(seated.get(stop_id)),
            "median_delay_crowded_s": split_median(crowded.get(stop_id)),
        }
    return result
