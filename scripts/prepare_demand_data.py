#!/usr/bin/env python3
"""One-off: cut the statewide boundary files down to the model footprint.

Inputs (not in git; see README "Demand layers"):
  data/demand/travel_zones_2021.geojson   TfNSW Travel Zones 2021 (~109MB, 4,236 zones)
  --poa-dir DIR                           optional: ABS Postal Areas 2021 shapefile, GDA2020
                                          (POA_2021_AUST_GDA2020_SHP.zip, unzipped)

Outputs (small, what the server loads at startup):
  data/demand/zones_footprint.geojson     travel zones intersecting the footprint
  data/demand/postcodes_footprint.geojson postal areas intersecting the footprint (only
                                          with --poa-dir; the Opal 2016 layer that used
                                          them was removed on 5 Nov 2026, so nothing
                                          reads this file now)

Zones keep each polygon whole rather than clipping it to the footprint: the
layers clip for drawing, but job counts belong to the whole area.

Needs pyshp for the shapefile (pip install pyshp); nothing else uses it.

    python scripts/prepare_demand_data.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import box, mapping, shape

from engine.config import load_config


def _footprint(cfg):
    bb = cfg.bounding_box
    return box(bb.west, bb.south, bb.east, bb.north)


def _write(path: Path, features: list[dict]) -> None:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    print(f"wrote {path} ({len(features)} features, {path.stat().st_size // 1024} KB)")


def prepare_zones(cfg, src: Path, dst: Path) -> None:
    footprint = _footprint(cfg)
    data = json.loads(src.read_text(encoding="utf-8"))
    keep = []
    for feature in data["features"]:
        geom = shape(feature["geometry"])
        if geom.intersects(footprint):
            props = feature["properties"]
            keep.append({
                "type": "Feature",
                "properties": {"tz21_code": int(props["tz21_code"]), "tz21_name": props["tz21_name"]},
                "geometry": mapping(geom),
            })
    _write(dst, keep)


def prepare_postcodes(cfg, poa_dir: Path, dst: Path) -> None:
    import shapefile  # pyshp

    footprint = _footprint(cfg)
    shp = next(poa_dir.glob("*.shp"))
    keep = []
    with shapefile.Reader(str(shp)) as reader:
        fields = [f[0] for f in reader.fields[1:]]
        for record in reader.iterShapeRecords():
            if not record.shape.points:
                continue
            geom = shape(record.shape.__geo_interface__)
            if geom.intersects(footprint):
                props = dict(zip(fields, record.record))
                keep.append({
                    "type": "Feature",
                    "properties": {"postcode": str(props["POA_CODE21"])},
                    "geometry": mapping(geom),
                })
    _write(dst, keep)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--poa-dir", type=Path, help="unzipped ABS POA 2021 GDA2020 shapefile")
    args = parser.parse_args()
    cfg = load_config()
    demand_dir = cfg.root / "data" / "demand"
    zones_src = demand_dir / "travel_zones_2021.geojson"
    if zones_src.exists():
        prepare_zones(cfg, zones_src, demand_dir / "zones_footprint.geojson")
    else:
        print(f"skipping zones: {zones_src} not found")
    if args.poa_dir:
        prepare_postcodes(cfg, args.poa_dir, demand_dir / "postcodes_footprint.geojson")
    return 0


if __name__ == "__main__":
    sys.exit(main())
