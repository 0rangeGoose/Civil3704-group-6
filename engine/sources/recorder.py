"""Wraps any Source and persists every raw payload it yields to disk.

Written early (Phase 2), not as an afterthought, per the brief: it's what
makes replay development possible without live feeds later. One
newline-delimited, gzip-compressed file per (mode, feed_type, hour, writer).

With a FootprintFilter, only what can appear on the model is written (see
engine/sources/footprint.py); the unfiltered feed is still what's yielded to
the live engine. ``tag`` goes into the filename so the server and the
scheduled peak recorder never append to the same file at once.
"""
from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import AsyncIterator

from engine.sources.base import RawFeed, Source
from engine.sources.footprint import FootprintFilter


class RecordingSource:
    def __init__(
        self,
        inner: Source,
        out_dir: Path,
        footprint: FootprintFilter | None = None,
        tag: str | None = None,
    ):
        self._inner = inner
        self._out_dir = out_dir
        self._footprint = footprint
        self._tag = tag

    async def stream(self) -> AsyncIterator[RawFeed]:
        async for raw in self._inner.stream():
            to_write = self._footprint.apply(raw) if self._footprint else raw
            if to_write is not None:
                self._write(to_write)
            yield raw

    def _write(self, raw: RawFeed) -> None:
        mode_dir = self._out_dir / raw.mode.replace("/", "_") / raw.feed_type
        mode_dir.mkdir(parents=True, exist_ok=True)
        hour_stamp = raw.fetched_at.strftime("%Y%m%dT%H")
        name = f"{hour_stamp}.{self._tag}.ndjson.gz" if self._tag else f"{hour_stamp}.ndjson.gz"
        record = {
            "fetched_at": raw.fetched_at.isoformat(),
            "payload_b64": base64.b64encode(raw.payload).decode("ascii"),
        }
        with gzip.open(mode_dir / name, "at", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
