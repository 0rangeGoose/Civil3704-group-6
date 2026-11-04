# Pulse CBD: context handoff (as of 4 Nov 2026)

Paste or upload this file at the start of a new chat. It covers everything worked out so far in Claude Code.

## Who and what
- I'm Anthony (GitHub `0rangeGoose`), in the CIVL3704 group "Pulse CBD". Yoseph is a teammate (team roles still need confirming).
- The system projects live Sydney transit onto a physical model of the CBD. A tablet controller drives the projector view.
- Active code: `engine/` (Python, aiohttp server), `renderer/` (`index.html` projector, `controller.html` tablet), `tests/`. Branch `integration`; main branch `main`.
- Run with `python scripts/run_publish.py`. 175 tests pass (`pytest`, no network needed).
- Showcase or demo: Week 13, around 6 Nov 2026. Deliverables: live demo, deployed system, code walkthrough, docs, 10 minutes of questions. Marking criteria: functionality, physical integration, analytics, code and docs, innovation.
- I wrote 49 of the 73 commits (counted across all branches with `git log --all`).

## Pipeline
TfNSW GTFS-Realtime every 15 s → decode → join to the static timetable (GTFS SQLite) → snap to route shapes → classify → server → projector and tablet over WebSocket.
- VehiclePosition gives position, bearing and occupancy. TripUpdate gives StopTimeUpdates (stop_id, delay, schedule_relationship, time).
- About 310 live vehicles across 7 modes in the CBD box.
- Design rules (docs/DECISIONS.md): no basemap (the model is the map); never blend lateness, traffic and crowding (pressure is the one deliberate blend); 200 m street sections instead of grid squares.

## Live view
- **Arrows** for every mode. Heading comes from the feed bearing, else from the trip's own route shape (`map_match.heading_at`, only for trips matched to their own shape, not the nearest-shape fallback), else from movement (at least 15 m). Sydney Trains send no bearing, so they use the route shape (39 of 42 trains get a heading).
- **Three colour views**: Schedule (per-mode TfNSW on-time thresholds), Traffic (TfNSW congestion level), Crowding (occupancy; middle carriage for trains).
- **Hover** shows the exact lateness ("3 min 20 s late"); the server sends `dev` (schedule deviation) and `b` (bearing) per vehicle.
- **Replay** of recordings (for example, the PM peak at 25×).
- **BOAM layers** (`engine/demand/boam.py`): typical weekday bus load and "where buses lose time" (delay gained per 100 m between consecutive stops). The periods AM 7–9 and PM 16:30–18:30 are our team's choice, not BOAM's own. Opal fare peaks are Mon–Thu 6:30–10 and 15–19. BOAM "actual" times have been GTFS-R predictions since March 2026, so BOAM is not fully independent of the live feed.
- **Pressure layer**: 5 percentile-ranked inputs (congestion, crowding, service_gap, opal, boam_load) with adjustable weights.

## Stop history (my main analytics feature)
- `engine/history/stop_tracker.py` is edge-triggered: when a trip's next_stop_id changes, it has just passed the old stop. The last reading taken before the change is logged to SQLite (`data/history/stop_visits.sqlite`).
- `store.stop_summaries()` gives, per stop: visits, median and mean delay, crowded share (occupancy level ≥ 1), and delay when seated versus crowded (at least 5 visits per split).
- Renderer: the "Stop history" toggle (key `o`), Median/Average switch, fixed colour bands (delay 60/120/240 s; crowded share 0.1/0.25/0.5), at least 20 visits before a stop is coloured. Endpoint `/stop_history` (cached 60 s).
- Data so far: 601,722 visits, 29 Oct – 4 Nov; 1,311 stops coloured.
- **Finding:** at 610 of 662 stops (92%), crowded vehicles arrived later than seated ones. This is a correlation, not proof of cause.
- A big gap between median and average marks an unreliable stop (rare large delays).
- Why the CBD looks greener: vehicles inherit delay toward the ends of routes, and the CBD has more rail. Stop colour shows **inherited** delay, not where the delay is caused.
- Doubled stops are real: opposite sides of the street, separate platforms.
- Skipped stops: about 4% of passes miss a stop and about 2% go backwards. SKIPPED and NO_DATA stop updates are ignored.
- Occupancy change between stops can only show the net direction of boarding and alighting, and 94.6% of stop-to-stop changes show no change in level. Not enough for a boarding/alighting view.

## Data quality fixes (good "sophistication" story)
- **Placeholder zeros:** a whole trip reporting exactly 0 s means "no live prediction", not on time. This was 68% of Sydney Trains readings. Now shown as unknown. 4,522 old zeros in the DB were set to NULL.
- **Full-trip feeds:** Metro, both light rail lines, ferries and NSW TrainLink list stops already passed. Fixed by comparing the feed header timestamp with each stop's predicted time (`_already_passed` in `engine/normalise/trip_updates.py`). 762 mis-attributed visits were deleted.
- **Glitches:** valid delay range is −30 min to +3 h (`DELAY_VALID_RANGE_S`); outside it the reading is treated as missing (for example, trams 41 h late). Per-mode Tukey far-out fences (Q1/Q3 ± 3×IQR) were used to choose these cut-offs.
- On-time definitions: Sydney Trains within 5 min; intercity within 6 min; buses from 59 s early to 5:59 late (contract figure, from a secondary source).
- Literature: Aemmer et al. (2022) treat segment delay as the change in schedule deviation. This is the basis for a future "delay gained per section" layer.
- Backup taken before the cleanup: `data/history/stop_visits.before-zero-cleanup.sqlite`.

## Route focus
- Tablet section "Single out routes": type a route short name (datalist), then Show or Show all; up to 8 routes shown as chips.
- Each route gets its own colour from an 8-colour, colour-blind-safe palette (not the official route colours). Colour slots are assigned by the server in `route_slots` and stay put when another route is removed.
- Other vehicles fade to 12% opacity. Stop layers only show the focused routes' stops. When both stop layers are off, the route is just a line (no stop rings). Route arrows were tried and then removed.
- Endpoints `/routes` and `/route?name=` (`engine/publish/route_focus.py`).

## Keyboard (projector)
`s` stops, `o` stop history, `l` and `k` other layers (check before the demo).

## Documents
- Individual report draft (8–10 pages): https://claude.ai/artifact/AVfQqHBFS9nUdNLdZ84fYg. Placeholders still to fill: team roles, AI-use statement, calibration error, uptime.
- Demo script (10-minute version): https://claude.ai/artifact/GoxK4NGvgTgE2N8xfovu8H

## 20-minute presentation outline
1. Hook and problem (2 min): live view straight away
2. How it's built (3 min): pipeline, engine/renderer split, design rules
3. Physical integration (2 min): calibration homography, nudge, verify overlay
4. Reading the live view (2 min): arrows, size, 3 colour views
5. Time and history (3 min): PM-peak replay, BOAM, pressure
6. Stop analysis (3 min): route 333 with stop history, 92% finding, median vs average
7. Data integrity (2 min): placeholder zeros, full-trip feeds, glitches
8. Code walkthrough (2 min): `trip_updates.py`, `stop_tracker.py`, run the tests live
9. Who would use it, limits, next steps (1 min)

Speaker split: Yoseph on 2, 3 and 5; Anthony on 4, 6, 7 and 8. Keep the projection running throughout and have a recorded peak ready as a fallback.

## To do before the showcase
- Restore `body { cursor: none; }` in `renderer/index.html` (currently commented out).
- Update `README.md` (it still says there is no model or projector, and describes dots).
- Write the API docs, deployment guide and user guide.
- Record a backup video.
- Optionally delete the backup DB and the `backup/before-geojson-removal` branch.
- Not enough morning-peak data yet: 6,508 AM visits vs 98,713 PM. Keep recording.

## Ideas discussed, not built
Delay gained per street section; time-period selector for stop history (AM/PM peak, weekday/weekend); boarding vs alighting view; glitch filter on live vehicles; an early-arrival state.
