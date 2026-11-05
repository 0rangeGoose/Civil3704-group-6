import pytest

from engine.demand.areas import bin_ranges, percentile_ranks, quintile_bins
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


def test_area_controls():
    c = ControlState()
    assert c.state["area_layer"] == "none" and c.state["area_unit"] == "zones"
    c.apply({"area_layer": "jobs", "area_unit": "grid"})
    assert c.state["area_layer"] == "jobs" and c.state["area_unit"] == "grid"
    for gone in ("population", "opal", "pressure", "service_gap"):  # all removed
        with pytest.raises(ControlError):
            c.apply({"area_layer": gone})
    with pytest.raises(ControlError):
        c.apply({"area_unit": "hexagons"})


def test_bin_ranges_give_each_fifth_its_lowest_and_highest_value():
    units = [{"jobs": v, "bins": {"jobs": b}} for v, b in [(5, 0), (12, 0), (40, 1), (900, 4), (0, None)]]
    assert bin_ranges(units, "jobs") == [[5, 12], [40, 40], None, None, [900, 900]]
