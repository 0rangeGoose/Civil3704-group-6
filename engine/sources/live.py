"""Polls the Transport for NSW realtime endpoints for every configured mode/feed type.

One HTTP request per (feed_type, mode) pair, every poll_interval_seconds --
that's the publication cadence, so polling faster wastes quota for no gain.
A 404/403 on a single endpoint is logged once and that endpoint is disabled
for the rest of the run; it must not take down the other modes. 429/5xx get
per-endpoint exponential backoff so one flaky feed doesn't slow the others.
A failed connection (Wi-Fi dropped, DNS down) backs off at most
NETWORK_BACKOFF_CAP_SECONDS, so polling resumes soon after the network does.

With an ApiBudget (engine/sources/budget.py) the time between cycles stretches
outside peak hours to stay inside TfNSW's daily quota. With ``defer_to``, the
source sits idle while that heartbeat is fresh -- how the scheduled recorder
stands aside while the server is already polling.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from engine.config import Config
from engine.sources.base import RawFeed
from engine.sources.budget import ApiBudget, Heartbeat

logger = logging.getLogger("pulse_cbd.live_source")

_MAX_BACKOFF_SECONDS = 300
NETWORK_BACKOFF_CAP_SECONDS = 30


class LiveSource:
    def __init__(
        self,
        cfg: Config,
        budget: ApiBudget | None = None,
        heartbeat: Heartbeat | None = None,
        defer_to: Heartbeat | None = None,
    ):
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

        self._budget = budget
        self._heartbeat = heartbeat
        self._defer_to = defer_to
        self._requests_this_cycle = 0
        self._last_logged_interval: float | None = None

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
                if self._defer_to is not None and self._defer_to.is_fresh():
                    # the server is polling and recording: don't pay twice
                    await asyncio.sleep(self._poll_interval)
                    continue
                self._requests_this_cycle = 0
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
                if self._heartbeat is not None:
                    self._heartbeat.beat()
                interval = self._poll_interval
                if self._budget is not None:
                    self._budget.record(self._requests_this_cycle)
                    interval = self._budget.cycle_interval()
                    if self._last_logged_interval is None or abs(interval - self._last_logged_interval) >= 5:
                        logger.info("polling every %.0fs (%d requests used today of %d)",
                                    interval, self._budget.used_today(), self._budget.daily_limit)
                        self._last_logged_interval = interval
                elapsed = loop.time() - cycle_start
                await asyncio.sleep(max(0.0, interval - elapsed))

    async def _fetch_once(self, client: httpx.AsyncClient, key: str, path: str) -> bytes | None:
        loop = asyncio.get_event_loop()
        wait = self._last_request_at + self._min_request_interval - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_at = loop.time()
        self._requests_this_cycle += 1
        try:
            resp = await client.get(path)
        except httpx.HTTPError as exc:
            logger.warning("%s: request failed: %s", key, exc)
            self._schedule_backoff(key, cap=NETWORK_BACKOFF_CAP_SECONDS)
            return None

        if resp.status_code == 200:
            self._backoff.pop(key, None)
            return resp.content
        if resp.status_code in (404, 403):
            logger.error(
                "%s: %s %s -- disabling this endpoint for the rest of the run "
                "(this path may need correcting in config/config.yaml)",
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

    def _schedule_backoff(self, key: str, cap: float = _MAX_BACKOFF_SECONDS) -> None:
        current = self._backoff.get(key, self._poll_interval)
        next_wait = min(current * 2, cap)
        self._backoff[key] = next_wait
        self._next_allowed[key] = asyncio.get_event_loop().time() + next_wait
