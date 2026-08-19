from pathlib import Path

from engine.static.filter import (
    build_filtered_static_data,
    filter_shapes,
    filter_stops,
    trips_touching_stops,
)
from tests.conftest import TEST_BBOX


def test_filter_stops_keeps_only_in_box(gtfs_bundle: Path):
    assert filter_stops(gtfs_bundle / "stops.txt", TEST_BBOX) == {"S1", "S3"}


def test_filter_shapes_keeps_shape_with_any_point_in_box(gtfs_bundle: Path):
    # SH1 has one point in the box and one far outside -- still retained.
    # SH2 is entirely outside -- dropped.
    assert filter_shapes(gtfs_bundle / "shapes.txt", TEST_BBOX) == {"SH1", "SH3"}


def test_trips_touching_stops_requires_a_retained_stop(gtfs_bundle: Path):
    stop_ids = filter_stops(gtfs_bundle / "stops.txt", TEST_BBOX)
    trip_ids = trips_touching_stops(gtfs_bundle / "stop_times.txt", stop_ids)
    # T2 only ever stops at S2 (outside the box) and must be excluded.
    assert trip_ids == {"T1", "T3"}


def test_build_filtered_static_data_end_to_end(gtfs_bundle: Path):
    result = build_filtered_static_data(gtfs_bundle, TEST_BBOX)

    assert result.stop_ids == {"S1", "S3"}
    assert result.trip_ids == {"T1", "T3"}
    assert result.route_ids == {"R1"}  # R2 only served by the excluded T2
    assert result.service_ids == {"SVC1"}
    assert result.agency_ids == {"A1"}
    assert result.shape_ids == {"SH1", "SH3"}

    assert {r["trip_id"] for r in result.trips_rows} == {"T1", "T3"}
    assert {r["route_id"] for r in result.routes_rows} == {"R1"}
    assert {r["agency_id"] for r in result.agency_rows} == {"A1"}
    assert {r["service_id"] for r in result.calendar_rows} == {"SVC1"}
    assert {r["service_id"] for r in result.calendar_dates_rows} == {"SVC1"}


def test_trip_touching_box_keeps_its_full_stop_sequence(gtfs_bundle: Path):
    """T1's second stop (S2) is outside the box, but since T1 touches S1 it
    must keep both stop_times rows -- a vehicle shouldn't pop into existence
    with no history."""
    from engine.static.filter import stream_filtered_rows

    result = build_filtered_static_data(gtfs_bundle, TEST_BBOX)
    rows = list(
        stream_filtered_rows(gtfs_bundle / "stop_times.txt", "trip_id", result.trip_ids)
    )
    t1_stops = {r["stop_id"] for r in rows if r["trip_id"] == "T1"}
    assert t1_stops == {"S1", "S2"}
