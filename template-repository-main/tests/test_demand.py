"""Tests for the demand-side ingestion (src/pipeline/ingestion/demand.py).

TfNSW's actual TZP24/TZ21 files are behind a portal login, so these use
small synthetic GeoJSON + CSV files instead - built with deliberately
different (but still matching) column names than the primary candidates,
to prove the flexible column detection actually works and isn't just
hardcoded to one guessed schema.
"""

import json
import time

import pytest
from google.transit import gtfs_realtime_pb2

from pipeline.analytics import congestion_grid, score_vehicles
from pipeline.ingestion.demand import DemandZone, DemandZones, load_demand_zones
from pipeline.ingestion.realtime_gtfs import RawFeeds
from pipeline.ingestion.static_gtfs import StaticGTFS, TripInfo
from pipeline.normalise import normalise

# A bbox covering zones A and C below, but not zone B (simulates the model
# footprint being much smaller than TZ21's state-wide coverage).
TEST_BBOX = {"min_lat": -33.90, "max_lat": -33.83, "min_lon": 151.15, "max_lon": 151.26}


def _vehicle_positions_feed(entries):
    """entries: list of (vehicle_id, trip_id, lat, lon, timestamp)"""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(time.time())
    for vid, trip_id, lat, lon, ts in entries:
        entity = feed.entity.add()
        entity.id = vid
        entity.vehicle.vehicle.id = vid
        entity.vehicle.trip.trip_id = trip_id
        entity.vehicle.position.latitude = lat
        entity.vehicle.position.longitude = lon
        entity.vehicle.timestamp = ts
    return feed


def _trip_updates_feed(entries):
    """entries: list of (trip_id, delay_seconds)"""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(time.time())
    for trip_id, delay in entries:
        entity = feed.entity.add()
        entity.id = trip_id
        entity.trip_update.trip.trip_id = trip_id
        stop_time_update = entity.trip_update.stop_time_update.add()
        stop_time_update.arrival.delay = delay
    return feed


def _write_zones_geojson(path):
    # GeoJSON coordinate order is [lon, lat].
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"TZ_CODE": "1"},  # exact-match candidate, different case
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [151.20, -33.88], [151.22, -33.88],
                        [151.22, -33.86], [151.20, -33.86], [151.20, -33.88],
                    ]],
                },
            },
            {
                "type": "Feature",
                "properties": {"TZ_CODE": "2"},  # far outside TEST_BBOX
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [151.00, -33.80], [151.02, -33.80],
                        [151.02, -33.78], [151.00, -33.78], [151.00, -33.80],
                    ]],
                },
            },
            {
                "type": "Feature",
                "properties": {"TZ_CODE": "3"},  # inside TEST_BBOX, lower employment
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [151.23, -33.85], [151.24, -33.85],
                        [151.24, -33.84], [151.23, -33.84], [151.23, -33.85],
                    ]],
                },
            },
        ],
    }
    path.write_text(json.dumps(geojson))


def _write_employment_csv(path):
    # Deliberately NOT the first-choice candidate names, to exercise the
    # substring-matching fallback in _find_column.
    path.write_text(
        "TZ_CODE,Total_Employment_2024,Year\n"
        "1,1000,2024\n"
        "2,500,2024\n"
        "3,200,2024\n"
        "1,900,2023\n"  # different year, should be ignored when year="2024" is passed
    )


@pytest.fixture
def demand_files(tmp_path):
    zones_path = tmp_path / "travel_zones_2021.geojson"
    employment_path = tmp_path / "tzp24_employment.csv"
    _write_zones_geojson(zones_path)
    _write_employment_csv(employment_path)
    return zones_path, employment_path


def test_load_demand_zones_returns_none_when_files_missing(tmp_path):
    result = load_demand_zones(
        zones_path=tmp_path / "missing.geojson",
        employment_path=tmp_path / "missing.csv",
    )
    assert result is None


def test_load_demand_zones_filters_to_bbox_and_normalises(demand_files):
    zones_path, employment_path = demand_files

    # reference_year=2024 makes the auto-pick-nearest-year deterministic
    # regardless of when this test happens to run.
    demand = load_demand_zones(zones_path, employment_path, bbox=TEST_BBOX, reference_year=2024)

    assert demand is not None
    zone_ids = {z.zone_id for z in demand.zones}
    assert zone_ids == {"1", "3"}  # zone "2" is outside TEST_BBOX
    assert demand.max_employment == 1000.0  # zone "1", year 2024 only (not summed with 2023's 900)


def test_load_demand_zones_respects_year_filter(demand_files):
    zones_path, employment_path = demand_files

    demand = load_demand_zones(zones_path, employment_path, bbox=TEST_BBOX, year="2024")

    zone_one = next(z for z in demand.zones if z.zone_id == "1")
    assert zone_one.employment == 1000.0  # not 1900 (which would double-count both years)


def test_demand_score_looks_up_the_containing_zone(demand_files):
    zones_path, employment_path = demand_files
    demand = load_demand_zones(zones_path, employment_path, bbox=TEST_BBOX, reference_year=2024)

    # Well inside zone "1" (employment 1000, the max -> normalised to 1.0)
    assert demand.demand_score(-33.87, 151.21) == pytest.approx(1.0)
    # Well inside zone "3" (employment 200 -> 200/1000)
    assert demand.demand_score(-33.845, 151.235) == pytest.approx(0.2)
    # Nowhere near any loaded zone
    assert demand.demand_score(-33.5, 151.5) is None


def test_congestion_grid_computes_priority_when_demand_supplied():
    demand = DemandZones(
        zones=[
            DemandZone(
                zone_id="1",
                employment=1000.0,
                bbox=(-33.88, -33.86, 151.20, 151.22),
                rings=[[
                    (-33.88, 151.20), (-33.88, 151.22),
                    (-33.86, 151.22), (-33.86, 151.20), (-33.88, 151.20),
                ]],
            )
        ],
        max_employment=1000.0,
    )

    static = StaticGTFS(
        trips={"trip_A": TripInfo(route_id="100", route_short_name="M10", shape_id=None)}
    )
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={
            "buses": _vehicle_positions_feed([("bus1", "trip_A", -33.87, 151.21, now)])
        },
        trip_updates={"buses": _trip_updates_feed([("trip_A", 600)])},  # very late -> high congestion
    )
    records = score_vehicles(normalise(raw, static, now=now), static)

    grid = congestion_grid(records, demand=demand)

    assert len(grid["cells"]) == 1
    cell = grid["cells"][0]
    assert cell["demand_score"] == pytest.approx(1.0)
    assert cell["priority_score"] == pytest.approx(cell["score"] * 1.0)
    assert cell["priority"] is True
    assert grid["priority_count"] == 1


def test_congestion_grid_has_no_priority_data_without_demand():
    static = StaticGTFS(trips={"trip_A": TripInfo(route_id="100", route_short_name="M10", shape_id=None)})
    now = int(time.time())
    raw = RawFeeds(
        vehicle_positions={"buses": _vehicle_positions_feed([("bus1", "trip_A", -33.87, 151.21, now)])},
        # a delay so the bus actually gets a congestion_score and shows up
        # in the grid at all (congestion_grid skips unscored vehicles)
        trip_updates={"buses": _trip_updates_feed([("trip_A", 120)])},
    )
    records = score_vehicles(normalise(raw, static, now=now), static)

    grid = congestion_grid(records)  # no demand supplied

    assert grid["priority_count"] == 0
    assert grid["cells"][0]["demand_score"] is None
    assert grid["cells"][0]["priority_score"] is None
    assert grid["cells"][0]["priority"] is False
