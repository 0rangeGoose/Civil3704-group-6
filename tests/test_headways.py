from engine.normalise.aggregate import TripPunctuality, TripReading
from engine.normalise.headways import TripTimetable, compute_headways


def test_headway_is_the_gap_to_the_next_trip_on_the_same_route_and_direction():
    trips = [
        ("a1", "333", "0", 7 * 3600),
        ("a2", "333", "0", 7 * 3600 + 300),
        ("a3", "333", "0", 7 * 3600 + 900),
        ("b1", "333", "1", 7 * 3600 + 60),   # other direction: its own sequence
        ("b2", "333", "1", 7 * 3600 + 1260),
        ("c1", "380", "0", 8 * 3600),        # only trip on its route: no gap
    ]
    gaps = compute_headways(trips)
    assert gaps["a1"] == 300 and gaps["a2"] == 600
    assert gaps["a3"] == 600          # last trip takes the gap before it
    assert gaps["b1"] == 1200 and gaps["b2"] == 1200
    assert "c1" not in gaps


def test_timetable_lookups_handle_unknown_trips():
    tt = TripTimetable({"a1": "stop1"}, {"a1": 300})
    assert tt.first_stop("a1") == "stop1" and tt.headway("a1") == 300
    assert tt.first_stop(None) is None and tt.headway("sydneytrains-live-id") is None


def test_each_trip_counts_once_with_its_latest_reading():
    p = TripPunctuality()
    now = 10_000.0
    # a rail trip's final-stop prediction updates as it approaches: the latest wins
    p.record("rail:T1", TripReading(now - 600, "sydneytrains", "T1", "final stop (predicted)", "X", 400, "late"))
    p.record("rail:T1", TripReading(now - 60, "sydneytrains", "T1", "final stop (predicted)", "X", 120, "on_time"))
    p.record("rail:T2", TripReading(now - 30, "sydneytrains", "T1", "final stop (predicted)", "X", 500, "late"))
    p.record("rail:T3", TripReading(now - 30, "sydneytrains", "T1", "final stop (predicted)", "X", None, "unknown"))
    p.record("bus:B1", TripReading(now - 5000, "buses", "333", "first stop", "S", 0, "on_time"))

    last_15 = p.breakdown(900, now)
    assert last_15 == {"sydneytrains": {"trips": 2, "on_time": 50.0, "late": 50.0}}  # T3 unmeasured: not counted
    assert p.breakdown(7200, now)["buses"] == {"trips": 1, "on_time": 100.0}
