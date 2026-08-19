"""Decode raw GTFS-Realtime protobuf payloads."""
from __future__ import annotations

from google.protobuf.message import DecodeError as _ProtobufDecodeError

from engine.normalise.proto import pb


class DecodeError(RuntimeError):
    pass


def parse_feed_message(raw: bytes) -> "pb.FeedMessage":
    feed = pb.FeedMessage()
    try:
        feed.ParseFromString(raw)
    except _ProtobufDecodeError as exc:
        raise DecodeError(str(exc)) from exc
    return feed
