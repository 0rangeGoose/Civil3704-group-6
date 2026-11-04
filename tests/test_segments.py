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


class _StraightShapes:
    """Straight shapes for pooling tests: shape id -> (start lat, lon, end lat, lon)."""
    M_PER_DEG = 111_320.0

    def __init__(self, lines):
        self.lines = lines

    def _length(self, shape_id):
        lat1, lon1, lat2, lon2 = self.lines[shape_id]
        kx = self.M_PER_DEG * 0.8303  # cos(33.87 deg)
        return ((lat2 - lat1) ** 2 * self.M_PER_DEG ** 2 + ((lon2 - lon1) * kx) ** 2) ** 0.5

    def shape_length_m(self, shape_id):
        return self._length(shape_id)

    def section_latlon(self, shape_id, start, end):
        lat1, lon1, lat2, lon2 = self.lines[shape_id]
        total = self._length(shape_id)
        at = lambda d: (lat1 + (lat2 - lat1) * d / total, lon1 + (lon2 - lon1) * d / total)
        return [at(start), at(end)]


STREET = (-33.870, 151.207, -33.860, 151.207)  # ~1.1 km due north


@pytest.fixture
def street_aggregator():
    d90 = 90 / 111_320  # 90 m in latitude
    return SegmentAggregator(_StraightShapes({
        "A": STREET,
        "B": (STREET[0] + d90, STREET[1], STREET[2], STREET[3]),  # same street and way, sections offset 90 m
        "back": (STREET[2], STREET[3], STREET[0], STREET[1]),     # same street, other direction
        "parallel": (STREET[0], STREET[1] + 0.00108, STREET[2], STREET[3] + 0.00108),  # ~100 m east
        "cross": (-33.8691, 151.2060, -33.8691, 151.2080),        # crosses it
    }), segment_m=200, window_s=600)


def test_routes_sharing_a_street_pool_their_vehicles(street_aggregator):
    street_aggregator.record(_vehicle("a", 100, congestion="SEVERE_CONGESTION", shape="A"), at=0)
    street_aggregator.record(_vehicle("b", 100, congestion="RUNNING_SMOOTHLY", shape="B"), at=0)
    by_id = {s["id"]: s for s in street_aggregator.snapshot(now=1)}
    # each route's section now shows the street, not its own single bus
    assert by_id["A:0"]["congestion"] == by_id["B:0"]["congestion"] == 1.5
    assert by_id["A:0"]["congestion_n"] == by_id["B:0"]["congestion_n"] == 2


@pytest.mark.parametrize("shape, along", [("back", 1000), ("parallel", 100), ("cross", 100)])
def test_other_directions_parallel_and_crossing_streets_stay_separate(street_aggregator, shape, along):
    street_aggregator.record(_vehicle("a", 100, congestion="SEVERE_CONGESTION", shape="A"), at=0)
    street_aggregator.record(_vehicle("o", along, congestion="RUNNING_SMOOTHLY", shape=shape), at=0)
    by_id = {s["id"]: s for s in street_aggregator.snapshot(now=1)}
    assert by_id["A:0"]["congestion"] == 3 and by_id["A:0"]["congestion_n"] == 1


def test_each_layer_counts_its_own_vehicles(street_aggregator):
    street_aggregator.record(_vehicle("a", 100, congestion="CONGESTION", occupancy="FEW_SEATS_AVAILABLE", shape="A"), at=0)
    street_aggregator.record(_vehicle("m", 100, mode="metro", occupancy="STANDING_ROOM_ONLY", shape="B"), at=0)
    s = {s["id"]: s for s in street_aggregator.snapshot(now=1)}["A:0"]
    assert (s["congestion_n"], s["crowding_n"], s["vehicles"]) == (1, 2, 2)


def test_same_street_check_does_not_depend_on_which_section_asks():
    import math, random
    from engine.normalise.segments import _same_street
    rng = random.Random(4)
    for _ in range(2000):
        frames = []
        for _ in range(2):
            angle = rng.uniform(-0.6, 0.6)
            frames.append((rng.uniform(-120, 120), rng.uniform(-40, 40), math.cos(angle), math.sin(angle)))
        assert _same_street(*frames) == _same_street(*reversed(frames))


def test_both_directions_value_pools_the_other_way_too(street_aggregator):
    street_aggregator.record(_vehicle("a", 100, congestion="SEVERE_CONGESTION", shape="A"), at=0)
    street_aggregator.record(_vehicle("o", 1000, congestion="RUNNING_SMOOTHLY", shape="back"), at=0)
    by_id = {s["id"]: s for s in street_aggregator.snapshot(now=1)}
    a = by_id["A:0"]
    assert (a["congestion"], a["congestion_n"]) == (3, 1)          # this way only
    assert (a["congestion_all"], a["congestion_all_n"]) == (1.5, 2)  # both ways
    assert by_id["back:5"]["congestion_all"] == 1.5


def test_inbound_means_heading_towards_town_hall(street_aggregator):
    # STREET runs north from -33.870 to -33.860; Town Hall is at -33.873, i.e. south of it
    street_aggregator.record(_vehicle("a", 100, congestion="CONGESTION", shape="A"), at=0)
    street_aggregator.record(_vehicle("o", 1000, congestion="CONGESTION", shape="back"), at=0)
    by_id = {s["id"]: s for s in street_aggregator.snapshot(now=1)}
    assert by_id["A:0"]["inbound"] is False
    assert by_id["back:5"]["inbound"] is True
