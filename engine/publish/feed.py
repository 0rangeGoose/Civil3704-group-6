"""Runs the poll -> decode -> enrich pipeline as a background task and keeps
an in-memory current snapshot -- the thing the WebSocket/HTTP layer serves.

This is the same pipeline scripts/run_phase3.py exercises headless, wired up
as a long-running service instead of a bounded CLI run.
"""
from __future__ import annotations

import logging
import time

from engine.config import Config
from engine.normalise.aggregate import RollingAdherence, count_active_alerts, load_cbd_ids
from engine.normalise.build import build_vehicle_records
from engine.normalise.enrich import enrich_vehicle_record
from engine.normalise.map_match import ShapeIndex
from engine.normalise.static_lookup import StaticLookup
from engine.normalise.trip_updates import build_trip_update_index
from engine.normalise.vehicle import VehicleRecord
from engine.sources.live import LiveSource
from engine.sources.recorder import RecordingSource

logger = logging.getLogger("pulse_cbd.feed")


class LiveVehicleFeed:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        db_path = cfg.path("db_path")
        self._lookup = StaticLookup(db_path)
        self._shapes = ShapeIndex(db_path)
        self._cbd_route_ids, self._cbd_stop_ids = load_cbd_ids(db_path)
        self._thresholds = cfg.raw["adherence_thresholds_seconds"]
        self._very_late_multiplier = self._thresholds["very_late_multiplier"]
        self._stale_after_seconds = cfg.raw["realtime_gtfs"]["stale_after_seconds"]

        self.snapshot: dict[str, VehicleRecord] = {}
        self.snapshot_updated_at: dict[str, float] = {}
        self.alerts_summary: dict = {"count": 0, "by_severity": {}, "route_ids": []}
        self.rolling_adherence = RollingAdherence()

        self._trip_update_index_by_mode: dict[str, dict] = {}
        recordings_dir = cfg.root / "data" / "recordings"
        self._source = RecordingSource(LiveSource(cfg), recordings_dir)

    async def run_forever(self) -> None:
        bbox = self._cfg.bounding_box
        async for raw in self._source.stream():
            if raw.feed_type == "trip_updates":
                self._trip_update_index_by_mode[raw.mode] = build_trip_update_index(raw.payload)
                continue
            if raw.feed_type == "service_alerts":
                self.alerts_summary = count_active_alerts(raw.payload, self._cbd_route_ids, self._cbd_stop_ids)
                continue
            if raw.feed_type != "vehicle_positions":
                continue

            records = build_vehicle_records(raw, bbox, self._lookup)
            trip_updates = self._trip_update_index_by_mode.get(raw.mode, {})
            now = time.time()
            for r in records:
                enriched = enrich_vehicle_record(
                    r, trip_updates, self._shapes, self._lookup, self._thresholds, self._very_late_multiplier
                )
                self.snapshot[enriched.vehicle_key] = enriched
                self.snapshot_updated_at[enriched.vehicle_key] = now
                self.rolling_adherence.record(enriched.mode, enriched.adherence_state, at=now)
            self._prune_stale(now)
            logger.info(
                "vehicle_positions:%s: %d vehicles, snapshot size %d",
                raw.mode, len(records), len(self.snapshot),
            )

    def _prune_stale(self, now: float) -> None:
        """Bounds memory. Fading a stale vehicle out visually is the
        renderer's job (it has /config's stale_after_seconds too) -- this is
        just the point past which we stop carrying it at all."""
        cutoff = now - self._stale_after_seconds
        stale_keys = [k for k, t in self.snapshot_updated_at.items() if t < cutoff]
        for k in stale_keys:
            self.snapshot.pop(k, None)
            self.snapshot_updated_at.pop(k, None)

    def close(self) -> None:
        self._lookup.close()
