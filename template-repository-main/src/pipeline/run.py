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
from typing import Dict, Optional

from . import config, history
from .analytics import RollingAggregator, congestion_grid, nearest_stop_id, route_line_scores, score_vehicles
from .ingestion import boam_stops
from .ingestion import demand as demand_ingestion
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
    demand = demand_ingestion.load_demand_zones()
    aggregator = RollingAggregator()

    cycle = 0
    try:
        while max_cycles is None or cycle < max_cycles:
            start = time.time()
            raw = realtime_gtfs.poll_all(api_key)
            records = score_vehicles(normalise(raw, static), static)
            aggregator.update(records)
            grid = congestion_grid(records, demand=demand)
            cycle += 1

            logger.info(
                f"Cycle {cycle}: {len(records)} vehicles in CBD, "
                f"{grid['hotspot_count']} congestion hotspot(s)"
            )
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
    demand = demand_ingestion.load_demand_zones()
    boam = boam_stops.load_boam_stops()
    aggregator = RollingAggregator()
    store = SnapshotStore()

    if demand is not None:
        # Comprehensive, bus-independent - computed once here, not per poll
        # cycle like congestion (demand data doesn't change during a run).
        demand_cells = demand.build_grid(config.CONGESTION_GRID_CELL_METRES)
        store.set_demand_grid(demand_cells, config.CONGESTION_GRID_CELL_METRES)
        logger.info(f"Demand grid built: {len(demand_cells)} cells (independent of bus positions)")

    if boam is not None:
        # Point markers, not a grid - every stop already has its own exact
        # coordinate. Same lifecycle as the other two: a day's peak
        # occupancy reading doesn't change during a run.
        boam_markers = boam.to_markers()
        store.set_boam_stops(boam_markers)
        logger.info(f"BOAM stop markers built: {len(boam_markers)} stops (independent of bus positions)")

    seen_shape_ids = set()
    last_history_log = 0.0
    # vehicle_id -> stop_id of the visit already logged for it - lets the
    # stop-occupancy logging below be edge-triggered ("just arrived at this
    # stop") instead of firing again every cycle a vehicle is still parked
    # there.
    last_stop_seen: Dict[str, str] = {}

    def on_update(raw):
        nonlocal last_history_log
        now = time.time()
        records = score_vehicles(normalise(raw, static), static)
        aggregator.update(records)
        grid = congestion_grid(records, demand=demand)
        route_lines = route_line_scores(records)

        # Geometry only needs sending once per shape - accumulate any newly
        # -seen ones into the store's growing cache (served at /route_shapes,
        # separate from this repeating snapshot).
        new_shapes = {}
        for shape_id, info in route_lines.items():
            if shape_id in seen_shape_ids:
                continue
            seen_shape_ids.add(shape_id)
            points = static.shapes.get(shape_id)
            if points:
                new_shapes[shape_id] = {
                    "points": [[lat, lon] for lat, lon in points],
                    "route_id": info["route_id"],
                    "route_short_name": info["route_short_name"],
                }
        if new_shapes:
            store.add_route_shapes(new_shapes)

        store.update(records, aggregator.snapshot(), grid, route_lines)
        logger.info(
            f"Snapshot updated: {len(records)} vehicles in CBD, "
            f"{grid['hotspot_count']} congestion hotspot(s), "
            f"{len(route_lines)} route line(s)"
        )

        # Live stop-occupancy logging - this project's own, continuously
        # self-built equivalent of TfNSW's day-after BOAM export (see
        # history.log_stop_occupancy / ingestion.boam_stops). Edge-triggered
        # on "just arrived at this stop", not every cycle a vehicle happens
        # to still be sitting there - EVERY poll cycle, unlike the slow
        # snapshot logging below, since a real arrival is a discrete event
        # that would otherwise be missed between samples. delay_seconds is
        # logged alongside occupancy (not derived from it) so
        # history.query_stop_lateness_by_occupancy can compare the two at
        # this exact stop, over time.
        new_visits = []
        seen_vehicle_ids = set()
        for record in records:
            seen_vehicle_ids.add(record.vehicle_id)
            if record.occupancy is None:
                continue
            stop_id = nearest_stop_id(record.latitude, record.longitude, static.stops)
            if stop_id is None:
                last_stop_seen.pop(record.vehicle_id, None)
                continue
            if last_stop_seen.get(record.vehicle_id) == stop_id:
                continue  # already logged this visit on an earlier cycle
            last_stop_seen[record.vehicle_id] = stop_id
            new_visits.append(
                {
                    "stop_id": stop_id,
                    "vehicle_id": record.vehicle_id,
                    "route_short_name": record.route_short_name,
                    "mode": record.mode,
                    "occupancy": record.occupancy,
                    "delay_seconds": record.delay_seconds,
                }
            )
        # Vehicles that dropped off the feed entirely (ghost-trail timeout,
        # trip ended) shouldn't keep stale state forever.
        for vehicle_id in list(last_stop_seen):
            if vehicle_id not in seen_vehicle_ids:
                last_stop_seen.pop(vehicle_id, None)
        if new_visits:
            history.log_stop_occupancy(new_visits, now=now)

        # Coarse, slow-interval logging for the historical/trend view (see
        # pipeline/history.py) - not every poll cycle, this data only needs
        # hour-of-day resolution.
        if now - last_history_log >= config.HISTORY_LOG_INTERVAL_SECONDS:
            history.log_snapshot(grid["cells"], route_lines, now=now)
            last_history_log = now

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
