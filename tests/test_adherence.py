from pathlib import Path

import yaml

from engine.normalise.adherence import PunctualityRules, classify

# The sourced limits, as in config/config.yaml
RULES = PunctualityRules(
    late_seconds={"suburban_rail": 300, "intercity": 360, "metro": 300, "light_rail": 300, "bus": 359, "ferry": 300},
    early_seconds={"bus": 59},
)


def test_cancelled_wins_regardless_of_deviation():
    assert classify(9999, "buses", True, RULES) == "cancelled"


def test_missing_deviation_is_unknown():
    assert classify(None, "buses", False, RULES) == "unknown"


def test_unmapped_mode_is_unknown():
    assert classify(10, "monorail", False, RULES) == "unknown"


def test_bus_on_time_window_is_the_contract_one():
    # Greater Sydney Bus Contract: no more than 59 s early, no more than 5 min 59 s late
    assert classify(-59, "buses", False, RULES) == "on_time"
    assert classify(-60, "buses", False, RULES) == "early"
    assert classify(359, "buses", False, RULES) == "on_time"
    assert classify(360, "buses", False, RULES) == "late"


def test_trains_are_never_early_and_use_tfnsw_limits():
    assert classify(-600, "sydneytrains", False, RULES) == "on_time"  # running early isn't penalised
    assert classify(300, "sydneytrains", False, RULES) == "on_time"   # within 5 min
    assert classify(301, "sydneytrains", False, RULES) == "late"
    assert classify(360, "nswtrains", False, RULES) == "on_time"      # intercity: within 6 min
    assert classify(361, "nswtrains", False, RULES) == "late"


def test_metro_light_rail_and_ferries_borrow_the_five_minute_rail_standard():
    for mode in ("metro", "lightrail_innerwest", "lightrail_cbdandsoutheast", "ferries"):
        assert classify(300, mode, False, RULES) == "on_time"
        assert classify(301, mode, False, RULES) == "late"


def test_missed_its_slot_needs_both_the_limit_and_the_scheduled_gap():
    # bus every 4 min: 5 min late is past the gap but still on time by the contract
    assert classify(300, "buses", False, RULES, headway_seconds=240) == "on_time"
    # past the limit and past the gap: the next bus would have come sooner
    assert classify(400, "buses", False, RULES, headway_seconds=240) == "very_late"
    # past the limit, but the next bus is 20 min behind: just late
    assert classify(400, "buses", False, RULES, headway_seconds=1200) == "late"
    # no known gap (e.g. Sydney Trains): never very late
    assert classify(3000, "sydneytrains", False, RULES) == "late"


def test_rules_load_from_the_project_config():
    raw = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "config.yaml").read_text())
    assert PunctualityRules.from_config(raw) == RULES
