"""Tests for the data pipeline (src/pipeline/): ingestion -> normalisation ->
analytics -> publish.

These use synthetic GTFS-Realtime protobuf messages, so they need no network
access and no TFNSW_API_KEY - this is the "data engine ... fully verifiable
with no renderer attached at all" property called out in the proposal.
"""

import time

import pytest
from google.transit import gtfs_realtime_pb2

from pipeline.analytics import (
    RollingAggregator,
    classify_adherence,
    congestion_grid,
    route_bunching_scores,
    route_line_scores,
    score_vehicles,
    snap_to_shape,
    vehicle_congestion_score,
)
from pipeline.ingestion.realtime_gtfs import RawFeeds
from pipeline.ingestion.static_gtfs import StaticGTFS, TripInfo
from pipeline.normalise import VehicleRecord, normalise
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


def test_normalise_reads_occupancy_status_when_the_feed_sets_it(static_gtfs):
    # Built directly (not via _vehicle_positions_feed) since occupancy_status
    # isn't part of that helper's tuple shape, which every other test here
    # also relies on.
    now = int(time.time())
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = now

    crowded = feed.entity.add()
    crowded.id = "bus1"
    crowded.vehicle.vehicle.id = "bus1"
    crowded.vehicle.trip.trip_id = "trip_A"
    crowded.vehicle.position.latitude = -33.87
    crowded.vehicle.position.longitude = 151.21
    crowded.vehicle.timestamp = now
    crowded.vehicle.occupancy_status = gtfs_realtime_pb2.VehiclePosition.STANDING_ROOM_ONLY

    unset = feed.entity.add()
    unset.id = "bus2"
    unset.vehicle.vehicle.id = "bus2"
    unset.vehicle.trip.trip_id = "trip_A"
    unset.vehicle.position.latitude = -33.87
    unset.vehicle.position.longitude = 151.21
    unset.vehicle.timestamp = now
    # occupancy_status deliberately left untouched - proto3 explicit
    # presence means HasField is False, not "EMPTY" (value 0).

    no_data = feed.entity.add()
    no_data.id = "bus3"
    no_data.vehicle.vehicle.id = "bus3"
    no_data.vehicle.trip.trip_id = "trip_A"
    no_data.vehicle.position.latitude = -33.87
    no_data.vehicle.position.longitude = 151.21
    no_data.vehicle.timestamp = now
    no_data.vehicle.occupancy_status = gtfs_realtime_pb2.VehiclePosition.NO_DATA_AVAILABLE

    raw = RawFeeds(vehicle_positions={"buses": feed})
    records = {r.vehicle_id: r for r in normalise(raw, static_gtfs, now=now)}

    assert records["bus1"].occupancy == "STANDING_ROOM_ONLY"
    assert records["bus2"].occupancy is None
    assert records["bus3"].occupancy is None  # NO_DATA_AVAILABLE treated the same as unset


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


def test_normalise_drops_vehicles_outside_the_cbd_bbox(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed(
                [
                    ("cbd_bus", "trip_A", -33.87, 151.21, now),      # inside the CBD box
                    ("far_bus", "trip_B", -33.70, 151.05, now),      # western suburbs, outside
                ]
            )
        },
        trip_updates={},
    )

    records = normalise(raw, static_gtfs, now=now)

    assert {r.vehicle_id for r in records} == {"cbd_bus"}


@pytest.mark.parametrize(
    "speed_mps, delay_seconds, expected",
    [
        (8.3, 0, 0.0),          # free-flow, on time -> no congestion
        (0.0, 0, 0.571),        # stationary, on time -> full speed term, re-weighted (no bunching)
        (None, 600, 1.0),       # no speed, 10 min late -> delay term saturates
        (None, None, None),     # nothing to go on
    ],
)
def test_vehicle_congestion_score(speed_mps, delay_seconds, expected):
    result = vehicle_congestion_score(speed_mps, delay_seconds)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=0.05)


def test_vehicle_congestion_score_uses_bunching_alone_when_others_missing():
    assert vehicle_congestion_score(None, None, 1.0) == pytest.approx(1.0)
    assert vehicle_congestion_score(None, None, 0.0) == pytest.approx(0.0)


def test_vehicle_congestion_score_blends_all_three_signals():
    # free-flow speed (0 contribution) + on time (0 contribution) + fully
    # bunched (1) -> only the bunching weight (0.15 of the 0.5 total weight
    # among these three signals, occupancy absent) contributes
    score = vehicle_congestion_score(8.3, 0, 1.0)
    assert score == pytest.approx(0.3, abs=0.01)


def test_vehicle_congestion_score_uses_occupancy_alone_when_others_missing():
    # FULL is the top of the 0-5 rank scale -> full weight, full value
    assert vehicle_congestion_score(None, None, None, "FULL") == pytest.approx(1.0)
    # EMPTY is the bottom of the scale -> zero contribution
    assert vehicle_congestion_score(None, None, None, "EMPTY") == pytest.approx(0.0)
    # STANDING_ROOM_ONLY is rank 3 of 5
    assert vehicle_congestion_score(None, None, None, "STANDING_ROOM_ONLY") == pytest.approx(0.6)


def test_vehicle_congestion_score_ignores_not_boardable_occupancy():
    # NOT_BOARDABLE means the vehicle isn't carrying passengers at all (e.g.
    # deadheading), not that it's empty - excluded from scoring entirely,
    # same as every signal being unavailable.
    assert vehicle_congestion_score(None, None, None, "NOT_BOARDABLE") is None


def test_vehicle_congestion_score_occupancy_dominates_the_blend():
    # A bus moving at free-flow speed, on time, not bunched, but FULL -
    # occupancy is the main indicator, so this should read as substantially
    # congested even though every other signal says "fine".
    score = vehicle_congestion_score(8.3, 0, 0.0, "FULL")
    assert score == pytest.approx(0.5, abs=0.01)  # occupancy's weight (0.5) is half the total


def _stationary_record(vehicle_id, lat, lon, occupancy=None):
    return VehicleRecord(
        vehicle_id=vehicle_id, mode="buses", route_id="100", route_short_name="M10",
        trip_id="trip_A", shape_id=None,
        latitude=lat, longitude=lon, bearing=None, speed=0.0,
        timestamp=int(time.time()), delay_seconds=0, cancelled=False,
        occupancy=occupancy,
    )


def test_score_vehicles_passes_occupancy_through_to_congestion_score():
    static = StaticGTFS(stops={"stop_1": (-33.8700, 151.2000)})
    # Far from any stop so speed still counts too - isolating that
    # occupancy, not just the near-stop exclusion, reaches the score.
    record = _stationary_record("v3", -33.8750, 151.2000, occupancy="FULL")

    score_vehicles([record], static)

    # (0.0, 0, 0.571) case plus occupancy=FULL blended in at its dominant weight
    assert record.congestion_score > 0.571


def test_score_vehicles_excludes_zero_speed_near_a_stop_from_scoring():
    # TfNSW's live feed never sets current_status/stop_id (confirmed against
    # the real feed - see config.NEAR_STOP_THRESHOLD_METRES), so a stationary
    # vehicle right next to a real stop is assumed to be boarding, not stuck.
    static = StaticGTFS(stops={"stop_1": (-33.8700, 151.2000)})
    record = _stationary_record("v1", -33.8701, 151.2000)  # ~11m from the stop

    score_vehicles([record], static)

    assert record.speed == 0.0  # the raw GPS reading is untouched, still shown honestly
    # speed dropped from scoring entirely -> only the on-time delay signal counts
    assert record.congestion_score == pytest.approx(0.0, abs=0.01)


def test_score_vehicles_still_flags_zero_speed_far_from_any_stop():
    static = StaticGTFS(stops={"stop_1": (-33.8700, 151.2000)})
    record = _stationary_record("v2", -33.8750, 151.2000)  # ~556m from the stop - not boarding

    score_vehicles([record], static)

    # same inputs as the (0.0, 0, 0.571) case in test_vehicle_congestion_score -
    # far from any stop, 0 km/h counts fully as congestion again.
    assert record.congestion_score == pytest.approx(0.571, abs=0.01)


def test_route_bunching_scores_flags_close_buses_on_same_shape(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed(
                [
                    ("close1", "trip_A", -33.8700, 151.2005, now),
                    ("close2", "trip_A", -33.8700, 151.2006, now),  # ~9m along the shape
                ]
            )
        },
        trip_updates={},
    )
    records = normalise(raw, static_gtfs, now=now)

    bunching = route_bunching_scores(records, static_gtfs)

    assert bunching.get("close1", 0.0) > 0.9
    assert bunching.get("close2", 0.0) > 0.9


def test_route_bunching_scores_ignores_far_apart_buses(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed(
                [
                    ("far1", "trip_A", -33.8700, 151.2000, now),
                    ("far2", "trip_A", -33.8700, 151.2100, now),  # ~925m along the shape
                ]
            )
        },
        trip_updates={},
    )
    records = normalise(raw, static_gtfs, now=now)

    bunching = route_bunching_scores(records, static_gtfs)

    assert bunching.get("far1", 0.0) < 0.2
    assert bunching.get("far2", 0.0) < 0.2


def test_route_bunching_scores_ignores_lone_bus_on_a_shape(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed([("solo", "trip_A", -33.87, 151.21, now)])
        },
        trip_updates={},
    )
    records = normalise(raw, static_gtfs, now=now)

    assert route_bunching_scores(records, static_gtfs) == {}


def test_route_line_scores_averages_by_shape_not_route(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed(
                [
                    ("bus1", "trip_A", -33.87, 151.21, now),
                    ("bus2", "trip_A", -33.871, 151.211, now),
                ]
            )
        },
        # bus1's trip is on time (0), bus2's is 10 min late (600) -> mean 0.5
        trip_updates={"buses": _trip_updates_feed([("trip_A", 0)])},
    )
    records = score_vehicles(normalise(raw, static_gtfs, now=now), static_gtfs)
    # Set distinct scores directly (rather than relying on real speed/delay
    # inputs) to prove this is a genuine average, not just pass-through.
    by_id = {r.vehicle_id: r for r in records}
    by_id["bus1"].congestion_score = 0.0
    by_id["bus2"].congestion_score = 1.0

    lines = route_line_scores(records)

    assert set(lines) == {"shape_100"}  # trip_A's shape_id, from the fixture
    line = lines["shape_100"]
    assert line["route_short_name"] == "M10"
    assert line["vehicle_count"] == 2
    assert line["score"] == pytest.approx(0.5)


def test_route_line_scores_excludes_unscored_vehicles(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed([("bus1", "trip_A", -33.87, 151.21, now)])
        },
        trip_updates={},  # no delay, no speed -> normalise leaves congestion_score None
    )
    records = normalise(raw, static_gtfs, now=now)

    assert route_line_scores(records) == {}


def test_congestion_grid_buckets_and_flags_hotspots(static_gtfs):
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed(
                [
                    ("slow1", "trip_A", -33.8710, 151.2060, now),
                    ("slow2", "trip_A", -33.8711, 151.2061, now),
                ]
            )
        },
        trip_updates={"buses": _trip_updates_feed([("trip_A", 600)])},
    )
    records = score_vehicles(normalise(raw, static_gtfs, now=now), static_gtfs)

    grid = congestion_grid(records)

    assert grid["cells"], "expected at least one populated grid cell"
    top = grid["cells"][0]
    assert top["vehicle_count"] == 2  # both buses fell in the same cell
    assert grid["hotspot_count"] >= 1
    # each cell exposes an explicit footprint so the map can show what it covers
    assert set(top["bounds"]) == {"min_lat", "max_lat", "min_lon", "max_lon"}
    assert top["cell_id"].startswith("r")
    assert "trip_A" not in top["routes"]  # routes are names/ids, populated
    assert top["routes"] == ["M10"]


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
