import json

import pytest

from engine.publish.control import ControlError, ControlState


def test_defaults_are_the_safe_showcase_state():
    s = ControlState().state
    assert s["theme"] == "dark"
    assert s["hud"] is False and s["calibrate"] is False and s["verify"] is False
    assert s["stops"] is False  # off by default, same as the other static overlay (interchanges is on, stops isn't -- 2,903 markers is a lot to default to)
    assert all(s["modes"].values())
    assert s["nudge"] == {"dx": 0.0, "dy": 0.0, "scale": 1.0}


@pytest.mark.parametrize("key", ["stops", "stop_history"])
def test_stop_toggles_validate_like_the_other_boolean_flags(key):
    c = ControlState()
    assert c.state[key] is False
    c.apply({key: True})
    assert c.state[key] is True
    with pytest.raises(ControlError):
        c.apply({key: "yes"})


def test_basemap_defaults_to_plain_and_accepts_detailed_and_full():
    c = ControlState()
    assert c.state["basemap"] == "plain"  # the model has its own streets
    for value in ("detailed", "full"):
        c.apply({"basemap": value})
        assert c.state["basemap"] == value
    with pytest.raises(ControlError):
        c.apply({"basemap": "satellite"})


def test_stop_metric_defaults_to_median_and_accepts_mean():
    c = ControlState()
    assert c.state["stop_metric"] == "median"
    c.apply({"stop_metric": "mean"})
    assert c.state["stop_metric"] == "mean"
    with pytest.raises(ControlError):
        c.apply({"stop_metric": "average"})


def test_routes_default_empty_dedupe_and_validate():
    c = ControlState()
    assert c.state["routes"] == []
    c.apply({"routes": ["333", "T1", "333"]})
    assert c.state["routes"] == ["333", "T1"]
    c.apply({"routes": []})
    assert c.state["routes"] == []
    for bad in ["333", [333], [""], ["x" * 33], [str(i) for i in range(9)]]:
        with pytest.raises(ControlError):
            c.apply({"routes": bad})


def test_route_colour_slots_stay_put_when_another_route_is_removed():
    c = ControlState()
    c.apply({"routes": ["333", "T1", "L2"]})
    assert c.state["route_slots"] == {"333": 0, "T1": 1, "L2": 2}

    c.apply({"routes": ["T1", "L2"]})
    assert c.state["route_slots"] == {"T1": 1, "L2": 2}

    c.apply({"routes": ["T1", "L2", "M50"]})
    assert c.state["route_slots"]["M50"] == 0  # reuses the freed slot


def test_route_slots_cannot_be_set_directly():
    with pytest.raises(ControlError):
        ControlState().apply({"route_slots": {"333": 5}})


def test_patch_merges_nested_fields():
    c = ControlState()
    c.apply({"modes": {"buses": False}, "nudge": {"dx": 4}})
    assert c.state["modes"]["buses"] is False
    assert c.state["modes"]["trains"] is True
    assert c.state["nudge"] == {"dx": 4.0, "dy": 0.0, "scale": 1.0}


@pytest.mark.parametrize("patch", [
    {"theme": "neon"},
    {"hud": "yes"},
    {"modes": {"trams": False}},
    {"nudge": {"dx": True}},
    {"nudge": {"rotate": 1}},
    {"unknown": 1},
    ["not", "an", "object"],
])
def test_invalid_patch_is_rejected_without_partial_changes(patch):
    c = ControlState()
    before = json.dumps(c.state, sort_keys=True)
    with pytest.raises(ControlError):
        c.apply({"hud": True, **patch} if isinstance(patch, dict) else patch)
    assert json.dumps(c.state, sort_keys=True) == before


def test_nudge_is_clamped():
    c = ControlState()
    c.apply({"nudge": {"scale": 10, "dx": -99999}})
    assert c.state["nudge"]["scale"] == 2.0
    assert c.state["nudge"]["dx"] == -2000.0


def test_nudge_persists_across_restarts_but_nothing_else_does(tmp_path):
    path = tmp_path / "nudge.json"
    c = ControlState(path)
    c.apply({"nudge": {"dx": 6, "scale": 1.01}, "hud": True})
    restarted = ControlState(path).state
    assert restarted["nudge"] == {"dx": 6.0, "dy": 0.0, "scale": 1.01}
    assert restarted["hud"] is False


def test_reset_nudge(tmp_path):
    c = ControlState(tmp_path / "nudge.json")
    c.apply({"nudge": {"dx": 6, "dy": -2, "scale": 0.9}})
    c.reset_nudge()
    assert c.state["nudge"] == {"dx": 0.0, "dy": 0.0, "scale": 1.0}


def test_corrupt_nudge_file_is_ignored(tmp_path):
    path = tmp_path / "nudge.json"
    path.write_text("{not json")
    assert ControlState(path).state["nudge"] == {"dx": 0.0, "dy": 0.0, "scale": 1.0}
