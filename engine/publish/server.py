"""Serves the renderer and the live vehicle feed: a WebSocket broadcasting a
compact JSON frame ~2x/sec, plus plain HTTP endpoints for debugging and the
one-time static data (corridor shapes, bounding box) the renderer needs on
load. Definition of done: this one command starts the engine and serves the
renderer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from engine.config import Config, load_config
from engine.publish.feed import LiveVehicleFeed
from engine.publish.shapes_export import build_render_shapes

logger = logging.getLogger("pulse_cbd.publish_server")

BROADCAST_INTERVAL_SECONDS = 0.5
RENDERER_INDEX = Path(__file__).resolve().parent.parent.parent / "renderer" / "index.html"


def _vehicle_frame(record) -> dict:
    lat = record.matched_lat if record.matched_lat is not None else record.lat
    lon = record.matched_lon if record.matched_lon is not None else record.lon
    return {
        "k": record.vehicle_key,
        "m": record.mode,
        "lat": lat,
        "lon": lon,
        "rc": record.route_color,
        "rsn": record.route_short_name,
        "adh": record.adherence_state,
        "ts": record.position_timestamp,
    }


async def index_handler(request: web.Request) -> web.Response:
    return web.Response(text=RENDERER_INDEX.read_text(), content_type="text/html")


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app["websockets"].add(ws)
    logger.info("renderer connected (%d total)", len(request.app["websockets"]))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        request.app["websockets"].discard(ws)
        logger.info("renderer disconnected (%d remaining)", len(request.app["websockets"]))
    return ws


async def snapshot_handler(request: web.Request) -> web.Response:
    feed: LiveVehicleFeed = request.app["feed"]
    return web.json_response([_vehicle_frame(r) for r in feed.snapshot.values()])


async def shapes_handler(request: web.Request) -> web.Response:
    return web.json_response(request.app["shapes"])


async def config_handler(request: web.Request) -> web.Response:
    cfg: Config = request.app["cfg"]
    bbox = cfg.bounding_box
    return web.json_response(
        {
            "bounding_box": {"north": bbox.north, "south": bbox.south, "west": bbox.west, "east": bbox.east},
            "stale_after_seconds": cfg.raw["realtime_gtfs"]["stale_after_seconds"],
            "poll_interval_seconds": cfg.raw["realtime_gtfs"]["poll_interval_seconds"],
        }
    )


async def _broadcast_loop(app: web.Application) -> None:
    feed: LiveVehicleFeed = app["feed"]
    while True:
        await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)
        sockets = app["websockets"]
        if not sockets:
            continue
        frame = json.dumps(
            {"t": time.time(), "vehicles": [_vehicle_frame(r) for r in feed.snapshot.values()]}
        )
        dead = set()
        for ws in list(sockets):
            try:
                await ws.send_str(frame)
            except ConnectionResetError:
                dead.add(ws)
        sockets -= dead


async def _start_background_tasks(app: web.Application) -> None:
    app["feed_task"] = asyncio.create_task(app["feed"].run_forever())
    app["broadcast_task"] = asyncio.create_task(_broadcast_loop(app))


async def _cleanup_background_tasks(app: web.Application) -> None:
    app["feed_task"].cancel()
    app["broadcast_task"].cancel()
    app["feed"].close()


def build_app(cfg: Config) -> web.Application:
    app = web.Application()
    app["cfg"] = cfg
    app["websockets"] = set()
    app["feed"] = LiveVehicleFeed(cfg)
    app["shapes"] = build_render_shapes(cfg.path("db_path"), cfg.bounding_box)

    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/snapshot", snapshot_handler)
    app.router.add_get("/shapes", shapes_handler)
    app.router.add_get("/config", config_handler)

    app.on_startup.append(_start_background_tasks)
    app.on_cleanup.append(_cleanup_background_tasks)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if not cfg.path("db_path").exists():
        raise SystemExit(f"error: {cfg.path('db_path')} not found -- run scripts/run_phase1.py first")
    app = build_app(cfg)
    logger.info("Pulse CBD serving on http://127.0.0.1:8765")
    web.run_app(app, host="127.0.0.1", port=8765, print=None)


if __name__ == "__main__":
    main()
