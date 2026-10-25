from datetime import datetime

import pytest

from engine.sources.budget import SYDNEY, ApiBudget, Heartbeat

PEAKS = [("06:55", "09:05"), ("16:25", "18:35")]


def _budget(tmp_path, limit=57000):
    return ApiBudget(tmp_path / "usage.json", daily_limit=limit, endpoints=15, poll_interval=15, peaks=PEAKS)


def at(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=SYDNEY)  # 26 Oct 2026 is a Monday


def test_usage_is_shared_counted_per_sydney_day_and_kept_a_week(tmp_path):
    a, b = _budget(tmp_path), _budget(tmp_path)  # two processes, one file
    a.record(100, now=at(26, 10))
    b.record(50, now=at(26, 11))
    assert a.used_today(now=at(26, 23, 59)) == 150
    assert a.used_today(now=at(27, 0, 1)) == 0           # reset at local midnight
    a.record(1, now=at(26, 12))                          # same day again
    b.record(5, now=datetime(2026, 11, 6, 9, tzinfo=SYDNEY))
    assert a.used_today(now=at(26, 13)) == 0             # older than a week: dropped


def test_peaks_poll_at_full_rate_even_with_little_budget_left(tmp_path):
    budget = _budget(tmp_path)
    assert budget.cycle_interval(now=at(26, 8), used=56000) == 15
    assert budget.cycle_interval(now=at(26, 17), used=56000) == 15


def test_off_peak_slows_down_to_keep_the_remaining_peaks_affordable(tmp_path):
    budget = _budget(tmp_path)
    # Monday 1am: both peaks still ahead (2 x 130 min x 60 req/min = 15,600 reserved)
    interval = budget.cycle_interval(now=at(26, 1), used=0)
    assert interval > 15
    # Spending at that pace until 6:55, then full rate in both peaks, stays in budget
    off_peak_minutes = 24 * 60 - 2 * 130
    spend = off_peak_minutes * 60 / interval * 15 + 2 * 130 * 60
    assert spend <= 57000


def test_weekends_have_no_reserve_and_spread_the_whole_budget(tmp_path):
    budget = _budget(tmp_path)
    assert budget.in_peak(at(24, 8)) is False            # Saturday
    assert budget.cycle_interval(now=at(24, 0, 1), used=0) == pytest.approx(86340 / (57000 / 15), rel=0.01)


def test_spent_budget_falls_back_to_the_slowest_pace(tmp_path):
    budget = _budget(tmp_path)
    assert budget.cycle_interval(now=at(26, 20), used=57000) == 600


def test_heartbeat_freshness(tmp_path):
    hb = Heartbeat(tmp_path / "beat", fresh_seconds=60)
    assert not hb.is_fresh()
    hb.beat()
    assert hb.is_fresh()
    hb.clear()
    assert not hb.is_fresh()
