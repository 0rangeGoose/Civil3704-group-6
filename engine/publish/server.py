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
from engine.publish.calibration import CalibrationPoint, compute_and_save_calibration, load_calibration
from engine.publish.feed import LiveVehicleFeed
from engine.publish.homography import HomographyError
from engine.publish.shapes_export import build_render_shapes

logger = logging.getLogger("pulse_cbd.publish_server")

BROADCAST_INTERVAL_SECONDS = 0.5
RENDERER_DIR = Path(__file__).resolve().parent.parent.parent / "renderer"
RENDERER_INDEX = RENDERER_DIR / "index.html"
STATS_INDEX = RENDERER_DIR / "stats.html"


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


def _interchange_frame(feed: "LiveVehicleFeed") -> list[dict]:
    counts = feed.rolling_interchange_activity.counts()
    return [
        {"name": ic.name, "lat": ic.lat, "lon": ic.lon, "count": counts.get(ic.name, 0)}
        for ic in feed.interchanges
    ]


async def index_handler(request: web.Request) -> web.Response:
    return web.Response(text=RENDERER_INDEX.read_text(), content_type="text/html")


async def stats_handler(request: web.Request) -> web.Response:
    """The separate wall-panel/tablet display -- per the brief, aggregate
    statistics never render onto the model surface itself."""
    return web.Response(text=STATS_INDEX.read_text(), content_type="text/html")


async def aggregate_handler(request: web.Request) -> web.Response:
    feed: LiveVehicleFeed = request.app["feed"]
    return web.json_response(feed.aggregate_snapshot())


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


async def get_calibration_handler(request: web.Request) -> web.Response:
    cfg: Config = request.app["cfg"]
    calibration = load_calibration(cfg.calibration_path)
    return web.json_response(calibration)


async def post_calibration_handler(request: web.Request) -> web.Response:
    """Save a new calibration from the renderer's calibration mode: >= 4
    (lat, lon, pixel_x, pixel_y) correspondences, per the brief."""
    cfg: Config = request.app["cfg"]
    try:
        body = await request.json()
        raw_points = body["points"]
        canvas_width = int(body["canvas_width"])
        canvas_height = int(body["canvas_height"])
        points = [
            CalibrationPoint(
                lat=float(p["lat"]), lon=float(p["lon"]),
                pixel_x=float(p["pixel_x"]), pixel_y=float(p["pixel_y"]),
            )
            for p in raw_points
        ]
    except (KeyError, TypeError, ValueError) as exc:
        return web.json_response({"error": f"invalid calibration payload: {exc}"}, status=400)

    bbox = cfg.bounding_box
    center_lat = (bbox.north + bbox.south) / 2
    center_lon = (bbox.west + bbox.east) / 2
    try:
        record = compute_and_save_calibration(
            cfg.calibration_path, points, canvas_width, canvas_height, center_lat, center_lon
        )
    except HomographyError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    logger.info("calibration saved: %d points, canvas %dx%d", len(points), canvas_width, canvas_height)
    return web.json_response(record)


_SEND_TIMEOUT_SECONDS = 2.0


async def _send_one(ws: web.WebSocketResponse, frame: str) -> bool:
    """Returns False if this socket should be dropped."""
    try:
        await asyncio.wait_for(ws.send_str(frame), timeout=_SEND_TIMEOUT_SECONDS)
        return True
    except (ConnectionResetError, ConnectionAbortedError, RuntimeError, asyncio.TimeoutError) as exc:
        logger.warning("dropping unresponsive renderer connection: %s", exc)
        return False


async def _broadcast_loop(app: web.Application) -> None:
    feed: LiveVehicleFeed = app["feed"]
    while True:
        await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)
        sockets = app["websockets"]
        if not sockets:
            continue
        frame = json.dumps(
            {
                "t": time.time(),
                "vehicles": [_vehicle_frame(r) for r in feed.snapshot.values()],
                "interchanges": _interchange_frame(feed),
            }
        )
        # A slow/frozen client must not be able to stall broadcasts to
        # everyone else -- sent concurrently, each with its own timeout,
        # rather than one at a time in a plain loop.
        results = await asyncio.gather(*(_send_one(ws, frame) for ws in list(sockets)))
        dead = {ws for ws, ok in zip(list(sockets), results) if not ok}
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
    app.router.add_get("/stats", stats_handler)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/snapshot", snapshot_handler)
    app.router.add_get("/shapes", shapes_handler)
    app.router.add_get("/config", config_handler)
    app.router.add_get("/aggregate", aggregate_handler)
    app.router.add_get("/calibration", get_calibration_handler)
    app.router.add_post("/calibration", post_calibration_handler)

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
