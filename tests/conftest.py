import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from engine.config import BoundingBox
from engine.static.cache import SCHEMA

# Simple unit-square box, independent of the real CBD bounding box, so the
# expected in/out membership of each fixture point is obvious by inspection.
TEST_BBOX = BoundingBox(north=1.0, south=0.0, west=0.0, east=1.0)


@pytest.fixture
def static_db(tmp_path: Path) -> Path:
    """A minimal SQLite cache (routes+trips only) for static-join tests."""
    db_path = tmp_path / "cache.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO routes VALUES (?,?,?,?,?,?,?,?)",
        ("R1", "A1", "333", "Route Three Three Three", "", "3", "FF0000", "FFFFFF"),
    )
    conn.execute(
        "INSERT INTO trips VALUES (?,?,?,?,?,?,?,?)",
        ("T1", "R1", "SVC1", "SH1", "City Circular", "0", None, "1"),
    )
    conn.commit()
    conn.close()
    return db_path

_FILES = {
    "agency.txt": [
        "agency_id,agency_name,agency_url,agency_timezone,agency_lang,agency_phone",
        "A1,Test Agency,http://example.com,Australia/Sydney,EN,",
    ],
    "routes.txt": [
        "route_id,agency_id,route_short_name,route_long_name,route_desc,route_type,route_color,route_text_color",
        "R1,A1,R1,Route One,,3,FF0000,FFFFFF",
        "R2,A1,R2,Route Two,,3,00FF00,FFFFFF",
    ],
    "trips.txt": [
        "route_id,service_id,trip_id,shape_id,trip_headsign,direction_id,block_id,wheelchair_accessible",
        "R1,SVC1,T1,SH1,Headsign One,0,,1",
        # T2 only ever stops outside the box -- must not survive filtering.
        "R2,SVC2,T2,SH2,Headsign Two,0,,1",
        # T3 uses a shape not shared with T1, to check shape-id union logic.
        "R1,SVC1,T3,SH3,Headsign Three,0,,1",
    ],
    "stops.txt": [
        "stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type,parent_station,wheelchair_boarding",
        "S1,,Stop One,0.5,0.5,0,,0",
        "S2,,Stop Two (outside),5.0,5.0,0,,0",
        "S3,,Stop Three,0.1,0.9,0,,0",
    ],
    "stop_times.txt": [
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence,stop_headsign,pickup_type,drop_off_type,shape_dist_traveled",
        "T1,08:00:00,08:00:00,S1,1,,0,0,0.0",
        "T1,08:05:00,08:05:00,S2,2,,0,0,500.0",
        "T2,08:00:00,08:00:00,S2,1,,0,0,0.0",
        "T3,08:00:00,08:00:00,S3,1,,0,0,0.0",
    ],
    "shapes.txt": [
        "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled",
        "SH1,0.5,0.5,1,0.0",
        "SH1,2.0,2.0,2,500.0",
        "SH2,5.0,5.0,1,0.0",
        "SH2,6.0,6.0,2,500.0",
        "SH3,0.2,0.2,1,0.0",
    ],
    "calendar.txt": [
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date",
        "SVC1,1,1,1,1,1,0,0,20260101,20261231",
        "SVC2,1,1,1,1,1,0,0,20260101,20261231",
    ],
    "calendar_dates.txt": [
        "service_id,date,exception_type",
        "SVC1,20260101,1",
    ],
}


@pytest.fixture
def gtfs_bundle(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    for name, lines in _FILES.items():
        (bundle_dir / name).write_text("\n".join(lines) + "\n")
    return bundle_dir
