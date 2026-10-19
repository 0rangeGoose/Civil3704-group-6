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
`1` for Congestion, `2` for Demand, `3` for Importance (Opal tap on/off),
`4` for Routes.

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
- The TfNSW custom protobuf extension (occupancy / accessibility beyond
  standard GTFS-RT) isn't decoded yet - `VehicleRecord` has no occupancy
  field. Needs TfNSW's extension `.proto` definitions.
- Population/demand data is wired in (`pipeline/ingestion/demand.py`) but
  optional - it needs `data/demand/travel_zones_2021.geojson` and
  `data/demand/tzp24_employment.csv` from the TfNSW Open Data Hub (free
  login) to activate; without them, Demand just shows "no data".
- Opal tap on/off importance is wired in (`pipeline/ingestion/opal_importance.py`)
  but optional too - it needs `data/demand/travel_zones_2021.geojson` (same
  file as above) and `data/opal/origin_destination_<week>.csv` (the "origin
  and destination by travel zone" file from TfNSW's Opal Tap On and Tap Off
  Release 3 (2020) dataset, also behind a free portal login) to activate;
  without them, Importance just shows "no data". Only one week is loaded by
  default (`ORIGIN_DESTINATION_PATH`); it's a snapshot from four
  non-consecutive weeks in 2020, not a live feed.
- No history/trend storage yet (current design is in-memory, current-state only).

## Documentation

Architecture decisions and data flow live in `../docs/ARCHITECTURE.md`.
