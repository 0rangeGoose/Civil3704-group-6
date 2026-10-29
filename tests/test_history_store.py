from datetime import datetime
from zoneinfo import ZoneInfo

from engine.history.store import record_stop_visit, stop_breakdown, stop_summaries

SYDNEY = ZoneInfo("Australia/Sydney")


def _at(year, month, day, hour=8, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=SYDNEY).timestamp()


def test_stop_breakdown_returns_empty_when_nothing_recorded(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"

    assert stop_breakdown(db_path) == {}


def test_round_trip_breaks_down_by_occupancy_level(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"
    record_stop_visit(db_path, "STOP_A", "v1", "M10", "buses", "MANY_SEATS_AVAILABLE", 10, recorded_at=_at(2026, 10, 29))
    record_stop_visit(db_path, "STOP_A", "v2", "M10", "buses", "MANY_SEATS_AVAILABLE", 30, recorded_at=_at(2026, 10, 29))
    record_stop_visit(db_path, "STOP_A", "v3", "M10", "buses", "FULL", 200, recorded_at=_at(2026, 10, 29))
    record_stop_visit(db_path, "STOP_B", "v4", "X1", "buses", "EMPTY", 0, recorded_at=_at(2026, 10, 29))

    result = stop_breakdown(db_path)

    assert result["STOP_A"]["MANY_SEATS_AVAILABLE"] == {"avg_delay_seconds": 20.0, "visit_count": 2}
    assert result["STOP_A"]["FULL"] == {"avg_delay_seconds": 200.0, "visit_count": 1}
    assert result["STOP_B"]["EMPTY"] == {"avg_delay_seconds": 0.0, "visit_count": 1}


def test_visits_with_no_delay_are_excluded_not_counted_as_zero(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"
    record_stop_visit(db_path, "STOP_A", "v1", "M10", "buses", "FULL", None, recorded_at=_at(2026, 10, 29))
    record_stop_visit(db_path, "STOP_A", "v2", "M10", "buses", "FULL", 100, recorded_at=_at(2026, 10, 29))

    result = stop_breakdown(db_path)

    assert result["STOP_A"]["FULL"] == {"avg_delay_seconds": 100.0, "visit_count": 1}


def test_visits_with_no_occupancy_are_excluded(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"
    record_stop_visit(db_path, "STOP_A", "v1", "M10", "buses", None, 50, recorded_at=_at(2026, 10, 29))

    assert stop_breakdown(db_path) == {}


def test_stop_breakdown_can_filter_to_one_stop(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"
    record_stop_visit(db_path, "STOP_A", "v1", "M10", "buses", "FULL", 100, recorded_at=_at(2026, 10, 29))
    record_stop_visit(db_path, "STOP_B", "v2", "X1", "buses", "EMPTY", 0, recorded_at=_at(2026, 10, 29))

    result = stop_breakdown(db_path, stop_id="STOP_A")

    assert set(result.keys()) == {"STOP_A"}


def test_stop_breakdown_can_filter_to_one_sydney_local_day(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"
    record_stop_visit(db_path, "STOP_A", "v1", "M10", "buses", "FULL", 100, recorded_at=_at(2026, 10, 28))
    record_stop_visit(db_path, "STOP_A", "v2", "M10", "buses", "EMPTY", 0, recorded_at=_at(2026, 10, 29))

    result = stop_breakdown(db_path, day="2026-10-29")

    assert result["STOP_A"] == {"EMPTY": {"avg_delay_seconds": 0.0, "visit_count": 1}}


def test_stop_summaries_empty_when_nothing_recorded(tmp_path):
    assert stop_summaries(tmp_path / "stop_visits.sqlite") == {}


def test_stop_summary_uses_medians_so_one_glitch_does_not_drag_it(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"
    for i, delay in enumerate([30, 60, 90, 4500]):  # 4500s = a 75-min feed glitch
        record_stop_visit(db_path, "STOP_A", f"v{i}", "M10", "buses", "MANY_SEATS_AVAILABLE", delay, recorded_at=_at(2026, 10, 29))

    summary = stop_summaries(db_path)["STOP_A"]

    assert summary["visits"] == 4
    assert summary["median_delay_s"] == 75


def test_stop_summary_splits_delay_by_seated_vs_crowded_arrivals(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"
    for i in range(6):
        record_stop_visit(db_path, "STOP_A", f"s{i}", "M10", "buses", "MANY_SEATS_AVAILABLE", 60, recorded_at=_at(2026, 10, 29))
    for i in range(5):
        record_stop_visit(db_path, "STOP_A", f"c{i}", "M10", "buses", "STANDING_ROOM_ONLY", 300, recorded_at=_at(2026, 10, 29))
    record_stop_visit(db_path, "STOP_A", "u1", "M10", "buses", None, 0, recorded_at=_at(2026, 10, 29))

    summary = stop_summaries(db_path)["STOP_A"]

    assert summary["visits"] == 12  # unknown occupancy still counts towards delay
    assert summary["crowded_share"] == round(5 / 11, 3)  # ...but not towards crowding
    assert summary["median_delay_seated_s"] == 60
    assert summary["median_delay_crowded_s"] == 300


def test_stop_summary_split_needs_enough_visits_on_each_side(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"
    for i in range(6):
        record_stop_visit(db_path, "STOP_A", f"s{i}", "M10", "buses", "EMPTY", 60, recorded_at=_at(2026, 10, 29))
    record_stop_visit(db_path, "STOP_A", "c1", "M10", "buses", "FULL", 900, recorded_at=_at(2026, 10, 29))

    summary = stop_summaries(db_path)["STOP_A"]

    assert summary["median_delay_seated_s"] == 60
    assert summary["median_delay_crowded_s"] is None


def test_recorded_at_defaults_to_now_when_omitted(tmp_path):
    db_path = tmp_path / "stop_visits.sqlite"

    record_stop_visit(db_path, "STOP_A", "v1", "M10", "buses", "FULL", 100)  # no recorded_at

    result = stop_breakdown(db_path)
    assert result["STOP_A"]["FULL"]["visit_count"] == 1
