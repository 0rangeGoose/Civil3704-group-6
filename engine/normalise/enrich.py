"""Adds map-matched position and schedule adherence to a Phase 2 VehicleRecord.

Map matching prefers the trip's own shape (from the static join) when
available -- exact and authoritative. Otherwise it falls back to nearest-shape
geometric matching, which also resolves route identity for the
sydneytrains/metro/CBD-light-rail id-mismatch quirk found in Phase 2:
geometric matching doesn't care what the realtime feed calls the route, only
where the vehicle actually is. A record upgraded this way is marked
confidence "geometric_match" rather than silently relabelled "static_join",
so it stays honest about how the route was identified.
"""
from __future__ import annotations

import dataclasses

from engine.normalise.adherence import PunctualityRules, classify
from engine.normalise.headways import TripTimetable
from engine.normalise.map_match import ShapeIndex
from engine.normalise.static_lookup import StaticLookup
from engine.normalise.trip_updates import TripUpdateInfo
from engine.normalise.vehicle import VehicleRecord

DEFAULT_MAX_MATCH_DISTANCE_M = 100.0


def enrich_vehicle_record(
    record: VehicleRecord,
    trip_updates: dict[str, TripUpdateInfo],
    shapes: ShapeIndex,
    lookup: StaticLookup,
    rules: PunctualityRules,
    timetable: TripTimetable | None = None,
    max_match_distance_m: float = DEFAULT_MAX_MATCH_DISTANCE_M,
) -> VehicleRecord:
    shape_id = None
    if record.confidence == "static_join" and record.trip_id:
        shape_id = lookup.shape_for_trip(record.trip_id)

    match = None
    own_trip_shape = bool(shape_id)
    if shape_id:
        match = shapes.snap_to_shape(shape_id, record.lat, record.lon)
    else:
        nearest = shapes.nearest_shape(record.lat, record.lon, max_match_distance_m)
        if nearest is not None:
            found_shape_id, match = nearest
            shape_id = found_shape_id
            if record.confidence == "unmatched":
                found_route_id = shapes.route_for_shape(found_shape_id)
                route = lookup.route_by_id(found_route_id) if found_route_id else None
                if route is not None:
                    record = dataclasses.replace(
                        record,
                        route_id=route.route_id,
                        route_short_name=route.route_short_name,
                        route_color=route.route_color,
                        confidence="geometric_match",
                    )

    trip_update = trip_updates.get(record.trip_id) if record.trip_id else None
    deviation = trip_update.next_stop_delay_seconds if trip_update else None
    cancelled = trip_update.cancelled if trip_update else False
    headway = timetable.headway(record.trip_id) if timetable else None
    adherence_state = classify(deviation, record.mode, cancelled, rules, headway)

    # Sydney Trains never send a bearing. A trip's own shape runs in its
    # direction of travel, so its tangent stands in -- but not a nearest-shape
    # fallback, which may be the opposite direction's line.
    bearing = record.bearing
    if bearing is None and own_trip_shape and match:
        bearing = shapes.heading_at(shape_id, match.distance_along_shape_m)

    return dataclasses.replace(
        record,
        bearing=bearing,
        shape_id=shape_id if match else None,
        matched_lat=match.matched_lat if match else None,
        matched_lon=match.matched_lon if match else None,
        distance_along_shape_m=match.distance_along_shape_m if match else None,
        schedule_deviation_seconds=deviation,
        adherence_state=adherence_state,
    )
