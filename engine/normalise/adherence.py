"""Classify a vehicle's schedule adherence from its Trip Update delay.

States: on_time, late, very_late, cancelled, unknown -- per the brief.
Transport for NSW's own on-time definitions differ by mode, so the
on-time threshold is per-mode (config/config.yaml, adherence_thresholds_seconds),
not global. The late/very_late split isn't specified in the brief beyond
"thresholds go in config" -- see docs/DECISIONS.md for the multiplier chosen.
"""
from __future__ import annotations

MODE_TO_THRESHOLD_CATEGORY = {
    "sydneytrains": "suburban_rail",
    "nswtrains": "intercity",
    "metro": "metro",
    "lightrail_innerwest": "light_rail",
    "lightrail_cbdandsoutheast": "light_rail",
    "buses": "bus",
    "ferries": "ferry",
}


def classify(
    deviation_seconds: int | None,
    mode: str,
    cancelled: bool,
    thresholds: dict[str, int],
    very_late_multiplier: float,
) -> str:
    if cancelled:
        return "cancelled"
    if deviation_seconds is None:
        return "unknown"

    category = MODE_TO_THRESHOLD_CATEGORY.get(mode)
    threshold = thresholds.get(category) if category else None
    if threshold is None:
        return "unknown"

    if deviation_seconds <= threshold:
        return "on_time"  # includes running early -- the brief's states don't include "early"
    if deviation_seconds <= threshold * very_late_multiplier:
        return "late"
    return "very_late"
