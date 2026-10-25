# Pulse CBD

A real-time, multimodal transit "heartbeat" for Sydney's CBD. It ingests live train,
metro, light rail, ferry and bus data from Transport for NSW, tracks every vehicle
currently moving through the CBD, and renders them as glowing points of light on a
map — built to eventually project onto a physical 3D scale model of the CBD, but
fully usable on a normal screen today.

```
python scripts/run_publish.py
```

...starts the whole thing: live data ingestion, map matching, schedule-adherence
scoring, and a browser-based renderer, all in one command.

---

## What it actually looks like

Open `http://127.0.0.1:8765/` once the server is running: a black background with
every bus, train, metro, light rail vehicle and ferry currently in the CBD shown as a
soft glowing dot, colour-coded by whether it's running on time, sized by mode (a
train reads bigger than a bus), with a short comet tail showing recent movement and
dim route corridors underneath so an empty street reads as empty rather than as a gap.

Open `http://<laptop address>:8765/control` on the tablet next to the model. The
projector mirrors the laptop screen, so the projected view shows map content only (no
buttons, hints or cursor), and everything is controlled from the tablet instead:

- **Display** (for visitors): which vehicle types are shown, interchange pulses, and a
  colour legend.
- **Team** (behind a PIN, `controller.team_pin` in config): projection theme,
  verification overlay, an on-projection status line, alignment nudge, crosshair
  calibration, and system health (vehicles per mode, feed age).
- **Stats**: on-time percentages by mode, active service alerts, and the busiest
  interchanges (this used to be the separate `/stats` page, which now redirects here).

The server prints the tablet URL on startup. Use the laptop's own hotspot for the
tablet: the server listens on every interface, and venue/university Wi-Fi often
blocks devices from reaching each other anyway.

## Why it's built the way it is

The original design brief called for a physical installation: a laser-cut/3D-printed
scale model of the CBD on a table, a projector mounted above it pointing straight
down, and the browser renderer's output projected onto the model so vehicles appear
to move along the model's actual streets and rail corridors in real time.

That physical setup doesn't exist yet — no model, no projector. Everything through
Phase 4 works identically with or without one. Phase 5 (calibration) is the piece
that specifically bridges to the physical world: it's fully built and tested with
synthetic data, but the *real* calibration session (physically aligning crosshairs to
registration marks on an actual model) can only happen once that hardware exists.
Until then, the renderer uses a simple flat aspect-correct projection of the
bounding box — good enough to view on any screen.

---

## Architecture: two layers, deliberately separated

**The data engine** ingests Transport for NSW's GTFS and GTFS-Realtime feeds,
normalises them, and publishes a clean stream of positioned vehicles with
schedule-adherence state. It has no idea a projector exists.

**The projection renderer** consumes that stream and draws it. Today that means a
plain aspect-correct fit of the bounding box into whatever browser window it's in;
once real calibration data exists, it'll mean a homography onto actual projector
pixel space — without any change to the drawing code itself.

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
      WebSocket (~2Hz)           HTTP (/snapshot, /aggregate,
      compact vehicle frames     /shapes, /config, /calibration)
              │                        │
              ▼                        ▼
      renderer/index.html      renderer/stats.html
      (the model view)          (the wall-panel dashboard)
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
| `adherence_state` | one of `on_time`, `late`, `very_late`, `cancelled`, `unknown` — thresholds differ by mode |
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

- **Rolling schedule adherence** — % on-time/late/very-late/etc. by mode, over trailing 5, 15, and 60-minute windows.
- **Active service alerts** — count and severity of alerts that intersect the CBD's routes/stops, out of everything TfNSW publishes statewide.
- **Interchange activity** — for 9 named interchanges (Central, Town Hall, Wynyard, Martin Place, Circular Quay, Barangaroo, Gadigal, Museum, St James), how many *distinct* vehicles have passed within 300m in the last 5 minutes. This drives the pulsing interchange nodes on the model view.

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
**jobs** (TZP24, 2026), **service gap** (jobs vs scheduled departures within 200 m,
7–9am on a weekday), **Opal 2016** (tap-ons/offs 7–9am, 21–25 Nov 2016) and
**pressure**, a live combined score of traffic, crowding, service gap and Opal 2016 whose
weights are set in Team → Pressure weights. Vehicles
turn white while one is on. How each is calculated: `engine/demand/areas.py`.

The source files aren't in git (`data/` is ignored). To set them up:

```bash
# Travel zones + jobs: from the team branch (Git LFS), or the TfNSW Open Data Hub
git lfs fetch group-repo mid-project-demo
for f in travel_zones_2021.geojson tzp24_employment.csv; do
  git show group-repo/mid-project-demo:template-repository-main/data/demand/$f | git lfs smudge > data/demand/$f
done
# Opal Tap On and Tap Off Release 2, time with location, 21-27 Nov 2016 (data.nsw.gov.au)
curl -L -o data/opal/time_loc_20161121-27.csv <resource URL>
# Postcode boundaries (ABS POA 2021, GDA2020 shapefile, ~53MB), unzipped somewhere temporary
pip install -e ".[prep]"
python scripts/prepare_demand_data.py --poa-dir /path/to/POA_2021_AUST_GDA2020_SHP
```

The server builds the layers in the background on first start (~5s) and caches them in
`data/demand/area_layers.json` until an input changes.

### BOAM (typical bus load and time lost)

Download BOAM day files from the TfNSW Open Data Hub (login needed), then:

```bash
python scripts/import_boam.py --delete-originals ~/Downloads/BOAM_*.txt   # keeps the footprint (~2.5MB/day)
python scripts/export_boam.py                                              # data/boam/sections.csv for the report
```

The server picks up every imported weekday on its next start (Display → Colour the streets
by → Typical bus load / Where buses lose time, with an AM/PM switch), and BOAM load becomes
a fifth input to the pressure layer.

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

Recordings replay from the tablet (Display → Time): any recorded AM/PM peak, or the
latest two hours, in real time or as a 10× / 25× / 50× time-lapse that loops. Live
polling pauses while a replay plays.

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
  .env                      TFNSW_API_KEY (gitignored)
  data/                     gitignored: static cache, recordings, calibration.json

  engine/
    config.py                Loads config.yaml + .env
    static/                  Phase 1 — fetch.py, filter.py, cache.py
    sources/                 Phase 2 — base.py (Source interface), live.py, recorder.py
    normalise/                Phase 2/3 — protobuf decode, quirk handling, static join,
                              map matching (projection.py, map_match.py), schedule
                              deviation (trip_updates.py), adherence classification,
                              the rolling aggregate layer (aggregate.py), and enrich.py
                              which ties it all onto a VehicleRecord
    publish/                  Phase 4/5/6 — feed.py (long-running live service),
                              server.py (aiohttp: WebSocket + HTTP), shapes_export.py
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

  tests/                     68 tests, one file per module
  docs/DECISIONS.md          Every non-obvious judgment call made during the build,
                              and why — read this for the "why" behind anything
                              that looks like it could've been done differently
```

---

## Running it

**Prerequisites**: Python 3.11+, a free Transport for NSW Open Data Hub API key
(register at opendata.transport.nsw.gov.au), and the venv set up:

```bash
cd ~/pulse-cbd
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

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

**Run it for real**:

```bash
python scripts/run_publish.py
```

Then open `http://127.0.0.1:8765/` full-screen on the laptop (it's mirrored to the
projector) and the `/control` URL the server prints on the tablet.

**Keyboard backup** on the projector page (each key goes through the server, so the
tablet stays in sync): **C** calibrate, **V** verification overlay, **H** status line,
**T** theme, **P** interchange pulses, **L** dot colour (schedule / traffic / crowding),
**K** street lines (off / traffic / crowding), **A** areas (off / jobs / service gap / Opal 2016 / pressure),
**G** areas on travel zones / grid, **B** BOAM peak AM / PM, **1–5** trains / metro / light rail / buses /
ferries, **arrows** nudge (shift ×10), **[ ]** scale, **0** reset nudge.

**Headless dev/debug tools** (no rendering, just prove the data pipeline works and
inspect it directly):

```bash
python scripts/run_phase2.py --cycles 5           # realtime ingestion only
python scripts/run_phase3.py --cycles 7            # + map matching/adherence + aggregate summary
```

**Tests**:

```bash
pytest        # 132 tests, ~1s, no network required
```

### Calibrating onto a physical model (once one exists)

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
Saving a new calibration resets the nudge.

---

## Configuration reference

Everything tunable lives in `config/config.yaml`:

- `bounding_box` — the CBD lat/lon box (currently ~5km N-S x 4km E-W)
- `static_gtfs` — the Complete GTFS bundle download URL and cache paths
- `realtime_gtfs.feeds` — the full endpoint matrix for all 7 modes x 3 feed types (a mix of v1/v2 paths, verified live against the real API)
- `adherence_thresholds_seconds` — per-mode on-time thresholds and the late→very_late multiplier
- `interchanges` / `interchange_radius_m` — which stations get pulse nodes, and the proximity radius
- `calibration_path` — where the computed homography is persisted

## Notable design decisions

`docs/DECISIONS.md` is a running log of every non-obvious call made without
stopping to ask, in the order they were made, each with its reasoning. Highlights:

- **Complete GTFS bundle**, not the smaller per-mode "For Realtime" subset — one request instead of seven, and the bounding-box filter already handles the size concern.
- **SQLite** for the filtered static cache — no extra dependency, indexed lookups.
- **Compiled the TfNSW realtime proto standalone** rather than using the `gtfs-realtime-bindings` package, because the fetched extension file redefines the base messages inline rather than importing Google's official proto.
- **Geometric map matching resolves the Sydney Trains/Metro/Light-Rail id-mismatch quirk** — confirmed live, not just theorised.
- **No basemap image** in the renderer — the route-corridor layer alone gives enough spatial reference.
- **Calibration uses a simple equirectangular projection**, not the UTM used for map matching, specifically so the browser (no pyproj available) can reproduce the exact same transform in plain JavaScript.
- **Two real bugs caught by live testing**, not code review: a blocking `alert()` that froze the page, and — more seriously — a WebSocket broadcast loop with no per-client timeout that one frozen connection could hang for every viewer, permanently.

## Current status

All six phases from the original design brief are built, tested (68 automated
tests), and live-verified against the real Transport for NSW API:

1. ✅ Static GTFS foundation
2. ✅ Headless realtime ingestion
3. ✅ Map matching and schedule adherence
4. ✅ Renderer on a flat screen
5. ✅ Calibration and projection tooling (tested with synthetic data)
6. ✅ Interchange pulses and the separate stats panel

**What's not done, and can't be until the hardware exists**: the actual physical
calibration session — aligning the renderer to a real 3D model under a real
projector. Everything the software side of that needs is built and tested.
