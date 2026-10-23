#!/usr/bin/env python3
"""Imports BOAM (Bus Opal Assignment Model) day files, keeping only the model
footprint, then optionally deletes the originals.

A BOAM day file is ~200-370MB: every bus trip at every stop in NSW, with
scheduled and actual arrival times, bus capacity and an occupancy band.
About 10% of rows are inside the footprint. Each day is reduced to
data/boam/BOAM_YYYYMMDD.csv.gz (a few MB) with only the columns
engine/demand/boam.py uses; the server builds its layers from those.

    python scripts/import_boam.py ~/Downloads/BOAM_2026*.txt
    python scripts/import_boam.py --delete-originals ~/Downloads/BOAM_2026*.txt

An original is only deleted after its extract has been written and read back
with the same row count.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import load_config
from engine.sources.footprint import padded_box

KEEP = [
    "SERVICE_DATE", "ROUTE", "ROUTE_VARIANT", "TRIP_ID", "DIRECTION", "TRANSIT_STOP_SEQUENCE",
    "SCHD_ARRIVE_TIME", "ACTUAL_ARRIVE_TIME", "TRANSIT_STOP", "LATITUDE", "LONGITUDE",
    "SEATED_CAPACITY", "TOTAL_CAPACITY", "OCCUPANCY_RANGE",
]
# A little padding, so the stop just outside the edge is kept and sections
# crossing into the footprint still have both ends.
PAD_RATIO = 0.03


def import_day(src: Path, out_dir: Path, box) -> tuple[Path, int, int]:
    match = re.search(r"(\d{8})", src.name)
    if not match:
        raise SystemExit(f"{src}: can't find a YYYYMMDD date in the file name")
    dst = out_dir / f"BOAM_{match.group(1)}.csv.gz"
    total = kept = 0
    with open(src, newline="", encoding="utf-8-sig") as f_in, gzip.open(dst, "wt", newline="") as f_out:
        reader = csv.DictReader(f_in, delimiter="|")
        missing = [c for c in KEEP if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{src}: missing expected columns {missing} -- has the BOAM format changed?")
        writer = csv.writer(f_out)
        writer.writerow(KEEP)
        for row in reader:
            total += 1
            try:
                lat, lon = float(row["LATITUDE"]), float(row["LONGITUDE"])
            except ValueError:
                continue
            if box.south <= lat <= box.north and box.west <= lon <= box.east:
                writer.writerow([row[c] for c in KEEP])
                kept += 1
    return dst, total, kept


def _count_rows(path: Path) -> int:
    with gzip.open(path, "rt", newline="") as f:
        return sum(1 for _ in f) - 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--delete-originals", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    out_dir = cfg.root / "data" / "boam"
    out_dir.mkdir(parents=True, exist_ok=True)
    box = padded_box(cfg.bounding_box, PAD_RATIO)
    for src in args.files:
        dst, total, kept = import_day(src, out_dir, box)
        size_mb = dst.stat().st_size / 1e6
        print(f"{src.name}: {total:,} rows, {kept:,} in footprint -> {dst.name} ({size_mb:.1f} MB)")
        if args.delete_originals:
            if _count_rows(dst) == kept:
                src.unlink()
                print(f"  deleted {src}")
            else:
                print(f"  NOT deleting {src}: extract row count doesn't match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
