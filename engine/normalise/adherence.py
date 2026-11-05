"""Classify a vehicle's punctuality from its Trip Update delay.

States: early, on_time, late, very_late, cancelled, unknown.

The on-time window per mode comes from config (``punctuality``), each limit
from a published source where one exists:

- Sydney Trains and NSW TrainLink intercity: TfNSW's punctuality standard,
  within 5 and 6 minutes of the timetable (measured by TfNSW at the final
  destination). Early running isn't penalised.
- Buses: the Greater Sydney Bus Contract (Schedule 4, 2023): no more than
  59 s early and no more than 5 min 59 s late (measured at the first stop).
  Leaving early is a failure for buses -- passengers miss them -- so it's its
  own state.
- Metro, light rail and ferries: no published definition was found, so the
  Sydney Trains 5-minute standard is applied, and labelled as borrowed.

**very_late** is not a TfNSW category. It means the vehicle has "missed its
slot": later than both the on-time limit and the scheduled gap to the next
trip on the same route and direction (engine/normalise/headways.py), so a
passenger would have done better catching the next service. Without a known
gap (e.g. Sydney Trains, whose live trip ids don't match the timetable) a
vehicle past the limit is just late.

Note what the live state measures: TfNSW's *predicted* delay at the vehicle's
*next* stop, wherever it is -- not TfNSW's official measuring points. The
per-trip measure at those points is engine/normalise/aggregate.py's
TripPunctuality.
"""
from __future__ import annotations

from dataclasses import dataclass

MODE_TO_THRESHOLD_CATEGORY = {
    "sydneytrains": "suburban_rail",
    "nswtrains": "intercity",
    "metro": "metro",
    "lightrail_innerwest": "light_rail",
    "lightrail_cbdandsoutheast": "light_rail",
    "buses": "bus",
    "ferries": "ferry",
}


@dataclass(frozen=True)
class PunctualityRules:
    """Per category: latest delay still on time, and (where a source sets one)
    how early a vehicle may run and still be on time, both in seconds."""
    late_seconds: dict[str, int]
    early_seconds: dict[str, int]

    @classmethod
    def from_config(cls, raw: dict) -> "PunctualityRules":
        p = raw["punctuality"]
        return cls(dict(p["late_seconds"]), dict(p.get("early_seconds") or {}))


def classify(
    deviation_seconds: int | None,
    mode: str,
    cancelled: bool,
    rules: PunctualityRules,
    headway_seconds: float | None = None,
) -> str:
    if cancelled:
        return "cancelled"
    if deviation_seconds is None:
        return "unknown"

    category = MODE_TO_THRESHOLD_CATEGORY.get(mode)
    late_limit = rules.late_seconds.get(category) if category else None
    if late_limit is None:
        return "unknown"

    early_limit = rules.early_seconds.get(category)
    if early_limit is not None and deviation_seconds < -early_limit:
        return "early"
    if deviation_seconds <= late_limit:
        return "on_time"
    if headway_seconds is not None and deviation_seconds > headway_seconds:
        return "very_late"  # missed its slot: the next service would have come sooner
    return "late"
