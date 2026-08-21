from pathlib import Path

from engine.publish.calibration import (
    CalibrationPoint,
    compute_and_save_calibration,
    load_calibration,
    to_local_metric,
)

CENTER_LAT, CENTER_LON = -33.8775, 151.2046  # roughly the real CBD bbox centre


def _cbd_points() -> list[CalibrationPoint]:
    # Four corners of a small region near the real CBD, mapped onto an
    # arbitrary 1920x1080 pixel canvas.
    return [
        CalibrationPoint(lat=-33.860, lon=151.200, pixel_x=100, pixel_y=100),
        CalibrationPoint(lat=-33.860, lon=151.215, pixel_x=1800, pixel_y=100),
        CalibrationPoint(lat=-33.895, lon=151.200, pixel_x=100, pixel_y=980),
        CalibrationPoint(lat=-33.895, lon=151.215, pixel_x=1800, pixel_y=980),
    ]


def test_to_local_metric_centre_maps_to_origin():
    x, y = to_local_metric(CENTER_LAT, CENTER_LON, CENTER_LAT, CENTER_LON)
    assert x == 0
    assert y == 0


def test_to_local_metric_north_is_positive_y():
    x, y = to_local_metric(CENTER_LAT + 0.01, CENTER_LON, CENTER_LAT, CENTER_LON)
    assert y > 0
    assert x == 0


def test_load_calibration_returns_none_when_missing(tmp_path: Path):
    assert load_calibration(tmp_path / "no_such_file.json") is None


def test_save_and_load_round_trip(tmp_path: Path):
    path = tmp_path / "calibration.json"
    saved = compute_and_save_calibration(
        path, _cbd_points(), canvas_width=1920, canvas_height=1080,
        center_lat=CENTER_LAT, center_lon=CENTER_LON,
    )

    assert path.exists()
    loaded = load_calibration(path)

    assert loaded["canvas_width"] == 1920
    assert loaded["canvas_height"] == 1080
    assert loaded["center_lat"] == CENTER_LAT
    assert loaded["center_lon"] == CENTER_LON
    assert len(loaded["points"]) == 4
    assert loaded["points"][0]["lat"] == -33.860
    assert len(loaded["homography"]) == 3
    assert len(loaded["homography"][0]) == 3
    assert "computed_at" in loaded
    assert loaded == saved


def test_recomputing_overwrites_the_previous_calibration(tmp_path: Path):
    path = tmp_path / "calibration.json"
    compute_and_save_calibration(
        path, _cbd_points(), canvas_width=1920, canvas_height=1080,
        center_lat=CENTER_LAT, center_lon=CENTER_LON,
    )

    fewer_points = _cbd_points()[:4]
    fewer_points[0].pixel_x = 50  # a different calibration session
    second = compute_and_save_calibration(
        path, fewer_points, canvas_width=1280, canvas_height=720,
        center_lat=CENTER_LAT, center_lon=CENTER_LON,
    )

    loaded = load_calibration(path)
    assert loaded["canvas_width"] == 1280
    assert loaded == second
