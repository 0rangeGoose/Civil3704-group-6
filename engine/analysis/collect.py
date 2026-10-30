"""Run recorded windows through the live engine, as fast as possible, and
collect what our travel-time tracker sees -- shared by the free-flow baseline
(scripts/build_freeflow.py) and the step 6 analysis
(scripts/analyse_congestion.py), so both measure exactly what the app shows.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable

from engine.normalise.travel_time import Interval
from engine.normalise.vehicle import VehicleRecord
from engine.publish.feed import LiveVehicleFeed
from engine.sources.replay import RecordingIndex, ReplaySource, Session


def replay_window(
    feed: LiveVehicleFeed,
    index: RecordingIndex,
    start: datetime,
    end: datetime,
    on_interval: Callable[[Interval, VehicleRecord], None],
) -> None:
    """Every Interval the feed's tracker closes in [start, end), with the
    vehicle record that closed it (for TfNSW's level at that moment)."""
    tracker = feed.travel_time
    original = tracker.observe

    def spy(v: VehicleRecord):
        interval = original(v)
        if interval is not None:
            on_interval(interval, v)
        return interval

    tracker.observe = spy
    try:
        feed.reset()
        source = ReplaySource(index, Session("window", "window", start, end, 1.0), speed=lambda: 1e9, loop=False)
        asyncio.run(feed.run_forever(source))
    finally:
        tracker.observe = original
