# Decisions Log

Judgment calls made without stopping to ask, per the project brief. Newest first.

## 2026-08-19 — Use the Complete GTFS bundle, not the per-mode "For Realtime" subset

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
