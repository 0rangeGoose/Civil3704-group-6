"""The normalised record emitted per vehicle currently in the bounding box.

Phase 2 fields are the raw-but-joined view (id, route, position, confidence).
Phase 3 adds the map-matched position and schedule-adherence fields, filled
in by enrich.py -- they default to None so a bare Phase-2 record is still
valid (e.g. if enrichment is skipped or a trip has no shape/Trip Update).
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
    # "static_join" | "degraded_route_fallback" | "geometric_match" | "unmatched"
    confidence: str

    # Phase 3: map matching
    matched_lat: float | None = None
    matched_lon: float | None = None
    distance_along_shape_m: float | None = None

    # Phase 3: schedule adherence
    schedule_deviation_seconds: int | None = None
    adherence_state: str | None = None  # on_time | late | very_late | cancelled | unknown

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
            "matched_lat": self.matched_lat,
            "matched_lon": self.matched_lon,
            "distance_along_shape_m": self.distance_along_shape_m,
            "schedule_deviation_seconds": self.schedule_deviation_seconds,
            "adherence_state": self.adherence_state,
        }
