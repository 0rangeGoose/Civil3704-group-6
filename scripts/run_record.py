#!/usr/bin/env python3
"""Records the live feeds, trimmed to the model footprint, for a fixed time
and then exits -- what the weekday peak schedule runs (see
scripts/install_recording_schedule.py). No server, no renderer.

Writes to data/recordings/ alongside anything the server records, tagged
"rec" in the filename so the two never append to the same file.

    python scripts/run_record.py --minutes 130
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import load_config
from engine.normalise.aggregate import load_cbd_ids
from engine.sources.budget import budget_from_config
from engine.sources.footprint import FootprintFilter
from engine.sources.live import LiveSource
from engine.sources.recorder import RecordingSource


async def record(minutes: float) -> int:
    cfg = load_config()
    db_path = cfg.path("db_path")
    if not db_path.exists():
        raise SystemExit(f"error: {db_path} not found -- run scripts/run_phase1.py first")
    _, stop_ids = load_cbd_ids(db_path)
    # Shares the day's request budget with the server, and stands aside while
    # the server is polling (it records the same feeds), taking over if it stops.
    budget, server_heartbeat = budget_from_config(cfg)
    source = RecordingSource(
        LiveSource(cfg, budget=budget, defer_to=server_heartbeat),
        cfg.root / "data" / "recordings",
        FootprintFilter(cfg.bounding_box, stop_ids),
        tag="rec",
    )
    deadline = time.monotonic() + minutes * 60
    count = 0
    stream = source.stream()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(stream.__anext__(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            count += 1
    finally:
        await stream.aclose()
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, required=True, help="how long to record for")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("pulse_cbd.record")
    log.info("recording for %.0f minutes", args.minutes)
    count = asyncio.run(record(args.minutes))
    log.info("done: %d feed payloads received", count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
