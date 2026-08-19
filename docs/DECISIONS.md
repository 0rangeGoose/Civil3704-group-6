# Decisions Log

Judgment calls made without stopping to ask, per the project brief. Newest first.

## 2026-08-19 — Geometric map matching resolved the Phase 2 route-id mismatch, as hoped

Phase 2's decision log flagged that sydneytrains/metro/lightrail_cbdandsoutheast
realtime feeds use route_ids with no relationship to the static GTFS bundle, and
guessed Phase 3's geometric map matching might make that a non-issue rather than
something to special-case. Confirmed live: `enrich.py`'s nearest-shape fallback
resolves real route short names purely from position for every one of those
vehicles (`sydneytrains 30/30`, `metro 4/4`, `lightrail_cbdandsoutheast 14/14` all
went from confidence "unmatched" to "geometric_match" with a correct route_short_name
in a live test run). Also confirmed live trip_id *does* still correlate between a
mode's vehicle_positions and trip_updates feeds even when neither matches the static
bundle -- sydneytrains adherence classified correctly (73-77% on_time across two live
runs) despite the route-id mismatch, because schedule deviation only needs matching
trip_ids across the two realtime feeds, not against static.

Also confirmed the active-alerts count against the CBD static route/stop id sets
works correctly (56 matching alerts found directly, 3 in a bounded 2-pass live run)
-- an earlier single-pass test run showed 0 only because `--cycles` cut the poll
loop off before it reached the service_alerts endpoints that pass, not a matching bug.

## 2026-08-19 — Bus/ferry adherence thresholds and the late/very_late split

The brief gives on-time thresholds for suburban rail (5 min), intercity (6 min), and
metro/light rail (2 min), but not buses or ferries. Set both to 300s (5 min), matching
suburban_rail, since buses in particular share similar real-world schedule slack and
the brief doesn't suggest tightening it. It also doesn't define where "late" ends and
"very_late" begins -- added `very_late_multiplier: 2` (so very_late starts at 2x the
mode's on-time threshold) as a simple, tunable default. Both live in
`config/config.yaml` under `adherence_thresholds_seconds` so they're one-line changes,
not a code change, if they turn out wrong once real running/cancelled data is seen.

## 2026-08-19 — New quirk found live, not in the original brief: sydneytrains/metro/CBD
light rail route_id and trip_id don't join to static GTFS at all

Live-tested `scripts/run_phase2.py` against the real feeds with a real API key (all 7
vehicle_positions modes, one full poll). Confidence breakdown by mode:

```
sydneytrains              32/32 unmatched
metro                       4/4 unmatched
lightrail_cbdandsoutheast 12/12 unmatched
lightrail_innerwest         5/5 static_join   (trip_id matches directly)
buses                    130/132 static_join  (2 unmatched -- plausible edge cases)
ferries                    10/10 degraded_route_fallback  (route_id matches, trip_id never does -- the documented quirk)
nswtrains                    0/0 (no vehicles in the box this poll)
```

sydneytrains/metro/CBD-southeast-light-rail realtime `route_id` values (`RTTA_DEF`,
`NSN_2k`, `1001_L2`, `1001_L3`, ...) don't resemble the static bundle's `route_id`
scheme (`2-T1-N-sj2-1`, `78-L2-sj2-1`) at all -- not a staleness issue, a different ID
namespace entirely, apparently sourced from a different backend system than the
published GTFS static schedule. Inner West light rail and buses, by contrast, join
cleanly. This isn't documented anywhere in the brief's "known feed quirks" section.

Not fixing this in Phase 2 -- the confidence flag exists precisely to represent this
kind of gap honestly rather than paper over it, and `build_vehicle_records` already
forwards these vehicles with correct positions and raw ids rather than dropping them.
Flagging for Phase 3: string-based route_id joining won't work for these three modes,
but Phase 3's actual map-matching (snapping position to a shape) may resolve the route
geometrically regardless of what the feed calls it, which would make this a non-issue
rather than something to special-case.

## 2026-08-19 — Ferry ghost-trail dedup rule: freshest non-future entity per vessel

The brief documents the ferry ghost-trail quirk (current + future positions both
reported for the same vessel, producing a phantom trail) and says to "filter to the
entity matching the vessel's current active trip," but doesn't specify how to identify
which entity that is. `engine/normalise/quirks.py::dedupe_ferry_entities` groups
entities by vehicle id, discards any timestamped more than 5 seconds past the poll's
fetch time (treated as a future/scheduled position), and keeps the most recently
timestamped survivor per vessel. Revisit if live data shows this heuristic picking the
wrong entity -- e.g. if "future" entries turn out to share the same timestamp as the
current one, or if vehicle id isn't actually stable across a vessel's entities.

## 2026-08-19 — Realtime endpoint matrix: confirmed live, all 20 paths correct

Checked opendata.transport.nsw.gov.au dataset/resource pages plus web search first
(sydneytrains/metro/lightrail-innerwest on v2 for trip_updates and vehicle_positions,
everything else v1, alerts fully v2 with a combined `/all`), but several resource
pages sit behind a login wall so that alone wasn't conclusive.

Once `TFNSW_API_KEY` was available, probed all 20 configured (feed_type, mode) paths
directly with the real key: every single one returned 200 (a few returned 429 on the
first pass only because the probe script fired all 20 requests back-to-back with no
spacing -- confirmed by re-checking, not a wrong path). No corrections needed to
`config/config.yaml`'s `feeds` matrix. `engine/sources/live.py` still logs-and-skips
on 404/403 rather than crashing, per the brief, in case an operator changes a path
later.

## 2026-08-19 — Compile TfNSW's 1007-extension proto standalone, skip `gtfs-realtime-bindings`

The brief's suggested architecture says to use the `gtfs-realtime-bindings` package for
the base GTFS-realtime messages and compile the TfNSW extension proto separately for
the custom field 1007. But the fetched extension file
(`engine/normalise/proto/gtfs-realtime_1007_extension.proto`, from
opendata.transport.nsw.gov.au's vehicle-positions-v2 dataset) doesn't `import` Google's
official `gtfs-realtime.proto` and extend it -- it redefines every base message
(FeedMessage, TripUpdate, VehiclePosition, Alert, ...) inline in the same file, with the
TfNSW extensions (`consist`, `tfnsw_vehicle_descriptor`, `track_direction`,
`carriage_seq_predictive_occupancy`, `update`) added via `extend` at field 1007 against
those locally-defined messages.

Mixing that with separately-compiled classes from `gtfs-realtime-bindings` risks two
independently-compiled definitions of the same messages disagreeing. Compiling this one
file with `grpc_tools.protoc` instead gives a single `gtfs_realtime_1007_extension_pb2`
module that decodes both the standard fields and the TfNSW extensions
(`entity.vehicle.Extensions[pb.consist]` etc.), with proto2 `optional`/extension
semantics handling absence gracefully on operators that don't populate them. Dropped
the `gtfs-realtime-bindings` dependency accordingly.

The brief prefers the smaller "For Realtime" subset when sufficient. That subset is
served per-mode at `https://api.transport.nsw.gov.au/v1/gtfs/schedule/{mode}`
(sydneytrains, buses, ferries, lightrail, nswtrains, regionbuses, metro) with no
combined archive — using it would mean fetching and merging seven separate zips.

The Complete bundle (`https://api.transport.nsw.gov.au/v1/publictransport/timetables/complete/gtfs`)
is one request, and Phase 1's bounding-box filter already exists specifically to cut
it down to CBD size, which addresses the "heavy" concern the brief was guarding
against. A copy of the Complete bundle was also already sitting locally
(`~/Downloads/full_greater_sydney_gtfs_static_0`), making it the path of least
friction to bootstrap Phase 1 without waiting on a fresh network fetch.

Revisit if the filtered SQLite cache ends up unexpectedly large, or if Phase 2's
realtime join logic turns out to want the per-mode subset's tighter trip-id
matching.

## 2026-08-19 — SQLite over Parquet for the filtered cache

The brief allows either. SQLite needs no extra dependency (stdlib `sqlite3`),
supports indexed lookups the normalise/map-matching layer will want in Phase 3
(e.g. "stop_times for trip X"), and keeps the whole filtered CBD dataset in one
file under `data/static_cache/`.

## 2026-08-19 — Static fetch endpoint verified against current docs

`config/config.yaml` originally guessed `v1/gtfs/schedule` as the Complete bundle
URL. Checked against opendata.transport.nsw.gov.au before writing the fetcher:
Complete bundle is `v1/publictransport/timetables/complete/gtfs`; `v1/gtfs/schedule/{mode}`
is the per-mode "For Realtime" subset instead. Corrected before any code depended
on the wrong path.
