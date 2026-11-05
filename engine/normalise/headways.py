"""Per timetabled trip: its first stop, and the scheduled gap to the next trip.

Both come from the static timetable, once at startup (~1 s for ~97k trips):

- **first stop** -- the stop with the lowest stop_sequence (stop_times holds
  whole trips, including stops outside the footprint, so this is the trip's
  real origin). TfNSW's bus contracts measure on-time running there.
- **headway** -- seconds from this trip's first departure to the next trip on
  the same route and direction, on the same kind of day. A vehicle later than
  this (and past the on-time limit) has "missed its slot": the next service
  would have come sooner (engine/normalise/adherence.py). The last trip of a
  day takes the gap before it.

Kinds of day: the first Tuesday, Saturday and Sunday the timetable covers
(today or later). A trip gets the gap from the first of those it runs on.
Trips without a match (Sydney Trains, Metro and CBD light rail, whose live
trip ids differ from the timetable's) have no headway.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger("pulse_cbd.headways")

DAY_TYPES = (1, 5, 6)  # Tuesday, Saturday, Sunday (date.weekday())


def _seconds(hms: str) -> int | None:
    try:
        h, m, s = (int(x) for x in hms.split(":"))
    except (AttributeError, ValueError):
        return None
    return h * 3600 + m * 60 + s


def _reference_days(conn: sqlite3.Connection) -> list[str]:
    lo, hi = conn.execute("SELECT min(start_date), max(end_date) FROM calendar").fetchone()
    if not lo:
        return []
    start = max(date.today(), date(int(lo[:4]), int(lo[4:6]), int(lo[6:])))
    end = date(int(hi[:4]), int(hi[4:6]), int(hi[6:]))
    days = []
    for weekday in DAY_TYPES:
        d = start + timedelta(days=(weekday - start.weekday()) % 7)
        if d <= end:
            days.append(d.strftime("%Y%m%d"))
    return days


def _active_services(conn: sqlite3.Connection, day: str) -> set[str]:
    weekday = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][
        date(int(day[:4]), int(day[4:6]), int(day[6:])).weekday()]
    active = {r[0] for r in conn.execute(
        f"SELECT service_id FROM calendar WHERE {weekday} = '1' AND start_date <= ? AND end_date >= ?", (day, day))}
    for service_id, exception in conn.execute("SELECT service_id, exception_type FROM calendar_dates WHERE date = ?", (day,)):
        if exception == "1":
            active.add(service_id)
        elif exception == "2":
            active.discard(service_id)
    return active


def compute_headways(trips: list[tuple[str, str, str, int]]) -> dict[str, int]:
    """trips: [(trip_id, route_id, direction_id, first_departure_s), ...] running on
    one day. Returns {trip_id: seconds to the next trip on its route and direction}."""
    groups: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for trip_id, route_id, direction_id, dep in trips:
        groups[(route_id, direction_id)].append((dep, trip_id))
    result: dict[str, int] = {}
    for runs in groups.values():
        runs.sort()
        if len(runs) < 2:
            continue
        for i, (dep, trip_id) in enumerate(runs):
            gap = runs[i + 1][0] - dep if i + 1 < len(runs) else dep - runs[i - 1][0]
            if gap > 0:
                result[trip_id] = gap
    return result


class TripTimetable:
    def __init__(self, first_stops: dict[str, str], headways: dict[str, int]):
        self._first_stops = first_stops
        self._headways = headways

    @classmethod
    def load(cls, db_path: Path) -> "TripTimetable":
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT t.trip_id, t.route_id, t.direction_id, t.service_id, st.stop_id, st.departure_time "
                "FROM trips t JOIN stop_times st ON st.trip_id = t.trip_id "
                "JOIN (SELECT trip_id, MIN(stop_sequence) AS seq FROM stop_times GROUP BY trip_id) f "
                "ON f.trip_id = st.trip_id AND f.seq = st.stop_sequence").fetchall()
            first_stops = {trip_id: stop_id for trip_id, _, _, _, stop_id, _ in rows}
            headways: dict[str, int] = {}
            for day in _reference_days(conn):
                active = _active_services(conn, day)
                running = [(trip_id, route_id, direction_id or "", dep)
                           for trip_id, route_id, direction_id, service_id, _, hms in rows
                           if service_id in active and (dep := _seconds(hms)) is not None]
                for trip_id, gap in compute_headways(running).items():
                    headways.setdefault(trip_id, gap)
        finally:
            conn.close()
        logger.info("timetable: %d trips, %d with a headway", len(first_stops), len(headways))
        return cls(first_stops, headways)

    def first_stop(self, trip_id: str | None) -> str | None:
        return self._first_stops.get(trip_id) if trip_id else None

    def headway(self, trip_id: str | None) -> int | None:
        return self._headways.get(trip_id) if trip_id else None
