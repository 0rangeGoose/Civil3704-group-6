"""WGS84 <-> local metric plane, for distance/projection math that degrees can't do directly.

UTM zone 56S (EPSG:32756) covers Sydney (approx 150-156 deg E) and keeps
distortion low across the CBD bounding box, matching the brief's suggested
architecture ("shapely and pyproj for geometry and map matching ... UTM zone
56S").
"""
from __future__ import annotations

from pyproj import Transformer

_UTM_56S = "EPSG:32756"
_WGS84 = "EPSG:4326"

_to_metric = Transformer.from_crs(_WGS84, _UTM_56S, always_xy=True)
_to_wgs84 = Transformer.from_crs(_UTM_56S, _WGS84, always_xy=True)


def to_metric(lat: float, lon: float) -> tuple[float, float]:
    """Returns (x, y) metres in UTM 56S."""
    x, y = _to_metric.transform(lon, lat)
    return x, y


def to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Returns (lat, lon)."""
    lon, lat = _to_wgs84.transform(x, y)
    return lat, lon
