import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fetch_basemap", Path(__file__).resolve().parent.parent / "scripts" / "fetch_basemap.py"
)
fetch_basemap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_basemap)


def _geom(*pts):
    return [{"lat": lat, "lon": lon} for lat, lon in pts]


def test_ways_sorted_into_layers():
    elements = [
        {"type": "way", "tags": {"highway": "primary"}, "geometry": _geom((0, 0), (0, 1))},
        {"type": "way", "tags": {"highway": "residential"}, "geometry": _geom((1, 0), (1, 1))},
        {"type": "way", "tags": {"railway": "rail"}, "geometry": _geom((2, 0), (2, 1))},
        {"type": "way", "tags": {"natural": "coastline"}, "geometry": _geom((3, 0), (3, 1))},
        {"type": "way", "tags": {"leisure": "park"}, "geometry": _geom((0, 0), (0, 1), (1, 1), (0, 0))},
        {"type": "way", "tags": {"leisure": "park"}, "geometry": _geom((5, 0), (5, 1), (6, 1))},  # not closed: dropped
        {"type": "way", "tags": {"highway": "footway"}, "geometry": _geom((4, 0), (4, 1))},  # not drawn
    ]
    layers = fetch_basemap.build(elements)
    assert {k: len(v) for k, v in layers.items()} == {
        "water": 0, "parks": 1, "coastline": 1, "roads_minor": 1, "roads_major": 1, "rail": 1, "labels": 0,
    }


def test_labels_for_places_stations_and_big_named_areas():
    elements = [
        {"type": "node", "lat": -33.88, "lon": 151.21, "tags": {"place": "suburb", "name": "Surry Hills"}},
        {"type": "node", "lat": -33.89, "lon": 151.19, "tags": {"place": "locality", "name": "Eveleigh Car Sidings"}},
        # wharves come one per berth: one label for the place
        {"type": "node", "lat": -33.8610, "lon": 151.2110, "tags": {"amenity": "ferry_terminal", "name": "Circular Quay 5A"}},
        {"type": "node", "lat": -33.8612, "lon": 151.2112, "tags": {"amenity": "ferry_terminal", "name": "Circular Quay 2B"}},
        {"type": "way", "center": {"lat": -33.87, "lon": 151.2}, "tags": {"tourism": "museum", "name": "Australian Museum"}},
        {"type": "node", "lat": -33.85, "lon": 151.22, "tags": {"amenity": "bus_station", "public_transport": "station", "name": "441 Terminus"}},
        # a ~1 km square park is named, a ~10 m one isn't
        {"type": "way", "tags": {"leisure": "park", "name": "Big Park"},
         "geometry": _geom((0, 0), (0, 0.01), (0.01, 0.01), (0.01, 0), (0, 0))},
        {"type": "way", "tags": {"leisure": "park", "name": "Pocket Park"},
         "geometry": _geom((0, 0), (0, 0.0001), (0.0001, 0.0001), (0, 0))},
    ]
    labels = {(lab["k"], lab["t"]) for lab in fetch_basemap.build(elements)["labels"]}
    assert labels == {("suburb", "Surry Hills"), ("station", "Circular Quay"),
                      ("landmark", "Australian Museum"), ("park", "Big Park")}


def test_road_names_repeat_along_the_road_and_tunnels_are_left_out():
    # ten 300 m pieces of one street (OSM splits streets at intersections)
    pieces = [{"type": "way", "tags": {"highway": "primary", "name": "Long Road"},
               "geometry": _geom((0, i * 0.003), (0, (i + 1) * 0.003))} for i in range(10)]
    tunnel = {"type": "way", "tags": {"highway": "motorway", "tunnel": "yes", "name": "Deep Tunnel"},
              "geometry": _geom((1, 0), (1, 0.01))}
    layers = fetch_basemap.build(pieces + [tunnel])
    roads = [lab for lab in layers["labels"] if lab["k"] == "road"]
    assert {lab["t"] for lab in roads} == {"Long Road"}
    assert 2 <= len(roads) <= 4  # 3.3 km of road, a name every ~1 km or more
    assert len(layers["roads_major"]) == 10  # the tunnel isn't drawn either


def test_multipolygon_outer_ways_are_joined_into_one_ring():
    # A park outline split into three ways, one of them pointing the other way
    members = [
        {"type": "way", "role": "outer", "geometry": _geom((0, 0), (0, 1))},
        {"type": "way", "role": "outer", "geometry": _geom((1, 1), (0, 1))},
        {"type": "way", "role": "outer", "geometry": _geom((1, 1), (0, 0))},
        {"type": "way", "role": "inner", "geometry": _geom((0.2, 0.2), (0.3, 0.3))},
    ]
    layers = fetch_basemap.build([{"type": "relation", "tags": {"natural": "water"}, "members": members}])
    (ring,) = layers["water"]
    assert ring[0] == ring[-1]
    assert sorted(map(tuple, ring[:-1])) == [(0, 0), (0, 1), (1, 1)]
