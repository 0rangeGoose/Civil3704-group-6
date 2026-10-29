import sqlite3
from pathlib import Path

from engine.publish.route_focus import load_route_catalog, route_detail


def _add_second_direction(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO routes VALUES (?,?,?,?,?,?,?,?)",
        ("R1-back", "A1", "333", "Route Three Three Three (back)", "", "3", "", "FFFFFF"),
    )
    conn.execute("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?)", ("T2", "R1-back", "SVC1", "SH2", "Back", "1", None, "1"))
    conn.executemany(
        "INSERT INTO stop_times VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("T1", "08:00:00", "08:00:00", "S1", 1, None, None, None, None),
            ("T1", "08:05:00", "08:05:00", "S2", 2, None, None, None, None),
            ("T2", "09:00:00", "09:00:00", "S2", 1, None, None, None, None),
            ("T2", "09:05:00", "09:05:00", "S3", 2, None, None, None, None),
        ],
    )
    conn.commit()
    conn.close()


def test_catalog_has_one_entry_per_short_name(static_db: Path):
    _add_second_direction(static_db)

    catalog = load_route_catalog(static_db)

    assert [r["name"] for r in catalog] == ["333"]
    assert catalog[0]["color"] == "FF0000"


def test_detail_groups_every_route_id_sharing_the_short_name(static_db: Path):
    _add_second_direction(static_db)

    detail = route_detail(static_db, "333")

    assert sorted(detail["shape_ids"]) == ["SH1", "SH2"]
    assert sorted(detail["stop_ids"]) == ["S1", "S2", "S3"]
    assert detail["color"] == "FF0000"  # the blank colour on the second route_id is skipped


def test_unknown_route_has_no_detail(static_db: Path):
    assert route_detail(static_db, "NOPE") is None
