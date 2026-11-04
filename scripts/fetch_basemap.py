#!/usr/bin/env python3
"""Downloads a detailed background map for the footprint from OpenStreetMap.

One Overpass API query for the model's footprint (plus PADDING), saved to
data/basemap/basemap.json for the projector's optional "Detailed" background
(control state `basemap`; off by default, since the model itself has the
streets). Stored locally so the map needs no internet at the showcase, and
drawn as lines/polygons through the same projector as everything else, so it
lines up with the model after calibration.

Layers: major roads, minor roads, rail (heavy, light rail, metro), coastline
(only the ocean shore; the harbour is a water polygon), water and parks,
plus labels for the "Full" option (`basemap` = "full"): suburbs and
neighbourhoods, stations and wharves, landmarks, big parks, named water and
major roads, each with a kind the projector ranks them by when they'd
overlap. Coordinates are rounded to 5 decimals (~1 m).

Map data (c) OpenStreetMap contributors, ODbL -- keep the attribution with it.

    python scripts/fetch_basemap.py            # download, then build
    python scripts/fetch_basemap.py --cached   # rebuild from the last download
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import load_config

# Public Overpass servers are often busy (502/504/429): try each a few times.
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
ATTEMPTS_PER_SERVER = 3
PADDING = 0.02  # share of the box added on each side, so roads run off the edge cleanly
ATTRIBUTION = "Map data (c) OpenStreetMap contributors, ODbL"

MAJOR_ROADS = {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link", "secondary_link"}
MINOR_ROADS = {"tertiary", "tertiary_link", "residential", "unclassified", "living_street", "pedestrian", "service"}
RAIL = {"rail", "light_rail", "subway"}
PARKS = {("leisure", "park"), ("leisure", "garden"), ("leisure", "golf_course"), ("landuse", "grass"), ("landuse", "recreation_ground")}

# Labels. Parks and water are only named above this size (m^2); a major road's
# name repeats every ROAD_LABEL_SPACING_M along it.
MIN_LABELLED_AREA_M2 = 40_000
ROAD_LABEL_SPACING_M = 1500
MIN_LABELLED_ROAD_M = 80  # pieces shorter than this can't hold their name
LABELLED_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "pedestrian"}
PLACE_KINDS = {"city": "suburb", "town": "suburb", "suburb": "suburb",
               "neighbourhood": "neighbourhood", "quarter": "neighbourhood", "island": "neighbourhood"}
# "Circular Quay 5A", "Darling Harbour Wharf 3", "Circular Quay, No. 3 Wharf" -> one label per place
WHARF_SUFFIX = re.compile(r"(,\s*No\.?\s*\d+\w*\s+Wharf|\s+Wharf\s+\d+\w*|\s+\d+[A-Z]?)$")
SAME_LABEL_WITHIN_M = 400  # e.g. Central's train, metro and light rail stations: one label


def _query(s: float, w: float, n: float, e: float) -> str:
    bbox = f"{s},{w},{n},{e}"
    roads = "|".join(sorted(MAJOR_ROADS | MINOR_ROADS))
    return f"""[out:json][timeout:180];
(
  way["highway"~"^({roads})$"]["service"!~"."]({bbox});
  way["railway"~"^({'|'.join(sorted(RAIL))})$"]["service"!~"."]({bbox});
  way["natural"="coastline"]({bbox});
  way["natural"="water"]({bbox});
  relation["natural"="water"]({bbox});
  way["leisure"~"^(park|garden|golf_course)$"]({bbox});
  relation["leisure"~"^(park|garden|golf_course)$"]({bbox});
  way["landuse"~"^(grass|recreation_ground)$"]({bbox});
);
out geom;
(
  node["place"~"^(city|town|suburb|neighbourhood|quarter|island)$"]["name"]({bbox});
  nwr["railway"="station"]["name"]({bbox});
  nwr["amenity"="ferry_terminal"]["name"]({bbox});
  nwr["tourism"~"^(museum|zoo|aquarium)$"]["name"]({bbox});
  nwr["tourism"="attraction"]["name"]["wikidata"]({bbox});
  nwr["leisure"="stadium"]["name"]({bbox});
  nwr["amenity"~"^(university|hospital)$"]["name"]({bbox});
  node["natural"="bay"]["name"]({bbox});
);
out center tags;"""


def _line(geometry: list[dict]) -> list[list[float]]:
    pts: list[list[float]] = []
    for g in geometry:
        p = [round(g["lat"], 5), round(g["lon"], 5)]
        if not pts or pts[-1] != p:
            pts.append(p)
    return pts


def _rings(ways: list[list[list[float]]]) -> list[list[list[float]]]:
    """Joins a multipolygon's outer member ways end to end into closed rings
    (OSM splits big outlines into several ways)."""
    rings, open_ = [], [w for w in ways if len(w) >= 2]
    while open_:
        ring = open_.pop()
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, w in enumerate(open_):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += w[-2::-1]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = w[:0:-1] + ring
                else:
                    continue
                open_.pop(i)
                changed = True
                break
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def _area_kind(tags: dict) -> str | None:
    if tags.get("natural") == "water":
        return "water"
    if any(tags.get(k) == v for k, v in PARKS):
        return "parks"
    return None


def _metres(a: list[float], b: list[float]) -> float:
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def _ring_area_centre(ring: list[list[float]]) -> tuple[float, list[float]]:
    """Area (m^2, shoelace on a local flat plane) and vertex-mean centre."""
    lat0 = ring[0][0]
    kx, ky = 111_320 * math.cos(math.radians(lat0)), 111_320
    xy = [((p[1] - ring[0][1]) * kx, (p[0] - lat0) * ky) for p in ring]
    area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:]))) / 2
    centre = [round(sum(p[0] for p in ring) / len(ring), 5), round(sum(p[1] for p in ring) / len(ring), 5)]
    return area, centre


def _point_kind(tags: dict) -> str | None:
    if tags.get("place") in PLACE_KINDS:
        return PLACE_KINDS[tags["place"]]
    # rail (train, metro, light rail) and ferries; bus interchanges are also
    # public_transport=station in OSM but would crowd out the real stations
    if tags.get("railway") == "station" or tags.get("amenity") == "ferry_terminal":
        return "station"
    if tags.get("natural") == "bay":
        return "water"
    if tags.get("tourism") or tags.get("leisure") == "stadium" or tags.get("amenity") in ("university", "hospital"):
        return "landmark"
    return None


def _road_labels(named: dict[str, list[list[list[float]]]]) -> list[dict]:
    """One anchor at the middle of each piece of a named road, longest pieces
    first, keeping the same name at least ROAD_LABEL_SPACING_M * 0.7 apart (OSM
    splits city streets at every intersection, so pieces are often ~100 m).
    A second point a little further on lets the projector angle the text."""
    out = []
    for name, ways in named.items():
        placed: list[list[float]] = []
        for pts in sorted(ways, key=lambda w: sum(_metres(a, b) for a, b in zip(w, w[1:])), reverse=True):
            seg_lengths = [_metres(a, b) for a, b in zip(pts, pts[1:])]
            total = sum(seg_lengths)
            if total < MIN_LABELLED_ROAD_M:
                break  # sorted: every later piece is shorter
            travelled = 0.0
            for (p1, p2), seg in zip(zip(pts, pts[1:]), seg_lengths):
                if travelled + seg >= total / 2:
                    if all(_metres(p1, q) > ROAD_LABEL_SPACING_M * 0.7 for q in placed):
                        placed.append(p1)
                        out.append({"t": name, "k": "road", "lat": p1[0], "lon": p1[1], "lat2": p2[0], "lon2": p2[1]})
                    break
                travelled += seg
    return out


def _dedupe(labels: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for lab in labels:
        if not any(k["t"] == lab["t"] and _metres([k["lat"], k["lon"]], [lab["lat"], lab["lon"]]) < SAME_LABEL_WITHIN_M
                   for k in kept):
            kept.append(lab)
    return kept


def build(elements: list[dict]) -> dict[str, list]:
    layers: dict[str, list] = {k: [] for k in ("water", "parks", "coastline", "roads_minor", "roads_major", "rail")}
    labels: list[dict] = []
    named_roads: dict[str, list] = {}

    def area_label(kind: str, ring: list[list[float]], name: str | None) -> None:
        if name:
            area, centre = _ring_area_centre(ring)
            if area >= MIN_LABELLED_AREA_M2:
                labels.append({"t": name, "k": "park" if kind == "parks" else "water", "lat": centre[0], "lon": centre[1], "area": round(area)})

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if el["type"] == "way" and "geometry" in el:
            pts = _line(el["geometry"])
            if len(pts) < 2:
                continue
            if tags.get("highway") and tags.get("tunnel") not in (None, "no"):
                continue  # underground: drawn on the surface it would mislead
            if name and tags.get("highway") in LABELLED_ROADS:
                named_roads.setdefault(name, []).append(pts)
            if tags.get("highway") in MAJOR_ROADS:
                layers["roads_major"].append(pts)
            elif tags.get("highway") in MINOR_ROADS:
                layers["roads_minor"].append(pts)
            elif tags.get("railway") in RAIL:
                layers["rail"].append(pts)
            elif tags.get("natural") == "coastline":
                layers["coastline"].append(pts)
            elif (kind := _area_kind(tags)) and pts[0] == pts[-1] and len(pts) >= 4:
                layers[kind].append(pts)
                area_label(kind, pts, name)
        elif el["type"] == "relation" and "members" in el and (kind := _area_kind(tags)):
            outers = [_line(m["geometry"]) for m in el["members"]
                      if m.get("type") == "way" and m.get("role") == "outer" and "geometry" in m]
            rings = _rings(outers)
            layers[kind].extend(rings)
            if rings:
                area_label(kind, max(rings, key=len), name)
        elif name and (kind := _point_kind(tags)):
            # the labels query (`out center`): nodes carry lat/lon, ways and relations a centre
            where = el.get("center", el)
            if kind == "station":
                name = WHARF_SUFFIX.sub("", name)
            if "lat" in where:
                labels.append({"t": name, "k": kind, "lat": round(where["lat"], 5), "lon": round(where["lon"], 5)})

    labels.sort(key=lambda lab: -lab.get("area", 0))  # bigger parks/water first within a kind
    layers["labels"] = _dedupe(labels) + _road_labels(named_roads)
    return layers


def _fetch(query: str) -> list[dict]:
    headers = {"User-Agent": "pulse-cbd basemap fetch (CIVL3704 student project)"}
    for url in OVERPASS_URLS:
        print(f"querying {url} (can take a minute)...")
        for attempt in range(1, ATTEMPTS_PER_SERVER + 1):
            try:
                resp = httpx.post(url, data={"data": query}, timeout=300, headers=headers)
                resp.raise_for_status()
                return resp.json()["elements"]
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                print(f"  {url} attempt {attempt}: {exc.__class__.__name__}: {str(exc)[:80]}")
                time.sleep(10 * attempt)
    raise SystemExit("error: every Overpass server failed -- try again later (the old basemap is kept)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cached", action="store_true", help="rebuild from the last download instead of querying")
    args = parser.parse_args()
    cfg = load_config()
    bb = cfg.bounding_box
    dlat, dlon = (bb.north - bb.south) * PADDING, (bb.east - bb.west) * PADDING
    query = _query(bb.south - dlat, bb.west - dlon, bb.north + dlat, bb.east + dlon)
    raw_path = cfg.root / "data" / "basemap" / "overpass_raw.json"
    if args.cached:
        elements = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        elements = _fetch(query)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(elements), encoding="utf-8")
    layers = build(elements)

    out = cfg.root / "data" / "basemap" / "basemap.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "attribution": ATTRIBUTION,
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "layers": layers,
    }
    out.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    counts = ", ".join(f"{k} {len(v)}" for k, v in layers.items())
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB): {counts}")
    print("restart the server to serve it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
