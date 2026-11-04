"""Shared display-control state for the projector view.

The projector mirrors the laptop screen, so nothing interactive can live on
the projected image itself. Instead the tablet controller (and the laptop
keyboard, as a backup) send small patches to the server over the existing
WebSocket; the server validates them here, holds the one authoritative copy,
and broadcasts the result to every connected page -- so the projector, the
tablet and any other open controller always agree.

Only the nudge (a small shift/scale on top of the calibration, for drift
between weekly sessions) and the corner warp (each corner of the footprint
dragged onto the model's corner, which also takes out projector keystone)
are persisted across restarts; everything else resets to its default, which
is the safe state to start a showcase from.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("pulse_cbd.control")

# Vehicle-mode groups as the controller shows them. A group can cover several
# feed modes (e.g. both light rail lines).
MODE_GROUPS: dict[str, list[str]] = {
    "trains": ["sydneytrains", "nswtrains"],
    "metro": ["metro"],
    "lightrail": ["lightrail_innerwest", "lightrail_cbdandsoutheast"],
    "buses": ["buses"],
    "ferries": ["ferries"],
}

# What vehicle-dot colour shows, and which street-segment layer is drawn (at
# most one line layer at a time; see docs/DECISIONS.md).
LENSES = ("adherence", "congestion", "crowding")
LINE_LAYERS = ("none", "congestion", "crowding", "boam_load", "boam_lost")
# Which BOAM peak the typical-load / time-lost streets and the pressure
# layer's BOAM input use.
BOAM_PERIODS = ("am", "pm")
STOP_METRICS = ("median", "mean")
MAX_FOCUSED_ROUTES = 8
# Static area layers (engine/demand/areas.py) and the unit they're drawn on.
AREA_LAYERS = ("none", "jobs", "service_gap", "opal", "pressure")
# Weights for the combined pressure layer (engine/demand/pressure.py).
PRESSURE_INPUTS = ("congestion", "crowding", "service_gap", "opal", "boam_load")
WEIGHT_LIMITS = (0.0, 3.0)
AREA_UNITS = ("zones", "grid")

IDENTITY_NUDGE = {"dx": 0.0, "dy": 0.0, "scale": 1.0}

# Playback: "live", or a recording session id from engine/sources/replay.py
# (the server checks it exists). Speed only matters for replays.
REPLAY_SPEED_LIMITS = (1.0, 100.0)
DEFAULT_REPLAY_SPEED = 25.0  # a 2h10m peak in ~5 minutes: the showcase default
_NUDGE_LIMITS = {"dx": (-2000.0, 2000.0), "dy": (-2000.0, 2000.0), "scale": (0.5, 2.0)}
# Corner warp: screen-pixel offset of each footprint corner from where the
# calibration (plus nudge) puts it. North-west, north-east, south-east, south-west.
WARP_CORNERS = ("tl", "tr", "br", "bl")
_WARP_LIMIT = (-2000.0, 2000.0)
IDENTITY_WARP = {corner: [0.0, 0.0] for corner in WARP_CORNERS}

DEFAULT_STATE: dict[str, Any] = {
    "theme": "dark",
    "verify": False,
    "hud": False,
    "calibrate": False,
    "corners": False,  # corner-fit mode: drag the footprint's corners on the projector
    "interchanges": True,
    "stops": False,
    "stop_history": False,
    "stop_metric": "median",
    "routes": [],  # route short names singled out; empty shows everything
    "route_slots": {},  # route name -> colour slot 0-7, assigned here, not by clients
    "lens": "adherence",
    "line_layer": "none",
    "area_layer": "none",
    "area_unit": "zones",
    "boam_period": "am",
    "weights": {name: 1.0 for name in PRESSURE_INPUTS},
    "playback": {"session": "live", "speed": DEFAULT_REPLAY_SPEED},
    "modes": {group: True for group in MODE_GROUPS},
    "nudge": dict(IDENTITY_NUDGE),
    "warp": copy.deepcopy(IDENTITY_WARP),
}


class ControlError(ValueError):
    pass


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


class ControlState:
    def __init__(self, nudge_path: Path | None = None):
        self._nudge_path = nudge_path
        self.state: dict[str, Any] = copy.deepcopy(DEFAULT_STATE)
        if nudge_path is not None and nudge_path.exists():
            try:
                saved = json.loads(nudge_path.read_text(encoding="utf-8"))
                # Older files hold just the nudge.
                self.apply(saved if isinstance(saved, dict) and "nudge" in saved else {"nudge": saved})
            except (ControlError, ValueError) as exc:
                logger.warning("ignoring unreadable nudge file %s: %s", nudge_path, exc)

    def apply(self, patch: Any) -> dict[str, Any]:
        """Validate and merge a patch. Raises ControlError without changing
        anything if any part of it is invalid."""
        if not isinstance(patch, dict):
            raise ControlError("patch must be an object")
        updated = copy.deepcopy(self.state)
        for key, value in patch.items():
            if key == "theme":
                if value not in ("dark", "light"):
                    raise ControlError(f"theme must be 'dark' or 'light', got {value!r}")
                updated["theme"] = value
            elif key == "lens":
                if value not in LENSES:
                    raise ControlError(f"lens must be one of {', '.join(LENSES)}")
                updated["lens"] = value
            elif key == "line_layer":
                if value not in LINE_LAYERS:
                    raise ControlError(f"line_layer must be one of {', '.join(LINE_LAYERS)}")
                updated["line_layer"] = value
            elif key == "area_layer":
                if value not in AREA_LAYERS:
                    raise ControlError(f"area_layer must be one of {', '.join(AREA_LAYERS)}")
                updated["area_layer"] = value
            elif key == "area_unit":
                if value not in AREA_UNITS:
                    raise ControlError(f"area_unit must be one of {', '.join(AREA_UNITS)}")
                updated["area_unit"] = value
            elif key == "boam_period":
                if value not in BOAM_PERIODS:
                    raise ControlError("boam_period must be 'am' or 'pm'")
                updated["boam_period"] = value
            elif key == "stop_metric":
                if value not in STOP_METRICS:
                    raise ControlError("stop_metric must be 'median' or 'mean'")
                updated["stop_metric"] = value
            elif key == "routes":
                if not isinstance(value, list) or not all(isinstance(r, str) and 0 < len(r) <= 32 for r in value):
                    raise ControlError("routes must be a list of route names")
                routes = list(dict.fromkeys(value))
                if len(routes) > MAX_FOCUSED_ROUTES:
                    raise ControlError(f"at most {MAX_FOCUSED_ROUTES} routes at once")
                # A route keeps its colour while it stays selected, so removing
                # one never repaints the others; a new one takes the lowest free slot.
                slots = {n: s for n, s in updated["route_slots"].items() if n in routes}
                free = [i for i in range(MAX_FOCUSED_ROUTES) if i not in slots.values()]
                for name in routes:
                    if name not in slots:
                        slots[name] = free.pop(0)
                updated["routes"] = routes
                updated["route_slots"] = slots
            elif key == "weights":
                if not isinstance(value, dict):
                    raise ControlError("weights must be an object")
                for name, w in value.items():
                    if name not in PRESSURE_INPUTS:
                        raise ControlError(f"unknown pressure input {name!r}")
                    if isinstance(w, bool) or not isinstance(w, (int, float)):
                        raise ControlError(f"weights.{name} must be a number")
                    updated["weights"][name] = _clamp(float(w), WEIGHT_LIMITS)
            elif key in ("verify", "hud", "calibrate", "corners", "interchanges", "stops", "stop_history"):
                if not isinstance(value, bool):
                    raise ControlError(f"{key} must be true/false")
                updated[key] = value
            elif key == "modes":
                if not isinstance(value, dict):
                    raise ControlError("modes must be an object")
                for group, on in value.items():
                    if group not in MODE_GROUPS:
                        raise ControlError(f"unknown mode group {group!r}")
                    if not isinstance(on, bool):
                        raise ControlError(f"modes.{group} must be true/false")
                    updated["modes"][group] = on
            elif key == "playback":
                if not isinstance(value, dict):
                    raise ControlError("playback must be an object")
                for field, item in value.items():
                    if field == "session":
                        if not isinstance(item, str) or not item:
                            raise ControlError("playback.session must be 'live' or a recording id")
                        updated["playback"]["session"] = item
                    elif field == "speed":
                        if isinstance(item, bool) or not isinstance(item, (int, float)):
                            raise ControlError("playback.speed must be a number")
                        updated["playback"]["speed"] = _clamp(float(item), REPLAY_SPEED_LIMITS)
                    else:
                        raise ControlError(f"unknown playback field {field!r}")
            elif key == "nudge":
                if not isinstance(value, dict):
                    raise ControlError("nudge must be an object")
                for field, amount in value.items():
                    if field not in _NUDGE_LIMITS:
                        raise ControlError(f"unknown nudge field {field!r}")
                    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                        raise ControlError(f"nudge.{field} must be a number")
                    updated["nudge"][field] = _clamp(float(amount), _NUDGE_LIMITS[field])
            elif key == "warp":
                if not isinstance(value, dict):
                    raise ControlError("warp must be an object")
                for corner, offset in value.items():
                    if corner not in WARP_CORNERS:
                        raise ControlError(f"unknown warp corner {corner!r}")
                    if (not isinstance(offset, list) or len(offset) != 2
                            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in offset)):
                        raise ControlError(f"warp.{corner} must be [dx, dy]")
                    updated["warp"][corner] = [_clamp(float(v), _WARP_LIMIT) for v in offset]
            else:
                raise ControlError(f"unknown control {key!r}")

        alignment_changed = updated["nudge"] != self.state["nudge"] or updated["warp"] != self.state["warp"]
        self.state = updated
        if alignment_changed:
            self._save_nudge()
        return self.state

    def reset_nudge(self) -> dict[str, Any]:
        """Back to the bare calibration: no nudge, no corner warp."""
        return self.apply({"nudge": dict(IDENTITY_NUDGE), "warp": copy.deepcopy(IDENTITY_WARP)})

    def _save_nudge(self) -> None:
        if self._nudge_path is None:
            return
        self._nudge_path.parent.mkdir(parents=True, exist_ok=True)
        self._nudge_path.write_text(
            json.dumps({"nudge": self.state["nudge"], "warp": self.state["warp"]}), encoding="utf-8")
