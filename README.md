# Inner Sydney Transit Projection Model

*(Formerly Pulse CBD; renamed 5 Nov 2026. The folder, code and service names still say `pulse-cbd`.)*

A real-time, multimodal transit "heartbeat" for Sydney's CBD. It ingests live train,
metro, light rail, ferry and bus data from Transport for NSW, tracks every vehicle
currently moving through the CBD, and projects them onto a physical scale model of the
CBD as arrows moving along the model's own streets and rail corridors. It also runs on
any normal screen. Every live frame is recorded, so past peaks can be replayed and each
stop builds up a history of how late and how full vehicles arrive.

```
python scripts/run_publish.py
```

...starts the whole thing: live data ingestion, map matching, schedule-adherence
scoring, recording, stop history and a browser-based renderer, all in one command.
First-time setup (Python environment, API key, timetable cache) is under
[Running it](#running-it).

---

## What it actually looks like

Open `http://127.0.0.1:8765/` once the server is running: a black background with
every bus, train, metro, light rail vehicle and ferry currently in the CBD shown as an
arrow pointing the way it's travelling, coloured by one of three views (schedule,
traffic or crowding), sized by mode (a train reads bigger than a bus), with a short tail
showing its last 30 seconds of travel and dim route corridors underneath so an empty
street reads as empty rather than as a gap. Hovering over a vehicle on the laptop shows
its exact lateness and highlights its route; hovering over a stop lists and highlights the
routes serving it.

Open `http://<laptop address>:8765/control` on the tablet next to the model. The
projector mirrors the laptop screen, so the projected view shows map content only (no
buttons, hints or cursor), and everything is controlled from the tablet instead:

- **Display** (for visitors): which vehicle types are shown, the colour view, area
  layers, stop markers and stop history, singling out routes, live or replayed
  time, and a legend that states the cut-off for every colour.
- **Team** (open to anyone; the PIN was removed on 5 Nov 2026): projection theme,
  verification overlay, an on-projection status line, alignment (nudge, corner fit,
  crosshair calibration), and system health (vehicles per mode, feed
  age, API usage).
- **Stats**: the share of trips on time by mode (each trip once, at TfNSW's own measuring points), active service alerts, and the busiest
  interchanges (this used to be the separate `/stats` page, which now redirects here).

The server prints the tablet URL on startup. Use the laptop's own hotspot for the
tablet: the server listens on every interface, and venue/university Wi-Fi often
blocks devices from reaching each other anyway.

## Why it's built the way it is

The original design brief called for a physical installation: a laser-cut/3D-printed
scale model of the CBD on a table, a projector mounted above it pointing straight
down, and the browser renderer's output projected onto the model so vehicles appear
to move along the model's actual streets and rail corridors in real time.

That is how it runs at the showcase: the model and projector are set up, and the
projection is aligned to the model with the calibration tools described under
[Calibrating onto the physical model](#calibrating-onto-the-physical-model). Without a
saved calibration (for example on a laptop screen), the renderer falls back to a simple
aspect-correct fit of the bounding box, so everything also works on any normal screen.

---

## Architecture: two layers, deliberately separated

**The data engine** ingests Transport for NSW's GTFS and GTFS-Realtime feeds,
normalises them, and publishes a clean stream of positioned vehicles with
schedule-adherence state. It has no idea a projector exists.

**The projection renderer** consumes that stream and draws it. With a saved
calibration it maps coordinates onto projector pixels through a homography, with the
nudge and corner fit on top; without one, it uses a plain aspect-correct fit of the
bounding box into the browser window. The drawing code is the same either way: only the
object that turns latitude/longitude into pixels changes.

The data engine runs completely headlessly and can be (and was, throughout
development) verified with no renderer attached at all.

### End-to-end data flow

```
Transport for NSW Open Data Hub
         │
         ├─ Static GTFS (daily)              ├─ GTFS-Realtime (every 15s)
         │  routes/stops/trips/shapes/        │  vehicle positions, trip updates,
         │  stop_times, filtered to CBD bbox  │  service alerts — 7 modes x 3 feeds
         ▼                                    ▼
   SQLite cache                          decode protobuf (incl. TfNSW's
   (data/static_cache/                    custom field-1007 extension)
    gtfs_cbd.db)                                │
         │                                       ▼
         │                              handle feed quirks (ferry ghost
         │                              trails, bus trip-id mutation, ...)
         │                                       │
         └───────────────┬───────────────────────┘
                          ▼
              join realtime → static (trip_id, or route_id
              fallback, or geometric nearest-shape fallback)
                          │
                          ▼
              map-match position onto route shape +
              compute schedule deviation → adherence state
                          │
                          ▼
              in-memory live snapshot (engine/publish/feed.py)
                          │
              ┌───────────┴────────────┐
              ▼                        ▼
      WebSocket (~2Hz)           HTTP (see API reference below)
      vehicle frames +           shapes, stops, stop history,
      shared control state       area layers, replays, exports
              │                        │
              ▼                        ▼
      renderer/index.html      renderer/controller.html
      (the projected model view) (the tablet: Display / Team / Stats)
```

---

## What gets computed, per vehicle

For every vehicle currently inside the CBD bounding box:

| Field | What it is |
|---|---|
| `vehicle_key` | stable identity (`mode:vehicle_id`) |
| `mode`, `route_short_name`, `route_color`, `headsign` | joined from the static GTFS cache |
| `lat` / `lon`, `bearing`, `position_timestamp` | raw reported position |
| `matched_lat` / `matched_lon`, `distance_along_shape_m` | position snapped onto its route's actual shape — raw GPS wanders off the road/rail corridor, this fixes that |
| `schedule_deviation_seconds` | delay in seconds, computed against the *next scheduled stop* from Trip Updates (not interpolated position) |
| `adherence_state` | one of `early`, `on_time`, `late`, `very_late` (missed its slot), `cancelled`, `unknown`; limits per mode from TfNSW's published standards (see `config/config.yaml`, `punctuality`) |
| `confidence` | `static_join`, `degraded_route_fallback`, `geometric_match`, or `unmatched` — see below |

### The `confidence` field, and why it exists

Not every realtime vehicle joins cleanly to the static schedule. The confidence field
says exactly how it was identified, rather than silently guessing:

- **`static_join`** — the realtime `trip_id` matched a trip in the static GTFS bundle directly. Ideal case.
- **`degraded_route_fallback`** — `trip_id` didn't match (e.g. a bus whose trip was altered, a known GTFS quirk), but `route_id` did.
- **`geometric_match`** — neither id matched, but the vehicle's *position* was close enough to a known route shape to identify it that way instead. This is what rescues Sydney Trains, Metro, and CBD/South-East Light Rail: their realtime feeds use a completely different id scheme than the static bundle (`RTTA_DEF`, `1001_L2`, ...) — discovered live during development. Geometric matching sidesteps the mismatch entirely.
- **`unmatched`** — couldn't identify the route at all. The vehicle is still shown (position is always real), just without route metadata.

### The aggregate layer

On top of individual vehicles, the engine maintains:

- **Trip punctuality** — % of trips on time by mode over the last 15 min, 1 h and 2 h, each trip counted once where TfNSW measures it: buses leaving their first stop, other modes at their final stop (predicted).
- **Active service alerts** — count and severity of alerts that intersect the CBD's routes/stops, out of everything TfNSW publishes statewide.
- **Interchange activity** — for 9 named interchanges (Central, Town Hall, Wynyard, Martin Place, Circular Quay, Barangaroo, Gadigal, Museum, St James), how many *distinct* vehicles have passed within 300m in the last 5 minutes. This feeds the busiest-interchanges bars on the tablet's Stats tab (the pulsing rings on the projection were removed on 5 Nov 2026).

## Documented feed quirks, and how they're handled

Real, TfNSW-specific data quirks the engine has to tolerate rather than choke on:

- **Ferry ghost trails** — the Sydney Ferries feed reports both a vessel's current position and future/scheduled ones in the same poll. Deduplicated to the freshest non-future entity per vessel.
- **Bus trip-id mutation** — when a bus route is altered, its trip_id changes and stops matching static data. Handled by the route-level fallback join above.
- **Buses arrive as one combined statewide feed** — filtered down to the CBD by the bounding-box position check plus the static join (irrelevant regional operators just don't match).
- **Light rail / newer-mode static lag** — a trip can appear in realtime before the static bundle catches up. Handled gracefully via the `unmatched`/`geometric_match` fallback rather than erroring.
- **Occupancy data is inconsistent across modes** — treated as optional metadata everywhere, never required.
- **(Discovered during build, not in the original spec)** Sydney Trains, Metro, and CBD/South-East Light Rail realtime feeds use route ids from a different namespace than the static bundle entirely — see `confidence: geometric_match` above.

## Demand layers

Static area layers, chosen on the tablet (Display → Colour the areas by), on either
TfNSW travel zones or a 150 m grid (Area shape) so you can compare them on the model:
**jobs** (TZP24, 2026) and **service gap** (jobs vs scheduled departures within 200 m,
7–9am on a weekday), each shown on its own. (An Opal 2016 tap layer and a combined
"pressure" score were removed on 5 Nov 2026.) Vehicles
turn white while one is on. How each is calculated: `engine/demand/areas.py`.

The source files aren't in git (`data/` is ignored). To set them up:

```bash
# Travel zones + jobs: from the team branch (Git LFS), or the TfNSW Open Data Hub
git lfs fetch group-repo mid-project-demo
for f in travel_zones_2021.geojson tzp24_employment.csv; do
  git show group-repo/mid-project-demo:template-repository-main/data/demand/$f | git lfs smudge > data/demand/$f
done
# cut the statewide zones down to the footprint
pip install -e ".[prep]"
python scripts/prepare_demand_data.py
```

The server builds the layers in the background on first start (~5s) and caches them in
`data/demand/area_layers.json` until an input changes.

## Removed on 5 Nov 2026: street colours, BOAM and the step 6 code

The "Colour the streets by" layers (TfNSW traffic, our own speed measure, crowding, BOAM
typical bus load and time lost), their direction modes, and the code behind them
(`segments.py`, `travel_time.py`, `boam.py`, `analysis/collect.py`, and the
`import_boam` / `export_boam` / `build_freeflow` / `analyse_congestion` / experiment
scripts) were removed at the owner's request. The step 6 results stay in
`docs/PROJECT_REPORT.md` §10; the code is in git history (commit `5676e3b` and earlier).

## Stop history and route focus

Every time a vehicle passes a stop, the server records how late it was and how full it
was (`engine/history/`, stored in `data/history/stop_visits.sqlite`). A vehicle has
passed a stop when the next stop in its TfNSW trip update changes. Only live data is
recorded: replays never write to the history.

**Stop history** (tablet: Display → Stop history, or **O**) colours each stop by its
median lateness, with fixed bands of 1, 2 and 4 minutes, or by how often vehicles arrive
with few seats left. The **Median / Average** switch changes the lateness summary: a big
gap between the two marks a stop that is usually fine but occasionally very late.
Hovering over a stop shows its visit count, median and average lateness, and lateness
for vehicles with seats available against fuller ones.

Rules applied before a stop is coloured:

- at least **20 visits** (at 20, the median is typically within ~25 s of the long-run
  value, under half the narrowest colour band);
- lateness outside **30 minutes early to 3 hours late** is treated as a feed error and
  left out (e.g. light rail readings ~42 hours late);
- a whole trip reporting exactly 0 s means TfNSW has no live prediction, so it counts
  as unknown, not on time; stops marked skipped or no-data, and stops a feed lists after
  the vehicle has already passed them, are ignored.

**Single out routes** (Display → Single out routes): type up to 8 route numbers. Each gets
its own colour from a colour-blind-safe palette, other vehicles fade out, and the stop
layers show only those routes' stops. Endpoints: `/stop_history`, `/routes`,
`/route?name=`.

## Background map

The model has its own streets, so the projection's background is plain by default. For a
laptop or flat-screen demo, the tablet's Display tab (Background map) or **M** switches to
**Detailed** (roads, rail, water, parks) or **Full** (Detailed plus suburb, station, landmark,
park, water and street names, placed so they never overlap). Both come from OpenStreetMap,
downloaded once and stored locally, so they need no internet at the showcase:

```bash
python scripts/fetch_basemap.py            # -> data/basemap/basemap.json (~3 MB), then restart
python scripts/fetch_basemap.py --cached   # rebuild from the last download
```

Until it has been downloaded, Detailed and Full are greyed out. Map data © OpenStreetMap
contributors (ODbL); the tablet shows the credit.

## Recording

Raw protobuf payloads are written to
`data/recordings/{mode}/{feed_type}/{UTC hour}.{writer}.ndjson.gz`, trimmed to the
model footprint first (`engine/sources/footprint.py`) — statewide feeds cost ~14GB in a
few hours. The server records while it runs (`serve` files), and a weekday schedule
records the AM and PM peaks even when it isn't running (`rec` files):

```bash
python scripts/run_record.py --minutes 130          # one-off recording
python scripts/install_recording_schedule.py        # weekday 06:55 and 16:25, 130 min each
sudo sh scripts/wake_schedule.sh                    # once: wakes the Mac before each peak
python scripts/install_recording_schedule.py --uninstall
```

Recordings replay from the tablet (Display → Time): any recorded AM/PM peak (tap Replay mode,
pick the day from the dropdown, then AM peak or PM peak), or the
latest two hours, in real time or as a 10× / 25× / 50× time-lapse that loops. Replays can be
paused (Play/Pause, or Space on the laptop) and scrubbed: tap the timeline, or −5/+5 min.
Live polling, and the server's own recording, pause while a replay is on, so switch back to
Live after a demo.

Polling stays inside TfNSW's daily quota (~60,000 requests per key, reset at midnight)
automatically: full rate during the weekday peaks, slower in between so the peaks stay
affordable, and the scheduled recorder stands aside while the server is already polling.
Today's usage and the current pace are under Team → System health and `/health`.

The schedule is a launchd agent (`~/Library/LaunchAgents/com.pulsecbd.recorder.plist`,
log in `data/recordings/recorder.log`). The Mac must be on power for scheduled wakes.

---

## Project layout

```
pulse-cbd/
  config/config.yaml       Single source of truth: bounding box, GTFS endpoints,
                            adherence thresholds, interchange list, calibration path.
                            Secrets never live here.
  pyproject.toml            Dependencies (runtime, plus [dev] for tests, [prep] for data prep)
  .env.example              Copy to .env and add TFNSW_API_KEY (.env is gitignored)
  data/                     gitignored: timetable cache, recordings, stop history,
                            calibration, demand/basemap inputs

  engine/
    config.py                Loads config.yaml + .env
    static/                  Phase 1 — fetch.py, filter.py, cache.py
    sources/                 Phase 2 — base.py (Source interface), live.py, recorder.py,
                              replay.py, footprint.py (trims recordings), budget.py
                              (daily API quota)
    normalise/                Phase 2/3 — protobuf decode, quirk handling, static join,
                              map matching (projection.py, map_match.py), schedule
                              deviation (trip_updates.py), adherence classification,
                              the rolling aggregate layer (aggregate.py), and enrich.py
                              which ties it all onto a VehicleRecord
                              Also headways.py (each trip's first stop and the
                              scheduled gap to the next trip)
    history/                  stop_tracker.py (detects a vehicle passing a stop) and
                              store.py (SQLite stop visits and per-stop summaries)
    demand/                   areas.py (jobs, service gap)
    analysis/                 export.py (CSV export of a recorded peak)
    publish/                  Phase 4/5/6 — feed.py (long-running live service),
                              server.py (aiohttp: WebSocket + HTTP), control.py (shared
                              tablet/projector state), route_focus.py, shapes_export.py
                              (clips corridor geometry for the browser), homography.py
                              + calibration.py (Phase 5)

  renderer/
    index.html                The model-facing Canvas renderer + calibration mode
    controller.html           The tablet controller: Display / Team / Stats tabs

  scripts/
    run_phase1.py             Static foundation only (build the SQLite cache)
    run_phase2.py             Headless realtime ingestion (dev/debug tool)
    run_phase3.py             + map matching/adherence (dev/debug tool)
    run_publish.py            The real entry point — starts everything
    run_record.py             Record without the server
    install_server_service.py / install_recording_schedule.py   macOS launchd jobs
    prepare_demand_data.py, fetch_basemap.py
                              One-off data setup for the optional layers
    export_peak.py            One recorded peak as three CSV files (also on the tablet: Display → Time)

  tests/                     173 tests, one file per module
  docs/DECISIONS.md          Every non-obvious judgment call made during the build,
                              and why — read this for the "why" behind anything
                              that looks like it could've been done differently
```

---

## Running it

**Prerequisites**: Python 3.11+, git, and a free Transport for NSW Open Data Hub API key
(register at opendata.transport.nsw.gov.au). Git LFS is only needed for the optional
demand layers.

**Install** (from the repository folder). macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"        # runtime dependencies + pytest
cp .env.example .env
```

Windows (PowerShell):

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

All dependencies are declared in `pyproject.toml`: shapely, pyproj, pyyaml, httpx,
protobuf, aiohttp and numpy at run time; pytest and grpcio-tools under `[dev]`; pyshp
under `[prep]`, only for `scripts/prepare_demand_data.py`. Plain `pip install -e .` is
enough to run the system but not the tests.

Put your API key in `.env` (never commit this file):

```
TFNSW_API_KEY=your-key-here
```

**One-time setup** — build the static GTFS cache (Phase 1). If you already have a
downloaded bundle, seed from it directly instead of re-fetching:

```bash
python scripts/run_phase1.py --seed-dir /path/to/gtfs/extract
# or, to fetch fresh from the API:
python scripts/run_phase1.py
```

**Run it**: on any platform,

```bash
python scripts/run_publish.py
```

**On the showcase Mac**, keep it running with launchd instead (starts at login, restarts
within 30 s if it stops; log in `data/recordings/server.log`; macOS only):

```bash
python scripts/install_server_service.py              # install and start
launchctl kickstart -k gui/$(id -u)/com.pulsecbd.server   # restart (e.g. after a code change)
python scripts/install_server_service.py --uninstall  # stop for good
```

While it's installed, `kill` only restarts the server, and `run_publish.py` in a
terminal will fail because the port is taken: uninstall the service first.

Then open `http://127.0.0.1:8765/` full-screen on the laptop (it's mirrored to the
projector) and the `/control` URL the server prints on the tablet.

**Keyboard backup** on the projector page (each key goes through the server, so the
tablet stays in sync): **C** calibrate, **F** corner fit, **V** verification overlay, **H** status line,
**T** theme, **M** background map (plain / detailed / full),
**S** stop markers, **O** stop history, **L** dot colour (schedule / traffic / crowding), **E** early buses (blue) on / off,
**A** areas (off / jobs / service gap),
**G** areas on travel zones / grid, **Space** pause/play a replay, **1–5** trains / metro / light rail / buses /
ferries, **arrows** nudge (shift ×10), **[ ]** scale, **0** reset nudge.

**Headless dev/debug tools** (no rendering, just prove the data pipeline works and
inspect it directly):

```bash
python scripts/run_phase2.py --cycles 5           # realtime ingestion only
python scripts/run_phase3.py --cycles 7            # + map matching/adherence + aggregate summary
```

**Tests**:

```bash
pytest        # 173 tests, a few seconds, no network or API key required
```

**Troubleshooting**

- *Tablet buttons for Jobs / Service gap are greyed out*: the demand
  input files aren't in `data/demand/` on this machine (see [Demand layers](#demand-layers)).
- *Detailed / Full background map greyed out*: run `scripts/fetch_basemap.py`.
- *Stop history is empty*: it fills while the server runs live; stops need 20 visits.
- *Nothing moves*: check `.env` has the API key and `/health` shows recent feed times.

### Calibrating onto the physical model

1. Point a projector at the model, run `scripts/run_publish.py`, and full-screen
   `http://127.0.0.1:8765/` on the projector's display.
2. Press **C** (or **Start** under Team → Alignment on the tablet) to enter
   calibration mode — four (or more) draggable crosshairs appear.
3. Drag each crosshair to line up with a physical registration mark on the model,
   and type in that mark's known latitude/longitude.
4. Click **Save calibration** — the server computes a homography and writes it to
   `data/calibration.json`. The renderer immediately switches to using it, and it
   persists across restarts.
5. Press **V** to toggle the verification overlay (route corridors rendered bright)
   to visually confirm the alignment looks right against the physical model before
   trusting it with live data.

Between weekly sessions the rig stays set up, so start each session with **V** and
fix small drift with the tablet's nudge (Team → Alignment) rather than recalibrating.

**Corner fit** (Team → Alignment → Corner fit, or **F**) is the quick way to line the
map up when the projector has moved or isn't square to the table: four handles appear
on the footprint's corners (NW, NE, SE, SW); drag each onto the matching corner of the
model on the laptop, and the whole map follows (it's a homography through those four
points, so it also takes out keystone). For fine moves, set "Arrows move" to a corner
and use the arrow pad. The offsets are saved with the nudge and survive restarts;
**Reset corners** puts them back. Saving a new crosshair calibration resets both the
nudge and the corners.

---

## API reference

The server (`engine/publish/server.py`) listens on port 8765. All responses are JSON
unless noted.

| Endpoint | What it returns |
|---|---|
| `GET /` | The projector page (`renderer/index.html`) |
| `GET /control` | The tablet controller (`renderer/controller.html`); `/stats` redirects here |
| `GET /ws` | WebSocket: vehicle frames (~2 Hz) and the shared control state; clients send `{"control": {...}}` patches |
| `GET /health` | Feed ages, vehicles per mode, API usage, connected pages, playback state |
| `GET /snapshot` | Every current vehicle record in full |
| `GET /aggregate` | Per-trip on-time shares by mode, active alerts, interchange activity |
| `GET /config` | The renderer's settings: bounding box, thresholds, palette, labels |
| `GET /shapes` | Route corridors clipped to the model footprint |
| `GET /stops` | Stop locations, each with the route names serving it |
| `GET /stop_history` | Per-stop visits, median/average lateness, crowded share, seated vs fuller lateness |
| `GET /routes` | Route catalogue for "Single out routes" |
| `GET /route?name=333` | One route's shapes and stops (404 if unknown) |
| `GET /areas` (`?meta=1`) | Area layers on zones and grid (or just what's available) |
| `GET /basemap` | The offline background map, if downloaded |
| `GET /recordings` | Replayable recorded peaks and their coverage |
| `GET /calibration` / `POST /calibration` | The saved homography / save a new one from crosshair points |
| `GET /export?session=2026-11-04-am&kind=vehicles` | One recorded peak as CSV (`vehicles`, `stop_visits` or `trips`); the first request replays the peak, about 2 minutes |

## Configuration reference

Everything tunable lives in `config/config.yaml`:

- `bounding_box` — the model's footprint in lat/lon (~13.2 km E-W x 6.6 km N-S)
- `static_gtfs` — the Complete GTFS bundle download URL and cache paths
- `realtime_gtfs.feeds` — the full endpoint matrix for all 7 modes x 3 feed types (a mix of v1/v2 paths, verified live against the real API)
- `punctuality` — per-mode on-time limits (late, and early for buses), each with its source: TfNSW rail punctuality, the Greater Sydney Bus Contract, and the borrowed 5-minute rail standard for Metro, light rail and ferries
- `interchanges` / `interchange_radius_m` — which stations the Stats tab counts activity at, and the proximity radius
- `calibration_path` — where the computed homography is persisted
- `realtime_gtfs.poll_interval_seconds` (15) and `stale_after_seconds` (90) — how often
  feeds are polled, and how long a silent vehicle stays on the map
- `server` — host and port (8765)
- `api_budget` — the daily request cap (57,000, ~5% under TfNSW's quota), the peak
  windows always polled at full rate, and the slowest pace when the budget runs low

The nudge and corner-fit offsets are saved separately in `data/projection_nudge.json`.

## Notable design decisions

`docs/DECISIONS.md` is a running log of every non-obvious call made without
stopping to ask, in the order they were made, each with its reasoning. Highlights:

- **Complete GTFS bundle**, not the smaller per-mode "For Realtime" subset — one request instead of seven, and the bounding-box filter already handles the size concern.
- **SQLite** for the filtered static cache — no extra dependency, indexed lookups.
- **Compiled the TfNSW realtime proto standalone** rather than using the `gtfs-realtime-bindings` package, because the fetched extension file redefines the base messages inline rather than importing Google's official proto.
- **Geometric map matching resolves the Sydney Trains/Metro/Light-Rail id-mismatch quirk** — confirmed live, not just theorised.
- **No basemap by default** — the model has the streets. An optional OpenStreetMap background (Detailed, or Full with names) is there for screen demos, stored offline and drawn through the same projection as everything else.
- **Calibration uses a simple equirectangular projection**, not the UTM used for map matching, specifically so the browser (no pyproj available) can reproduce the exact same transform in plain JavaScript.
- **Two real bugs caught by live testing**, not code review: a blocking `alert()` that froze the page, and — more seriously — a WebSocket broadcast loop with no per-client timeout that one frozen connection could hang for every viewer, permanently.

## Current status

All six phases from the original design brief are built, tested (208 automated
tests, as of 5 Nov 2026), live-verified against the real Transport for NSW API, and
running on the physical model:

1. ✅ Static GTFS foundation
2. ✅ Headless realtime ingestion
3. ✅ Map matching and schedule adherence
4. ✅ Renderer on a flat screen
5. ✅ Calibration and projection onto the physical model (crosshair calibration,
   nudge, corner fit)
6. ✅ Interchange activity and the stats panel (now the tablet's Stats tab; the projected pulses were removed on 5 Nov)

Built on top since: recording and replay of every peak, the tablet controller, area
layers, punctuality from TfNSW's published standards, per-trip on-time figures, stop
history with route focus and hover highlighting, CSV export, and an optional background
map. (Street colours, BOAM and the step 6 analysis code were built and later removed.)

**Known limitations**: stop colours show lateness inherited from earlier in the trip,
not only delay caused at that stop; the crowding–lateness relationship is an
association, not proof of cause; the morning peak has far fewer recorded visits than the
evening peak; and occasionally TfNSW attaches a bus to the wrong trip, which the median
(not the average) protects against. See `docs/DECISIONS.md` for the reasoning behind
every threshold.
