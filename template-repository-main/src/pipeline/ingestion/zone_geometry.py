"""Point-in-polygon helpers shared by every travel-zone-based ingestion
module (pipeline.ingestion.demand loads TZ21 + employment; opal_importance
loads TZ21 + Opal tap on/off origin-destination). Both need the same "is
this point inside this zone's GeoJSON polygon" test, so it lives here once
instead of twice.
"""

from __future__ import annotations

from typing import List, Tuple

Ring = List[Tuple[float, float]]  # [(lat, lon), ...]


def ring_bbox(ring: Ring) -> Tuple[float, float, float, float]:
    lats = [p[0] for p in ring]
    lons = [p[1] for p in ring]
    return min(lats), max(lats), min(lons), max(lons)


def point_in_ring(lat: float, lon: float, ring: Ring) -> bool:
    """Standard ray-casting point-in-polygon test."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]
        if (lon_i > lon) != (lon_j > lon):
            x_intersect = (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i + 1e-15) + lat_i
            if lat < x_intersect:
                inside = not inside
        j = i
    return inside


def point_in_rings(lat: float, lon: float, rings: List[Ring]) -> bool:
    """rings[0] is the exterior boundary; any further rings are holes cut out of it."""
    if not rings or not point_in_ring(lat, lon, rings[0]):
        return False
    return not any(point_in_ring(lat, lon, hole) for hole in rings[1:])


def extract_rings(geometry: dict) -> List[Ring]:
    """GeoJSON coordinates are [lon, lat] - flip to (lat, lon) here so the
    rest of the pipeline never has to think about GeoJSON's axis order."""
    coords = geometry.get("coordinates", [])
    gtype = geometry.get("type")

    def flip(ring):
        return [(point[1], point[0]) for point in ring]

    if gtype == "Polygon":
        return [flip(ring) for ring in coords]
    if gtype == "MultiPolygon":
        return [flip(ring) for polygon in coords for ring in polygon]
    return []
