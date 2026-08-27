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
from typing import Dict, List

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
        self._updated_at: float = 0.0

    def update(self, vehicles: List[VehicleRecord], aggregates: Dict[str, RouteAggregate]) -> None:
        with self._lock:
            self._vehicles = [dataclasses.asdict(v) for v in vehicles]
            self._route_adherence = {k: dataclasses.asdict(v) for k, v in aggregates.items()}
            self._updated_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "updated_at": self._updated_at,
                "vehicle_count": len(self._vehicles),
                "vehicles": self._vehicles,
                "route_adherence": self._route_adherence,
            }


def build_app(store: SnapshotStore):
    """Build the FastAPI app. Imported lazily so the rest of the pipeline
    (ingestion/normalisation/analytics) has zero web-framework dependency
    and stays testable without fastapi/uvicorn installed."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect

    app = FastAPI(title="Transit AR Pipeline Snapshot")

    @app.get("/snapshot")
    def get_snapshot():
        return store.snapshot()

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
