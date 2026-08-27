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
- Population/demand data (Travel Zone Projections) isn't wired in - static
  GTFS + realtime only, so far.
- No history/trend storage yet (current design is in-memory, current-state only).

## Documentation

Architecture decisions and data flow live in `../docs/ARCHITECTURE.md`.
