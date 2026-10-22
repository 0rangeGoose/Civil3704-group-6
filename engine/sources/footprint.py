"""Trims raw feeds down to the model footprint before they are recorded.

TfNSW's feeds are statewide: recording them as-is cost ~14GB in a few hours
of development. Replay only ever needs what can appear on the model, so:

- vehicle_positions keep only entities inside a padded footprint (the padding
  gives a vehicle some history before it enters frame);
- trip_updates keep only trips that either belong to a vehicle recently seen
  inside that padded footprint, or stop at a stop in the static cache (which
  is filtered to the same footprint) -- the second rule covers Sydney Trains
  and Metro, whose realtime trip ids don't match the static bundle;
- service_alerts pass through untouched (they're small, and the engine
  already filters them by route/stop on the way in).

The filtered payload is re-serialised as a normal FeedMessage, so replay
decodes it exactly like a live one.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from engine.config import BoundingBox
from engine.normalise.decode import DecodeError, parse_feed_message
from engine.normalise.proto import pb
from engine.sources.base import RawFeed

logger = logging.getLogger("pulse_cbd.footprint")


def padded_box(bbox: BoundingBox, pad_ratio: float) -> BoundingBox:
    lat_pad = (bbox.north - bbox.south) * pad_ratio
    lon_pad = (bbox.east - bbox.west) * pad_ratio
    return BoundingBox(
        north=bbox.north + lat_pad,
        south=bbox.south - lat_pad,
        west=bbox.west - lon_pad,
        east=bbox.east + lon_pad,
    )


class FootprintFilter:
    def __init__(self, bbox: BoundingBox, stop_ids: set[str], pad_ratio: float = 0.1):
        self._box = padded_box(bbox, pad_ratio)
        self._stop_ids = stop_ids
        # mode -> trip ids from the latest and previous vehicle_positions poll,
        # so a trip update arriving just before its vehicle's next position
        # isn't dropped.
        self._recent_trips: dict[str, tuple[set[str], set[str]]] = {}

    def apply(self, raw: RawFeed) -> RawFeed | None:
        """The trimmed feed, or None if nothing in it is worth keeping."""
        if raw.feed_type not in ("vehicle_positions", "trip_updates"):
            return raw
        try:
            feed = parse_feed_message(raw.payload)
        except DecodeError as exc:
            logger.warning("%s/%s: can't decode, recording unfiltered: %s", raw.feed_type, raw.mode, exc)
            return raw

        if raw.feed_type == "vehicle_positions":
            keep = [e for e in feed.entity if self._vehicle_in_box(e)]
            trips = {e.vehicle.trip.trip_id for e in keep if e.vehicle.trip.trip_id}
            latest, _ = self._recent_trips.get(raw.mode, (set(), set()))
            self._recent_trips[raw.mode] = (trips, latest)
        else:
            latest, previous = self._recent_trips.get(raw.mode, (set(), set()))
            recent = latest | previous
            keep = [e for e in feed.entity if self._trip_relevant(e, recent)]

        if not keep:
            return None
        trimmed = pb.FeedMessage()
        trimmed.header.CopyFrom(feed.header)
        trimmed.entity.extend(keep)
        return replace(raw, payload=trimmed.SerializeToString())

    def _vehicle_in_box(self, entity) -> bool:
        if not entity.HasField("vehicle") or not entity.vehicle.HasField("position"):
            return False
        pos = entity.vehicle.position
        b = self._box
        return b.south <= pos.latitude <= b.north and b.west <= pos.longitude <= b.east

    def _trip_relevant(self, entity, recent_trips: set[str]) -> bool:
        if not entity.HasField("trip_update"):
            return False
        tu = entity.trip_update
        if tu.trip.trip_id and tu.trip.trip_id in recent_trips:
            return True
        return any(stu.stop_id in self._stop_ids for stu in tu.stop_time_update if stu.stop_id)
