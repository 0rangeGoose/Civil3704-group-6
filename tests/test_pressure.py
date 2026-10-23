import pytest

from engine.demand.pressure import DEFAULT_WEIGHTS, PressureModel, combine, score_bins
from engine.publish.control import ControlError, ControlState


def _square(uid, lat, lon, gap, opal, size=0.01):
    ring = [[lat, lon], [lat, lon + size], [lat + size, lon + size], [lat + size, lon], [lat, lon]]
    return {"id": uid, "rings": [ring], "gap": gap, "opal": opal}


def _segment(sid, lat, lon, congestion=None, crowding=None):
    return {"id": sid, "pts": [[lat, lon], [lat + 0.001, lon + 0.001], [lat + 0.002, lon + 0.002]],
            "congestion": congestion, "crowding": crowding}


AREAS = {
    "zones": [
        _square("a", -33.90, 151.20, gap=0.8, opal=5000),   # underserved, busy
        _square("b", -33.90, 151.22, gap=-0.6, opal=100),   # well served, quiet
        _square("c", -33.90, 151.24, gap=0.0, opal=0),      # no vehicles right now
    ],
    "grid": [],
}


def test_combine_reweights_over_the_inputs_a_unit_has():
    ranks = {"congestion": {"a": 1.0}, "opal": {"a": 0.0, "b": 1.0}}
    scores = combine(ranks, {"congestion": 1, "opal": 1})
    assert scores["a"] == 0.5        # mean of both inputs
    assert scores["b"] == 1.0        # only opal: not dragged down by a missing live input


def test_zero_weight_leaves_an_input_out():
    ranks = {"congestion": {"a": 1.0, "b": 0.0}, "opal": {"a": 0.0, "b": 1.0}}
    assert combine(ranks, {"congestion": 0, "opal": 1}) == {"a": 0.0, "b": 1.0}


def test_score_bins_use_fifths_of_the_scores_not_fixed_bands():
    # Scores bunched around the middle, as averaged ranks are, still use all five colours
    scores = {f"u{i}": 0.4 + i * 0.02 for i in range(10)}
    bins = score_bins(scores)
    assert bins["u0"] == 0 and bins["u9"] == 4
    assert sorted(set(bins.values())) == [0, 1, 2, 3, 4]


def test_pressure_model_puts_live_segments_in_their_area():
    model = PressureModel(AREAS)
    segments = [
        _segment("s1", -33.897, 151.203, congestion=3, crowding=2),  # in a
        _segment("s2", -33.897, 151.223, congestion=0, crowding=0),  # in b
        _segment("far", -33.70, 151.00, congestion=3),              # outside every area
    ]
    scores = model.scores("zones", segments, DEFAULT_WEIGHTS)
    assert scores["a"]["score"] > scores["b"]["score"]
    assert scores["a"]["inputs"]["congestion"] == 1.0 and scores["b"]["inputs"]["congestion"] == 0.0
    assert "congestion" not in scores["c"]["inputs"]   # not measured: neutral fill, not reported
    assert set(scores) == {"a", "b", "c"}


def test_areas_without_vehicles_get_a_neutral_live_rank():
    # Only static inputs differ: c (gap 0.0, no Opal) must not outrank a just
    # because a has a live reading and c doesn't.
    model = PressureModel(AREAS)
    scores = model.scores("zones", [_segment("s1", -33.897, 151.203, congestion=3, crowding=3)], DEFAULT_WEIGHTS)
    # c: congestion 0.5, crowding 0.5, gap rank 0.5, opal rank 0.0 -> 0.375
    assert scores["c"]["score"] == pytest.approx(0.375)
    assert scores["a"]["score"] > scores["c"]["score"]


def test_weights_control():
    c = ControlState()
    assert c.state["weights"] == {"congestion": 1.0, "crowding": 1.0, "service_gap": 1.0, "opal": 1.0}
    c.apply({"weights": {"opal": 0, "congestion": 9}})
    assert c.state["weights"]["opal"] == 0.0 and c.state["weights"]["congestion"] == 3.0
    for bad in ({"jobs": 1}, {"opal": "high"}):
        with pytest.raises(ControlError):
            c.apply({"weights": bad})
    with pytest.raises(ControlError):
        c.apply({"area_layer": "demand"})
    c.apply({"area_layer": "pressure"})
