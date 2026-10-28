"""Tests for historical trend storage (src/pipeline/history.py).

Uses a temp SQLite file (via monkeypatching history.DB_PATH) so these never
touch the real data/cache/history.sqlite, and control `now` directly so
hour-of-day bucketing is deterministic regardless of when the test runs.
"""

import datetime

import pytest

from pipeline import history


@pytest.fixture(autouse=True)
def temp_history_db(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "history_test.sqlite")


def _at_hour(hour: int, day: str = "2026-01-05") -> float:
    """A timestamp guaranteed to fall in `hour` on `day` (YYYY-MM-DD)."""
    year, month, date_ = (int(p) for p in day.split("-"))
    return datetime.datetime(year, month, date_, hour, 30, 0).timestamp()


def test_query_period_returns_empty_when_nothing_logged():
    result = history.query_period([7, 8])

    assert result == {
        "congestion": {"cells": [], "hotspot_count": 0, "cell_metres": 150},
        "route_lines": {},
        "date": None,
    }


def test_log_and_query_round_trip_for_a_cell():
    cells = [{"cell_id": "r10c20", "score": 0.8, "vehicle_count": 3}]
    history.log_snapshot(cells, {}, now=_at_hour(8))

    result = history.query_period([7, 8])

    assert result["date"] == "2026-01-05"
    assert len(result["congestion"]["cells"]) == 1
    cell = result["congestion"]["cells"][0]
    assert cell["cell_id"] == "r10c20"
    assert cell["score"] == pytest.approx(0.8)
    assert cell["vehicle_count"] == 3
    assert cell["hotspot"] is True  # 0.8 >= the 0.6 threshold


def test_query_period_averages_multiple_readings_within_the_same_day():
    history.log_snapshot([{"cell_id": "r1c1", "score": 0.2, "vehicle_count": 1}], {}, now=_at_hour(7))
    history.log_snapshot([{"cell_id": "r1c1", "score": 0.6, "vehicle_count": 3}], {}, now=_at_hour(8))

    result = history.query_period([7, 8])

    cell = result["congestion"]["cells"][0]
    assert cell["score"] == pytest.approx(0.4)  # mean of 0.2 and 0.6
    assert cell["vehicle_count"] == 2  # mean of 1 and 3


def test_query_period_uses_only_the_most_recent_day_not_a_blend():
    # Same cell, same hour window, two different days with very different
    # scores - the older day should be ignored entirely, not averaged in.
    history.log_snapshot(
        [{"cell_id": "r1c1", "score": 0.1, "vehicle_count": 1}], {}, now=_at_hour(8, "2026-01-04")
    )
    history.log_snapshot(
        [{"cell_id": "r1c1", "score": 0.9, "vehicle_count": 5}], {}, now=_at_hour(8, "2026-01-05")
    )

    result = history.query_period([7, 8])

    assert result["date"] == "2026-01-05"  # the more recent day
    cell = result["congestion"]["cells"][0]
    assert cell["score"] == pytest.approx(0.9)  # only the 05th's reading, not blended with the 04th's 0.1
    assert cell["vehicle_count"] == 5


def test_query_period_excludes_hours_outside_the_requested_window():
    history.log_snapshot([{"cell_id": "r1c1", "score": 0.9, "vehicle_count": 5}], {}, now=_at_hour(14))

    result = history.query_period([7, 8])  # AM peak - excludes hour 14

    assert result["congestion"]["cells"] == []
    assert result["date"] is None


def test_log_and_query_round_trip_for_a_route():
    route_lines = {
        "shape_1": {"route_id": "100", "route_short_name": "M10", "score": 0.5, "vehicle_count": 4}
    }
    history.log_snapshot([], route_lines, now=_at_hour(17))

    result = history.query_period([16, 17])

    assert "shape_1" in result["route_lines"]
    line = result["route_lines"]["shape_1"]
    assert line["route_short_name"] == "M10"
    assert line["score"] == pytest.approx(0.5)
    assert line["vehicle_count"] == 4


def test_log_stop_occupancy_is_a_noop_for_an_empty_list():
    history.log_stop_occupancy([], now=_at_hour(8))  # must not raise or create rows

    assert history.query_stop_occupancy_peaks() == {}


def test_query_stop_occupancy_peaks_tracks_the_worst_reading_per_stop():
    history.log_stop_occupancy(
        [
            {"stop_id": "200035", "vehicle_id": "bus1", "route_short_name": "B1", "mode": "buses", "occupancy": "MANY_SEATS_AVAILABLE"},
            {"stop_id": "200035", "vehicle_id": "bus2", "route_short_name": "B1", "mode": "buses", "occupancy": "FULL"},
            {"stop_id": "200035", "vehicle_id": "bus3", "route_short_name": "B1", "mode": "buses", "occupancy": "FEW_SEATS_AVAILABLE"},
            {"stop_id": "200036", "vehicle_id": "bus4", "route_short_name": "M10", "mode": "buses", "occupancy": "EMPTY"},
        ],
        now=_at_hour(8),
    )

    peaks = history.query_stop_occupancy_peaks()

    assert peaks["200035"] == {"peak_rank": 5, "visit_count": 3}  # FULL (rank 5) is the worst of the three
    assert peaks["200036"] == {"peak_rank": 0, "visit_count": 1}


def test_query_stop_occupancy_peaks_ignores_not_boardable_readings():
    history.log_stop_occupancy(
        [{"stop_id": "200035", "vehicle_id": "bus1", "route_short_name": None, "mode": "buses", "occupancy": "NOT_BOARDABLE"}],
        now=_at_hour(8),
    )

    assert history.query_stop_occupancy_peaks() == {}


def test_query_stop_occupancy_peaks_uses_only_the_most_recent_day():
    history.log_stop_occupancy(
        [{"stop_id": "200035", "vehicle_id": "bus1", "route_short_name": "B1", "mode": "buses", "occupancy": "FULL"}],
        now=_at_hour(8, "2026-01-04"),
    )
    history.log_stop_occupancy(
        [{"stop_id": "200035", "vehicle_id": "bus2", "route_short_name": "B1", "mode": "buses", "occupancy": "EMPTY"}],
        now=_at_hour(8, "2026-01-05"),
    )

    peaks = history.query_stop_occupancy_peaks()  # no day given -> most recent

    assert peaks["200035"] == {"peak_rank": 0, "visit_count": 1}  # only the 05th's EMPTY reading


def test_query_stop_occupancy_peaks_can_target_a_specific_day():
    history.log_stop_occupancy(
        [{"stop_id": "200035", "vehicle_id": "bus1", "route_short_name": "B1", "mode": "buses", "occupancy": "FULL"}],
        now=_at_hour(8, "2026-01-04"),
    )
    history.log_stop_occupancy(
        [{"stop_id": "200035", "vehicle_id": "bus2", "route_short_name": "B1", "mode": "buses", "occupancy": "EMPTY"}],
        now=_at_hour(8, "2026-01-05"),
    )

    peaks = history.query_stop_occupancy_peaks(day="2026-01-04")

    assert peaks["200035"] == {"peak_rank": 5, "visit_count": 1}


def test_log_stop_occupancy_accepts_delay_seconds_optionally():
    # No delay_seconds key at all (older-style call) must not raise.
    history.log_stop_occupancy(
        [{"stop_id": "200035", "vehicle_id": "bus1", "route_short_name": "B1", "mode": "buses", "occupancy": "FULL"}],
        now=_at_hour(8),
    )

    assert history.query_stop_lateness_by_occupancy() == {}  # excluded - no delay_seconds logged


def test_query_stop_lateness_by_occupancy_breaks_down_by_rank():
    history.log_stop_occupancy(
        [
            {"stop_id": "200035", "vehicle_id": "bus1", "route_short_name": "B1", "mode": "buses",
             "occupancy": "STANDING_ROOM_ONLY", "delay_seconds": 300},
            {"stop_id": "200035", "vehicle_id": "bus2", "route_short_name": "B1", "mode": "buses",
             "occupancy": "STANDING_ROOM_ONLY", "delay_seconds": 180},
            {"stop_id": "200035", "vehicle_id": "bus3", "route_short_name": "B1", "mode": "buses",
             "occupancy": "MANY_SEATS_AVAILABLE", "delay_seconds": 20},
        ],
        now=_at_hour(8),
    )

    result = history.query_stop_lateness_by_occupancy()

    assert result["200035"][3] == {"avg_delay_seconds": 240.0, "visit_count": 2}  # STANDING_ROOM_ONLY (rank 3)
    assert result["200035"][1] == {"avg_delay_seconds": 20.0, "visit_count": 1}   # MANY_SEATS_AVAILABLE (rank 1)


def test_query_stop_lateness_by_occupancy_excludes_visits_with_no_delay():
    history.log_stop_occupancy(
        [
            {"stop_id": "200035", "vehicle_id": "bus1", "route_short_name": "B1", "mode": "buses",
             "occupancy": "FULL", "delay_seconds": None},
            {"stop_id": "200035", "vehicle_id": "bus2", "route_short_name": "B1", "mode": "buses",
             "occupancy": "FULL", "delay_seconds": 100},
        ],
        now=_at_hour(8),
    )

    result = history.query_stop_lateness_by_occupancy()

    assert result["200035"][5] == {"avg_delay_seconds": 100.0, "visit_count": 1}  # only bus2 counted


def test_query_stop_lateness_by_occupancy_can_filter_to_one_stop():
    history.log_stop_occupancy(
        [
            {"stop_id": "200035", "vehicle_id": "bus1", "route_short_name": "B1", "mode": "buses",
             "occupancy": "FULL", "delay_seconds": 100},
            {"stop_id": "200036", "vehicle_id": "bus2", "route_short_name": "M10", "mode": "buses",
             "occupancy": "EMPTY", "delay_seconds": 0},
        ],
        now=_at_hour(8),
    )

    result = history.query_stop_lateness_by_occupancy(stop_id="200035")

    assert set(result.keys()) == {"200035"}
