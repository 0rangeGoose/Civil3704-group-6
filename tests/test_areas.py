import pytest

from engine.demand.areas import bin_ranges, gap_bin, percentile_ranks, quintile_bins, service_gap
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


def test_area_controls():
    c = ControlState()
    assert c.state["area_layer"] == "none" and c.state["area_unit"] == "zones"
    c.apply({"area_layer": "service_gap", "area_unit": "grid"})
    assert c.state["area_layer"] == "service_gap" and c.state["area_unit"] == "grid"
    for gone in ("population", "opal", "pressure"):  # opal and pressure were removed 5 Nov
        with pytest.raises(ControlError):
            c.apply({"area_layer": gone})
    with pytest.raises(ControlError):
        c.apply({"area_unit": "hexagons"})


def test_bin_ranges_give_each_fifth_its_lowest_and_highest_value():
    units = [{"jobs": v, "bins": {"jobs": b}} for v, b in [(5, 0), (12, 0), (40, 1), (900, 4), (0, None)]]
    assert bin_ranges(units, "jobs") == [[5, 12], [40, 40], None, None, [900, 900]]
