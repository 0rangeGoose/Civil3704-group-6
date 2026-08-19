import sqlite3
from pathlib import Path

import pytest

from engine.config import BoundingBox
from engine.publish.shapes_export import build_render_shapes
from engine.static.cache import SCHEMA

BBOX = BoundingBox(north=1.0, south=0.0, west=0.0, east=1.0)


@pytest.fixture
def long_shape_db(tmp_path: Path) -> Path:
    """A shape with one point inside the box and two points far outside it,
    mimicking a regional trip that only briefly crosses the CBD."""
    db_path = tmp_path / "cache.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO shapes VALUES (?,?,?,?,?)",
        [
            ("LONG", 50.0, 50.0, 1, 0.0),  # far outside
            ("LONG", 0.5, 0.5, 2, 100.0),  # inside the box
            ("LONG", -50.0, -50.0, 3, 200.0),  # far outside
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def test_clips_to_padded_box_keeping_boundary_neighbours(long_shape_db: Path):
    shapes = build_render_shapes(long_shape_db, BBOX, pad_ratio=0.15)

    assert "LONG" in shapes
    points = shapes["LONG"]
    # The far-outside point at (50,50) is dropped -- not adjacent to the
    # in-box point. The in-box point and both its immediate neighbours are
    # kept (the neighbour rule), so all 3 points here actually survive
    # since each is adjacent to the middle one... check precisely instead:
    assert [0.5, 0.5] in points
    assert [50.0, 50.0] in points  # neighbour of the in-box point, kept
    assert [-50.0, -50.0] in points  # also a neighbour of the in-box point


def test_shape_entirely_outside_padded_box_is_dropped(tmp_path: Path):
    db_path = tmp_path / "cache.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO shapes VALUES (?,?,?,?,?)",
        [("FAR", 50.0, 50.0, 1, 0.0), ("FAR", 51.0, 51.0, 2, 100.0)],
    )
    conn.commit()
    conn.close()

    shapes = build_render_shapes(db_path, BBOX, pad_ratio=0.15)
    assert "FAR" not in shapes


def test_a_middle_point_between_two_distant_out_of_box_runs_is_dropped(tmp_path: Path):
    """4 points: two far outside, then two more far outside on the other
    side -- none adjacent to anything in-box, so all should be dropped."""
    db_path = tmp_path / "cache.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO shapes VALUES (?,?,?,?,?)",
        [
            ("FAR2", 50.0, 50.0, 1, 0.0),
            ("FAR2", 51.0, 51.0, 2, 100.0),
            ("FAR2", 52.0, 52.0, 3, 200.0),
            ("FAR2", 53.0, 53.0, 4, 300.0),
        ],
    )
    conn.commit()
    conn.close()

    shapes = build_render_shapes(db_path, BBOX, pad_ratio=0.15)
    assert "FAR2" not in shapes
