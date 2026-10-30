"""Persists calibration to a JSON file so it survives restarts, per the brief.

Orchestrates lat/lon -> local metric plane -> homography. Deliberately uses
a simple equirectangular projection anchored at the bounding box centre here
-- the brief's suggested alternative to UTM 56S -- rather than the pyproj
UTM transform map matching uses (engine/normalise/projection.py), because
the renderer has to reproduce this exact same lat/lon -> metric step in
plain JS with no pyproj available. See docs/DECISIONS.md.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from engine.publish.homography import compute_homography

DEG_TO_M = 111_320.0  # metres per degree of latitude, standard approximation


class CalibrationPoint:
    __slots__ = ("lat", "lon", "pixel_x", "pixel_y")

    def __init__(self, lat: float, lon: float, pixel_x: float, pixel_y: float):
        self.lat = lat
        self.lon = lon
        self.pixel_x = pixel_x
        self.pixel_y = pixel_y


def to_local_metric(lat: float, lon: float, center_lat: float, center_lon: float) -> tuple[float, float]:
    x = (lon - center_lon) * math.cos(math.radians(center_lat)) * DEG_TO_M
    y = (lat - center_lat) * DEG_TO_M
    return x, y


def compute_and_save_calibration(
    path: Path,
    points: list[CalibrationPoint],
    canvas_width: int,
    canvas_height: int,
    center_lat: float,
    center_lon: float,
) -> dict:
    correspondences = []
    for p in points:
        x, y = to_local_metric(p.lat, p.lon, center_lat, center_lon)
        correspondences.append((x, y, p.pixel_x, p.pixel_y))

    matrix = compute_homography(correspondences)

    record = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "canvas_width": canvas_width,
        "canvas_height": canvas_height,
        "center_lat": center_lat,
        "center_lon": center_lon,
        "points": [
            {"lat": p.lat, "lon": p.lon, "pixel_x": p.pixel_x, "pixel_y": p.pixel_y} for p in points
        ],
        "homography": matrix.tolist(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def load_calibration(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
