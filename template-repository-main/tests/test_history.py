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
    """A timestamp guaranteed to fall in `hour` on `day`, regardless of the
    machine's local timezone quirks around DST - noon-ish keeps it simple."""
    return datetime.datetime(2026, 1, 5, hour, 30, 0).timestamp() if day == "2026-01-05" else None


def test_query_period_returns_empty_when_nothing_logged():
    result = history.query_period([7, 8])

    assert result == {
        "congestion": {"cells": [], "hotspot_count": 0, "priority_count": 0, "cell_metres": 150},
        "route_lines": {},
        "sample_days": 0,
    }


def test_log_and_query_round_trip_for_a_cell():
    cells = [{"cell_id": "r10c20", "score": 0.8, "vehicle_count": 3}]
    history.log_snapshot(cells, {}, now=_at_hour(8))

    result = history.query_period([7, 8])

    assert result["sample_days"] == 1
    assert len(result["congestion"]["cells"]) == 1
    cell = result["congestion"]["cells"][0]
    assert cell["cell_id"] == "r10c20"
    assert cell["score"] == pytest.approx(0.8)
    assert cell["vehicle_count"] == 3
    assert cell["hotspot"] is True  # 0.8 >= the 0.6 threshold


def test_query_period_averages_across_multiple_readings():
    history.log_snapshot([{"cell_id": "r1c1", "score": 0.2, "vehicle_count": 1}], {}, now=_at_hour(7))
    history.log_snapshot([{"cell_id": "r1c1", "score": 0.6, "vehicle_count": 3}], {}, now=_at_hour(8))

    result = history.query_period([7, 8])

    cell = result["congestion"]["cells"][0]
    assert cell["score"] == pytest.approx(0.4)  # mean of 0.2 and 0.6
    assert cell["vehicle_count"] == 2  # mean of 1 and 3


def test_query_period_excludes_hours_outside_the_requested_window():
    history.log_snapshot([{"cell_id": "r1c1", "score": 0.9, "vehicle_count": 5}], {}, now=_at_hour(14))

    result = history.query_period([7, 8])  # AM peak - excludes hour 14

    assert result["congestion"]["cells"] == []
    assert result["sample_days"] == 0


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
