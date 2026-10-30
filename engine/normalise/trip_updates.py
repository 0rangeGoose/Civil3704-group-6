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

A delay of exactly 0 at *every* stop is TfNSW's placeholder for "no live
prediction", not an on-time trip (checked 2026-10-30: 68% of Sydney Trains
readings were exactly 0 against ~0.2% expected, 99% of zero readings were
whole-trip zeros, and trips flipped in and out of it mid-run). Such trips get
delay None -- unknown, not on time -- but keep next_stop_id, which is still
real. Stops marked NO_DATA or SKIPPED carry a delay field too, and are ignored.

Not every operator prunes served stops, despite the convention above: Metro,
both light rail lines, ferries and NSW TrainLink send the whole trip from
stop 1 every time (checked 2026-10-30), with the origin's departure delay
always 0. So the next stop is the first whose predicted departure (or
arrival) time hasn't passed yet, by the feed's own header timestamp.
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


_IGNORED_STOP_RELATIONSHIPS = (
    pb.TripUpdate.StopTimeUpdate.NO_DATA,
    pb.TripUpdate.StopTimeUpdate.SKIPPED,
)


def _stop_delay(stu) -> int | None:
    if stu.HasField("arrival") and stu.arrival.HasField("delay"):
        return stu.arrival.delay
    if stu.HasField("departure") and stu.departure.HasField("delay"):
        return stu.departure.delay
    return None


def _already_passed(stu, now: int | None) -> bool:
    if now is None:
        return False
    if stu.HasField("departure") and stu.departure.HasField("time"):
        return stu.departure.time < now
    if stu.HasField("arrival") and stu.arrival.HasField("time"):
        return stu.arrival.time < now
    return False


def build_trip_update_index(payload: bytes) -> dict[str, TripUpdateInfo]:
    try:
        feed = parse_feed_message(payload)
    except DecodeError:
        return {}
    now = feed.header.timestamp if feed.header.HasField("timestamp") else None

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

        usable = [
            (stu, d)
            for stu in tu.stop_time_update
            if stu.schedule_relationship not in _IGNORED_STOP_RELATIONSHIPS
            and not _already_passed(stu, now)
            and (d := _stop_delay(stu)) is not None
        ]
        delay = None
        next_stop_id = None
        if usable:
            next_stop_id = usable[0][0].stop_id or None
            if any(d != 0 for _, d in usable):
                delay = usable[0][1]
        elif tu.HasField("delay"):
            delay = tu.delay

        index[trip_id] = TripUpdateInfo(
            next_stop_delay_seconds=delay, next_stop_id=next_stop_id, cancelled=cancelled
        )
    return index
