import asyncio
import base64
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.publish.control import ControlError, ControlState
from engine.sources.replay import RESTART, RecordingIndex, ReplaySource, Session

UTC = timezone.utc
# Thu 22 Oct 2026, 07:00 Sydney (AEDT, UTC+11) = Wed 21 Oct 20:00 UTC
AM_START = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)


def _write(root: Path, mode: str, feed_type: str, tag: str | None, times: list[datetime], marker: str):
    """One recording file per UTC hour, in the recorder's format."""
    by_hour: dict[datetime, list[datetime]] = {}
    for t in times:
        by_hour.setdefault(t.replace(minute=0, second=0, microsecond=0), []).append(t)
    for hour, items in by_hour.items():
        d = root / mode / feed_type
        d.mkdir(parents=True, exist_ok=True)
        name = f"{hour:%Y%m%dT%H}.{tag}.ndjson.gz" if tag else f"{hour:%Y%m%dT%H}.ndjson.gz"
        with gzip.open(d / name, "at") as f:
            for t in items:
                payload = base64.b64encode(f"{marker}@{t.isoformat()}".encode()).decode()
                f.write(json.dumps({"fetched_at": t.isoformat(), "payload_b64": payload}) + "\n")


def _every(start: datetime, minutes: int, step_s: int = 300) -> list[datetime]:
    return [start + timedelta(seconds=s) for s in range(0, minutes * 60, step_s)]


def test_sessions_list_peaks_with_coverage_and_latest(tmp_path):
    _write(tmp_path, "buses", "vehicle_positions", "rec", _every(AM_START, 60, step_s=15), "a")  # 1 of 2 AM hours
    sessions = {s.id: s for s in RecordingIndex(tmp_path).sessions()}
    am = sessions["2026-10-22-am"]
    assert am.label == "AM peak · Thu 22 Oct"
    assert am.start == AM_START and am.end == AM_START + timedelta(hours=2)
    assert am.coverage == 0.5
    assert "2026-10-22-pm" not in sessions
    assert next(iter(sessions)) == "latest"


def test_sparse_peaks_are_hidden(tmp_path):
    # Files for both hours, but only a few updates (the rate-limited September case)
    _write(tmp_path, "buses", "vehicle_positions", None, _every(AM_START, 120, step_s=1200), "a")
    assert "2026-10-22-am" not in {s.id for s in RecordingIndex(tmp_path).sessions()}


def test_one_writer_per_hour_preferring_the_scheduled_recorder(tmp_path):
    times = _every(AM_START, 60)
    _write(tmp_path, "buses", "vehicle_positions", "serve", times, "serve")
    _write(tmp_path, "buses", "vehicle_positions", "rec", times, "rec")
    ((mode, feed_type, paths),) = RecordingIndex(tmp_path).files(AM_START, AM_START + timedelta(hours=1))
    assert [p.name for p in paths] == ["20261021T20.rec.ndjson.gz"]


async def _collect(source: ReplaySource, limit: int) -> list:
    out = []
    async for raw in source.stream():
        out.append(raw)
        if len(out) >= limit:
            break
    return out


def test_replay_merges_feeds_in_time_order_within_the_window(tmp_path):
    _write(tmp_path, "buses", "vehicle_positions", "rec", _every(AM_START - timedelta(minutes=10), 30), "bus")
    _write(tmp_path, "metro", "trip_updates", "rec", _every(AM_START + timedelta(seconds=150), 20), "metro")
    session = Session("s", "test", AM_START, AM_START + timedelta(minutes=15), 1.0)
    source = ReplaySource(RecordingIndex(tmp_path), session, speed=lambda: 1e6, loop=False)
    out = asyncio.run(_collect(source, 100))
    assert out[0].feed_type == RESTART
    played = out[1:]
    times = [r.fetched_at for r in played]
    assert times == sorted(times)
    assert all(session.start <= t < session.end for t in times)
    assert {r.mode for r in played} == {"buses", "metro"}
    assert played[0].payload.startswith(b"bus@")


def test_replay_paces_by_speed(tmp_path):
    _write(tmp_path, "buses", "vehicle_positions", "rec", _every(AM_START, 1, step_s=15)[:3], "bus")  # 0s, 15s, 30s
    session = Session("s", "test", AM_START, AM_START + timedelta(minutes=1), 1.0)
    source = ReplaySource(RecordingIndex(tmp_path), session, speed=lambda: 100.0, loop=False)

    async def timed():
        loop = asyncio.get_event_loop()
        start = loop.time()
        await _collect(source, 4)
        return loop.time() - start

    elapsed = asyncio.run(timed())
    assert 0.25 <= elapsed < 1.0  # 30s of recording at 100x = 0.3s
    assert source.progress == pytest.approx(0.5)


def test_replay_loops_with_a_restart_marker(tmp_path):
    _write(tmp_path, "buses", "vehicle_positions", "rec", [AM_START], "bus")
    session = Session("s", "test", AM_START, AM_START + timedelta(minutes=1), 1.0)
    source = ReplaySource(RecordingIndex(tmp_path), session, speed=lambda: 100.0, loop=True)
    kinds = [r.feed_type for r in asyncio.run(_collect(source, 4))]
    assert kinds == [RESTART, "vehicle_positions", RESTART, "vehicle_positions"]


def test_truncated_file_ends_that_stream_without_crashing(tmp_path):
    _write(tmp_path, "buses", "vehicle_positions", "rec", _every(AM_START, 10, step_s=60), "bus")
    path = next((tmp_path / "buses" / "vehicle_positions").iterdir())
    path.write_bytes(path.read_bytes()[:-20])  # cut mid-stream, like a crash during a write
    session = Session("s", "test", AM_START, AM_START + timedelta(minutes=10), 1.0)
    source = ReplaySource(RecordingIndex(tmp_path), session, speed=lambda: 1e6, loop=False)
    out = asyncio.run(_collect(source, 100))
    assert out[0].feed_type == RESTART


def test_playback_control_validation():
    c = ControlState()
    assert c.state["playback"] == {"session": "live", "speed": 25.0, "paused": False}
    c.apply({"playback": {"session": "2026-10-22-am", "speed": 500, "paused": True}})
    assert c.state["playback"] == {"session": "2026-10-22-am", "speed": 100.0, "paused": True}
    for bad in ({"session": ""}, {"speed": "fast"}, {"loop": True}, {"paused": "yes"}):
        with pytest.raises(ControlError):
            c.apply({"playback": bad})


def test_seek_feeds_a_warm_up_then_plays_from_the_target(tmp_path):
    times = _every(AM_START, 40, step_s=60)  # one bus payload a minute for 40 min
    _write(tmp_path, "buses", "vehicle_positions", "rec", times, "bus")
    session = Session("s", "test", AM_START, AM_START + timedelta(minutes=40), 1.0)
    target = AM_START + timedelta(minutes=30)
    source = ReplaySource(RecordingIndex(tmp_path), session, speed=lambda: 1e6, loop=False, start_at=target)
    out = asyncio.run(_collect(source, 100))
    assert out[0].feed_type == RESTART and out[0].fetched_at == target - timedelta(minutes=10)
    played = [r.fetched_at for r in out[1:]]
    assert played[0] == target - timedelta(minutes=10)   # warm-up from 10 min before
    assert played[-1] == AM_START + timedelta(minutes=39)
    assert len(played) == 20


def test_pause_holds_playback_until_resumed(tmp_path):
    _write(tmp_path, "buses", "vehicle_positions", "rec", _every(AM_START, 1, step_s=15)[:2], "bus")
    session = Session("s", "test", AM_START, AM_START + timedelta(minutes=1), 1.0)
    state = {"paused": True}
    source = ReplaySource(RecordingIndex(tmp_path), session, speed=lambda: 1e6, loop=False,
                          paused=lambda: state["paused"])

    async def run():
        got = []
        async def consume():
            async for raw in source.stream():
                got.append(raw)
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.5)
        held = len(got)            # only the restart marker while paused
        state["paused"] = False
        await asyncio.wait_for(task, 2)
        return held, len(got)

    held, total = asyncio.run(run())
    assert held == 1 and total == 3
