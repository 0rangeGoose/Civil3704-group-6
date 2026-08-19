from pathlib import Path

from engine.normalise.static_lookup import StaticLookup


def test_exact_trip_match_is_static_join(static_db: Path):
    with StaticLookup(static_db) as lookup:
        result = lookup.join(trip_id="T1", route_id=None)
    assert result.confidence == "static_join"
    assert result.route.route_short_name == "333"
    assert result.headsign == "City Circular"


def test_mutated_trip_id_falls_back_to_route(static_db: Path):
    """The documented quirk: a bus trip_id changes under trip alteration and
    no longer joins to the static bundle, but route_id is still valid."""
    with StaticLookup(static_db) as lookup:
        result = lookup.join(trip_id="T1-ALTERED-9999", route_id="R1")
    assert result.confidence == "degraded_route_fallback"
    assert result.route.route_short_name == "333"
    assert result.headsign is None  # no per-trip data available in fallback


def test_unresolvable_trip_and_route_is_unmatched(static_db: Path):
    """E.g. light rail static data lagging behind realtime: the trip is
    genuinely absent from static, not just mutated."""
    with StaticLookup(static_db) as lookup:
        result = lookup.join(trip_id="GHOST-TRIP", route_id="GHOST-ROUTE")
    assert result.confidence == "unmatched"
    assert result.route is None
    assert result.headsign is None


def test_missing_ids_are_unmatched(static_db: Path):
    with StaticLookup(static_db) as lookup:
        result = lookup.join(trip_id=None, route_id=None)
    assert result.confidence == "unmatched"
