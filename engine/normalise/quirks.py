"""Feed-specific quirk handling documented in the project brief.

- Ferry ghost trails: the Sydney Ferries vehicle-positions feed reports both
  current and future positions per vessel, producing a trail of phantom
  vessels along the route. ``dedupe_ferry_entities`` keeps only the freshest
  non-future entity per vehicle id.
- Bus trip-id mutation / light rail static lag: handled in
  ``static_lookup.py``'s fallback join, not here.
- Combined bus feed / regional operators: handled implicitly in
  ``build.py`` -- a vehicle whose route never appears in the Phase 1
  CBD-filtered static cache is only forwarded if its *position* is inside
  the bounding box (``StaticLookup.join`` returns "unmatched" but the
  vehicle isn't dropped); everything genuinely outside the box is dropped
  by the position filter regardless of which operator published it.
"""
from __future__ import annotations

from datetime import datetime


def dedupe_ferry_entities(entities: list, fetched_at: datetime) -> list:
    """Collapse multiple VehiclePosition entities per vessel to the one
    representing its current position.

    Groups by vehicle descriptor id (falling back to entity id if the feed
    omits one), drops any entity timestamped more than a few seconds after
    ``fetched_at`` (a future/scheduled position, not a current one), then
    keeps the most recently timestamped survivor in each group. The exact
    rule for "current" isn't documented anywhere public, so this is a
    judgment call.
    """
    fetched_epoch = fetched_at.timestamp()
    best: dict[str, tuple[int, object]] = {}
    for entity in entities:
        vp = entity.vehicle
        vehicle_id = vp.vehicle.id if vp.HasField("vehicle") and vp.vehicle.id else entity.id
        ts = vp.timestamp if vp.HasField("timestamp") else 0
        if ts and ts > fetched_epoch + 5:
            continue  # future/ghost position
        current = best.get(vehicle_id)
        if current is None or ts > current[0]:
            best[vehicle_id] = (ts, entity)
    return [entity for _, entity in best.values()]
