"""The normalised record Phase 2 emits per vehicle currently in the bounding box.

Map-matched position, schedule deviation and adherence state are Phase 3's
job (they need the shapes/stop_times tables and Trip Updates) -- this is
deliberately just the raw-but-joined view.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VehicleRecord:
    vehicle_key: str
    mode: str
    trip_id: str | None
    route_id: str | None
    route_short_name: str | None
    route_color: str | None
    headsign: str | None
    lat: float
    lon: float
    bearing: float | None
    position_timestamp: int | None
    occupancy_status: str | None
    confidence: str  # "static_join" | "degraded_route_fallback" | "unmatched"

    def to_json(self) -> dict:
        return {
            "vehicle_key": self.vehicle_key,
            "mode": self.mode,
            "trip_id": self.trip_id,
            "route_id": self.route_id,
            "route_short_name": self.route_short_name,
            "route_color": self.route_color,
            "headsign": self.headsign,
            "lat": self.lat,
            "lon": self.lon,
            "bearing": self.bearing,
            "position_timestamp": self.position_timestamp,
            "occupancy_status": self.occupancy_status,
            "confidence": self.confidence,
        }
