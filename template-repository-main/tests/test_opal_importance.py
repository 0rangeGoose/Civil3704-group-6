"""Tests for the Opal tap on/off ingestion (src/pipeline/ingestion/opal_importance.py).

TfNSW's real origin-destination file is behind a portal login, so these use
a small synthetic GeoJSON + CSV instead - same approach as test_demand.py,
which this module's zone-loading closely follows.
"""

import json

import pytest

from pipeline.ingestion.opal_importance import OpalZone, OpalZones, load_opal_importance

# A bbox covering zones A and C below, but not zone B (mirrors test_demand's
# TEST_BBOX: the model footprint is much smaller than TZ21's state-wide
# coverage).
TEST_BBOX = {"min_lat": -33.90, "max_lat": -33.83, "min_lon": 151.15, "max_lon": 151.26}


def _write_zones_geojson(path):
    # GeoJSON coordinate order is [lon, lat].
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"tz21_code": "1"},
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
                "properties": {"tz21_code": "2"},  # far outside TEST_BBOX
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
                "properties": {"tz21_code": "3"},  # inside TEST_BBOX, much quieter
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [151.23, -33.85], [151.24, -33.85],
                        [151.24, -33.84], [151.23, -33.84], [151.23, -33.85],
                    ]],
                },
            },
            {
                "type": "Feature",
                "properties": {"tz21_code": "4"},  # inside TEST_BBOX, no OD rows at all
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [151.17, -33.89], [151.18, -33.89],
                        [151.18, -33.88], [151.17, -33.88], [151.17, -33.89],
                    ]],
                },
            },
        ],
    }
    path.write_text(json.dumps(geojson))


def _write_od_csv(path):
    # Every row's count is added to BOTH TZcode_on and TZcode_off (see
    # _sum_taps_by_zone) - zone 1 and zone 3 only ever trade with zone 2
    # here (never with each other), so each one's total is just its own
    # two rows summed, with no cross-contamination to reason about.
    path.write_text(
        "mode,date,TZname_on,TZcode_on,TZname_off,TZcode_off,count\n"
        "bus,20200224,Zone One,1,Zone Two,2,900\n"    # zone 1 boards 900
        "train,20200224,Zone Two,2,Zone One,1,100\n"  # zone 1 alights 100 -> total 1000
        "bus,20200224,Zone Three,3,Zone Two,2,50\n"   # zone 3 boards 50
        "train,20200224,Zone Two,2,Zone Three,3,50\n"  # zone 3 alights 50 -> total 100
    )


@pytest.fixture
def opal_files(tmp_path):
    zones_path = tmp_path / "travel_zones_2021.geojson"
    od_path = tmp_path / "origin_destination_24feb.csv"
    _write_zones_geojson(zones_path)
    _write_od_csv(od_path)
    return od_path, zones_path


def test_load_opal_importance_returns_none_when_files_missing(tmp_path):
    result = load_opal_importance(
        od_path=tmp_path / "missing.csv",
        zones_path=tmp_path / "missing.geojson",
    )
    assert result is None


def test_load_opal_importance_filters_to_bbox_and_sums_both_tap_ends(opal_files):
    od_path, zones_path = opal_files

    opal = load_opal_importance(od_path, zones_path, bbox=TEST_BBOX)

    assert opal is not None
    zone_ids = {z.zone_id for z in opal.zones}
    # zone "2" is outside TEST_BBOX; zone "4" has no OD rows at all
    assert zone_ids == {"1", "3"}
    zone_one = next(z for z in opal.zones if z.zone_id == "1")
    zone_three = next(z for z in opal.zones if z.zone_id == "3")
    assert zone_one.taps == pytest.approx(1000.0)  # 900 on + 100 off
    assert zone_three.taps == pytest.approx(100.0)  # 50 on + 50 off


def test_load_opal_importance_respects_mode_filter(opal_files):
    od_path, zones_path = opal_files

    opal = load_opal_importance(od_path, zones_path, bbox=TEST_BBOX, modes={"bus"})

    zone_one = next(z for z in opal.zones if z.zone_id == "1")
    zone_three = next(z for z in opal.zones if z.zone_id == "3")
    assert zone_one.taps == pytest.approx(900.0)  # only the bus row counts now
    assert zone_three.taps == pytest.approx(50.0)


def test_importance_score_is_a_zscore_relative_to_loaded_zones(opal_files):
    od_path, zones_path = opal_files
    opal = load_opal_importance(od_path, zones_path, bbox=TEST_BBOX)

    # Two zones loaded: taps 1000 and 100 -> mean 550, pstdev 450.
    assert opal.mean_taps == pytest.approx(550.0)
    assert opal.std_taps == pytest.approx(450.0)

    # Well inside zone "1" (busier than average -> positive z-score)
    assert opal.importance_score(-33.87, 151.21) == pytest.approx((1000 - 550) / 450, abs=0.01)
    # Well inside zone "3" (quieter than average -> negative z-score)
    assert opal.importance_score(-33.845, 151.235) == pytest.approx((100 - 550) / 450, abs=0.01)
    # Nowhere near any loaded zone
    assert opal.importance_score(-33.5, 151.5) is None


def test_importance_score_none_without_spread():
    # A single zone has no std to score against.
    opal = OpalZones(
        zones=[
            OpalZone(
                zone_id="1", taps=1000.0,
                bbox=(-33.88, -33.86, 151.20, 151.22),
                rings=[[
                    (-33.88, 151.20), (-33.88, 151.22),
                    (-33.86, 151.22), (-33.86, 151.20), (-33.88, 151.20),
                ]],
            )
        ],
        mean_taps=1000.0,
        std_taps=0.0,
    )
    assert opal.importance_score(-33.87, 151.21) is None


def test_build_grid_matches_importance_score(opal_files):
    od_path, zones_path = opal_files
    opal = load_opal_importance(od_path, zones_path, bbox=TEST_BBOX)

    cells = opal.build_grid(cell_metres=150)

    assert cells
    for cell in cells:
        center = cell["center"]
        expected = opal.importance_score(center["lat"], center["lon"])
        assert cell["importance_score"] == pytest.approx(expected, abs=0.01)
