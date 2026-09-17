"""Publish layer: an in-memory snapshot of every tracked vehicle, served over
HTTP (poll) and WebSocket (push at ~SNAPSHOT_PUSH_HZ).

This is the seam between the headless pipeline and the presentation layer
(model/projector renderer, wall-panel dashboard) described in the proposal:
nothing downstream of SnapshotStore needs to know anything about GTFS.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
import time
from typing import Dict, List, Optional
from pathlib import Path

from . import config, history
from .analytics import RouteAggregate
from .normalise import VehicleRecord

logger = logging.getLogger(__name__)


class SnapshotStore:
    """Thread-safe holder for "current state of every tracked vehicle".

    The ingestion loop (a background thread) writes to this; the HTTP/WS
    server (asyncio, in the main thread) reads from it. A plain lock is
    enough since writes are infrequent (once per poll cycle) and cheap.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._vehicles: List[dict] = []
        self._route_adherence: Dict[str, dict] = {}
        self._congestion: dict = {"cells": [], "hotspot_count": 0}
        self._demand_grid: dict = {"cells": [], "cell_metres": config.CONGESTION_GRID_CELL_METRES}
        self._route_lines: Dict[str, dict] = {}
        self._route_shapes: Dict[str, dict] = {}
        self._updated_at: float = 0.0

    def set_demand_grid(self, cells: List[dict], cell_metres: float) -> None:
        """The comprehensive, bus-independent demand layer (see
        ingestion.demand.DemandZones.build_grid) - set once at startup, not
        part of the repeating snapshot since it never changes during a run."""
        with self._lock:
            self._demand_grid = {"cells": cells, "cell_metres": cell_metres}

    def demand_grid(self) -> dict:
        with self._lock:
            return self._demand_grid

    def add_route_shapes(self, shapes: Dict[str, dict]) -> None:
        """Accumulate route-line geometry (shape_id -> {points, route_id,
        route_short_name}) as new shapes are seen among live buses. A small,
        ever-growing cache - bounded by however many distinct bus route
        shapes actually run through the model - served separately from
        /snapshot (GET /route_shapes) since geometry doesn't change once a
        shape has been seen, so there's no need to resend it every poll."""
        with self._lock:
            self._route_shapes.update(shapes)

    def route_shapes(self) -> dict:
        with self._lock:
            return dict(self._route_shapes)

    def update(
        self,
        vehicles: List[VehicleRecord],
        aggregates: Dict[str, RouteAggregate],
        congestion: Optional[dict] = None,
        route_lines: Optional[Dict[str, dict]] = None,
    ) -> None:
        with self._lock:
            self._vehicles = [dataclasses.asdict(v) for v in vehicles]
            self._route_adherence = {k: dataclasses.asdict(v) for k, v in aggregates.items()}
            self._congestion = congestion or {"cells": [], "hotspot_count": 0}
            self._route_lines = route_lines or {}
            self._updated_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "updated_at": self._updated_at,
                "model_bounds": config.CBD_BBOX,
                "vehicle_count": len(self._vehicles),
                "vehicles": self._vehicles,
                "route_adherence": self._route_adherence,
                "congestion": self._congestion,
                "route_lines": self._route_lines,
            }


def build_app(store: SnapshotStore):
    """Build the FastAPI app. Imported lazily so the rest of the pipeline
    (ingestion/normalisation/analytics) has zero web-framework dependency
    and stays testable without fastapi/uvicorn installed."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import RedirectResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Transit AR Pipeline Snapshot")
    dashboard_dir = Path(__file__).resolve().parents[2] / "dashboard"
    app.mount("/dashboard", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")

    @app.get("/")
    def index():
        return RedirectResponse(url="/dashboard/")

    @app.get("/snapshot")
    def get_snapshot():
        """Current state of every tracked vehicle + congestion grid. Polled by the dashboard/renderer."""
        return store.snapshot()

    @app.get("/demand_grid")
    def get_demand_grid():
        """The full demand layer (job density per block), independent of any
        bus position - fetched once by the frontend on load, not part of the
        repeating /snapshot poll, since it's static for the life of a run."""
        return store.demand_grid()

    @app.get("/route_shapes")
    def get_route_shapes():
        """Geometry for every bus route shape seen so far, keyed by shape_id.
        Accumulates over time as new routes are seen (bus service patterns
        shift through the day) - fetch this periodically, not every poll,
        to pick up newly-appeared routes; /snapshot's route_lines carries
        just the small per-shape score, not the geometry, each cycle."""
        return store.route_shapes()

    @app.get("/history")
    def get_history(period: str = "now"):
        """Hour-of-day average for a named period (am_peak/midday/pm_peak/
        evening, see config.TIME_PERIODS) - same {"congestion", "route_lines"}
        shape /snapshot uses, so the frontend can feed it through the exact
        same renderer as live data. "now" or any unrecognised period returns
        empty - the frontend already has live data for that case."""
        period_def = config.TIME_PERIODS.get(period)
        if not period_def:
            return {
                "congestion": {"cells": [], "hotspot_count": 0, "priority_count": 0},
                "route_lines": {},
                "sample_days": 0,
                "period": period,
                "label": period,
            }
        result = history.query_period(period_def["hours"])
        result["period"] = period
        result["label"] = period_def["label"]
        return result

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "updated_at": store.snapshot()["updated_at"]}

    @app.websocket("/ws")
    async def ws_snapshot(websocket: WebSocket):
        await websocket.accept()
        interval = 1.0 / config.SNAPSHOT_PUSH_HZ
        try:
            while True:
                await websocket.send_json(store.snapshot())
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            logger.info("Renderer disconnected from /ws")

    return app


def run_server(store: SnapshotStore, host: str = config.SNAPSHOT_HOST, port: int = config.SNAPSHOT_PORT) -> None:
    """Blocking call - run this on the main thread, with ingestion in a background thread."""
    import uvicorn

    app = build_app(store)
    uvicorn.run(app, host=host, port=port, log_level="warning")
