import importlib.util
from pathlib import Path

import pytest

from engine.normalise.travel_time import FreeFlow, TravelTimeTracker, level_for
from engine.normalise.vehicle import VehicleRecord

_spec = importlib.util.spec_from_file_location(
    "analyse_congestion", Path(__file__).resolve().parent.parent / "scripts" / "analyse_congestion.py")
analyse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyse)


def bus(d, ts, key="b1", shape="SH1", mode="buses"):
    ts += 1_000_000  # a real epoch-like time (0 would read as "no timestamp")
    return VehicleRecord(
        vehicle_key=key, mode=mode, trip_id="T1", route_id="R1", route_short_name="333", route_color=None,
        headsign=None, lat=0, lon=0, bearing=None, position_timestamp=ts, occupancy_status=None,
        confidence="geometric_match", shape_id=shape, distance_along_shape_m=d)


def test_interval_closes_after_300m_and_compares_with_free_flow():
    # free flow 10 m/s on segment SH1:1 (200-400m)
    tracker = TravelTimeTracker(FreeFlow({"SH1:1": 10.0}, {}))
    assert tracker.observe(bus(0, 1000)) is None
    assert tracker.observe(bus(150, 1030)) is None          # only 150m so far
    iv = tracker.observe(bus(400, 1080))                   # 400m in 80s = 5 m/s
    assert iv.speed_mps == 5.0 and iv.segment == ("SH1", 1)
    assert iv.tti == 2.0 and iv.level == 1                 # twice as slow as at night


def test_shape_fallback_and_no_baseline():
    tracker = TravelTimeTracker(FreeFlow({}, {"SH1": 8.0}))
    tracker.observe(bus(0, 0))
    assert tracker.observe(bus(320, 40)).tti == 1.0        # 8 m/s vs shape's 8 m/s
    bare = TravelTimeTracker(None)
    bare.observe(bus(0, 0))
    iv = bare.observe(bus(320, 40))
    assert iv.speed_mps == 8.0 and iv.tti is None and iv.level is None


def test_resets_on_new_shape_long_gap_or_going_backwards():
    tracker = TravelTimeTracker(None)
    tracker.observe(bus(0, 0))
    assert tracker.observe(bus(400, 50, shape="SH2")) is None    # another route: new start
    assert tracker.observe(bus(800, 400, shape="SH2")) is None   # 350s gap: new start
    assert tracker.observe(bus(500, 420, shape="SH2")) is None   # went backwards: new start


def test_only_buses_and_parked_buses_ignored():
    tracker = TravelTimeTracker(None)
    tracker.observe(bus(0, 0, mode="metro"))
    assert tracker.observe(bus(400, 40, mode="metro")) is None
    tracker.observe(bus(0, 0))
    assert tracker.observe(bus(300, 3000)) is None               # gap > 180s anyway


@pytest.mark.parametrize("tti, level", [(1.0, 0), (1.5, 0), (1.8, 1), (2.5, 2), (4, 3)])
def test_levels(tti, level):
    assert level_for(tti) == level


def test_agreement_statistics():
    perfect = analyse.agreement([(0, 0), (1, 1), (2, 2), (3, 3)] * 5)
    assert perfect["exact"] == 1.0 and perfect["weighted_kappa"] == 1.0
    off_by_one = analyse.agreement([(0, 1), (1, 2), (2, 3), (3, 2)] * 5)
    assert off_by_one["exact"] == 0.0 and off_by_one["within_one_level"] == 1.0
    assert analyse.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert analyse.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
