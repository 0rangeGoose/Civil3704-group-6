# Source Code Directory

## Layout

```
src/
├── main.py                        # Entry point (CLI: --mode serve|headless)
├── file.env                       # Local env vars (TFNSW_API_KEY) - gitignored, don't commit real keys
└── pipeline/                      # Headless data engine (see docs/ARCHITECTURE.md)
    ├── config.py                  # CBD bbox, feed URLs, polling intervals, thresholds
    ├── ingestion/
    │   ├── static_gtfs.py         # Static GTFS: load, bbox-filter, SQLite cache
    │   └── realtime_gtfs.py       # GTFS-Realtime: concurrent multi-feed polling
    ├── normalise.py                # Join positions + delays + static route/shape; drop ghost trails
    ├── analytics.py                # Map matching (snap to shape), adherence scoring, rolling aggregates
    ├── publish.py                  # In-memory snapshot; HTTP GET /snapshot + WebSocket /ws
    └── run.py                      # Orchestration: run_headless() / run_with_server()
```

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Add your TfNSW Open Data Hub API key to `.env` (repo root) or `src/file.env`:
   ```
   TFNSW_API_KEY=your-key-here
   ```
3. Download static GTFS data into `data/gtfs/` (needs at least `routes.txt`,
   `stops.txt`, `trips.txt`; add `shapes.txt` too if you want map matching).
   Get it from the [TfNSW Open Data Hub](https://opendata.transport.nsw.gov.au/).

## Running

```
python src/main.py                    # serve mode (default): background ingestion +
                                       # live snapshot on http://localhost:8080
python src/main.py --mode headless    # no server - just polls and logs each cycle
python src/main.py --mode headless --max-cycles 3   # bounded run, useful for a quick check
```

While the server is running, open `http://localhost:8080/dashboard` for the
full dashboard (vehicle positions, congestion/demand layer toggle,
route adherence) - both poll `/snapshot` every five seconds and show a clear
connection error if the pipeline is unavailable.

For the projector: `http://localhost:8080/dashboard/map.html` is the map
alone, no header/metrics/table. Layer switching there is keyboard-only (no
visible buttons, so nothing sits on top of the projected image) - press
`1` for Congestion, `2` for Demand, `3` for Stop Crowding (BOAM peak
occupancy), `4` for Routes.

The map-drawing logic (model bounds, congestion blocks, vehicle markers) is
shared between both pages via `dashboard/map-core.js`, so a fix there only
has to be made once.

In `serve` mode, the projection renderer / dashboard consumes:
- `GET  http://localhost:8080/snapshot` - current state as JSON, poll on demand
- `WS   ws://localhost:8080/ws` - the same snapshot pushed ~2x/second
- `GET  http://localhost:8080/healthz` - liveness check

## Testing the pipeline without any hardware

The pipeline is intentionally decoupled from the renderer - `pipeline/run.py`'s
`run_headless()` mode needs no projector/model, only a valid API key (network
access to TfNSW). For fully offline tests (no key, no network), see
`tests/test_pipeline.py`, which feeds synthetic GTFS-Realtime messages through
`normalise()` / `analytics.py` / `publish.py` directly:

```
pytest tests/ -v
```

## Known gaps / next steps

- Only `buses` is enabled in `pipeline/config.py`'s `ACTIVE_MODES` for now.
  The other modes' URLs are filled in but some (sydneytrains, metro,
  lightrail vehicle positions/trip updates - moved to a `/v2/` endpoint in
  May 2025) are marked `UNVERIFIED` and need confirming against a logged-in
  TfNSW Open Data Hub session before being added to `ACTIVE_MODES`.
- `VehicleRecord.occupancy` reads the feed's own **standard** GTFS-RT
  `occupancy_status` field directly (checked live, 2026-10: 100% of buses,
  100% of metro, ~40% of Sydney Trains vehicles in one poll) - shown as an
  extra line in each vehicle's dashboard popup. An earlier version of this
  note said occupancy needed a custom TfNSW protobuf extension to decode;
  that was wrong - `google.transit.gtfs_realtime_pb2` already decodes it
  with no extra work, it just hadn't been read into `VehicleRecord` yet.
  It's also the MAIN signal in `analytics.vehicle_congestion_score` now
  (`config.CONGESTION_OCCUPANCY_WEIGHT = 0.5`, half the total weight) - a
  bus that's genuinely full is congestion that matters directly, where
  slowness/lateness/bunching can each have unrelated causes. The other
  three weights were simply halved to make room, which preserves their
  relative proportions exactly when occupancy is unavailable for a given
  vehicle (re-normalised, same as any other missing signal).
- `history.log_stop_occupancy()` / `query_stop_occupancy_peaks()` build
  this project's own **live-sourced** equivalent of the BOAM file below -
  every poll cycle, `pipeline.run`'s `on_update` checks each vehicle with
  an occupancy reading against `analytics.nearest_stop_id()` (reusing the
  same stop proximity check congestion scoring already does) and logs one
  row to `stop_occupancy_history` in `data/cache/history.sqlite` the
  moment a vehicle arrives at a stop - edge-triggered, so a bus dwelling
  there for several poll cycles logs once, not once per cycle. Unlike
  BOAM, this needs no day-after download and no portal login; it just
  takes time to accumulate (nothing is logged before the pipeline has
  been running). Not wired into the dashboard yet - it's a data source,
  queryable the same shape as `boam_stops.load_boam_stops()` but without
  needing TfNSW's file at all once enough history has built up.
- Population/demand data is wired in (`pipeline/ingestion/demand.py`) but
  optional - it needs `data/demand/travel_zones_2021.geojson` and
  `data/demand/tzp24_employment.csv` from the TfNSW Open Data Hub (free
  login) to activate; without them, Demand just shows "no data".
- Opal tap on/off importance (`pipeline/ingestion/opal_importance.py`) is
  disconnected from `run.py`/the dashboard for now - none of the three
  enrichment layers were showing genuinely real-time data, and this was
  the least live of the three (a single 2020 snapshot week, vs. BOAM's one
  real day and demand's structural job-density figures). The module and
  its tests (`tests/test_opal_importance.py`) are untouched; re-enabling it
  is just restoring the `opal_importance`/`opal_*` lines in `run.py`,
  `publish.py` (already has `set_opal_grid`/`opal_grid`/`/opal_grid`,
  never removed) and `dashboard/map-core.js`/`map.js`/`app.js`/`index.html`.
- Stop-level crowding is wired in (`pipeline/ingestion/boam_stops.py`) but
  optional too - it needs a `data/boam/BOAM_<YYYYMMDD>.txt` daily export
  (pipe-delimited) from TfNSW's BOAM (Bus Opal Assignment Model) dataset,
  also behind a free portal login; without one, Stop Crowding just shows
  "no data". Unlike Demand/Importance this is real per-stop data (exact
  stop lat/lon, no travel-zone aggregation), but it's a single day's
  snapshot, not a live feed, and "importance" here is a stop's PEAK
  occupancy bucket reached that day, not an average (see the module
  docstring for why - a plain average washes out to near-nothing).
- No history/trend storage yet (current design is in-memory, current-state only).

## Documentation

Architecture decisions and data flow live in `../docs/ARCHITECTURE.md`.
