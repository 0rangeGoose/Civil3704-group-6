"""Normalisation layer.

Turns raw, per-feed protobuf entities into one clean list of VehicleRecord
objects: joins vehicle positions against trip updates (for delay) and
against static GTFS (for route name / shape), and applies "quirk handling"
- dropping stale ("ghost trail") vehicles and picking a stable vehicle key
when the feed doesn't set one consistently.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from google.transit import gtfs_realtime_pb2

from . import config
from .ingestion.realtime_gtfs import RawFeeds
from .ingestion.static_gtfs import StaticGTFS

logger = logging.getLogger(__name__)

CANCELED = gtfs_realtime_pb2.TripDescriptor.CANCELED


@dataclass
class VehicleRecord:
    vehicle_id: str
    mode: str
    route_id: Optional[str]
    route_short_name: Optional[str]
    trip_id: Optional[str]
    shape_id: Optional[str]
    latitude: float
    longitude: float
    bearing: Optional[float]
    speed: Optional[float]
    timestamp: int
    delay_seconds: Optional[int]
    cancelled: bool
    # Filled in by the analytics layer (analytics.score_vehicles); left None
    # by normalisation so this stage stays purely about cleaning/joining.
    congestion_score: Optional[float] = None


def _in_bbox(lat: float, lon: float, bbox: dict) -> bool:
    return (
        bbox["min_lat"] <= lat <= bbox["max_lat"]
        and bbox["min_lon"] <= lon <= bbox["max_lon"]
    )


def _vehicle_key(vehicle) -> str:
    """Pick a stable identity even when feeds are inconsistent about it.

    Some TfNSW bus operators mutate vehicle.id between updates but keep
    vehicle.label stable (or vice versa) - prefer whichever is non-empty,
    falling back to the trip_id so the vehicle doesn't just vanish.
    """
    if vehicle.vehicle.id:
        return vehicle.vehicle.id
    if vehicle.vehicle.label:
        return vehicle.vehicle.label
    if vehicle.trip.trip_id:
        return f"trip:{vehicle.trip.trip_id}"
    return "unknown"


def _extract_trip_delays(trip_update_feeds: Dict[str, gtfs_realtime_pb2.FeedMessage]) -> Dict[str, dict]:
    """trip_id -> {"delay_seconds": int|None, "cancelled": bool}"""
    delays: Dict[str, dict] = {}
    for mode, feed in trip_update_feeds.items():
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            trip_id = tu.trip.trip_id
            if not trip_id:
                continue

            cancelled = tu.trip.schedule_relationship == CANCELED
            delay_seconds = None
            for stu in tu.stop_time_update:
                if stu.HasField("arrival") and stu.arrival.HasField("delay"):
                    delay_seconds = stu.arrival.delay
                    break
                if stu.HasField("departure") and stu.departure.HasField("delay"):
                    delay_seconds = stu.departure.delay
                    break
            if delay_seconds is None and tu.HasField("delay"):
                delay_seconds = tu.delay

            delays[trip_id] = {"delay_seconds": delay_seconds, "cancelled": cancelled}
    return delays


def normalise(
    raw: RawFeeds,
    static: StaticGTFS,
    now: Optional[float] = None,
    bbox: Optional[dict] = config.CBD_BBOX,
) -> List[VehicleRecord]:
    """Combine vehicle positions + trip updates + static GTFS into clean records.

    If `bbox` is given (defaults to the CBD box), vehicles outside it are
    dropped so the output matches the scale-model footprint. Pass bbox=None
    to keep the whole network.
    """
    now = now if now is not None else time.time()
    trip_delays = _extract_trip_delays(raw.trip_updates)

    seen: Dict[str, VehicleRecord] = {}
    for mode, feed in raw.vehicle_positions.items():
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            v = entity.vehicle
            if not v.HasField("position"):
                continue

            if bbox is not None and not _in_bbox(v.position.latitude, v.position.longitude, bbox):
                continue

            timestamp = v.timestamp if v.HasField("timestamp") else int(now)
            if now - timestamp > config.GHOST_TRAIL_STALE_SECONDS:
                # Quirk handling: drop stale "ghost trail" vehicles instead
                # of letting a frozen position sit on the model forever.
                continue

            trip_id = v.trip.trip_id or None
            static_info = static.lookup_trip(trip_id) if trip_id else None

            # Static join with fallback: prefer the static lookup, but fall
            # back to whatever route_id the realtime feed itself carries
            # (TfNSW usually includes it directly) so an out-of-date static
            # bundle doesn't drop the vehicle entirely.
            route_id = static_info.route_id if static_info else (v.trip.route_id or None)
            route_short_name = static_info.route_short_name if static_info else route_id
            shape_id = static_info.shape_id if static_info else None

            delay_info = trip_delays.get(trip_id, {}) if trip_id else {}

            key = _vehicle_key(v)
            seen[key] = VehicleRecord(
                vehicle_id=key,
                mode=mode,
                route_id=route_id,
                route_short_name=route_short_name,
                trip_id=trip_id,
                shape_id=shape_id,
                latitude=v.position.latitude,
                longitude=v.position.longitude,
                bearing=v.position.bearing if v.position.HasField("bearing") else None,
                speed=v.position.speed if v.position.HasField("speed") else None,
                timestamp=timestamp,
                delay_seconds=delay_info.get("delay_seconds"),
                cancelled=delay_info.get("cancelled", False),
            )

    return list(seen.values())
