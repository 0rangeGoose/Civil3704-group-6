"""Serves the renderer and the live vehicle feed: a WebSocket broadcasting a
compact JSON frame ~2x/sec, plus plain HTTP endpoints for debugging and the
one-time static data (corridor shapes, bounding box) the renderer needs on
load. Definition of done: this one command starts the engine and serves the
renderer.

The same WebSocket also carries display control: a page sends
``{"control": {...patch...}}``, and every connected page receives the merged
``{"control": {...state...}}`` (see engine/publish/control.py). Vehicle
frames carry no "control" key, which is how pages tell the two apart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from engine.config import Config, load_config
from engine.normalise.levels import CONGESTION_LABELS, CROWDING_LABELS, congestion_level, crowding_level
from engine.normalise.segments import CONGESTION_MODES
from engine.publish.calibration import CalibrationPoint, compute_and_save_calibration, load_calibration
from engine.publish.control import MODE_GROUPS, ControlError, ControlState
from engine.publish.feed import LiveVehicleFeed
from engine.publish.homography import HomographyError
from engine.publish.shapes_export import build_render_shapes
from engine.demand.areas import build_area_layers
from engine.demand.pressure import PressureModel
from engine.demand.boam import build_boam_layers
from engine.history import store as stop_visit_store
from engine.sources.replay import RecordingIndex, ReplaySource

logger = logging.getLogger("pulse_cbd.publish_server")

BROADCAST_INTERVAL_SECONDS = 0.5
RENDERER_DIR = Path(__file__).resolve().parent.parent.parent / "renderer"
RENDERER_INDEX = RENDERER_DIR / "index.html"
CONTROLLER_INDEX = RENDERER_DIR / "controller.html"


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
        "dev": record.schedule_deviation_seconds,
        "cg": congestion_level(record.congestion_level) if record.mode in CONGESTION_MODES else None,
        "oc": crowding_level(record.occupancy_status),
        "ts": record.position_timestamp,
        "b": record.bearing,
    }


def _interchange_frame(feed: "LiveVehicleFeed") -> list[dict]:
    counts = feed.rolling_interchange_activity.counts(now=feed.clock)
    return [
        {"name": ic.name, "lat": ic.lat, "lon": ic.lon, "count": counts.get(ic.name, 0)}
        for ic in feed.interchanges
    ]


async def index_handler(request: web.Request) -> web.Response:
    # encoding="utf-8" explicitly -- Path.read_text()'s default is the OS
    # locale encoding, which is UTF-8 on macOS/Linux but cp1252 on Windows;
    # these templates have real non-ASCII characters (e.g. "·"), so reading
    # them with the Windows default raises UnicodeDecodeError.
    return web.Response(text=RENDERER_INDEX.read_text(encoding="utf-8"), content_type="text/html")


async def controller_handler(request: web.Request) -> web.Response:
    """The tablet controller: visitor controls, the team panel, and the stats
    that used to be /stats -- per the brief, text never renders onto the
    model surface itself, and the projector mirrors the laptop screen, so
    every control lives here instead."""
    return web.Response(text=CONTROLLER_INDEX.read_text(encoding="utf-8"), content_type="text/html")


async def stats_redirect_handler(request: web.Request) -> web.Response:
    raise web.HTTPFound("/control#stats")


async def health_handler(request: web.Request) -> web.Response:
    feed: LiveVehicleFeed = request.app["feed"]
    return web.json_response({
        **feed.health(),
        "connected_pages": len(request.app["websockets"]),
        "playback": _playback_frame(request.app),
    })


async def aggregate_handler(request: web.Request) -> web.Response:
    feed: LiveVehicleFeed = request.app["feed"]
    return web.json_response(feed.aggregate_snapshot())


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app["websockets"].add(ws)
    logger.info("renderer connected (%d total)", len(request.app["websockets"]))
    await _send_one(ws, json.dumps({"control": request.app["control"].state}))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
            if msg.type == WSMsgType.TEXT:
                await _handle_control_message(request.app, ws, msg.data)
    finally:
        request.app["websockets"].discard(ws)
        logger.info("renderer disconnected (%d remaining)", len(request.app["websockets"]))
    return ws


async def _handle_control_message(app: web.Application, ws: web.WebSocketResponse, data: str) -> None:
    try:
        message = json.loads(data)
        patch = message["control"]
        session = patch.get("playback", {}).get("session") if isinstance(patch, dict) else None
        if session not in (None, "live"):
            known = await asyncio.to_thread(app["replay_index"].find, session)
            if known is None:
                raise ControlError(f"no recording {session!r}")
        before = app["control"].state["playback"]["session"]
        app["control"].apply(patch)
    except (ValueError, KeyError, TypeError, AttributeError, ControlError) as exc:
        await _send_one(ws, json.dumps({"control_error": str(exc)}))
        return
    if app["control"].state["playback"]["session"] != before:
        await switch_source(app)
    await broadcast_control(app)


async def broadcast_control(app: web.Application) -> None:
    await _broadcast(app, json.dumps({"control": app["control"].state}))


async def segments_handler(request: web.Request) -> web.Response:
    feed: LiveVehicleFeed = request.app["feed"]
    return web.json_response(feed.segments.snapshot(feed.clock))


async def areas_handler(request: web.Request) -> web.Response:
    """Static area layers; built (or loaded from cache) in the background at
    startup, so this is empty for the first few seconds on a fresh build."""
    layers = request.app["areas"] or {"available": {}, "zones": [], "grid": [], "meta": {}}
    if request.query.get("meta"):  # the tablet only needs what's available and how it was built
        return web.json_response({"available": layers["available"], "meta": layers["meta"]})
    return web.json_response(layers)


async def pressure_handler(request: web.Request) -> web.Response:
    """Live combined score per area for the current unit and weights:
    {unit_id: {score, bin, inputs}}."""
    model: PressureModel | None = request.app["pressure_model"]
    if model is None:
        return web.json_response({})
    unit = request.query.get("unit", "zones")
    feed: LiveVehicleFeed = request.app["feed"]
    state = request.app["control"].state
    return web.json_response(model.scores(unit, feed.segments.snapshot(feed.clock), state["weights"], state["boam_period"]))


async def boam_handler(request: web.Request) -> web.Response:
    """Typical load and time lost per stop-to-stop section (BOAM), both peaks.
    Empty until built in the background at startup, or if none imported."""
    boam = request.app["boam"] or {"sections": [], "meta": {"days": []}}
    if request.query.get("meta"):
        return web.json_response(boam["meta"])
    return web.json_response(boam)


async def recordings_handler(request: web.Request) -> web.Response:
    index: RecordingIndex = request.app["replay_index"]
    sessions = await asyncio.to_thread(index.sessions)
    return web.json_response([s.to_json() for s in sessions])


def _playback_frame(app: web.Application) -> dict:
    feed: LiveVehicleFeed = app["feed"]
    replay: ReplaySource | None = app["replay"]
    if replay is None:
        return {"mode": "live", "t": feed.clock, "speed": 1}
    return {
        "mode": "replay",
        "session": replay.session.id,
        "label": replay.session.label,
        "t": feed.clock,
        "speed": app["control"].state["playback"]["speed"],
        "progress": round(replay.progress, 4),
    }


async def switch_source(app: web.Application) -> None:
    """Swap the feed between live polling and a replay. Live polling (and the
    server's own recording) pauses while a replay plays."""
    feed: LiveVehicleFeed = app["feed"]
    session_id = app["control"].state["playback"]["session"]
    app["feed_task"].cancel()
    try:
        await app["feed_task"]
    except asyncio.CancelledError:
        pass
    feed.reset()
    if feed.heartbeat is not None:
        feed.heartbeat.clear()  # replaying: the scheduled recorder may poll again
    if session_id == "live":
        app["replay"] = None
        app["feed_task"] = asyncio.create_task(feed.run_forever())
    else:
        session = app["replay_index"].find(session_id)
        control = app["control"]
        app["replay"] = ReplaySource(app["replay_index"], session, speed=lambda: control.state["playback"]["speed"])
        app["feed_task"] = asyncio.create_task(feed.run_forever(app["replay"]))
    logger.info("playback switched to %s", session_id)


async def snapshot_handler(request: web.Request) -> web.Response:
    feed: LiveVehicleFeed = request.app["feed"]
    return web.json_response([_vehicle_frame(r) for r in feed.snapshot.values()])


async def shapes_handler(request: web.Request) -> web.Response:
    return web.json_response(request.app["shapes"])


async def stops_handler(request: web.Request) -> web.Response:
    """Every stop in the footprint, for the optional stop-markers layer --
    static for the life of a run (loaded once in LiveVehicleFeed.__init__),
    same fetch-once-and-cache pattern as /shapes."""
    feed: LiveVehicleFeed = request.app["feed"]
    return web.json_response([
        {"id": s.stop_id, "name": s.name, "lat": s.lat, "lon": s.lon} for s in feed.stops
    ])


STOP_HISTORY_CACHE_S = 60.0


async def stop_history_handler(request: web.Request) -> web.Response:
    """Per-stop delay and crowding summaries from the stop-visit history.
    Rescanning the whole table is ~100ms, so it runs off the event loop and
    is cached -- the history only grows by a few rows a second anyway."""
    cache = request.app["stop_history_cache"]
    if time.monotonic() - cache["at"] > STOP_HISTORY_CACHE_S:
        feed: LiveVehicleFeed = request.app["feed"]
        cache["body"] = await asyncio.to_thread(stop_visit_store.stop_summaries, feed.stop_visits_db_path)
        cache["at"] = time.monotonic()
    return web.json_response(cache["body"])


async def config_handler(request: web.Request) -> web.Response:
    cfg: Config = request.app["cfg"]
    bbox = cfg.bounding_box
    return web.json_response(
        {
            "bounding_box": {"north": bbox.north, "south": bbox.south, "west": bbox.west, "east": bbox.east},
            "stale_after_seconds": cfg.raw["realtime_gtfs"]["stale_after_seconds"],
            "poll_interval_seconds": cfg.raw["realtime_gtfs"]["poll_interval_seconds"],
            "mode_groups": MODE_GROUPS,
            "congestion_labels": CONGESTION_LABELS,
            "crowding_labels": CROWDING_LABELS,
            # Keeps visitors out of the team panel; not a security boundary.
            "team_pin": str(cfg.raw.get("controller", {}).get("team_pin", "")),
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
    # Crosshairs are placed in raw screen pixels, so any nudge on top of the
    # old calibration no longer applies to the new one.
    request.app["control"].reset_nudge()
    request.app["control"].apply({"calibrate": False})
    await broadcast_control(request.app)
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


async def _broadcast(app: web.Application, frame: str) -> None:
    # A slow/frozen client must not be able to stall broadcasts to
    # everyone else -- sent concurrently, each with its own timeout,
    # rather than one at a time in a plain loop.
    sockets = app["websockets"]
    targets = list(sockets)
    results = await asyncio.gather(*(_send_one(ws, frame) for ws in targets))
    sockets -= {ws for ws, ok in zip(targets, results) if not ok}


async def _broadcast_loop(app: web.Application) -> None:
    feed: LiveVehicleFeed = app["feed"]
    while True:
        await asyncio.sleep(BROADCAST_INTERVAL_SECONDS)
        if not app["websockets"]:
            continue
        frame = json.dumps(
            {
                "t": time.time(),
                "epoch": feed.epoch,
                "playback": _playback_frame(app),
                "vehicles": [_vehicle_frame(r) for r in feed.snapshot.values()],
                "interchanges": _interchange_frame(feed),
            }
        )
        await _broadcast(app, frame)


async def _load_areas(app: web.Application) -> None:
    try:
        app["areas"] = await asyncio.to_thread(build_area_layers, app["cfg"])
        try:
            app["boam"] = await asyncio.to_thread(build_boam_layers, app["cfg"], app["feed"].shapes)
        except Exception:
            logger.exception("BOAM layers failed to build")
        app["pressure_model"] = await asyncio.to_thread(PressureModel, app["areas"], app["boam"])
    except Exception:  # the live map must still come up without them
        logger.exception("area layers failed to build")


async def _start_background_tasks(app: web.Application) -> None:
    app["areas_task"] = asyncio.create_task(_load_areas(app))
    app["feed_task"] = asyncio.create_task(app["feed"].run_forever())
    app["broadcast_task"] = asyncio.create_task(_broadcast_loop(app))


async def _cleanup_background_tasks(app: web.Application) -> None:
    app["feed_task"].cancel()
    app["broadcast_task"].cancel()
    if app["feed"].heartbeat is not None:
        app["feed"].heartbeat.clear()  # let the scheduled recorder take over at once
    app["feed"].close()


def build_app(cfg: Config) -> web.Application:
    app = web.Application()
    app["cfg"] = cfg
    app["websockets"] = set()
    app["stop_history_cache"] = {"at": float("-inf"), "body": {}}
    app["control"] = ControlState(cfg.root / "data" / "projection_nudge.json")
    app["replay"] = None
    app["areas"] = None
    app["pressure_model"] = None
    app["boam"] = None
    app["feed"] = LiveVehicleFeed(cfg)
    app["shapes"] = build_render_shapes(cfg.path("db_path"), cfg.bounding_box)
    app["replay_index"] = RecordingIndex(app["feed"].recordings_dir)

    app.router.add_get("/", index_handler)
    app.router.add_get("/control", controller_handler)
    app.router.add_get("/stats", stats_redirect_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/snapshot", snapshot_handler)
    app.router.add_get("/shapes", shapes_handler)
    app.router.add_get("/stops", stops_handler)
    app.router.add_get("/stop_history", stop_history_handler)
    app.router.add_get("/segments", segments_handler)
    app.router.add_get("/recordings", recordings_handler)
    app.router.add_get("/areas", areas_handler)
    app.router.add_get("/pressure", pressure_handler)
    app.router.add_get("/boam", boam_handler)
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
    server_cfg = cfg.raw.get("server", {})
    host = server_cfg.get("host", "127.0.0.1")
    port = int(server_cfg.get("port", 8765))
    logger.info("Pulse CBD projector view: http://127.0.0.1:%d/", port)
    for address in _lan_addresses() if host == "0.0.0.0" else []:
        logger.info("tablet controller: http://%s:%d/control", address, port)
    web.run_app(app, host=host, port=port, print=None)


def _lan_addresses() -> list[str]:
    """This machine's IPv4 addresses other than loopback, so the log can
    show the tablet which URL to open (e.g. the laptop's hotspot address)."""
    import socket

    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except socket.gaierror:
        pass
    return sorted(a for a in addresses if not a.startswith("127."))


if __name__ == "__main__":
    main()
