from pathlib import Path

import pytest

from engine.normalise.map_match import ShapeIndex


def test_snap_point_on_the_line_has_near_zero_offset(static_db: Path):
    index = ShapeIndex(static_db)
    match = index.snap_to_shape("SH1", -33.865, 151.207)
    assert match is not None
    assert match.distance_from_shape_m == pytest.approx(0, abs=1.0)
    # Roughly halfway along the ~1100m shape.
    assert match.distance_along_shape_m == pytest.approx(550, rel=0.1)


def test_snap_point_off_the_line_reports_offset_distance(static_db: Path):
    index = ShapeIndex(static_db)
    # ~0.001 deg east at this latitude is roughly 93m.
    match = index.snap_to_shape("SH1", -33.865, 151.208)
    assert match is not None
    assert 50 < match.distance_from_shape_m < 150


def test_snap_unknown_shape_returns_none(static_db: Path):
    index = ShapeIndex(static_db)
    assert index.snap_to_shape("NO_SUCH_SHAPE", -33.865, 151.207) is None


def test_nearest_shape_finds_the_only_shape_within_range(static_db: Path):
    index = ShapeIndex(static_db)
    result = index.nearest_shape(-33.865, 151.2071, max_distance_m=50)
    assert result is not None
    shape_id, match = result
    assert shape_id == "SH1"
    assert match.distance_from_shape_m < 50


def test_nearest_shape_respects_max_distance(static_db: Path):
    index = ShapeIndex(static_db)
    # Nowhere near the CBD -- must not match even though SH1 is the only shape.
    result = index.nearest_shape(-34.5, 150.5, max_distance_m=500)
    assert result is None


def test_route_for_shape(static_db: Path):
    index = ShapeIndex(static_db)
    assert index.route_for_shape("SH1") == "R1"
    assert index.route_for_shape("NO_SUCH_SHAPE") is None
