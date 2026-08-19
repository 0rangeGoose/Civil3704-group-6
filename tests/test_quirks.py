from datetime import datetime, timezone

from engine.normalise.proto import pb
from engine.normalise.quirks import dedupe_ferry_entities


def _entity(entity_id: str, vehicle_id: str, lat: float, lon: float, timestamp: int) -> "pb.FeedEntity":
    e = pb.FeedEntity()
    e.id = entity_id
    e.vehicle.vehicle.id = vehicle_id
    e.vehicle.position.latitude = lat
    e.vehicle.position.longitude = lon
    e.vehicle.timestamp = timestamp
    return e


def test_dedupe_keeps_only_current_entity_per_vessel():
    fetched_at = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    now = int(fetched_at.timestamp())

    entities = [
        _entity("e1", "MV_VIVONNE", -33.86, 151.21, now - 10),  # current
        _entity("e2", "MV_VIVONNE", -33.85, 151.22, now + 600),  # ghost: future position
        _entity("e3", "MV_SUPPLY", -33.87, 151.20, now - 5),  # a different, unrelated vessel
    ]

    result = dedupe_ferry_entities(entities, fetched_at)

    assert len(result) == 2
    ids = {e.id for e in result}
    assert ids == {"e1", "e3"}


def test_dedupe_keeps_freshest_when_multiple_current_entries_exist():
    fetched_at = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    now = int(fetched_at.timestamp())

    entities = [
        _entity("stale", "MV_VIVONNE", -33.86, 151.21, now - 30),
        _entity("fresh", "MV_VIVONNE", -33.861, 151.211, now - 5),
    ]

    result = dedupe_ferry_entities(entities, fetched_at)

    assert len(result) == 1
    assert result[0].id == "fresh"


def test_dedupe_falls_back_to_entity_id_when_vehicle_id_missing():
    fetched_at = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    e = pb.FeedEntity()
    e.id = "e1"
    e.vehicle.position.latitude = -33.86
    e.vehicle.position.longitude = 151.21
    # no vehicle.vehicle.id and no timestamp set

    result = dedupe_ferry_entities([e], fetched_at)

    assert len(result) == 1
    assert result[0].id == "e1"
