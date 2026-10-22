from datetime import datetime, timezone

from engine.normalise.proto import pb
from engine.sources.base import RawFeed
from engine.sources.footprint import FootprintFilter
from tests.conftest import TEST_BBOX

_AT = datetime(2026, 10, 23, 7, 0, 0, tzinfo=timezone.utc)


def _positions(vehicles: list[tuple[str, str, float, float]]) -> RawFeed:
    """vehicles: (entity_id, trip_id, lat, lon)."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1000
    for entity_id, trip_id, lat, lon in vehicles:
        e = feed.entity.add()
        e.id = entity_id
        e.vehicle.trip.trip_id = trip_id
        e.vehicle.position.latitude = lat
        e.vehicle.position.longitude = lon
    return RawFeed("buses", "vehicle_positions", _AT, feed.SerializeToString())


def _trip_updates(trips: list[tuple[str, list[str]]]) -> RawFeed:
    """trips: (trip_id, [stop_id, ...])."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1000
    for trip_id, stop_ids in trips:
        e = feed.entity.add()
        e.id = trip_id
        e.trip_update.trip.trip_id = trip_id
        for stop_id in stop_ids:
            e.trip_update.stop_time_update.add().stop_id = stop_id
    return RawFeed("buses", "trip_updates", _AT, feed.SerializeToString())


def _ids(raw: RawFeed) -> list[str]:
    feed = pb.FeedMessage()
    feed.ParseFromString(raw.payload)
    return [e.id for e in feed.entity]


def test_keeps_vehicles_inside_padded_footprint_only():
    f = FootprintFilter(TEST_BBOX, stop_ids=set(), pad_ratio=0.1)
    out = f.apply(_positions([
        ("in", "T1", 0.5, 0.5),
        ("in_padding", "T2", 1.05, 0.5),  # outside the box but within 10% padding
        ("out", "T3", 5.0, 5.0),
    ]))
    assert _ids(out) == ["in", "in_padding"]


def test_returns_none_when_nothing_is_inside():
    f = FootprintFilter(TEST_BBOX, stop_ids=set())
    assert f.apply(_positions([("out", "T1", 5.0, 5.0)])) is None


def test_trip_updates_kept_for_recent_vehicles_or_footprint_stops():
    f = FootprintFilter(TEST_BBOX, stop_ids={"S_IN"})
    f.apply(_positions([("v1", "T_SEEN", 0.5, 0.5), ("v2", "T_FAR", 5.0, 5.0)]))
    out = f.apply(_trip_updates([
        ("T_SEEN", ["S_OUT"]),   # vehicle was just seen in the footprint
        ("T_STOP", ["S_IN"]),    # stops inside the footprint (train id mismatch case)
        ("T_FAR", ["S_OUT"]),    # neither
    ]))
    assert _ids(out) == ["T_SEEN", "T_STOP"]


def test_trip_seen_one_poll_ago_is_still_kept():
    f = FootprintFilter(TEST_BBOX, stop_ids=set())
    f.apply(_positions([("v1", "T1", 0.5, 0.5)]))
    f.apply(_positions([("v2", "T2", 0.5, 0.5)]))  # T1's vehicle has left
    assert _ids(f.apply(_trip_updates([("T1", []), ("T2", [])]))) == ["T1", "T2"]


def test_alerts_and_undecodable_payloads_pass_through_unchanged():
    f = FootprintFilter(TEST_BBOX, stop_ids=set())
    alerts = RawFeed("all", "service_alerts", _AT, b"anything")
    assert f.apply(alerts) is alerts
    garbage = RawFeed("buses", "vehicle_positions", _AT, b"\xff\xff not protobuf")
    assert f.apply(garbage) is garbage
