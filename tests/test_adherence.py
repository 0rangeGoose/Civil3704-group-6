from engine.normalise.adherence import classify

THRESHOLDS = {
    "suburban_rail": 300,
    "intercity": 360,
    "metro": 120,
    "light_rail": 120,
    "bus": 300,
    "ferry": 300,
}


def test_cancelled_wins_regardless_of_deviation():
    assert classify(9999, "buses", cancelled=True, thresholds=THRESHOLDS, very_late_multiplier=2) == "cancelled"


def test_missing_deviation_is_unknown():
    assert classify(None, "buses", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "unknown"


def test_unmapped_mode_is_unknown():
    assert classify(10, "monorail", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "unknown"


def test_running_early_is_on_time():
    assert classify(-600, "buses", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "on_time"


def test_within_threshold_is_on_time():
    assert classify(300, "buses", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "on_time"


def test_just_past_threshold_is_late():
    assert classify(301, "buses", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "late"


def test_at_very_late_multiplier_is_still_late():
    assert classify(600, "buses", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "late"


def test_past_very_late_multiplier_is_very_late():
    assert classify(601, "buses", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "very_late"


def test_metro_has_a_tighter_threshold_than_bus():
    # 150s is on_time for a bus (threshold 300) but late for metro (threshold 120).
    assert classify(150, "buses", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "on_time"
    assert classify(150, "metro", cancelled=False, thresholds=THRESHOLDS, very_late_multiplier=2) == "late"
