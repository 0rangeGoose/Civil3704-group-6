"""CSV exports of one recorded peak, for spreadsheets and report figures.

The peak is replayed through the live engine as fast as possible (the same
code the projection runs), and three tables are written in that one pass:

- **vehicles.csv** -- every vehicle update: where it was, its route, delay,
  on-time state and TfNSW's traffic and crowding fields.
- **stop_visits.csv** -- every time a vehicle passed a stop, with its delay
  and crowding then. Worked out from the replay itself, so it exists for any
  recorded peak, not only the live history (which starts 30 Oct).
- **trips.csv** -- each trip once, at TfNSW's measuring point (buses leaving
  their first stop, other modes' predicted delay at the final stop), with its
  on-time state: what the Stats tab counts (engine/normalise/aggregate.py).

The replay starts 10 minutes before the peak so trips and stop
visits already in progress are tracked; rows before the peak's start aren't
written.
"""
from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.normalise.vehicle import VehicleRecord
from engine.publish.feed import LiveVehicleFeed
from engine.sources.replay import RecordingIndex, ReplaySource, Session

SYDNEY = ZoneInfo("Australia/Sydney")
PREFILL = timedelta(minutes=10)
KINDS = ("vehicles", "stop_visits", "trips")

VEHICLE_COLUMNS = ["time", "vehicle", "mode", "route", "headsign", "trip_id", "lat", "lon", "bearing",
                   "delay_s", "on_time_state", "tfnsw_traffic", "tfnsw_crowding", "position_reported_at"]
STOP_COLUMNS = ["time", "stop_id", "stop_name", "stop_lat", "stop_lon", "route", "mode", "vehicle",
                "delay_s", "tfnsw_crowding"]
TRIP_COLUMNS = ["time", "trip", "mode", "route", "measured_at", "stop_id", "stop_name", "delay_s", "state"]


def _local(ts: float) -> str:
    return datetime.fromtimestamp(ts, SYDNEY).isoformat(timespec="seconds")


def _blank(v):
    return "" if v is None else v


def export_peak(feed: LiveVehicleFeed, index: RecordingIndex, session_id: str, out_dir: Path) -> dict[str, Path]:
    """Writes the CSVs for a recorded session into out_dir; returns {kind: path}."""
    session = index.find(session_id)
    if session is None:
        raise ValueError(f"no recorded session {session_id!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    start_ts, end_ts = session.start.timestamp(), session.end.timestamp()
    stops = {s.stop_id: s for s in feed.stops}

    tmp = {kind: out_dir / f".{kind}.csv.part" for kind in KINDS}
    files = {kind: open(path, "w", newline="", encoding="utf-8") for kind, path in tmp.items()}
    writers = {kind: csv.writer(f) for kind, f in files.items()}
    writers["vehicles"].writerow(VEHICLE_COLUMNS)
    writers["stop_visits"].writerow(STOP_COLUMNS)
    writers["trips"].writerow(TRIP_COLUMNS)
    trips: dict = {}  # trip key -> latest TripReading inside the peak

    def on_vehicle(v: VehicleRecord) -> None:
        now = feed.clock
        if start_ts <= now < end_ts:
            lat = v.matched_lat if v.matched_lat is not None else v.lat
            lon = v.matched_lon if v.matched_lon is not None else v.lon
            writers["vehicles"].writerow([
                _local(now), v.vehicle_key, v.mode, _blank(v.route_short_name), _blank(v.headsign), _blank(v.trip_id),
                round(lat, 6), round(lon, 6), "" if v.bearing is None else round(v.bearing), _blank(v.schedule_deviation_seconds),
                _blank(v.adherence_state), _blank(v.congestion_level), _blank(v.occupancy_status),
                _local(v.position_timestamp) if v.position_timestamp else "",
            ])

    def record_visit(vehicle: VehicleRecord, next_stop_id):
        tracker = feed.stop_visits
        visit = type(tracker).record(tracker, vehicle, next_stop_id)
        now = feed.clock
        if visit is not None and start_ts <= now < end_ts:
            stop = stops.get(visit["stop_id"])
            writers["stop_visits"].writerow([
                _local(now), visit["stop_id"], stop.name if stop else "",
                stop.lat if stop else "", stop.lon if stop else "",
                _blank(visit["route_short_name"]), visit["mode"], visit["vehicle_key"],
                _blank(visit["delay_seconds"]), _blank(visit["occupancy_status"]),
            ])
        return visit

    def record_trip(trip_key, reading):
        if start_ts <= reading.at < end_ts and reading.state != "unknown":
            trips[trip_key] = reading
        tracker = feed.trip_punctuality
        return type(tracker).record(tracker, trip_key, reading)

    # Every replay pass starts with a reset, which replaces the stop-visit
    # tracker and the trip measure, so the hooks go back on after each one.
    def hook():
        feed.stop_visits.record = record_visit
        feed.trip_punctuality.record = record_trip

    def reset():
        type(feed).reset(feed)
        hook()

    feed.reset = reset
    feed.on_vehicle = on_vehicle
    try:
        reset()
        window = Session("export", "export", session.start - PREFILL, session.end, 1.0)
        asyncio.run(feed.run_forever(ReplaySource(index, window, speed=lambda: 1e9, loop=False)))
        for key, r in sorted(trips.items(), key=lambda kv: kv[1].at):
            stop = stops.get(r.stop_id) if r.stop_id else None
            writers["trips"].writerow([
                _local(r.at), key, r.mode, _blank(r.route), r.point, _blank(r.stop_id),
                stop.name if stop else "", _blank(r.delay_seconds), r.state,
            ])
    finally:
        del feed.reset
        feed.on_vehicle = None
        for obj, name in ((feed.stop_visits, "record"), (feed.trip_punctuality, "record")):
            obj.__dict__.pop(name, None)
        for f in files.values():
            f.close()

    result = {}
    for kind, path in tmp.items():
        final = out_dir / f"{kind}.csv"
        path.replace(final)  # only complete files get the real name
        result[kind] = final
    return result
