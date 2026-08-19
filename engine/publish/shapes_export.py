"""Prepares route-corridor shapes for the renderer's base layer.

The Phase 1 cache retains a shape in full if *any* point crosses the CBD
bounding box (see docs/DECISIONS.md's "Use the Complete GTFS bundle" entry),
so a regional NSW Trains shape can run hundreds of km outside the box.
Sending that whole thing to a browser would be enormous (the cache has 2500
shapes / 3.8M points total) and pointless -- this clips each shape down to a
padded version of the bounding box before export, which is the "revisit
before rendering" flagged back in Phase 1.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from engine.config import BoundingBox


def _padded(bbox: BoundingBox, pad_ratio: float) -> BoundingBox:
    lat_pad = (bbox.north - bbox.south) * pad_ratio
    lon_pad = (bbox.east - bbox.west) * pad_ratio
    return BoundingBox(
        north=bbox.north + lat_pad,
        south=bbox.south - lat_pad,
        west=bbox.west - lon_pad,
        east=bbox.east + lon_pad,
    )


def _in_box(lat: float, lon: float, bbox: BoundingBox) -> bool:
    return bbox.south <= lat <= bbox.north and bbox.west <= lon <= bbox.east


def build_render_shapes(
    db_path: Path, bbox: BoundingBox, pad_ratio: float = 0.15
) -> dict[str, list[list[float]]]:
    """{shape_id: [[lat, lon], ...]} clipped to a padded bounding box.

    A point survives if it, or either immediate neighbour, is inside the
    padded box -- so a corridor crossing the boundary stays visually
    continuous instead of being cut off abruptly mid-line.
    """
    padded = _padded(bbox, pad_ratio)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        raw: dict[str, list[tuple[int, float, float]]] = {}
        for shape_id, seq, lat, lon in conn.execute(
            "SELECT shape_id, shape_pt_sequence, shape_pt_lat, shape_pt_lon FROM shapes"
        ):
            raw.setdefault(shape_id, []).append((seq, lat, lon))
    finally:
        conn.close()

    result: dict[str, list[list[float]]] = {}
    for shape_id, pts in raw.items():
        pts.sort(key=lambda p: p[0])
        inside = [_in_box(lat, lon, padded) for _, lat, lon in pts]
        keep = [
            inside[i] or (i > 0 and inside[i - 1]) or (i < len(pts) - 1 and inside[i + 1])
            for i in range(len(pts))
        ]
        if not any(keep):
            continue
        clipped = [[lat, lon] for (_, lat, lon), k in zip(pts, keep) if k]
        if len(clipped) >= 2:
            result[shape_id] = clipped
    return result
