"""Polls the Transport for NSW realtime endpoints for every configured mode/feed type.

One HTTP request per (feed_type, mode) pair, every poll_interval_seconds --
that's the publication cadence, so polling faster wastes quota for no gain.
A 404/403 on a single endpoint is logged once and that endpoint is disabled
for the rest of the run; it must not take down the other modes. 429/5xx get
per-endpoint exponential backoff so one flaky feed doesn't slow the others.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from engine.config import Config
from engine.sources.base import RawFeed

logger = logging.getLogger("pulse_cbd.live_source")

_MAX_BACKOFF_SECONDS = 300


class LiveSource:
    def __init__(self, cfg: Config):
        if not cfg.api_key:
            raise RuntimeError(
                "TFNSW_API_KEY is not set; live polling needs it. See .env.example."
            )
        realtime_cfg = cfg.raw["realtime_gtfs"]
        self._base = realtime_cfg["base_host"]
        self._poll_interval = realtime_cfg["poll_interval_seconds"]
        self._min_request_interval = realtime_cfg.get("min_request_interval_seconds", 0.0)
        self._last_request_at = float("-inf")
        self._feeds = realtime_cfg["feeds"]
        self._headers = {"Authorization": f"apikey {cfg.api_key}"}

        self._backoff: dict[str, float] = {}
        self._next_allowed: dict[str, float] = {}
        self._disabled: set[str] = set()

    def _endpoints(self):
        for feed_type, modes in self._feeds.items():
            for mode, path in modes.items():
                yield feed_type, mode, path

    async def stream(self) -> AsyncIterator[RawFeed]:
        loop = asyncio.get_event_loop()
        async with httpx.AsyncClient(base_url=self._base, headers=self._headers, timeout=30) as client:
            while True:
                cycle_start = loop.time()
                for feed_type, mode, path in self._endpoints():
                    key = f"{feed_type}:{mode}"
                    if key in self._disabled:
                        continue
                    if loop.time() < self._next_allowed.get(key, 0):
                        continue
                    payload = await self._fetch_once(client, key, path)
                    if payload is not None:
                        yield RawFeed(
                            mode=mode,
                            feed_type=feed_type,
                            fetched_at=datetime.now(timezone.utc),
                            payload=payload,
                        )
                elapsed = loop.time() - cycle_start
                await asyncio.sleep(max(0.0, self._poll_interval - elapsed))

    async def _fetch_once(self, client: httpx.AsyncClient, key: str, path: str) -> bytes | None:
        loop = asyncio.get_event_loop()
        wait = self._last_request_at + self._min_request_interval - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_at = loop.time()
        try:
            resp = await client.get(path)
        except httpx.HTTPError as exc:
            logger.warning("%s: request failed: %s", key, exc)
            self._schedule_backoff(key)
            return None

        if resp.status_code == 200:
            self._backoff.pop(key, None)
            return resp.content
        if resp.status_code in (404, 403):
            logger.error(
                "%s: %s %s -- disabling this endpoint for the rest of the run "
                "(check docs/DECISIONS.md, this path may need correcting)",
                key, resp.status_code, path,
            )
            self._disabled.add(key)
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            logger.warning("%s: %s -- backing off", key, resp.status_code)
            self._schedule_backoff(key)
            return None
        logger.warning("%s: unexpected status %s", key, resp.status_code)
        return None

    def _schedule_backoff(self, key: str) -> None:
        current = self._backoff.get(key, self._poll_interval)
        next_wait = min(current * 2, _MAX_BACKOFF_SECONDS)
        self._backoff[key] = next_wait
        self._next_allowed[key] = asyncio.get_event_loop().time() + next_wait
