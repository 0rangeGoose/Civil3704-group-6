"""Orchestrates the pipeline: ingestion -> normalisation -> analytics -> publish.

Two entry points, matching the "verifiable with no renderer attached"
requirement from the proposal's architecture sketch:

- `run_headless()`   - polls and logs each cycle's summary, no server at all.
  Use this to check the pipeline works before touching the projector.
- `run_with_server()` - same pipeline, but pushes each cycle into a
  SnapshotStore served over HTTP (GET /snapshot) and WebSocket (/ws) for the
  projection renderer / wall-panel dashboard to consume.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from . import config
from .analytics import RollingAggregator
from .ingestion import realtime_gtfs, static_gtfs
from .normalise import normalise
from .publish import SnapshotStore, run_server

logger = logging.getLogger(__name__)


def run_headless(api_key: str, max_cycles: Optional[int] = None) -> None:
    """Run the pipeline with no server/renderer attached.

    Set max_cycles for a bounded test run (e.g. in CI or a quick manual
    check); leave it None to poll forever until Ctrl+C.
    """
    static = static_gtfs.load_static_gtfs(api_key)
    aggregator = RollingAggregator()

    cycle = 0
    try:
        while max_cycles is None or cycle < max_cycles:
            start = time.time()
            raw = realtime_gtfs.poll_all(api_key)
            records = normalise(raw, static)
            aggregator.update(records)
            cycle += 1

            logger.info(f"Cycle {cycle}: {len(records)} vehicles tracked")
            for route_id, agg in aggregator.snapshot().items():
                logger.info(
                    f"  route {route_id}: on_time={agg.on_time_pct}% "
                    f"late={agg.late_pct}% very_late={agg.very_late_pct}% "
                    f"cancelled={agg.cancelled_count} (n={agg.sample_count})"
                )

            if max_cycles is None or cycle < max_cycles:
                elapsed = time.time() - start
                interval = config.POLL_INTERVAL_SECONDS["vehicle_positions"]
                time.sleep(max(0.0, interval - elapsed))
    except KeyboardInterrupt:
        logger.info("Stopped")


def run_with_server(api_key: str) -> None:
    """Run ingestion in a background thread; serve the live snapshot on the main thread."""
    static = static_gtfs.load_static_gtfs(api_key)
    aggregator = RollingAggregator()
    store = SnapshotStore()

    def on_update(raw):
        records = normalise(raw, static)
        aggregator.update(records)
        store.update(records, aggregator.snapshot())
        logger.info(f"Snapshot updated: {len(records)} vehicles tracked")

    ingest_thread = threading.Thread(
        target=realtime_gtfs.poll_loop,
        kwargs={"api_key": api_key, "on_update": on_update},
        daemon=True,
    )
    ingest_thread.start()

    logger.info(
        f"Serving live snapshot on http://{config.SNAPSHOT_HOST}:{config.SNAPSHOT_PORT} "
        "(GET /snapshot, WS /ws)"
    )
    run_server(store)  # blocking
