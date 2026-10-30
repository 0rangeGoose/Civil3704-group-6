"""Plays recorded feeds back through the engine, in real time or as a time-lapse.

Recordings are one gzip NDJSON file per (mode, feed_type, UTC hour, writer)
(see recorder.py). A replay picks a time window, merges every feed's records
in that window into one stream ordered by when they were fetched, and yields
them paced against the wall clock at ``speed`` x real time. The engine then
processes them exactly as if they were live, using each payload's fetch time
as "now" -- so 10-minute windows mean 10 minutes of *recorded* time even at
25x.

When the server and the scheduled recorder both wrote the same hour (tags
"serve" and "rec"), only one is read so payloads aren't doubled: the larger
file. The recorder only polls when the server's heartbeat goes stale, so its
file for an hour the server also wrote is usually a few stray cycles; preferring
it by tag replaced a full hour with almost nothing (29 Oct PM peak).

``RecordingIndex`` lists what can be replayed: each weekday AM peak
(7:00-9:00) and PM peak (16:30-18:30) Sydney time that has bus positions
recorded, plus the most recent two hours of anything recorded, which is
what makes testing possible before the first scheduled peak exists.

Coverage counts actual bus position updates against one per poll interval,
not just which hour files exist: the old server's September recordings have
files for every hour but only 1-3 updates in each (it was being
rate-limited), which would replay as a near-empty model. Sessions under
MIN_COVERAGE are left out.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import heapq
import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Callable, Iterator
from zoneinfo import ZoneInfo

from engine.sources.base import RawFeed

logger = logging.getLogger("pulse_cbd.replay")

SYDNEY = ZoneInfo("Australia/Sydney")
PEAKS = {
    "am": ("AM peak", time(7, 0), time(9, 0)),
    "pm": ("PM peak", time(16, 30), time(18, 30)),
}
LATEST_SPAN = timedelta(hours=2)
POLL_INTERVAL_S = 15
MIN_COVERAGE = 0.1
_TAG_PREFERENCE = ("rec", "serve", None)  # tie-break only; None = untagged files from before writer tags existed

# A RawFeed with this feed_type marks the start of a new pass through the
# window (the first, and every loop after): the engine clears its state.
RESTART = "replay_restart"


@dataclass(frozen=True)
class Session:
    id: str
    label: str
    start: datetime  # UTC
    end: datetime    # UTC
    coverage: float  # share of the window's hours that have bus positions recorded

    def to_json(self) -> dict:
        return {
            "id": self.id, "label": self.label, "coverage": round(self.coverage, 2),
            "start": self.start.isoformat(), "end": self.end.isoformat(),
        }


def _parse_name(path: Path) -> tuple[datetime, str | None] | None:
    """'20261022T22.rec.ndjson.gz' -> (UTC hour, 'rec'); None if not a recording."""
    parts = path.name.split(".")
    if len(parts) < 3 or parts[-2:] != ["ndjson", "gz"]:
        return None
    try:
        hour = datetime.strptime(parts[0], "%Y%m%dT%H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    tag = parts[1] if len(parts) == 4 else None
    return hour, tag


def _pick_writer(tags: dict[str | None, Path]) -> Path:
    """The one file to read for an hour: the largest, ties by _TAG_PREFERENCE."""
    def size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return -1
    ordered = [tags[t] for t in _TAG_PREFERENCE if t in tags]
    return max(ordered, key=size)  # max keeps the first of equal sizes


def _hours(start: datetime, end: datetime) -> list[datetime]:
    hour = start.replace(minute=0, second=0, microsecond=0)
    out = []
    while hour < end:
        out.append(hour)
        hour += timedelta(hours=1)
    return out


class RecordingIndex:
    def __init__(self, recordings_dir: Path):
        self._dir = recordings_dir
        # path -> ((size, mtime), [fetched_at, ...]) so listing sessions only
        # re-reads files that have grown since last time
        self._times_cache: dict[Path, tuple[tuple[int, float], list[datetime]]] = {}

    def _bus_update_times(self) -> list[datetime]:
        """Fetch times of every recorded bus vehicle_positions payload, one
        writer per hour (the same choice replay makes)."""
        vp_dir = self._dir / "buses" / "vehicle_positions"
        if not vp_dir.is_dir():
            return []
        by_hour: dict[datetime, dict[str | None, Path]] = {}
        for path in vp_dir.iterdir():
            if parsed := _parse_name(path):
                by_hour.setdefault(parsed[0], {})[parsed[1]] = path
        times: list[datetime] = []
        for tags in by_hour.values():
            path = _pick_writer(tags)
            times.extend(self._file_times(path))
        return sorted(times)

    def _file_times(self, path: Path) -> list[datetime]:
        stat = path.stat()
        key = (stat.st_size, stat.st_mtime)
        cached = self._times_cache.get(path)
        if cached and cached[0] == key:
            return cached[1]
        times = []
        try:
            with gzip.open(path, "rt") as f:
                for line in f:
                    # fetched_at is the first field the recorder writes
                    # ('{"fetched_at": "<here>"'); slicing it out avoids
                    # decoding the large payload just to read a timestamp
                    try:
                        times.append(datetime.fromisoformat(line[16:line.index('"', 16)]))
                    except ValueError:
                        continue
        except (OSError, EOFError):
            pass
        self._times_cache[path] = (key, times)
        return times

    @staticmethod
    def _coverage(times: list[datetime], start: datetime, end: datetime) -> float:
        count = sum(start <= t < end for t in times)
        expected = (end - start).total_seconds() / POLL_INTERVAL_S
        return min(1.0, count / expected)

    def sessions(self) -> list[Session]:
        times = self._bus_update_times()
        if not times:
            return []
        result = []
        days = sorted({t.astimezone(SYDNEY).date() for t in times}, reverse=True)
        for day in days:
            for key, (name, start_t, end_t) in PEAKS.items():
                start = datetime.combine(day, start_t, SYDNEY).astimezone(timezone.utc)
                end = datetime.combine(day, end_t, SYDNEY).astimezone(timezone.utc)
                coverage = self._coverage(times, start, end)
                if coverage >= MIN_COVERAGE:
                    label = f"{name} · {day:%a %-d %b}"
                    result.append(Session(f"{day.isoformat()}-{key}", label, start, end, coverage))
        latest_end = times[-1] + timedelta(seconds=1)
        latest_start = latest_end - LATEST_SPAN
        result.insert(0, Session(
            "latest",
            f"Latest 2 hours recorded · until {latest_end.astimezone(SYDNEY):%a %-d %b %H:%M}",
            latest_start, latest_end,
            self._coverage(times, latest_start, latest_end),
        ))
        return result

    def find(self, session_id: str) -> Session | None:
        return next((s for s in self.sessions() if s.id == session_id), None)

    def files(self, start: datetime, end: datetime) -> list[tuple[str, str, list[Path]]]:
        """[(mode, feed_type, [hour files in order]), ...] covering the window,
        one writer per hour."""
        wanted = set(_hours(start, end))
        result = []
        for mode_dir in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            for type_dir in sorted(p for p in mode_dir.iterdir() if p.is_dir()):
                by_hour: dict[datetime, dict[str | None, Path]] = {}
                for path in type_dir.iterdir():
                    parsed = _parse_name(path)
                    if parsed and parsed[0] in wanted:
                        by_hour.setdefault(parsed[0], {})[parsed[1]] = path
                chosen = []
                for hour in sorted(by_hour):
                    chosen.append(_pick_writer(by_hour[hour]))
                if chosen:
                    result.append((mode_dir.name, type_dir.name, chosen))
        return result


def _records(mode: str, feed_type: str, paths: list[Path], start: datetime, end: datetime) -> Iterator[RawFeed]:
    for path in paths:
        try:
            with gzip.open(path, "rt") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        fetched_at = datetime.fromisoformat(record["fetched_at"])
                    except (ValueError, KeyError):
                        continue
                    if fetched_at < start:
                        continue
                    if fetched_at >= end:
                        return
                    yield RawFeed(mode, feed_type, fetched_at, base64.b64decode(record["payload_b64"]))
        except (OSError, EOFError) as exc:
            # A file still being written, or cut off by a crash, ends early.
            logger.warning("%s: stopped reading early: %s", path, exc)


# On a seek, this much recorded time before the target is fed through
# unpaced, so 10-minute street windows and trip updates are already filled
# when playback resumes there.
SEEK_WARMUP = timedelta(minutes=10)


class ReplaySource:
    def __init__(
        self,
        index: RecordingIndex,
        session: Session,
        speed: Callable[[], float],
        loop: bool = True,
        paused: Callable[[], bool] = lambda: False,
        start_at: datetime | None = None,
    ):
        self._index = index
        self.session = session
        self._speed = speed
        self._paused = paused
        self._loop = loop
        self._start_at = start_at  # first pass only; a loop starts from the beginning
        self.position: datetime = start_at or session.start  # recorded time currently being played

    @property
    def progress(self) -> float:
        span = (self.session.end - self.session.start).total_seconds()
        return max(0.0, min(1.0, (self.position - self.session.start).total_seconds() / span))

    async def stream(self) -> AsyncIterator[RawFeed]:
        clock = asyncio.get_event_loop()
        while True:
            start_at, self._start_at = self._start_at, None
            read_from = max(self.session.start, start_at - SEEK_WARMUP) if start_at else self.session.start
            yield RawFeed("replay", RESTART, read_from, b"")
            streams = [
                _records(mode, feed_type, paths, read_from, self.session.end)
                for mode, feed_type, paths in self._index.files(read_from, self.session.end)
            ]
            anchor_wall = anchor_rec = None
            anchor_speed = None
            count = 0
            for raw in heapq.merge(*streams, key=lambda r: r.fetched_at):
                if start_at and raw.fetched_at < start_at:
                    # warm-up before a seek target: no pacing, no pausing
                    self.position = raw.fetched_at
                    if count % 20 == 0:
                        await asyncio.sleep(0)
                    count += 1
                    yield raw
                    continue
                if self._paused():
                    while self._paused():
                        await asyncio.sleep(0.2)
                    anchor_wall = None  # resume from here, not from where the clock says
                speed = max(0.1, self._speed())
                if anchor_wall is None or speed != anchor_speed:
                    # (Re)anchor so a speed change applies from here on, not retroactively.
                    anchor_wall, anchor_rec, anchor_speed = clock.time(), raw.fetched_at, speed
                due = anchor_wall + (raw.fetched_at - anchor_rec).total_seconds() / speed
                delay = due - clock.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif count % 20 == 0:
                    await asyncio.sleep(0)  # running behind: still let the server breathe
                self.position = raw.fetched_at
                count += 1
                yield raw
            if not self._loop:
                return
            if count == 0:
                logger.warning("replay %s: no recorded data in window", self.session.id)
                await asyncio.sleep(5)
