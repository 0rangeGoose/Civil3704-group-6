from pathlib import Path

import pytest

from engine.normalise.enrich import enrich_vehicle_record
from engine.normalise.map_match import ShapeIndex
from engine.normalise.static_lookup import StaticLookup
from engine.normalise.trip_updates import TripUpdateInfo
from engine.normalise.vehicle import VehicleRecord

from engine.normalise.adherence import PunctualityRules

RULES = PunctualityRules(late_seconds={"bus": 300}, early_seconds={})


def _record(**overrides) -> VehicleRecord:
    base = dict(
        vehicle_key="buses:v1",
        mode="buses",
        trip_id="T1",
        route_id=None,
        route_short_name=None,
        route_color=None,
        headsign=None,
        lat=-33.865,
        lon=151.207,
        bearing=None,
        position_timestamp=1000,
        occupancy_status=None,
        confidence="unmatched",
    )
    base.update(overrides)
    return VehicleRecord(**base)


def test_static_join_snaps_to_the_trips_shape(static_db: Path):
    record = _record(confidence="static_join")
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, {}, shapes, lookup, RULES)

    assert enriched.confidence == "static_join"
    assert enriched.matched_lat is not None
    assert enriched.distance_along_shape_m == pytest.approx(550, rel=0.1)


def test_missing_bearing_is_taken_from_the_trips_own_shape(static_db: Path):
    record = _record(confidence="static_join", bearing=None)
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, {}, shapes, lookup, RULES)

    assert enriched.bearing is not None
    assert min(enriched.bearing, 360 - enriched.bearing) < 2  # SH1 runs north


def test_feed_bearing_is_kept_over_the_shape_heading(static_db: Path):
    record = _record(confidence="static_join", bearing=123.0)
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, {}, shapes, lookup, RULES)

    assert enriched.bearing == 123.0


def test_nearest_shape_fallback_does_not_invent_a_bearing(static_db: Path):
    record = _record(confidence="unmatched", trip_id=None, bearing=None)
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, {}, shapes, lookup, RULES)

    assert enriched.matched_lat is not None
    assert enriched.bearing is None


def test_unmatched_record_near_a_shape_is_upgraded_to_geometric_match(static_db: Path):
    record = _record(confidence="unmatched", trip_id=None)
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, {}, shapes, lookup, RULES)

    assert enriched.confidence == "geometric_match"
    assert enriched.route_id == "R1"
    assert enriched.route_short_name == "333"
    assert enriched.matched_lat is not None


def test_unmatched_record_far_from_any_shape_stays_unmatched(static_db: Path):
    record = _record(confidence="unmatched", trip_id=None, lat=-34.5, lon=150.5)
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, {}, shapes, lookup, RULES)

    assert enriched.confidence == "unmatched"
    assert enriched.matched_lat is None


def test_degraded_route_fallback_is_not_upgraded_by_geometric_match(static_db: Path):
    record = _record(confidence="degraded_route_fallback", trip_id="T1-MUTATED", route_id="R1")
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, {}, shapes, lookup, RULES)

    # Already had a route via id join -- geometric match should only add
    # position data, not relabel confidence.
    assert enriched.confidence == "degraded_route_fallback"
    assert enriched.matched_lat is not None


def test_schedule_deviation_and_adherence_from_trip_update(static_db: Path):
    record = _record(confidence="static_join", trip_id="T1")
    trip_updates = {"T1": TripUpdateInfo(next_stop_delay_seconds=120, cancelled=False)}
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, trip_updates, shapes, lookup, RULES)

    assert enriched.schedule_deviation_seconds == 120
    assert enriched.adherence_state == "on_time"


def test_missing_trip_update_is_unknown_adherence(static_db: Path):
    record = _record(confidence="static_join", trip_id="T1")
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, {}, shapes, lookup, RULES)

    assert enriched.schedule_deviation_seconds is None
    assert enriched.adherence_state == "unknown"


def test_cancelled_trip_update_sets_cancelled_adherence(static_db: Path):
    record = _record(confidence="static_join", trip_id="T1")
    trip_updates = {"T1": TripUpdateInfo(next_stop_delay_seconds=None, cancelled=True)}
    shapes = ShapeIndex(static_db)
    with StaticLookup(static_db) as lookup:
        enriched = enrich_vehicle_record(record, trip_updates, shapes, lookup, RULES)

    assert enriched.adherence_state == "cancelled"
