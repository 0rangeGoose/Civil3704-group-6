"""Tests for the data pipeline (src/pipeline/): ingestion -> normalisation ->
analytics -> publish.

These use synthetic GTFS-Realtime protobuf messages, so they need no network
access and no TFNSW_API_KEY - this is the "data engine ... fully verifiable
with no renderer attached at all" property called out in the proposal.
"""

import time

import pytest
from google.transit import gtfs_realtime_pb2

from pipeline.analytics import RollingAggregator, classify_adherence, snap_to_shape
from pipeline.ingestion.realtime_gtfs import RawFeeds
from pipeline.ingestion.static_gtfs import StaticGTFS, TripInfo
from pipeline.normalise import normalise
from pipeline.publish import SnapshotStore


def _vehicle_positions_feed(entries):
    """entries: list of (vehicle_id, trip_id, lat, lon, timestamp)"""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(time.time())
    for vid, trip_id, lat, lon, ts in entries:
        entity = feed.entity.add()
        entity.id = vid
        entity.vehicle.vehicle.id = vid
        entity.vehicle.trip.trip_id = trip_id
        entity.vehicle.position.latitude = lat
        entity.vehicle.position.longitude = lon
        entity.vehicle.timestamp = ts
    return feed


def _trip_updates_feed(entries):
    """entries: list of (trip_id, delay_seconds)"""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(time.time())
    for trip_id, delay in entries:
        entity = feed.entity.add()
        entity.id = trip_id
        entity.trip_update.trip.trip_id = trip_id
        stop_time_update = entity.trip_update.stop_time_update.add()
        stop_time_update.arrival.delay = delay
    return feed


@pytest.fixture
def static_gtfs():
    return StaticGTFS(
        trips={
            "trip_A": TripInfo(route_id="100", route_short_name="M10", shape_id="shape_100"),
            "trip_B": TripInfo(route_id="200", route_short_name="M20", shape_id=None),
        },
        shapes={"shape_100": [(-33.87, 151.20), (-33.87, 151.22)]},
    )


def test_normalise_joins_position_with_delay_and_static_route(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed([("bus1", "trip_A", -33.87, 151.21, now)])
        },
        trip_updates={"buses": _trip_updates_feed([("trip_A", 30)])},
    )

    records = normalise(raw, static_gtfs, now=now)

    assert len(records) == 1
    record = records[0]
    assert record.vehicle_id == "bus1"
    assert record.route_short_name == "M10"
    assert record.delay_seconds == 30


def test_normalise_drops_stale_ghost_trail_vehicles(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed(
                [
                    ("bus_fresh", "trip_A", -33.87, 151.21, now),
                    ("bus_stale", "trip_B", -33.88, 151.20, now - 999),
                ]
            )
        },
        trip_updates={},
    )

    records = normalise(raw, static_gtfs, now=now)

    assert {r.vehicle_id for r in records} == {"bus_fresh"}


@pytest.mark.parametrize(
    "delay_seconds, cancelled, expected",
    [
        (30, False, "on_time"),
        (120, False, "late"),
        (400, False, "very_late"),
        (0, True, "cancelled"),
        (None, False, "unknown"),
    ],
)
def test_classify_adherence(delay_seconds, cancelled, expected):
    assert classify_adherence(delay_seconds, cancelled) == expected


def test_rolling_aggregator_computes_per_route_percentages(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed(
                [
                    ("bus1", "trip_A", -33.87, 151.21, now),
                    ("bus2", "trip_B", -33.88, 151.20, now),
                ]
            )
        },
        trip_updates={"buses": _trip_updates_feed([("trip_A", 30), ("trip_B", 400)])},
    )
    records = normalise(raw, static_gtfs, now=now)

    aggregator = RollingAggregator(window_size=10)
    aggregator.update(records)
    snapshot = aggregator.snapshot()

    assert snapshot["100"].on_time_pct == 100.0
    assert snapshot["200"].very_late_pct == 100.0


def test_snap_to_shape_projects_onto_nearest_segment(static_gtfs):
    shape = static_gtfs.shapes["shape_100"]
    result = snap_to_shape(-33.87, 151.21, shape)

    assert result is not None
    snapped_lat, snapped_lon, distance_m = result
    assert distance_m < 50  # point already sits on the shape line


def test_snap_to_shape_returns_none_for_degenerate_shape():
    assert snap_to_shape(-33.87, 151.21, [(-33.87, 151.21)]) is None


def test_snapshot_store_reflects_latest_update(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed([("bus1", "trip_A", -33.87, 151.21, now)])
        },
        trip_updates={"buses": _trip_updates_feed([("trip_A", 30)])},
    )
    records = normalise(raw, static_gtfs, now=now)
    aggregator = RollingAggregator()
    aggregator.update(records)

    store = SnapshotStore()
    store.update(records, aggregator.snapshot())
    snapshot = store.snapshot()

    assert snapshot["vehicle_count"] == 1
    assert "100" in snapshot["route_adherence"]
