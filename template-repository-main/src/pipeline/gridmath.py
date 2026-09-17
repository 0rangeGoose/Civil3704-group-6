"""Shared lat/lon grid math.

Used by both analytics.py (the per-poll congestion cells, built
from wherever buses currently are) and ingestion/demand.py (the one-time,
comprehensive demand grid, built independent of buses). Kept in its own
module rather than analytics.py so demand.py can use it too without a
circular import (analytics.py already imports DemandZones from
ingestion.demand).

Both consumers must use the SAME reference latitude and cell size for their
grids to align on the map - that's why this lives in one place instead of
being duplicated.
"""

import math

from . import config

# Reference latitude for longitude spacing, so every cell is the same
# real-world size and a (row, col) key maps back to a fixed rectangle,
# regardless of which module is asking.
GRID_REF_LAT = (config.CBD_BBOX["min_lat"] + config.CBD_BBOX["max_lat"]) / 2


def grid_steps(cell_metres: float):
    lat_step = cell_metres / 111_320.0
    lon_step = cell_metres / (111_320.0 * math.cos(math.radians(GRID_REF_LAT)))
    return lat_step, lon_step


def cell_key(lat: float, lon: float, cell_metres: float):
    lat_step, lon_step = grid_steps(cell_metres)
    return (int(math.floor(lat / lat_step)), int(math.floor(lon / lon_step)))


def cell_bounds(row: int, col: int, cell_metres: float) -> dict:
    lat_step, lon_step = grid_steps(cell_metres)
    return {
        "min_lat": round(row * lat_step, 6),
        "max_lat": round((row + 1) * lat_step, 6),
        "min_lon": round(col * lon_step, 6),
        "max_lon": round((col + 1) * lon_step, 6),
    }
