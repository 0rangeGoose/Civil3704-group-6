#!/usr/bin/env python3
"""Phase 2: realtime ingestion, headless.

Poll every configured mode/feed, decode protobuf, handle the documented
quirks, and emit normalised vehicle records -- to stdout (one JSON line per
poll cycle by default) or to a file with --out. Every raw payload is also
recorded to data/recordings/ regardless, so replay/synthetic sources (later)
have real fixtures to work from. No rendering here.

Requires TFNSW_API_KEY in .env, and the Phase 1 SQLite cache
(run scripts/run_phase1.py first).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import load_config
from engine.normalise.build import build_vehicle_records
from engine.normalise.static_lookup import StaticLookup
from engine.sources.live import LiveSource
from engine.sources.recorder import RecordingSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pulse_cbd.run_phase2")


async def run(max_cycles: int | None, out_path: Path | None) -> None:
    cfg = load_config()
    db_path = cfg.path("db_path")
    if not db_path.exists():
        print(f"error: {db_path} not found -- run scripts/run_phase1.py first", file=sys.stderr)
        raise SystemExit(1)

    recordings_dir = cfg.root / "data" / "recordings"
    source = RecordingSource(LiveSource(cfg), recordings_dir)

    out_file = open(out_path, "a") if out_path else None
    bbox = cfg.bounding_box
    cycles = 0
    feed_counts: dict[str, int] = {}
    vehicle_count_this_cycle = 0

    with StaticLookup(db_path) as lookup:
        try:
            async for raw in source.stream():
                feed_key = f"{raw.feed_type}:{raw.mode}"
                feed_counts[feed_key] = feed_counts.get(feed_key, 0) + 1

                records = build_vehicle_records(raw, bbox, lookup)
                for record in records:
                    line = json.dumps(record.to_json())
                    if out_file:
                        out_file.write(line + "\n")
                        out_file.flush()
                    else:
                        print(line)
                vehicle_count_this_cycle += len(records)

                if raw.feed_type == "vehicle_positions":
                    confidence_counts: dict[str, int] = {}
                    for r in records:
                        confidence_counts[r.confidence] = confidence_counts.get(r.confidence, 0) + 1
                    logger.info(
                        "%s: %d vehicles in box %s",
                        feed_key, len(records), confidence_counts or "{}",
                    )
                    cycles += 1
                    if max_cycles is not None and cycles >= max_cycles:
                        break
        finally:
            if out_file:
                out_file.close()

    print("\nPhase 2 summary", file=sys.stderr)
    print("-" * 40, file=sys.stderr)
    for key, count in sorted(feed_counts.items()):
        print(f"{key:45s} {count} polls", file=sys.stderr)
    print(f"recordings written under {recordings_dir}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cycles", type=int, default=None,
        help="Stop after this many vehicle_positions poll cycles across all modes "
        "(each mode counts separately). Omit to run forever.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Append normalised vehicle records here as JSON lines, instead of stdout.",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args.cycles, args.out))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
