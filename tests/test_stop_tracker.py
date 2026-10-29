from engine.history.stop_tracker import StopVisitTracker
from engine.normalise.vehicle import VehicleRecord


def _vehicle(key, occupancy=None, delay=None, route="M10", mode="buses"):
    return VehicleRecord(
        vehicle_key=key,
        mode=mode,
        trip_id="T1",
        route_id="R1",
        route_short_name=route,
        route_color=None,
        headsign=None,
        lat=-33.86,
        lon=151.20,
        bearing=None,
        position_timestamp=None,
        occupancy_status=occupancy,
        confidence="static_join",
        schedule_deviation_seconds=delay,
    )


def test_first_observation_emits_nothing():
    tracker = StopVisitTracker()

    visit = tracker.record(_vehicle("v1", occupancy="FEW_SEATS_AVAILABLE", delay=30), next_stop_id="STOP_A")

    assert visit is None


def test_same_next_stop_emits_nothing_but_updates_the_tracked_reading():
    tracker = StopVisitTracker()
    tracker.record(_vehicle("v1", occupancy="MANY_SEATS_AVAILABLE", delay=10), next_stop_id="STOP_A")

    visit = tracker.record(_vehicle("v1", occupancy="STANDING_ROOM_ONLY", delay=50), next_stop_id="STOP_A")

    assert visit is None


def test_stop_change_emits_a_visit_for_the_stop_just_passed():
    tracker = StopVisitTracker()
    tracker.record(_vehicle("v1", occupancy="MANY_SEATS_AVAILABLE", delay=10), next_stop_id="STOP_A")
    # still approaching A, reading updates - this is the one that should be
    # reported for A, not the first one
    tracker.record(_vehicle("v1", occupancy="STANDING_ROOM_ONLY", delay=50), next_stop_id="STOP_A")

    visit = tracker.record(_vehicle("v1", occupancy="FEW_SEATS_AVAILABLE", delay=5), next_stop_id="STOP_B")

    assert visit == {
        "stop_id": "STOP_A",
        "vehicle_key": "v1",
        "route_short_name": "M10",
        "mode": "buses",
        "occupancy_status": "STANDING_ROOM_ONLY",  # the LAST reading seen approaching A, not the first
        "delay_seconds": 50,
    }


def test_missing_next_stop_id_is_ignored_not_treated_as_a_change():
    tracker = StopVisitTracker()
    tracker.record(_vehicle("v1", occupancy="MANY_SEATS_AVAILABLE", delay=10), next_stop_id="STOP_A")

    visit = tracker.record(_vehicle("v1", occupancy="FULL", delay=999), next_stop_id=None)

    assert visit is None


def test_tracking_recovers_after_a_missed_trip_update():
    tracker = StopVisitTracker()
    tracker.record(_vehicle("v1", occupancy="MANY_SEATS_AVAILABLE", delay=10), next_stop_id="STOP_A")
    tracker.record(_vehicle("v1", occupancy="FULL", delay=999), next_stop_id=None)  # dropped this cycle

    # next_stop_id is still compared against the last KNOWN one (STOP_A), not reset
    visit = tracker.record(_vehicle("v1", occupancy="FEW_SEATS_AVAILABLE", delay=5), next_stop_id="STOP_B")

    assert visit["stop_id"] == "STOP_A"
    assert visit["occupancy_status"] == "MANY_SEATS_AVAILABLE"  # last reading actually tracked for A


def test_two_vehicles_are_tracked_independently():
    tracker = StopVisitTracker()
    tracker.record(_vehicle("v1", occupancy="EMPTY", delay=0), next_stop_id="STOP_A")
    tracker.record(_vehicle("v2", occupancy="FULL", delay=200), next_stop_id="STOP_X")

    visit1 = tracker.record(_vehicle("v1", occupancy="EMPTY", delay=0), next_stop_id="STOP_B")
    visit2 = tracker.record(_vehicle("v2", occupancy="FULL", delay=200), next_stop_id="STOP_Y")

    assert visit1["stop_id"] == "STOP_A"
    assert visit1["vehicle_key"] == "v1"
    assert visit2["stop_id"] == "STOP_X"
    assert visit2["vehicle_key"] == "v2"
