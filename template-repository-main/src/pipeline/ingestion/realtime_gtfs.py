"""GTFS-Realtime ingestion: vehicle positions, trip updates and service alerts.

Feeds for every active mode are polled concurrently (one thread per feed)
so that adding more modes doesn't multiply the wall-clock time per cycle.
Each feed fails independently - one dead endpoint (e.g. an unverified v2
URL, see config.py) logs an error but doesn't stop the others from updating.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import requests
from google.transit import gtfs_realtime_pb2

from .. import config

logger = logging.getLogger(__name__)

FEED_TYPES = ("vehicle_positions", "trip_updates", "alerts")


@dataclass
class RawFeeds:
    """Decoded protobuf feed messages, keyed by feed type then mode."""

    vehicle_positions: Dict[str, gtfs_realtime_pb2.FeedMessage] = field(default_factory=dict)
    trip_updates: Dict[str, gtfs_realtime_pb2.FeedMessage] = field(default_factory=dict)
    alerts: Dict[str, gtfs_realtime_pb2.FeedMessage] = field(default_factory=dict)
    fetched_at: float = field(default_factory=time.time)

    def by_type(self, feed_type: str) -> Dict[str, gtfs_realtime_pb2.FeedMessage]:
        return getattr(self, feed_type)


def download_feed(url: str, api_key: str, timeout: int = 30) -> gtfs_realtime_pb2.FeedMessage:
    """Download and decode a single GTFS-Realtime feed."""
    response = requests.get(
        url,
        headers={"Authorization": f"apikey {api_key}"},
        timeout=timeout,
    )
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)
    return feed


def poll_all(api_key: str, modes: Optional[list] = None) -> RawFeeds:
    """Fetch every (feed_type, mode) combination for the active modes, concurrently."""
    modes = modes or config.ACTIVE_MODES
    jobs = [
        (feed_type, mode, config.REALTIME_FEEDS[mode][feed_type])
        for mode in modes
        for feed_type in FEED_TYPES
        if mode in config.REALTIME_FEEDS and feed_type in config.REALTIME_FEEDS[mode]
    ]

    results = RawFeeds()
    if not jobs:
        return results

    def fetch_one(job):
        feed_type, mode, url = job
        try:
            return feed_type, mode, download_feed(url, api_key)
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else "?"
            logger.error(f"{mode}/{feed_type}: HTTP {status} from {url}")
        except requests.RequestException as error:
            logger.error(f"{mode}/{feed_type}: request failed ({error})")
        except Exception as error:  # decode errors, etc.
            logger.error(f"{mode}/{feed_type}: could not parse feed ({error})")
        return feed_type, mode, None

    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        for feed_type, mode, feed in pool.map(fetch_one, jobs):
            if feed is not None:
                results.by_type(feed_type)[mode] = feed

    return results


def poll_loop(
    api_key: str,
    on_update: Callable[[RawFeeds], None],
    modes: Optional[list] = None,
    interval_seconds: Optional[int] = None,
) -> None:
    """Poll forever, calling `on_update` with each batch of raw feeds.

    Runs until interrupted (KeyboardInterrupt propagates to the caller).
    """
    interval = interval_seconds or config.POLL_INTERVAL_SECONDS["vehicle_positions"]
    while True:
        start = time.time()
        raw = poll_all(api_key, modes)
        on_update(raw)

        elapsed = time.time() - start
        time.sleep(max(0.0, interval - elapsed))
