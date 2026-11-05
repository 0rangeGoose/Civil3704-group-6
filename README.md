# Inner Sydney Transit Projection Model

A live map of inner Sydney's public transport, projected onto a physical scale model.

A projector above the model shines an image straight down onto it. The image shows
every train, metro, light rail vehicle, ferry and bus currently inside the model's
area (typically 270 to 490 at once) as a small arrow on the street or track it is
really on, updated every 15 seconds from Transport for NSW's live data. Vehicles can be
coloured by how late they are, how congested their traffic is or how full they are;
stops can be coloured by their typical delay; and an area layer shows where the jobs
are. It shows what is happening now, or replays any
recorded morning or evening peak as a sped-up time-lapse. Visitors control it from a
tablet beside the model.

CIVL3704, Group 6.

```bash
python scripts/run_publish.py
```

starts everything: live data, map matching, punctuality scoring, recording, stop
history and the browser pages. First-time setup is under [Running it](#running-it).

---

## Using it

Open `http://127.0.0.1:8765/` full-screen on the laptop; the projector mirrors that
screen. The projected page shows only map content: a black background, every vehicle as
an arrow pointing the way it's travelling, sized by mode, with a short tail of its last
30 seconds and dim route corridors underneath. Pointing at a vehicle shows its exact
lateness, and pointing at a stop lists the routes serving it. Clicking a vehicle or stop
highlights those routes until you click it again or click empty map.

Open `http://<laptop address>:8765/control` on the tablet (the server prints this URL
on startup). Use the laptop's own hotspot: venue and university Wi-Fi often stops
devices reaching each other.

- **Display** (for visitors): vehicle types shown, the colour view (schedule, traffic
  or crowding), area layers, stop markers and stop history, singling out routes, the
  route highlight colour, live or replayed time, CSV export, and a legend stating the
  cut-off for every colour.
- **Team**: projection theme, verification overlay, an on-projection status line,
  alignment (nudge, corner fit, crosshair calibration) and system health (vehicles per
  mode, feed age, API usage).
- **Stats**: the share of trips on time by mode, active service alerts and the busiest
  interchanges.

**Keyboard shortcuts** on the projector page (each goes through the server, so the
tablet stays in sync): **C** calibrate, **F** corner fit, **V** verification overlay,
**H** status line, **T** theme, **M** background map (plain / detailed / full),
**S** stop markers, **O** stop history, **L** colour view, **E** early buses on / off,
**A** jobs layer on / off, **G** areas on travel zones / grid,
**Space** pause or play a replay, **1–5** trains / metro / light rail / buses / ferries,
**arrows** nudge (Shift ×10), **[ ]** scale, **0** reset nudge.

---

## Architecture

Two layers, kept separate:

- **The data engine** (`engine/`, Python) downloads Transport for NSW's GTFS timetable
  and GTFS-Realtime feeds, cleans them up, and publishes a stream of positioned
  vehicles with their punctuality. It knows nothing about the projector and runs
  headless.
- **The renderer** (`renderer/`, plain HTML and Canvas) draws that stream. With a saved
  calibration it maps latitude/longitude onto projector pixels through a homography;
  without one it fits the model's bounding box into the browser window, so it also works
  on any normal screen.

```
Transport for NSW Open Data Hub
         │
         ├─ Static GTFS (daily)              ├─ GTFS-Realtime (every 15 s)
         │  routes, stops, trips, shapes,     │  vehicle positions, trip updates,
         │  stop times, cut to the model      │  service alerts: 7 modes × 3 feeds
         ▼                                    ▼
   SQLite cache                          decode protobuf (including TfNSW's
   (data/static_cache/gtfs_cbd.db)       field-1007 extension)
         │                                    │
         │                               handle feed quirks
         └──────────────┬─────────────────────┘
                        ▼
            join live vehicles to the timetable
            (trip_id, else route_id, else nearest route shape)
                        │
                        ▼
            snap position onto the route shape;
            delay → punctuality state
                        │
                        ▼
            live snapshot (engine/publish/feed.py)
                        │
            ┌───────────┴────────────┐
            ▼                        ▼
    WebSocket (~2 Hz)          HTTP (see API reference)
    vehicles + shared          shapes, stops, stop history,
    control state              area layers, replays, exports
            │                        │
            ▼                        ▼
    renderer/index.html      renderer/controller.html
    (projector)              (tablet)
```

### What is computed for each vehicle

| Field | What it is |
|---|---|
| `vehicle_key` | Stable identity (`mode:vehicle_id`) |
| `mode`, `route_short_name`, `route_color`, `headsign` | From the timetable |
| `lat` / `lon`, `bearing`, `position_timestamp` | Position as reported |
| `matched_lat` / `matched_lon`, `distance_along_shape_m` | Position snapped onto the route's shape, since raw GPS wanders off the road or track |
| `schedule_deviation_seconds` | TfNSW's predicted delay at the next stop; positive is late |
| `adherence_state` | `early`, `on_time`, `late`, `very_late`, `cancelled` or `unknown` (see [Punctuality](#punctuality)) |
| `confidence` | How the vehicle was matched to the timetable (below) |

**Confidence.** Not every live vehicle joins cleanly to the timetable, so each one
says how it was identified:

- `static_join`: the live `trip_id` matched a timetable trip directly.
- `degraded_route_fallback`: the trip didn't match (for example a bus whose trip was
  altered), but the route did.
- `geometric_match`: neither matched, but the position lay close enough to a known
  route shape. This is what rescues Sydney Trains, Metro and CBD & South East Light
  Rail, whose live feeds use a different ID scheme from the timetable.
- `unmatched`: no route found. The vehicle is still shown, without route details.

### Punctuality

On time means no later (and, for buses, no earlier) than each mode's limit, set in
`config/config.yaml` under `punctuality`:

| Mode | On time | Source |
|---|---|---|
| Sydney Trains | up to 5 min late | TfNSW punctuality standard |
| Intercity (NSW TrainLink) | up to 6 min late | TfNSW punctuality standard |
| Buses | 59 s early to 5 min 59 s late, at the first stop | Greater Sydney Bus Contract, Schedule 4 (2023) |
| Metro, light rail, ferries | up to 5 min late | No published standard found; the Sydney Trains standard is applied |

**Very late** is this project's own category: past the limit *and* later than the
scheduled gap to the next trip on the route, so the next service would have come
sooner (`engine/normalise/headways.py`). Buses more than 59 s early are marked
**early** (blue); the tablet's Early buses switch can show them as on time instead.

The Stats tab counts each trip once, where TfNSW measures it: buses as they leave their
first stop, other modes by the predicted delay at their final stop. Trips with no live
prediction aren't counted; cancelled ones count as not on time.

### Feed quirks handled

- **Ferry ghost trails**: the ferry feed reports a vessel's current and future
  positions in the same update. Only the freshest current one is kept.
- **Bus trip IDs change** when a trip is altered, so they stop matching the timetable.
  The route-level fallback handles this.
- **Buses come as one statewide feed**, filtered to the model by position and by the
  timetable join.
- **New trips can appear live before the timetable has them**: handled by the
  geometric and unmatched fallbacks.
- **Occupancy data is patchy across modes**, so it is optional everywhere.
- **Rail and light rail use a different route ID scheme** live than in the timetable:
  handled by geometric matching.

---

## Features

### Stop history and route focus

Each time a vehicle passes a stop, the server records how late and how full it was
(`engine/history/`, stored in `data/history/stop_visits.sqlite`). A vehicle has passed a
stop when the next stop in its trip update changes. Only live data is recorded; replays
never write to the history.

**Stop history** (Display → Stop history, or **O**) colours each stop by its median
lateness, in bands of 1, 2 and 4 minutes, or by how often vehicles arrive with few
seats left. The Median / Average switch changes the lateness summary: a big gap between
the two marks a stop that is usually fine but occasionally very late. Before a stop is
coloured:

- it needs at least **20 visits** (by then the median is typically within about 25 s of
  its long-run value);
- lateness outside **30 minutes early to 3 hours late** is treated as a feed error;
- a trip reporting exactly 0 s throughout has no live prediction, so it counts as
  unknown; skipped stops and stops listed after the vehicle has passed are ignored.

**Single out routes**: type up to 8 route names. Each gets its own colour from a
colour-blind-safe palette, other vehicles fade back, and the stop layers show only
those routes' stops.

**Route highlight**: clicking a vehicle or stop lights up its routes in white (navy
on the light theme), or in blue with the Display tab's Blue highlight switch on.

### Area layers

Display → Colour the areas by: **jobs**, the employment projected for 2026 in each area
(TfNSW TZP24, by place of work), on TfNSW travel zones or a 150 m grid, in five equal-sized
groups. Calculations are in `engine/demand/areas.py`.

The source files aren't in git (`data/` is ignored). To set them up:

```bash
# Travel zones and jobs: from the team branch (Git LFS), or the TfNSW Open Data Hub
git lfs fetch origin mid-project-demo
for f in travel_zones_2021.geojson tzp24_employment.csv; do
  git show origin/mid-project-demo:template-repository-main/data/demand/$f | git lfs smudge > data/demand/$f
done
pip install -e ".[prep]"
python scripts/prepare_demand_data.py   # cut the statewide zones down to the model
```

The server builds the layers on first start (about 5 s) and caches them in
`data/demand/area_layers.json`.

### Background map

The model has its own streets, so the projection's background is plain by default. For
a screen demo, Display → Background map (or **M**) switches to **Detailed** (roads,
rail, water, parks) or **Full** (Detailed plus place and street names). Both come from
OpenStreetMap and are stored locally, so no internet is needed at the venue:

```bash
python scripts/fetch_basemap.py            # → data/basemap/basemap.json (~3 MB), then restart
python scripts/fetch_basemap.py --cached   # rebuild from the last download
```

Map data © OpenStreetMap contributors (ODbL).

### Recording and replay

Raw feed data is saved to
`data/recordings/{mode}/{feed_type}/{UTC hour}.{writer}.ndjson.gz`, trimmed to the model
first (`engine/sources/footprint.py`). The server records while it runs, and a weekday
schedule records the morning and evening peaks even when it isn't running:

```bash
python scripts/run_record.py --minutes 130          # one-off recording
python scripts/install_recording_schedule.py        # weekdays 06:55 and 16:25, 130 min each
sudo sh scripts/wake_schedule.sh                    # once: wakes the Mac before each peak (written by the installer)
python scripts/install_recording_schedule.py --uninstall
```

Display → Time replays any recorded peak, or the latest two hours, in real time or at
10× / 25× / 50×, looping. Replays can be paused and scrubbed. Live polling and recording
pause during a replay, so switch back to Live afterwards.

Polling stays inside TfNSW's daily quota (about 60,000 requests per key, reset at
midnight): full rate during the weekday peaks, slower in between. Usage is shown under
Team → System health and at `/health`.

### CSV export

Display → Time → Export, or `scripts/export_peak.py`, writes one recorded peak as three
CSV files. Times are Sydney time with the offset (e.g. `2026-11-04T07:00:08+11:00`);
blank means no value. The first export of a peak replays it (about 2 minutes).

**vehicles.csv**: one row per vehicle per feed update (about every 15 s).

| Column | Meaning |
|---|---|
| `time` | When the update was downloaded |
| `vehicle` | Mode and TfNSW vehicle ID |
| `mode`, `route`, `headsign`, `trip_id` | Feed mode, route name, destination, timetable trip |
| `lat`, `lon` | Position snapped to the route line, or raw GPS if it couldn't be placed |
| `bearing` | Direction of travel in degrees from north (from the route line for Sydney Trains, which send none) |
| `delay_s` | Predicted delay at the next stop in seconds; positive is late |
| `on_time_state` | `early`, `on_time`, `late`, `very_late`, `cancelled` or `unknown` |
| `tfnsw_traffic` | TfNSW `congestion_level` (buses only in practice) |
| `tfnsw_crowding` | TfNSW `occupancy_status` (middle carriage for trains) |
| `position_reported_at` | The vehicle's own timestamp for that position |

**stop_visits.csv**: one row each time a vehicle passed a stop.

| Column | Meaning |
|---|---|
| `time` | When the vehicle was seen to have passed the stop |
| `stop_id`, `stop_name`, `stop_lat`, `stop_lon` | The stop |
| `route`, `mode`, `vehicle` | The vehicle |
| `delay_s` | Its delay leaving the stop; blank where TfNSW gave no real prediction |
| `tfnsw_crowding` | Its `occupancy_status` leaving the stop |

**trips.csv**: one row per trip, measured once where TfNSW measures it (what the Stats
tab counts).

| Column | Meaning |
|---|---|
| `time` | When the reading was taken (the last one wins) |
| `trip` | Mode and TfNSW trip ID |
| `mode`, `route` | Feed mode and route name |
| `measured_at` | `first stop` (buses) or `final stop (predicted)` (other modes) |
| `stop_id`, `stop_name` | The stop measured at |
| `delay_s` | Delay there in seconds; positive is late |
| `state` | `early`, `on_time`, `late` or `cancelled` |

---

## Running it

**Prerequisites**: Python 3.11+, git, and a free Transport for NSW Open Data Hub API key
(opendata.transport.nsw.gov.au). Git LFS is only needed for the area layers.

**Install**. macOS / Linux:

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

Put your API key in `.env` (Git ignores this file):

```
TFNSW_API_KEY=your-key-here
```

**Build the timetable cache** (once, and whenever the timetable changes):

```bash
python scripts/run_phase1.py                              # download from the API
python scripts/run_phase1.py --seed-dir /path/to/gtfs     # or use a bundle you already have
```

**Run**:

```bash
python scripts/run_publish.py
```

On the showcase Mac it can run as a launchd service instead (starts at login, restarts
within 30 s if it stops; log in `data/recordings/server.log`):

```bash
python scripts/install_server_service.py                  # install and start
launchctl kickstart -k gui/$(id -u)/com.pulsecbd.server   # restart after a code change
python scripts/install_server_service.py --uninstall      # remove
```

While the service is installed, `run_publish.py` in a terminal fails because the port is
taken.

**Tests** (a few seconds, no network or API key needed):

```bash
pytest
```

**Headless tools** for checking the data pipeline without the renderer:

```bash
python scripts/run_phase2.py --cycles 5    # live ingestion only
python scripts/run_phase3.py --cycles 7    # + map matching and punctuality
```

**Troubleshooting**

- *Jobs greyed out*: the input files aren't in `data/demand/` (see
  [Area layers](#area-layers)).
- *Detailed / Full background map greyed out*: run `scripts/fetch_basemap.py`.
- *Stop history empty*: it fills while the server runs live; stops need 20 visits.
- *Nothing moves*: check `.env` has the API key and `/health` shows recent feed times.

### Calibrating onto the physical model

1. Point the projector at the model, start the server and open
   `http://127.0.0.1:8765/` full-screen on the projector's display.
2. Press **C** (or Team → Alignment → Start on the tablet). Four or more draggable
   crosshairs appear.
3. Drag each onto a registration mark on the model and type in that mark's latitude
   and longitude.
4. **Save calibration**: the server computes a homography and writes it to
   `data/calibration.json`. It is used straight away and survives restarts.
5. Press **V** for the verification overlay (route corridors drawn bright) to check the
   alignment against the model.

**Corner fit** (Team → Alignment → Corner fit, or **F**) is the quick fix when the
projector has moved or isn't square: drag the four corner handles onto the model's
corners and the whole map follows, keystone included. Small drift between sessions is
best fixed with the nudge. Saving a new crosshair calibration resets the nudge and
corners.

---

## Project layout

```
config/config.yaml        All settings: bounding box, feed endpoints, punctuality limits,
                          interchanges, server, API budget. No secrets.
pyproject.toml            Dependencies ([dev] for tests, [prep] for data preparation)
.env.example              Copy to .env and add TFNSW_API_KEY
data/                     Ignored by git: timetable cache, recordings, stop history,
                          calibration, area and background map inputs

engine/
  config.py               Loads config.yaml and .env
  static/                 Timetable download, filtering and SQLite cache
  sources/                Live polling, recording, replay, footprint trimming, API budget
  normalise/              Protobuf decoding, quirks, timetable join, map matching,
                          delay, punctuality, headways, the rolling aggregates
  history/                Stop visit detection and storage
  demand/                 Area layer (jobs)
  analysis/               CSV export of a recorded peak
  publish/                Live service, aiohttp server, shared control state,
                          route focus, shapes for the browser, calibration

renderer/
  index.html              Projector page and calibration mode
  controller.html         Tablet: Display / Team / Stats

scripts/
  run_publish.py          Starts everything
  run_phase1.py           Builds the timetable cache
  run_phase2.py, run_phase3.py   Headless pipeline checks
  run_record.py           Record without the server
  install_server_service.py, install_recording_schedule.py
                          macOS scheduling
  prepare_demand_data.py, fetch_basemap.py   One-off data setup
  export_peak.py          CSV export of a recorded peak

tests/                    One test file per module
```

## API reference

The server (`engine/publish/server.py`) listens on port 8765. Responses are JSON unless
noted.

| Endpoint | Returns |
|---|---|
| `GET /` | The projector page |
| `GET /control` | The tablet page (`/stats` redirects here) |
| `GET /ws` | WebSocket: vehicle frames (~2 Hz) and shared control state; clients send `{"control": {...}}` patches |
| `GET /health` | Feed ages, vehicles per mode, API usage, connected pages, playback state |
| `GET /snapshot` | Every current vehicle record |
| `GET /aggregate` | On-time shares by mode, active alerts, interchange activity |
| `GET /config` | Renderer settings: bounding box, limits, palette, labels |
| `GET /shapes` | Route corridors clipped to the model |
| `GET /stops` | Stops, each with the routes serving it |
| `GET /stop_history` | Per-stop visits, median and average lateness, crowding |
| `GET /routes` | Route list for Single out routes |
| `GET /route?name=333` | One route's shapes and stops (404 if unknown) |
| `GET /areas` (`?meta=1`) | Area layers on zones and grid (or what's available) |
| `GET /basemap` | The offline background map, if downloaded |
| `GET /recordings` | Replayable peaks and their coverage |
| `GET` / `POST /calibration` | The saved homography / save a new one |
| `GET /export?session=2026-11-04-am&kind=vehicles` | One recorded peak as CSV (`vehicles`, `stop_visits` or `trips`) |

## Design decisions

- **The model is the map**: no background map by default; the optional OpenStreetMap
  one is for screen demos.
- **The Complete GTFS bundle**, filtered to the model's bounding box, rather than seven
  per-mode downloads.
- **SQLite** for the timetable cache: no extra service, indexed lookups.
- **The TfNSW realtime proto is compiled standalone** rather than using
  `gtfs-realtime-bindings`, because TfNSW's extension file redefines the base messages.
- **Geometric map matching** solves the rail and light rail ID mismatch.
- **Calibration uses a simple equirectangular projection** so the browser can reproduce
  exactly the same transform in plain JavaScript.
- **Punctuality limits come from published standards** where they exist (see
  [Punctuality](#punctuality)).
- **Lateness, traffic and crowding are never blended** into one colour; each is its own
  view.

## Known limitations

- Stop colours include lateness carried from earlier in the trip, not only delay caused
  at that stop.
- The link between crowding and lateness is an association, not proof of cause.
- The morning peak has far fewer recorded stop visits than the evening peak.
- TfNSW occasionally attaches a bus to the wrong trip; the median (rather than the
  average) protects stop history against this.
- Metro, light rail and ferries have no published on-time standard, so the Sydney Trains
  one is borrowed.
