"""Congestion and crowding as ordinal levels, from TfNSW's own per-vehicle fields.

Both come straight from the realtime feed rather than being estimated:

- ``congestion_level`` (GTFS-RT VehiclePosition): populated for buses and
  Metro, always UNKNOWN for Sydney Trains (checked live 2026-09-25).
- ``occupancy_status``: vehicle-level for buses and Metro; Sydney Trains mostly
  report it per carriage instead, via TfNSW's 1007 ``consist`` extension.

Each maps to a small integer level so segments can average readings from
several vehicles. Levels are ordinal categories, not measurements -- a mean of
1.4 means "between stop-and-go and congested", and is only ever shown by
rounding back to a category.
"""
from __future__ import annotations

CONGESTION_LEVELS = {
    "RUNNING_SMOOTHLY": 0,
    "STOP_AND_GO": 1,
    "CONGESTION": 2,
    "SEVERE_CONGESTION": 3,
}
CONGESTION_LABELS = ["Running smoothly", "Stop and go", "Congested", "Severely congested"]

# EMPTY folds into "many seats" and the three "no room" states into one, so
# the projection shows four distinguishable colours rather than seven.
CROWDING_LEVELS = {
    "EMPTY": 0,
    "MANY_SEATS_AVAILABLE": 0,
    "FEW_SEATS_AVAILABLE": 1,
    "STANDING_ROOM_ONLY": 2,
    "CRUSHED_STANDING_ROOM_ONLY": 3,
    "FULL": 3,
    "NOT_ACCEPTING_PASSENGERS": 3,
}
CROWDING_LABELS = ["Seats available", "Few seats", "Standing room", "Crowded / full"]


def congestion_level(name: str | None) -> int | None:
    """None for UNKNOWN_CONGESTION_LEVEL or a missing field."""
    return CONGESTION_LEVELS.get(name) if name else None


def crowding_level(name: str | None) -> int | None:
    return CROWDING_LEVELS.get(name) if name else None


def crowding_from_carriages(carriage_names: list[str]) -> str | None:
    """A train's vehicle-level occupancy from its per-carriage readings.

    Uses the carriage *median*, not the worst carriage: one packed car at the
    front of an otherwise half-empty train shouldn't paint the whole train as
    full. Returns the occupancy name of that median carriage.
    """
    known = sorted((CROWDING_LEVELS[n], n) for n in carriage_names if n in CROWDING_LEVELS)
    if not known:
        return None
    return known[(len(known) - 1) // 2][1]
