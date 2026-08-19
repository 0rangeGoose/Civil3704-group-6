"""Turns one polled RawFeed into normalised VehicleRecords.

Only vehicle_positions feeds produce records in Phase 2 -- trip_updates and
service_alerts are decoded/recorded but schedule deviation and adherence are
Phase 3's job (they need shapes/stop_times, not just the vehicle positions
table).
"""
from __future__ import annotations

import logging

from engine.config import BoundingBox
from engine.normalise.decode import DecodeError, parse_feed_message
from engine.normalise.proto import pb
from engine.normalise.quirks import dedupe_ferry_entities
from engine.normalise.static_lookup import StaticLookup
from engine.normalise.vehicle import VehicleRecord
from engine.sources.base import RawFeed

logger = logging.getLogger("pulse_cbd.build")


def _in_box(lat: float, lon: float, bbox: BoundingBox) -> bool:
    return bbox.south <= lat <= bbox.north and bbox.west <= lon <= bbox.east


def build_vehicle_records(
    raw: RawFeed, bbox: BoundingBox, lookup: StaticLookup
) -> list[VehicleRecord]:
    if raw.feed_type != "vehicle_positions":
        return []

    try:
        feed = parse_feed_message(raw.payload)
    except DecodeError as exc:
        logger.error("%s/%s: failed to decode: %s", raw.feed_type, raw.mode, exc)
        return []

    entities = [e for e in feed.entity if e.HasField("vehicle")]
    if "ferries" in raw.mode:
        entities = dedupe_ferry_entities(entities, raw.fetched_at)

    records: list[VehicleRecord] = []
    for entity in entities:
        vp = entity.vehicle
        if not vp.HasField("position"):
            continue
        lat, lon = vp.position.latitude, vp.position.longitude
        if not _in_box(lat, lon, bbox):
            continue

        trip_id = vp.trip.trip_id if vp.HasField("trip") and vp.trip.trip_id else None
        route_id = vp.trip.route_id if vp.HasField("trip") and vp.trip.route_id else None
        join = lookup.join(trip_id, route_id)

        vehicle_id = (
            vp.vehicle.id if vp.HasField("vehicle") and vp.vehicle.id else entity.id
        )
        occupancy = (
            pb.VehiclePosition.OccupancyStatus.Name(vp.occupancy_status)
            if vp.HasField("occupancy_status")
            else None
        )

        records.append(
            VehicleRecord(
                vehicle_key=f"{raw.mode}:{vehicle_id}",
                mode=raw.mode,
                trip_id=trip_id,
                route_id=join.route.route_id if join.route else route_id,
                route_short_name=join.route.route_short_name if join.route else None,
                route_color=join.route.route_color if join.route else None,
                headsign=join.headsign,
                lat=lat,
                lon=lon,
                bearing=vp.position.bearing if vp.position.HasField("bearing") else None,
                position_timestamp=vp.timestamp if vp.HasField("timestamp") else None,
                occupancy_status=occupancy,
                confidence=join.confidence,
            )
        )
    return records
