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

from . import config
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

    def update(
        self,
        vehicles: List[VehicleRecord],
        aggregates: Dict[str, RouteAggregate],
        congestion: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._vehicles = [dataclasses.asdict(v) for v in vehicles]
            self._route_adherence = {k: dataclasses.asdict(v) for k, v in aggregates.items()}
            self._congestion = congestion or {"cells": [], "hotspot_count": 0}
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
