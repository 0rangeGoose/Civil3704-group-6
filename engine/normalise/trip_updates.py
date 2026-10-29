"""Decode Trip Updates into per-trip schedule deviation, keyed by trip_id.

Per the brief: compute deviation against the next scheduled stop, not by
interpolating vehicle position, because Trip Updates are stop-anchored. The
first entry in a Trip Update's stop_time_update list is, by GTFS-RT
convention, the next stop the vehicle hasn't yet passed (operators prune
stops once served). Falls back to the trip-level delay field only if no
per-stop delay is available at all.

``next_stop_id`` comes from the same stop_time_update entry as the delay
(not just the first entry in the list, which may lack a stop_id or a
delay) -- checked live 2026-10-29: unlike VehiclePosition (which never
carries current stop_id/stop_sequence), StopTimeUpdate.stop_id is
populated on 97.4% of trip updates with a trip_id. That makes "which stop
is this trip approaching" an authoritative feed value rather than a
geometric guess against static stop coordinates -- see engine/history/.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.normalise.decode import DecodeError, parse_feed_message
from engine.normalise.proto import pb


@dataclass
class TripUpdateInfo:
    next_stop_delay_seconds: int | None
    cancelled: bool
    next_stop_id: str | None = None


def build_trip_update_index(payload: bytes) -> dict[str, TripUpdateInfo]:
    try:
        feed = parse_feed_message(payload)
    except DecodeError:
        return {}

    index: dict[str, TripUpdateInfo] = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        if not trip_id:
            continue

        cancelled = (
            tu.trip.HasField("schedule_relationship")
            and tu.trip.schedule_relationship == pb.TripDescriptor.CANCELED
        )

        delay = None
        next_stop_id = None
        for stu in tu.stop_time_update:
            if stu.HasField("arrival") and stu.arrival.HasField("delay"):
                delay = stu.arrival.delay
            elif stu.HasField("departure") and stu.departure.HasField("delay"):
                delay = stu.departure.delay
            if delay is not None:
                next_stop_id = stu.stop_id or None
                break
        if delay is None and tu.HasField("delay"):
            delay = tu.delay

        index[trip_id] = TripUpdateInfo(
            next_stop_delay_seconds=delay, next_stop_id=next_stop_id, cancelled=cancelled
        )
    return index
