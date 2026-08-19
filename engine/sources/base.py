"""Common interface all vehicle-data sources implement.

The engine never needs to know whether it's reading from the live API, a
recorded replay, or a synthetic generator -- one interface, N implementations.
Only ``live.py`` exists so far (Phase 2's scope); ``replay``/``synthetic``
slot in here later without touching engine/normalise code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Protocol


@dataclass
class RawFeed:
    mode: str
    feed_type: str  # "trip_updates" | "vehicle_positions" | "service_alerts"
    fetched_at: datetime
    payload: bytes


class Source(Protocol):
    def stream(self) -> AsyncIterator[RawFeed]:
        """Yield raw protobuf payloads as they become available."""
        ...
