from datetime import datetime, timezone
from pathlib import Path

from engine.normalise.build import build_vehicle_records
from engine.normalise.proto import pb
from engine.normalise.static_lookup import StaticLookup
from engine.sources.base import RawFeed
from tests.conftest import TEST_BBOX


def _feed_message(entities: list[tuple]) -> bytes:
    """entities: list of (entity_id, trip_id, route_id, lat, lon) tuples."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1000
    for entity_id, trip_id, route_id, lat, lon in entities:
        e = feed.entity.add()
        e.id = entity_id
        if trip_id:
            e.vehicle.trip.trip_id = trip_id
        if route_id:
            e.vehicle.trip.route_id = route_id
        e.vehicle.position.latitude = lat
        e.vehicle.position.longitude = lon
        e.vehicle.vehicle.id = f"veh-{entity_id}"
        e.vehicle.timestamp = 1000
    return feed.SerializeToString()


def _raw(mode: str, payload: bytes) -> RawFeed:
    return RawFeed(
        mode=mode,
        feed_type="vehicle_positions",
        fetched_at=datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc),
        payload=payload,
    )


def test_only_in_box_vehicles_are_returned(static_db: Path):
    payload = _feed_message([
        ("e1", "T1", "R1", 0.5, 0.5),  # inside TEST_BBOX
        ("e2", "T1", "R1", 5.0, 5.0),  # outside TEST_BBOX
    ])
    with StaticLookup(static_db) as lookup:
        records = build_vehicle_records(_raw("buses", payload), TEST_BBOX, lookup)

    assert len(records) == 1
    assert records[0].vehicle_key == "buses:veh-e1"
    assert records[0].confidence == "static_join"
    assert records[0].route_short_name == "333"


def test_mutated_trip_id_still_produces_a_degraded_record(static_db: Path):
    payload = _feed_message([("e1", "T1-MUTATED", "R1", 0.5, 0.5)])
    with StaticLookup(static_db) as lookup:
        records = build_vehicle_records(_raw("buses", payload), TEST_BBOX, lookup)

    assert len(records) == 1
    assert records[0].confidence == "degraded_route_fallback"
    assert records[0].route_short_name == "333"


def test_non_vehicle_positions_feed_yields_nothing(static_db: Path):
    raw = RawFeed(
        mode="buses",
        feed_type="trip_updates",
        fetched_at=datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc),
        payload=b"irrelevant",
    )
    with StaticLookup(static_db) as lookup:
        assert build_vehicle_records(raw, TEST_BBOX, lookup) == []
