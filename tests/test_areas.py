import pytest

from engine.demand.areas import gap_bin, opal_base_name, percentile_ranks, quintile_bins, service_gap
from engine.publish.control import ControlError, ControlState


def test_percentile_ranks_share_ties():
    ranks = percentile_ranks({"a": 1, "b": 5, "c": 5, "d": 9})
    assert ranks["a"] == 0.0 and ranks["d"] == 1.0
    assert ranks["b"] == ranks["c"] == pytest.approx(0.5)


def test_quintile_bins_skip_empty_units():
    bins = quintile_bins({f"z{i}": float(i) for i in range(11)})  # z0 has nothing
    assert "z0" not in bins
    assert bins["z1"] == 0 and bins["z10"] == 4
    assert sorted(set(bins.values())) == [0, 1, 2, 3, 4]


def test_service_gap_compares_ranks_and_ignores_jobless_units():
    jobs = {"busy_unserved": 900, "busy_served": 1000, "quiet_served": 10, "park": 0}
    departures = {"busy_unserved": 2, "busy_served": 400, "quiet_served": 300, "park": 50}
    gaps = service_gap(jobs, departures)
    assert "park" not in gaps
    assert gaps["busy_unserved"] > 0 > gaps["quiet_served"]
    assert gap_bin(gaps["busy_unserved"]) > gap_bin(gaps["quiet_served"])


@pytest.mark.parametrize("gap, expected", [(-1, 0), (-0.3, 1), (0, 2), (0.2, 3), (0.9, 4)])
def test_gap_bins(gap, expected):
    assert gap_bin(gap) == expected


@pytest.mark.parametrize("mode, loc, expected", [
    ("ferry", "Circular Quay, No. 3 Wharf", ("Circular Quay", "Wharf")),
    ("ferry", "Darling Harbour Wharf 3", ("Darling Harbour", "Wharf")),
    ("train", "Circular Quay Station", ("Circular Quay", "Station")),
    ("lightrail", "Central Station Light Rail", ("Central", "Light Rail")),
    ("lightrail", "The Star Light Rail", ("The Star", "Light Rail")),
])
def test_opal_names_reduce_to_place_and_mode_word(mode, loc, expected):
    assert opal_base_name(mode, loc) == expected


def test_area_controls():
    c = ControlState()
    assert c.state["area_layer"] == "none" and c.state["area_unit"] == "zones"
    c.apply({"area_layer": "service_gap", "area_unit": "grid"})
    assert c.state["area_layer"] == "service_gap" and c.state["area_unit"] == "grid"
    with pytest.raises(ControlError):
        c.apply({"area_layer": "population"})
    with pytest.raises(ControlError):
        c.apply({"area_unit": "hexagons"})
