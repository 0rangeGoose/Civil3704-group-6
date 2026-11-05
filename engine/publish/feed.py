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
from engine.history import store as stop_visit_store
from engine.history.stop_tracker import StopVisitTracker
from engine.normalise.aggregate import (
    Interchange,
    RollingAdherence,
    RollingInterchangeActivity,
    Stop,
    TripPunctuality,
    TripReading,
    count_active_alerts,
    load_cbd_ids,
    load_interchanges,
    load_stops,
    nearby_interchanges,
)
from engine.normalise.adherence import PunctualityRules, classify
from engine.normalise.build import build_vehicle_records
from engine.normalise.enrich import enrich_vehicle_record
from engine.normalise.headways import TripTimetable
from engine.normalise.map_match import ShapeIndex
from engine.normalise.static_lookup import StaticLookup
from engine.normalise.trip_updates import build_trip_update_index
from engine.normalise.vehicle import VehicleRecord
from engine.sources.budget import budget_from_config
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
        self._rules = PunctualityRules.from_config(cfg.raw)
        self.timetable = TripTimetable.load(db_path)  # first stops and scheduled gaps per trip
        self._stale_after_seconds = cfg.raw["realtime_gtfs"]["stale_after_seconds"]

        self.interchanges: list[Interchange] = load_interchanges(db_path, cfg.raw["interchanges"])
        self._interchange_radius_m = cfg.raw.get("interchange_radius_m", 300)
        self.stops: list[Stop] = load_stops(db_path)
        self.recordings_dir = cfg.root / "data" / "recordings"
        # Called with every enriched vehicle record, if set: the CSV export
        # (engine/analysis/export.py) listens here.
        self.on_vehicle = None
        self.api_budget, self.heartbeat = budget_from_config(cfg)
        self.stop_visits_db_path = stop_visit_store.default_db_path(cfg)

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
        self.trip_punctuality = TripPunctuality()
        self.rolling_interchange_activity = RollingInterchangeActivity()
        self._trip_update_index_by_mode: dict[str, dict] = {}
        self.stop_visits = StopVisitTracker()
        self.epoch += 1

    @property
    def shapes(self) -> ShapeIndex:
        return self._shapes

    def live_source(self) -> RecordingSource:
        footprint = FootprintFilter(self._cfg.bounding_box, self._cbd_stop_ids)
        source = LiveSource(self._cfg, budget=self.api_budget, heartbeat=self.heartbeat)
        return RecordingSource(source, self.recordings_dir, footprint, tag="serve")

    async def run_forever(self, source=None) -> None:
        bbox = self._cfg.bounding_box
        # Stop visits are history: only live polling adds to it. A replay
        # would write the recorded visits again on every pass (and loop).
        record_stop_visits = source is None
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
                    r, trip_updates, self._shapes, self._lookup, self._rules, self.timetable
                )
                self.snapshot[enriched.vehicle_key] = enriched
                self.snapshot_updated_at[enriched.vehicle_key] = now
                self.rolling_adherence.record(enriched.mode, enriched.adherence_state, at=now)
                if self.on_vehicle is not None:
                    self.on_vehicle(enriched)

                trip_update_info = trip_updates.get(enriched.trip_id) if enriched.trip_id else None
                next_stop_id = trip_update_info.next_stop_id if trip_update_info else None
                visit = self.stop_visits.record(enriched, next_stop_id)
                if visit is not None and record_stop_visits:
                    stop_visit_store.record_stop_visit(self.stop_visits_db_path,**visit, recorded_at=now)
                self._record_trip_punctuality(enriched, trip_update_info, visit, now)

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

    def _record_trip_punctuality(self, v: VehicleRecord, info, visit: dict | None, now: float) -> None:
        """Each trip once, at TfNSW's measuring point (engine/normalise/aggregate.py)."""
        if not v.trip_id:
            return
        if v.mode == "buses":
            # leaving the trip's first stop: the bus contract's measuring point
            if visit is not None and visit["stop_id"] == self.timetable.first_stop(v.trip_id):
                delay = visit["delay_seconds"]
                state = classify(delay, v.mode, False, self._rules)
                self.trip_punctuality.record(f"{v.mode}:{v.trip_id}", TripReading(
                    now, v.mode, v.route_short_name, "first stop", visit["stop_id"], delay, state))
        elif info is not None:
            # predicted delay at the final stop: TfNSW's rail measuring point
            delay = info.final_stop_delay_seconds
            state = classify(delay, v.mode, info.cancelled, self._rules)
            self.trip_punctuality.record(f"{v.mode}:{v.trip_id}", TripReading(
                now, v.mode, v.route_short_name, "final stop (predicted)", info.final_stop_id, delay, state))

    def aggregate_snapshot(self) -> dict:
        """Everything the stats panel needs: rolling adherence by window,
        active alerts, and per-interchange recent activity counts."""
        interchange_counts = self.rolling_interchange_activity.counts(now=self.clock)
        return {
            "adherence_by_window": self.rolling_adherence.breakdown_all_windows(now=self.clock),
            "punctuality_by_window": self.trip_punctuality.breakdown_all_windows(now=self.clock),
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
            "api": self.api_budget.status() if self.api_budget else None,
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
