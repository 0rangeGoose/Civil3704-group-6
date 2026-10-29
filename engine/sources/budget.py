"""Keeps live polling inside TfNSW's daily request quota, automatically.

Measured on Sun 25 Oct 2026: the API key was refused (429) after 59,964
requests counted from local midnight, and accepted again just after the next
midnight -- a quota of ~60,000 requests a day, resetting at midnight Sydney
time. Polling all 15 endpoints every 15s costs ~86,000 a day, so a server left
running all day ran out at 7pm and lost the evening (and would lose a PM peak).

How the pace adjusts:

- Every process that polls (the server, the scheduled peak recorder) adds its
  requests to one shared count for the day (``data/api_usage.json``, under a
  file lock), so the budget is per API key, not per process.
- **Peak windows** (weekday AM and PM, config ``api_budget.peaks``) always poll
  at the full rate.
- **Outside them**, the requests the rest of today's peaks will need are held
  back, and what's left is spread evenly until midnight: the cycle interval
  stretches from 15s up to at most ``max_interval_seconds``.
- Out of budget entirely -> one cycle per ``max_interval_seconds`` (a few
  hundred requests a day), so live view degrades instead of going dark.

The scheduled recorder and a running server would otherwise both pay for the
same peak. ``Heartbeat`` lets the recorder stand aside while the server is
polling, and take over if the server stops.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("pulse_cbd.budget")

SYDNEY = ZoneInfo("Australia/Sydney")

# Whole-file exclusive lock around data/api_usage.json, so the server and the
# scheduled recorder (two separate OS processes) can't race on the shared
# daily count. fcntl.flock has no Windows equivalent; msvcrt.locking is the
# nearest match but locks a *byte range* from the current file position, not
# the whole file, and needs at least one byte there to lock -- an empty file
# (first run) gets a one-byte placeholder so locking doesn't fail on it. The
# placeholder isn't valid JSON, but the read path below already falls back to
# {} on anything that doesn't parse, so that's harmless.
if sys.platform == "win32":
    import msvcrt

    def _lock_file(f) -> None:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            f.write(" ")
            f.flush()
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(f) -> None:
        fcntl.flock(f, fcntl.LOCK_EX)

    def _unlock_file(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


def _parse_hhmm(text: str) -> tuple[int, int]:
    h, m = text.split(":")
    return int(h), int(m)


class ApiBudget:
    def __init__(
        self,
        usage_path: Path,
        daily_limit: int,
        endpoints: int,
        poll_interval: float,
        peaks: list[tuple[str, str]],
        max_interval: float = 600.0,
        tz: ZoneInfo = SYDNEY,
    ):
        self._path = usage_path
        self.daily_limit = daily_limit
        self._endpoints = max(1, endpoints)
        self._poll = poll_interval
        self._peaks = [(_parse_hhmm(a), _parse_hhmm(b)) for a, b in peaks]
        self._max_interval = max_interval
        self._tz = tz

    # --- shared count -------------------------------------------------------

    @contextmanager
    def _locked(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a+", encoding="utf-8") as f:
            _lock_file(f)
            try:
                f.seek(0)
                text = f.read()
                try:
                    data = json.loads(text) if text.strip() else {}
                except ValueError:
                    data = {}
                yield data
                f.seek(0)
                f.truncate()
                f.write(json.dumps(data))
            finally:
                _unlock_file(f)

    def _day(self, now: datetime) -> str:
        return now.astimezone(self._tz).date().isoformat()

    def record(self, requests: int, now: datetime | None = None) -> None:
        if requests <= 0:
            return
        day = self._day(now or datetime.now(self._tz))
        oldest = (datetime.fromisoformat(day) - timedelta(days=7)).date().isoformat()
        with self._locked() as data:  # the dict is written back as mutated
            for old in [k for k in data if k < oldest]:  # keep a week, for debugging
                del data[old]
            data[day] = data.get(day, 0) + requests

    def used_today(self, now: datetime | None = None) -> int:
        day = self._day(now or datetime.now(self._tz))
        with self._locked() as data:
            return int(data.get(day, 0))

    # --- pacing -------------------------------------------------------------

    def in_peak(self, now: datetime) -> bool:
        local = now.astimezone(self._tz)
        if local.weekday() >= 5:
            return False
        minutes = local.hour * 60 + local.minute
        return any(a[0] * 60 + a[1] <= minutes < b[0] * 60 + b[1] for a, b in self._peaks)

    def _peak_minutes_left(self, now: datetime) -> float:
        local = now.astimezone(self._tz)
        if local.weekday() >= 5:
            return 0.0
        minutes = local.hour * 60 + local.minute + local.second / 60
        left = 0.0
        for a, b in self._peaks:
            start, end = a[0] * 60 + a[1], b[0] * 60 + b[1]
            left += max(0.0, end - max(start, minutes))
        return left

    def full_rate_per_minute(self) -> float:
        return self._endpoints * 60.0 / self._poll

    def cycle_interval(self, now: datetime | None = None, used: int | None = None) -> float:
        """Seconds between the starts of polling cycles right now."""
        now = now or datetime.now(self._tz)
        if self.in_peak(now):
            return self._poll
        used = self.used_today(now) if used is None else used
        reserve = self.full_rate_per_minute() * self._peak_minutes_left(now)
        available = self.daily_limit - used - reserve
        local = now.astimezone(self._tz)
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_left = max(60.0, (midnight - local).total_seconds())
        if available <= 0:
            return self._max_interval
        cycles_affordable = available / self._endpoints
        interval = seconds_left / cycles_affordable
        return min(self._max_interval, max(self._poll, interval))

    def status(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(self._tz)
        used = self.used_today(now)
        return {
            "used_today": used,
            "daily_limit": self.daily_limit,
            "in_peak": self.in_peak(now),
            "cycle_interval_seconds": round(self.cycle_interval(now, used), 1),
        }


def budget_from_config(cfg) -> tuple[ApiBudget | None, Heartbeat | None]:
    """The shared ApiBudget and the server's Heartbeat, from config/config.yaml.
    (None, None) if the config has no api_budget section."""
    section = cfg.raw.get("api_budget")
    if not section:
        return None, None
    endpoints = sum(len(modes) for modes in cfg.raw["realtime_gtfs"]["feeds"].values())
    budget = ApiBudget(
        usage_path=cfg.root / section["usage_path"],
        daily_limit=int(section["daily_limit"]),
        endpoints=endpoints,
        poll_interval=float(cfg.raw["realtime_gtfs"]["poll_interval_seconds"]),
        peaks=[tuple(p) for p in section["peaks"]],
        max_interval=float(section.get("max_interval_seconds", 600)),
    )
    return budget, Heartbeat(cfg.root / section["heartbeat_path"])


class Heartbeat:
    """A file whose modification time says "the server is polling right now"."""

    def __init__(self, path: Path, fresh_seconds: float = 60.0):
        self._path = path
        self._fresh = fresh_seconds

    def beat(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch()

    def is_fresh(self) -> bool:
        try:
            return time.time() - os.path.getmtime(self._path) < self._fresh
        except OSError:
            return False

    def clear(self) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass
