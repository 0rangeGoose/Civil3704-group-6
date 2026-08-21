from pathlib import Path

from engine.normalise.aggregate import (
    Interchange,
    RollingAdherence,
    RollingInterchangeActivity,
    count_active_alerts,
    count_services_near_interchanges,
    load_cbd_ids,
    load_interchanges,
    nearby_interchanges,
)
from engine.normalise.proto import pb


def test_rolling_adherence_breakdown_percentages():
    roll = RollingAdherence(windows_seconds=(300, 900, 3600))
    now = 1_000_000.0
    roll.record("buses", "on_time", at=now - 10)
    roll.record("buses", "on_time", at=now - 20)
    roll.record("buses", "late", at=now - 30)
    roll.record("buses", "on_time", at=now - 400)  # outside the 300s window

    breakdown_5min = roll.breakdown(300, now=now)
    assert breakdown_5min["buses"]["on_time"] == 66.7
    assert breakdown_5min["buses"]["late"] == 33.3

    breakdown_15min = roll.breakdown(900, now=now)
    assert breakdown_15min["buses"]["on_time"] == 75.0
    assert breakdown_15min["buses"]["late"] == 25.0


def test_rolling_adherence_prunes_beyond_the_largest_window():
    roll = RollingAdherence(windows_seconds=(300, 900, 3600))
    now = 1_000_000.0
    roll.record("buses", "on_time", at=now - 4000)  # older than the 3600s max window
    roll.record("buses", "on_time", at=now - 5)

    roll._prune(now)
    assert len(roll._obs) == 1


def test_rolling_adherence_separates_modes():
    roll = RollingAdherence(windows_seconds=(300, 900, 3600))
    now = 1_000_000.0
    roll.record("buses", "on_time", at=now - 5)
    roll.record("sydneytrains", "late", at=now - 5)

    breakdown = roll.breakdown(300, now=now)
    assert breakdown["buses"] == {"on_time": 100.0}
    assert breakdown["sydneytrains"] == {"late": 100.0}


def test_load_interchanges_centroids_matching_stops(static_db: Path):
    import sqlite3

    conn = sqlite3.connect(static_db)
    conn.execute(
        "INSERT INTO stops VALUES (?,?,?,?,?,?,?,?)",
        ("S1", "", "Central Station, Stand A", -33.880, 151.206, "0", None, "0"),
    )
    conn.execute(
        "INSERT INTO stops VALUES (?,?,?,?,?,?,?,?)",
        ("S2", "", "Central Station, Stand B", -33.882, 151.208, "0", None, "0"),
    )
    conn.commit()
    conn.close()

    interchanges = load_interchanges(static_db, ["Central", "Nonexistent Place"])
    assert len(interchanges) == 1
    assert interchanges[0].name == "Central"
    assert interchanges[0].lat == -33.881
    assert interchanges[0].lon == 151.207


def test_count_services_near_interchanges():
    interchanges = [Interchange(name="Central", lat=-33.881, lon=151.207)]
    positions = [
        (-33.881, 151.207),  # right at Central
        (-33.881, 151.2071),  # very close
        (-34.5, 150.5),  # nowhere near
    ]
    counts = count_services_near_interchanges(interchanges, positions, radius_m=300)
    assert counts == {"Central": 2}


def _alert_feed(route_id: str, severity=None) -> bytes:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "alert1"
    sel = e.alert.informed_entity.add()
    sel.route_id = route_id
    if severity is not None:
        e.alert.severity_level = severity
    return feed.SerializeToString()


def test_count_active_alerts_matches_cbd_route():
    payload = _alert_feed("R1", severity=pb.Alert.SEVERE)
    result = count_active_alerts(payload, cbd_route_ids={"R1"}, cbd_stop_ids=set())
    assert result["count"] == 1
    assert result["by_severity"] == {"SEVERE": 1}
    assert result["route_ids"] == ["R1"]


def test_count_active_alerts_ignores_non_cbd_route():
    payload = _alert_feed("SOME_OTHER_ROUTE")
    result = count_active_alerts(payload, cbd_route_ids={"R1"}, cbd_stop_ids=set())
    assert result["count"] == 0


def test_load_cbd_ids(static_db: Path):
    route_ids, stop_ids = load_cbd_ids(static_db)
    assert route_ids == {"R1"}
    assert stop_ids == set()  # fixture's static_db has no stops rows by default


def test_nearby_interchanges_returns_names_within_radius():
    interchanges = [
        Interchange(name="Central", lat=-33.881, lon=151.207),
        Interchange(name="Wynyard", lat=-33.865, lon=151.206),
    ]
    names = nearby_interchanges(interchanges, -33.881, 151.2071, radius_m=300)
    assert names == ["Central"]


def test_nearby_interchanges_empty_when_nothing_close():
    interchanges = [Interchange(name="Central", lat=-33.881, lon=151.207)]
    assert nearby_interchanges(interchanges, -34.5, 150.5, radius_m=300) == []


def test_rolling_interchange_activity_counts_distinct_vehicles():
    roll = RollingInterchangeActivity(window_seconds=300)
    now = 1_000_000.0
    roll.record("Central", "buses:v1", at=now - 10)
    roll.record("Central", "buses:v2", at=now - 20)
    roll.record("Central", "buses:v1", at=now - 5)  # same vehicle again, still 15s poll cadence
    roll.record("Wynyard", "buses:v3", at=now - 15)

    counts = roll.counts(now=now)
    assert counts["Central"] == 2  # v1 counted once despite two observations
    assert counts["Wynyard"] == 1


def test_rolling_interchange_activity_prunes_outside_window():
    roll = RollingInterchangeActivity(window_seconds=300)
    now = 1_000_000.0
    roll.record("Central", "buses:v1", at=now - 400)  # outside the window
    roll.record("Central", "buses:v2", at=now - 10)

    counts = roll.counts(now=now)
    assert counts == {"Central": 1}
