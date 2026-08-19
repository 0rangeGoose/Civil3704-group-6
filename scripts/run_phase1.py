#!/usr/bin/env python3
"""Phase 1: static foundation.

Fetch (or seed) the static GTFS bundle, filter it to the CBD bounding box,
build the local SQLite cache, and print a summary of what was retained.
No realtime, no rendering -- see docs/DECISIONS.md and the project brief.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import load_config
from engine.static.fetch import ensure_static_bundle, seed_from_local, StaticBundleError
from engine.static.filter import build_filtered_static_data
from engine.static.cache import build_cache


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed-dir",
        type=Path,
        default=None,
        help="Point the cache at an already-downloaded GTFS extract instead of "
        "hitting the Open Data Hub API (no TFNSW_API_KEY needed).",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore the cache freshness window and re-fetch from the API.",
    )
    args = parser.parse_args()

    cfg = load_config()

    if args.seed_dir:
        print(f"Seeding static bundle cache from {args.seed_dir} ...")
        bundle_dir = seed_from_local(cfg, args.seed_dir)
    else:
        try:
            bundle_dir = ensure_static_bundle(cfg, force_refresh=args.force_refresh)
        except StaticBundleError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(f"Static bundle ready at {bundle_dir}")

    bbox = cfg.bounding_box
    print(
        f"Filtering to bounding box "
        f"N={bbox.north} S={bbox.south} W={bbox.west} E={bbox.east} ..."
    )
    t0 = time.monotonic()
    filtered = build_filtered_static_data(bundle_dir, bbox)
    filter_elapsed = time.monotonic() - t0

    db_path = cfg.path("db_path")
    t1 = time.monotonic()
    counts = build_cache(db_path, bundle_dir, filtered)
    cache_elapsed = time.monotonic() - t1

    print()
    print("Phase 1 summary")
    print("-" * 40)
    print(f"agencies         {counts['agency']}")
    print(f"routes           {counts['routes']}")
    print(f"stops            {counts['stops']}")
    print(f"trips            {counts['trips']}")
    print(f"stop_times rows  {counts['stop_times']}")
    print(f"shapes           {len(filtered.shape_ids)} ({counts['shapes']} points)")
    print(f"calendar entries {counts['calendar']}")
    print(f"calendar_dates   {counts['calendar_dates']}")
    print("-" * 40)
    print(f"filter time  {filter_elapsed:.1f}s")
    print(f"cache write  {cache_elapsed:.1f}s")
    print(f"cache db     {db_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
