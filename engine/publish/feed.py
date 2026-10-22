"""Runs the poll -> decode -> enrich pipeline as a background task and keeps
an in-memory current snapshot -- the thing the WebSocket/HTTP layer serves.

This is the same pipeline scripts/run_phase3.py exercises headless, wired up
as a long-running service instead of a bounded CLI run.

The source is either live polling (recorded as it goes) or a replay of
recordings (engine/sources/replay.py). Either way, "now" is the fetch time of
the payload being processed (``self.clock``), not the wall clock -- so rolling
windows cover recorded time during a time-lapse, and live behaves as before.
"""
from __future__ import annotations

import logging
import time

from engine.config import Config
from engine.normalise.aggregate import (
    Interchange,
    RollingAdherence,
    RollingInterchangeActivity,
    count_active_alerts,
    load_cbd_ids,
    load_interchanges,
    nearby_interchanges,
)
from engine.normalise.build import build_vehicle_records
from engine.normalise.enrich import enrich_vehicle_record
from engine.normalise.map_match import ShapeIndex
from engine.normalise.segments import SegmentAggregator
from engine.normalise.static_lookup import StaticLookup
from engine.normalise.trip_updates import build_trip_update_index
from engine.normalise.vehicle import VehicleRecord
from engine.sources.footprint import FootprintFilter
from engine.sources.live import LiveSource
from engine.sources.recorder import RecordingSource
from engine.sources.replay import RESTART

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

        self.interchanges: list[Interchange] = load_interchanges(db_path, cfg.raw["interchanges"])
        self._interchange_radius_m = cfg.raw.get("interchange_radius_m", 300)
        self.recordings_dir = cfg.root / "data" / "recordings"

        self.epoch = 0  # bumped on every reset, so pages know to drop what they're showing
        self.reset()

    def reset(self) -> None:
        """Forget everything derived from the current source -- on switching
        between live and replay, and each time a replay loops."""
        self.clock = time.time()
        self.snapshot: dict[str, VehicleRecord] = {}
        self.snapshot_updated_at: dict[str, float] = {}
        self.alerts_summary: dict = {"count": 0, "by_severity": {}, "route_ids": []}
        self.last_positions_at: dict[str, float] = {}  # mode -> wall time its latest positions arrived
        self.rolling_adherence = RollingAdherence()
        self.segments = SegmentAggregator(self._shapes)
        self.rolling_interchange_activity = RollingInterchangeActivity()
        self._trip_update_index_by_mode: dict[str, dict] = {}
        self.epoch += 1

    def live_source(self) -> RecordingSource:
        footprint = FootprintFilter(self._cfg.bounding_box, self._cbd_stop_ids)
        return RecordingSource(LiveSource(self._cfg), self.recordings_dir, footprint, tag="serve")

    async def run_forever(self, source=None) -> None:
        bbox = self._cfg.bounding_box
        source = source or self.live_source()
        async for raw in source.stream():
            if raw.feed_type == RESTART:
                self.reset()
                continue
            self.clock = raw.fetched_at.timestamp()
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
            now = self.clock
            for r in records:
                enriched = enrich_vehicle_record(
                    r, trip_updates, self._shapes, self._lookup, self._thresholds, self._very_late_multiplier
                )
                self.snapshot[enriched.vehicle_key] = enriched
                self.snapshot_updated_at[enriched.vehicle_key] = now
                self.rolling_adherence.record(enriched.mode, enriched.adherence_state, at=now)
                self.segments.record(enriched, at=now)

                position_lat = enriched.matched_lat if enriched.matched_lat is not None else enriched.lat
                position_lon = enriched.matched_lon if enriched.matched_lon is not None else enriched.lon
                for name in nearby_interchanges(
                    self.interchanges, position_lat, position_lon, self._interchange_radius_m
                ):
                    self.rolling_interchange_activity.record(name, enriched.vehicle_key, at=now)
            self.last_positions_at[raw.mode] = time.time()
            self._prune_stale(now)
            logger.info(
                "vehicle_positions:%s: %d vehicles, snapshot size %d",
                raw.mode, len(records), len(self.snapshot),
            )

    def aggregate_snapshot(self) -> dict:
        """Everything the stats panel (and the model renderer's interchange
        pulses) need: rolling adherence by window, active alerts, and
        per-interchange recent activity counts."""
        interchange_counts = self.rolling_interchange_activity.counts(now=self.clock)
        return {
            "adherence_by_window": self.rolling_adherence.breakdown_all_windows(now=self.clock),
            "alerts": self.alerts_summary,
            "interchanges": [
                {"name": ic.name, "lat": ic.lat, "lon": ic.lon, "count": interchange_counts.get(ic.name, 0)}
                for ic in self.interchanges
            ],
        }

    def health(self) -> dict:
        """What the controller's team panel shows: vehicles on the model per
        mode, and how long since each mode's feed last arrived."""
        now = time.time()
        by_mode: dict[str, int] = {}
        for record in self.snapshot.values():
            by_mode[record.mode] = by_mode.get(record.mode, 0) + 1
        return {
            "vehicles_total": len(self.snapshot),
            "vehicles_by_mode": by_mode,
            "feed_age_seconds": {mode: round(now - t, 1) for mode, t in self.last_positions_at.items()},
        }

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
