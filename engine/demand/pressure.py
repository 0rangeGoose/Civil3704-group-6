"""The combined "pressure" layer: one 0-1 score per area from four inputs.

Inputs, each turned into a 0-1 percentile rank across the areas that have it:

- **congestion** -- mean live TfNSW traffic level of the street segments in the
  area (engine/normalise/segments.py, last 10 minutes);
- **crowding** -- mean live crowding level of those segments;
- **service_gap** -- the static jobs-vs-service gap (engine/demand/areas.py);
- **opal** -- Opal 2016 AM-peak taps (areas with none rank lowest);
- **boam_load** -- typical bus load (share of capacity) on the BOAM sections in
  the area, for the chosen peak (engine/demand/boam.py). Areas with no BOAM
  sections get the neutral rank, like the live inputs.

Jobs is deliberately not an input: it's already inside the service gap, and
counting it twice would tilt the score towards wherever offices are.

score = sum(weight * rank) / sum(weight). An area with no live vehicles right
now gets the neutral rank (NEUTRAL_RANK) for traffic and crowding, so every area
is averaged over the same inputs. (Dropping the missing inputs instead was
tried first: areas with no buses at that moment were then scored on two
static inputs alone and filled the top of the ranking, ahead of areas with
real readings.) Percentile ranks, not min-max scaling, so one extreme area
can't flatten every other.

Live inputs come from street segments; a segment counts towards the area its
midpoint falls in. Ranks are relative to the footprint at that moment, so the
layer answers "where is it worst right now", not "is it bad in absolute terms".
"""
from __future__ import annotations

from collections import defaultdict

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from engine.demand.areas import percentile_ranks

INPUTS = ("congestion", "crowding", "service_gap", "opal", "boam_load")
DEFAULT_WEIGHTS = {name: 1.0 for name in INPUTS}
LIVE_INPUTS = ("congestion", "crowding")
NEUTRAL_FILL_INPUTS = ("congestion", "crowding", "boam_load")
NEUTRAL_RANK = 0.5


def combine(ranks: dict[str, dict[str, float]], weights: dict[str, float]) -> dict[str, float]:
    """{input: {unit: rank}} -> {unit: score}, re-weighting per unit over the
    inputs that unit has."""
    totals: dict[str, float] = defaultdict(float)
    weight_sums: dict[str, float] = defaultdict(float)
    for name, by_unit in ranks.items():
        w = weights.get(name, 0.0)
        if w <= 0:
            continue
        for unit, rank in by_unit.items():
            totals[unit] += w * rank
            weight_sums[unit] += w
    return {u: round(totals[u] / weight_sums[u], 3) for u in totals if weight_sums[u] > 0}


def score_bins(scores: dict[str, float]) -> dict[str, int]:
    """0-4 by fifths of the scores themselves. Fixed 0.2-wide bands don't work
    here: averaging several ranks pulls almost every score towards 0.5, so
    the outer two colours would never appear."""
    return {u: min(4, int(r * 5)) for u, r in percentile_ranks(scores).items()}


class PressureModel:
    def __init__(self, areas: dict, boam: dict | None = None):
        self._units: dict[str, list[str]] = {}
        self._trees: dict[str, STRtree] = {}
        self._static: dict[str, dict[str, dict[str, float]]] = {}
        self._all_ids: dict[str, list[str]] = {}
        for unit_type in ("zones", "grid"):
            units = areas.get(unit_type, [])
            ids, polys = [], []
            for unit in units:
                for ring in unit["rings"]:
                    if len(ring) >= 3:
                        # (lon, lat) so shapely's x/y match the segment points below
                        polys.append(Polygon([(lon, lat) for lat, lon in ring]))
                        ids.append(unit["id"])
            self._units[unit_type] = ids
            self._all_ids[unit_type] = [u["id"] for u in units]
            self._trees[unit_type] = STRtree(polys) if polys else None
            self._static[unit_type] = {
                "service_gap": percentile_ranks({u["id"]: u["gap"] for u in units if u["gap"] is not None}),
                "opal": percentile_ranks({u["id"]: u["opal"] for u in units}),
            }
        self._segment_unit: dict[tuple[str, str], str | None] = {}
        # BOAM typical load per unit and period, ranked once: it's static.
        self._boam: dict[tuple[str, str], dict[str, float]] = {}
        for unit_type in ("zones", "grid"):
            for period in ("am", "pm"):
                loads: dict[str, list[float]] = defaultdict(list)
                for section in (boam or {}).get("sections", []):
                    value = (section.get(period) or {}).get("load")
                    unit = self._unit_of(unit_type, section) if value is not None else None
                    if unit is not None:
                        loads[unit].append(value)
                self._boam[(unit_type, period)] = percentile_ranks(
                    {u: sum(v) / len(v) for u, v in loads.items()})

    def _unit_of(self, unit_type: str, segment: dict) -> str | None:
        key = (unit_type, segment["id"])
        if key not in self._segment_unit:
            pts = segment["pts"]
            tree = self._trees.get(unit_type)
            unit = None
            if tree is not None and pts:
                lat, lon = pts[len(pts) // 2]
                hits = tree.query(Point(lon, lat), predicate="within")
                if len(hits):
                    unit = self._units[unit_type][int(hits[0])]
            self._segment_unit[key] = unit
        return self._segment_unit[key]

    def scores(self, unit_type: str, segments: list[dict], weights: dict[str, float],
               period: str = "am") -> dict[str, dict]:
        """{unit_id: {"score", "bin", "inputs": {input: rank}}} for every unit
        with at least one weighted input."""
        live: dict[str, dict[str, list[float]]] = {"congestion": defaultdict(list), "crowding": defaultdict(list)}
        for segment in segments:
            unit = self._unit_of(unit_type, segment)
            if unit is None:
                continue
            for name in live:
                if segment.get(name) is not None:
                    live[name][unit].append(segment[name])
        ranks = {
            name: percentile_ranks({u: sum(v) / len(v) for u, v in by_unit.items()})
            for name, by_unit in live.items()
        }
        ranks["boam_load"] = dict(self._boam.get((unit_type, period), {}))
        measured = {name: set(r) for name, r in ranks.items()}
        for name in NEUTRAL_FILL_INPUTS:
            for unit in self._all_ids.get(unit_type, []):
                ranks[name].setdefault(unit, NEUTRAL_RANK)
        ranks.update(self._static.get(unit_type, {}))
        combined = combine(ranks, weights)
        bins = score_bins(combined)
        return {
            unit: {
                "score": score,
                "bin": bins[unit],
                # only inputs actually measured for this area, so the legend
                # and report can tell real readings from the neutral fill
                "inputs": {name: round(r[unit], 2) for name, r in ranks.items()
                           if unit in r and (name not in measured or unit in measured[name])},
            }
            for unit, score in combined.items()
        }
