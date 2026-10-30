"""Detects when a vehicle has just passed a stop, from the Trip Update's own
next_stop_id changing -- not a distance-to-stop guess. See
engine.normalise.trip_updates' module docstring for why that feed value is
trustworthy enough to use directly (97.4% coverage, checked live).

A "visit" is the LAST occupancy/delay reading seen while a stop was still
the trip's next one, emitted the moment next_stop_id changes to something
else (the vehicle has now passed it) -- so the reading attributed to stop A
is what the vehicle looked like right before leaving A, not an average or
an estimate of what A is generally like.

Pure accumulator, no I/O, same shape as engine.normalise.segments's
SegmentAggregator: .record() returns what (if anything) is ready to log;
engine.history.store does the actual writing.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.normalise.vehicle import VehicleRecord


@dataclass
class _Tracked:
    next_stop_id: str
    occupancy_status: str | None
    delay_seconds: int | None
    route_short_name: str | None
    mode: str


class StopVisitTracker:
    def __init__(self) -> None:
        self._tracked: dict[str, _Tracked] = {}

    def record(self, vehicle: VehicleRecord, next_stop_id: str | None) -> dict | None:
        """Call once per enriched VehicleRecord, with the next_stop_id its
        matching TripUpdateInfo carries (None if there's no trip update, or
        it has no stop-level stop_id). Returns a visit dict ready for
        engine.history.store.record_stop_visit() (minus day/recorded_at,
        which the caller timestamps) if the vehicle just passed a stop,
        else None.
        """
        if next_stop_id is None:
            # No usable reading this cycle - keep whatever was last tracked
            # rather than discarding it; one missed Trip Update shouldn't
            # erase a vehicle's tracked progress toward its next stop.
            return None

        prev = self._tracked.get(vehicle.vehicle_key)
        visit = None
        if prev is not None and prev.next_stop_id != next_stop_id:
            visit = {
                "stop_id": prev.next_stop_id,
                "vehicle_key": vehicle.vehicle_key,
                "route_short_name": prev.route_short_name,
                "mode": prev.mode,
                "occupancy_status": prev.occupancy_status,
                "delay_seconds": prev.delay_seconds,
            }

        self._tracked[vehicle.vehicle_key] = _Tracked(
            next_stop_id=next_stop_id,
            occupancy_status=vehicle.occupancy_status,
            delay_seconds=vehicle.schedule_deviation_seconds,
            route_short_name=vehicle.route_short_name,
            mode=vehicle.mode,
        )
        return visit
