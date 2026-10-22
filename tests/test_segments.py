from datetime import datetime, timezone
from pathlib import Path

import pytest

from engine.normalise.build import build_vehicle_records
from engine.normalise.levels import crowding_from_carriages
from engine.normalise.map_match import ShapeIndex
from engine.normalise.proto import pb
from engine.normalise.segments import SegmentAggregator
from engine.normalise.static_lookup import StaticLookup
from engine.normalise.vehicle import VehicleRecord
from engine.publish.control import ControlError, ControlState
from engine.sources.base import RawFeed
from tests.conftest import TEST_BBOX


def _vehicle(key, along_m, mode="buses", congestion=None, occupancy=None, shape="SH1"):
    return VehicleRecord(
        vehicle_key=key, mode=mode, trip_id="T1", route_id="R1", route_short_name="333",
        route_color=None, headsign=None, lat=-33.865, lon=151.207, bearing=None,
        position_timestamp=None, occupancy_status=occupancy, confidence="static_join",
        congestion_level=congestion, shape_id=shape, distance_along_shape_m=along_m,
    )


@pytest.fixture
def aggregator(static_db: Path) -> SegmentAggregator:
    # SH1 in the fixture db is a ~1.1km straight line, so 200m sections.
    return SegmentAggregator(ShapeIndex(static_db), segment_m=200, window_s=600)


def test_readings_land_on_the_section_they_are_in(aggregator):
    aggregator.record(_vehicle("a", 50, congestion="SEVERE_CONGESTION"), at=0)
    aggregator.record(_vehicle("b", 450, congestion="RUNNING_SMOOTHLY"), at=0)
    by_id = {s["id"]: s for s in aggregator.snapshot(now=1)}
    assert by_id["SH1:0"]["congestion"] == 3
    assert by_id["SH1:2"]["congestion"] == 0
    assert len(by_id["SH1:0"]["pts"]) >= 2


def test_each_vehicle_counts_once_at_its_latest_reading(aggregator):
    for t in range(4):  # a bus stuck at a light for four polls
        aggregator.record(_vehicle("stuck", 10, congestion="SEVERE_CONGESTION"), at=t)
    aggregator.record(_vehicle("stuck", 10, congestion="STOP_AND_GO"), at=5)
    aggregator.record(_vehicle("other", 20, congestion="RUNNING_SMOOTHLY"), at=5)
    (section,) = aggregator.snapshot(now=6)
    assert section["vehicles"] == 2
    assert section["congestion"] == 0.5


def test_old_readings_expire(aggregator):
    aggregator.record(_vehicle("a", 10, congestion="CONGESTION"), at=0)
    assert aggregator.snapshot(now=599)
    assert aggregator.snapshot(now=601) == []


def test_congestion_only_counts_road_modes_but_crowding_counts_all(aggregator):
    aggregator.record(_vehicle("m", 10, mode="metro", congestion="SEVERE_CONGESTION",
                               occupancy="STANDING_ROOM_ONLY"), at=0)
    (section,) = aggregator.snapshot(now=1)
    assert section["congestion"] is None
    assert section["crowding"] == 2


def test_unmatched_or_unreported_vehicles_are_ignored(aggregator):
    aggregator.record(_vehicle("no-shape", 10, congestion="CONGESTION", shape=None), at=0)
    aggregator.record(_vehicle("nothing", 10), at=0)
    assert aggregator.snapshot(now=1) == []


def test_train_crowding_uses_the_median_carriage():
    names = ["MANY_SEATS_AVAILABLE", "MANY_SEATS_AVAILABLE", "FEW_SEATS_AVAILABLE", "FULL"]
    assert crowding_from_carriages(names) == "MANY_SEATS_AVAILABLE"
    assert crowding_from_carriages(["STANDING_ROOM_ONLY", "FULL", "FULL"]) == "FULL"
    assert crowding_from_carriages([]) is None


def test_build_reads_congestion_and_carriage_crowding(static_db: Path):
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    bus = feed.entity.add()
    bus.id = "bus"
    bus.vehicle.position.latitude, bus.vehicle.position.longitude = 0.5, 0.5
    bus.vehicle.congestion_level = pb.VehiclePosition.STOP_AND_GO
    bus.vehicle.occupancy_status = pb.VehiclePosition.FEW_SEATS_AVAILABLE
    train = feed.entity.add()
    train.id = "train"
    train.vehicle.position.latitude, train.vehicle.position.longitude = 0.5, 0.5
    train.vehicle.congestion_level = pb.VehiclePosition.UNKNOWN_CONGESTION_LEVEL
    for status in ("STANDING_ROOM_ONLY", "STANDING_ROOM_ONLY", "MANY_SEATS_AVAILABLE"):
        car = train.vehicle.Extensions[pb.consist].add()
        car.position_in_consist = 1
        car.occupancy_status = pb.CarriageDescriptor.OccupancyStatus.Value(status)
    raw = RawFeed("buses", "vehicle_positions", datetime(2026, 10, 23, tzinfo=timezone.utc),
                  feed.SerializeToString())
    with StaticLookup(static_db) as lookup:
        records = {r.vehicle_key: r for r in build_vehicle_records(raw, TEST_BBOX, lookup)}
    assert records["buses:bus"].congestion_level == "STOP_AND_GO"
    assert records["buses:bus"].occupancy_status == "FEW_SEATS_AVAILABLE"
    assert records["buses:train"].congestion_level is None
    assert records["buses:train"].occupancy_status == "STANDING_ROOM_ONLY"


def test_lens_and_line_layer_controls():
    c = ControlState()
    assert c.state["lens"] == "adherence" and c.state["line_layer"] == "none"
    c.apply({"lens": "crowding", "line_layer": "congestion"})
    assert c.state["lens"] == "crowding" and c.state["line_layer"] == "congestion"
    with pytest.raises(ControlError):
        c.apply({"lens": "none"})
    with pytest.raises(ControlError):
        c.apply({"line_layer": "demand"})
