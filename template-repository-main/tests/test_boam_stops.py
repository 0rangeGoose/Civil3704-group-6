"""Tests for the BOAM stop-level ingestion (src/pipeline/ingestion/boam_stops.py).

TfNSW's real BOAM export is behind a portal login and is a ~400MB
statewide file, so these use a small synthetic pipe-delimited file instead
- same columns as the real thing, just a handful of rows.
"""

import pytest

from pipeline.ingestion.boam_stops import BOAM_DIR, load_boam_stops

TEST_BBOX = {"min_lat": -33.90, "max_lat": -33.83, "min_lon": 151.15, "max_lon": 151.26}

HEADER = (
    "SERVICE_DATE|ROUTE|ROUTE_VARIANT|TRIP_ID|DIRECTION|TRIP_POINT|"
    "TRANSIT_STOP_SEQUENCE|SCHD_ARRIVE_TIME|ACTUAL_ARRIVE_TIME|SCHD_HOUR_BAND|"
    "TRIP_CODE|TRANSIT_STOP|TRANSIT_STOP_DESCRIPTION|SUBURB|LATITUDE|LONGITUDE|"
    "DEPOT|BUS_CONFIGURATION|BUS_DOORS|SEATED_CAPACITY|STANDING_CAPACITY|"
    "TOTAL_CAPACITY|OPAL_RECORD_STATUS|OCCUPANCY_RANGE"
)


def _row(stop, desc, lat, lon, occupancy, trip_id="t1"):
    # Only the columns load_boam_stops actually reads are given real
    # values; the rest are blank, matching real-world sparse columns like
    # OPAL_RECORD_STATUS (always blank in the real file).
    return (
        f"2026-10-15|100|100-1|{trip_id}|Outbound||1|09:00:00|09:01:00|09:00 to 09:59|"
        f"|{stop}|{desc}|SYDNEY|{lat}|{lon}|Depot|Bus|2|40|20|60||{occupancy}"
    )


@pytest.fixture
def boam_file(tmp_path):
    path = tmp_path / "BOAM_20261015.txt"
    lines = [
        HEADER,
        _row("1", "Stop One", -33.87, 151.21, "0-20"),
        _row("1", "Stop One", -33.87, 151.21, "81-100"),   # same stop, busier visit -> new peak
        _row("1", "Stop One", -33.87, 151.21, "21-40"),    # same stop, quieter -> doesn't lower the peak
        _row("2", "Stop Two", -33.845, 151.235, "41-60"),
        _row("3", "Stop Three (outside bbox)", -33.5, 151.5, "101-120"),
        "bad|row|with|too|few|fields",  # malformed - should be skipped, not raise
    ]
    path.write_text("\n".join(lines))
    return path


def test_load_boam_stops_returns_none_when_no_file(tmp_path):
    result = load_boam_stops(data_dir=tmp_path)
    assert result is None


def test_load_boam_stops_filters_to_bbox_and_tracks_peak(boam_file):
    boam = load_boam_stops(path=boam_file, bbox=TEST_BBOX)

    assert boam is not None
    stop_ids = {s.stop_id for s in boam.stops}
    assert stop_ids == {"1", "2"}  # stop "3" is outside TEST_BBOX

    stop_one = next(s for s in boam.stops if s.stop_id == "1")
    assert stop_one.peak_rank == 4  # 81-100 -> rank 4, not reset by the later quieter visit
    assert stop_one.visit_count == 3
    assert stop_one.description == "Stop One"

    stop_two = next(s for s in boam.stops if s.stop_id == "2")
    assert stop_two.peak_rank == 2  # 41-60
    assert stop_two.visit_count == 1


def test_to_markers_shape(boam_file):
    boam = load_boam_stops(path=boam_file, bbox=TEST_BBOX)
    markers = boam.to_markers()

    assert len(markers) == 2
    stop_one = next(m for m in markers if m["stop_id"] == "1")
    assert stop_one["peak_label"] == "81-100"
    assert stop_one["lat"] == pytest.approx(-33.87)
    assert stop_one["lon"] == pytest.approx(151.21)
    assert stop_one["visit_count"] == 3


def test_load_boam_stops_picks_most_recent_file_by_name(tmp_path):
    older = tmp_path / "BOAM_20261001.txt"
    newer = tmp_path / "BOAM_20261015.txt"
    older.write_text(HEADER + "\n" + _row("1", "Old", -33.87, 151.21, "0-20"))
    newer.write_text(HEADER + "\n" + _row("1", "New", -33.87, 151.21, "0-20"))

    boam = load_boam_stops(data_dir=tmp_path, bbox=TEST_BBOX)

    assert boam.stops[0].description == "New"


def test_boam_dir_defaults_under_data_boam():
    assert BOAM_DIR.name == "boam"
    assert BOAM_DIR.parent.name == "data"
