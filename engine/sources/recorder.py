"""Wraps any Source and persists every raw payload it yields to disk.

Written early (Phase 2), not as an afterthought, per the brief: it's what
makes replay development possible without live feeds later. One
newline-delimited, gzip-compressed file per (mode, feed_type, hour).
"""
from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from typing import AsyncIterator

from engine.sources.base import RawFeed, Source


class RecordingSource:
    def __init__(self, inner: Source, out_dir: Path):
        self._inner = inner
        self._out_dir = out_dir

    async def stream(self) -> AsyncIterator[RawFeed]:
        async for raw in self._inner.stream():
            self._write(raw)
            yield raw

    def _write(self, raw: RawFeed) -> None:
        mode_dir = self._out_dir / raw.mode.replace("/", "_") / raw.feed_type
        mode_dir.mkdir(parents=True, exist_ok=True)
        hour_stamp = raw.fetched_at.strftime("%Y%m%dT%H")
        path = mode_dir / f"{hour_stamp}.ndjson.gz"
        record = {
            "fetched_at": raw.fetched_at.isoformat(),
            "payload_b64": base64.b64encode(raw.payload).decode("ascii"),
        }
        with gzip.open(path, "at") as f:
            f.write(json.dumps(record) + "\n")
