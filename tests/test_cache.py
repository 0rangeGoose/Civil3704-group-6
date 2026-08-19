import sqlite3
from pathlib import Path

from engine.static.cache import build_cache
from engine.static.filter import build_filtered_static_data
from tests.conftest import TEST_BBOX


def test_build_cache_writes_expected_row_counts(gtfs_bundle: Path, tmp_path: Path):
    filtered = build_filtered_static_data(gtfs_bundle, TEST_BBOX)
    db_path = tmp_path / "cache.db"

    counts = build_cache(db_path, gtfs_bundle, filtered)

    assert counts == {
        "agency": 1,
        "routes": 1,
        "stops": 2,
        "trips": 2,
        "calendar": 1,
        "calendar_dates": 1,
        "stop_times": 3,  # T1 has 2 rows, T3 has 1
        "shapes": 3,  # SH1 has 2 points, SH3 has 1
    }

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 2
        assert conn.execute(
            "SELECT stop_id FROM stops WHERE stop_id = 'S2'"
        ).fetchone() is None  # outside-box stop must not appear

        t1_stops = {
            row[0]
            for row in conn.execute(
                "SELECT stop_id FROM stop_times WHERE trip_id = 'T1'"
            ).fetchall()
        }
        assert t1_stops == {"S1", "S2"}
    finally:
        conn.close()


def test_build_cache_is_idempotent_when_rerun(gtfs_bundle: Path, tmp_path: Path):
    filtered = build_filtered_static_data(gtfs_bundle, TEST_BBOX)
    db_path = tmp_path / "cache.db"

    build_cache(db_path, gtfs_bundle, filtered)
    counts_second_run = build_cache(db_path, gtfs_bundle, filtered)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 2
    finally:
        conn.close()
    assert counts_second_run["trips"] == 2
