# Decisions Log

Judgment calls made without stopping to ask, per the project brief. Newest first.

## 2026-11-05 — Tablet legend: a stated definition for every colour

"What the colours mean" now gives each colour its cut-off, not just a name. Where the
engine sets a number, the tablet reads it rather than repeating it: `/config` now sends
`adherence_thresholds_seconds` and `thresholds_tti`, and `/areas?meta=1` sends `ranges`,
the lowest and highest jobs / Opal value in each fifth, per unit (computed per request
from the built layers, so no cache change). Stop-history bands are copied from
`renderer/index.html` with a comment, as the colours already were.

- **TfNSW traffic and crowding have no numbers to give**: the feed sends categories,
  and TfNSW doesn't publish the speeds or passenger counts behind them. The legend says
  so and names the feed value behind each colour, rather than inventing ranges; our own
  traffic measure is the one with stated cut-offs.
- **Streets** state the rounding rule (mean of levels: under 0.5 → 0, 0.5 to under 1.5
  → 1, …) and solid = 2+ vehicles, faint = 1.
- **Areas** in fifths show the real range for the unit on show (e.g. jobs per 150 m
  square: up to 14, 14–28, 28–54, 54–128, 128–11,761). The bottom fifth reads "up to",
  since partial-job shares round to 0. Pressure is only relative, and says so.
- Stop history had no legend on the tablet; it has one now. The street legend also now
  shows when an area layer hides the vehicle legend (before, a street layer matching the
  vehicle colouring went unexplained then).

## 2026-11-05 — Replays: every recorded day, picked from a dropdown

The tablet showed only the first 7 sessions from `/recordings` (latest + 3 days), so
anything before 1 Nov couldn't be chosen even though the server listed all 25 peaks over
14 days. Now **Live now** and **Latest 2 hours** stay as buttons, next to a **Replay mode**
button (same style) that opens the recorded peaks: a **day dropdown** (newest first) next
to **AM peak / PM peak**. Opening it changes nothing on the model; it stays open while a
recorded peak plays and closes when Live now or Latest is tapped. A dropdown rather
than more buttons because 25 buttons would fill the tablet's Time section and grow every
day of recording. Changing the day while a peak is playing jumps to the same peak on the
new day (or the one it has); while live it only picks the day, so nothing changes on the
model until a peak is tapped. A missing peak's button is greyed out, and the note under
"Recorded peak" flags peaks under 90% recorded (e.g. 31 Oct–1 Nov at 66%). No server change.

## 2026-11-04 — Street directions: one colour (default), side by side, or one way only; one line per stretch

The owner disliked colours overlapping (red over green, the lower one peeking out at the
edges). Of 1,882 overlapping pairs with different colours at the 8:00 am peak on 4 Nov,
**72% were a street's two directions** drawn on top of each other, 28% different routes'
pieces in the same direction (offset along the street, so pooled over slightly different
stretches). Both ways of showing directions were asked for, switchable
(`street_directions`; tablet: Street directions; keyboard **D**):

- **One colour** (default, `combined`): both directions pooled into one value per street
  (`*_all` from `engine/normalise/segments.py`; for BOAM, `load_all_bin`/`lost_all_bin`,
  trip-weighted over every section whose midpoint lies on the line, either way, from
  `engine/demand/boam.py`). Loses the inbound/outbound difference.
- **Side by side** (`split`): each direction its own 4 px line, shifted to its own
  left-hand side so the two just touch.
- **To city / From city** (`inbound`/`outbound`): one direction only. "Towards the city"
  means heading towards Town Hall (`CITY_CENTRE`); inside the CBD itself that is close to
  arbitrary.
- **One line per stretch, whatever the mode**: lines are laid down best evidence first
  (solid before faint, more vehicles or trips, then the worse level), and a later line only
  draws the parts of itself that no earlier line runs *beside* (within 5 px across, same
  way, or either way for One colour). "Beside", not "near": the next piece of the same
  route starts where the last ended and mustn't leave a gap. Built into one cached canvas
  when the data, layer, mode or layout changes, instead of redrawing every frame.
- Checked on the paused 4 Nov AM replay in all four modes and on the BOAM load layer.

## 2026-11-04 — Street colours: pooled along shared streets, faint when thin, no glow

A concern was raised that street colours misrepresent sparse areas, where one bus or
one stop stands for a long stretch of road. Measured before changing anything (decided
with the project owner):

- **Live layers** (Traffic, Traffic (our measure), Crowding): a reading only ever colours
  the 200 m piece it's in, so it is never stretched along the road. But at the 8:00 am
  peak on Wed 4 Nov, **~80% of coloured pieces rested on one vehicle, in the CBD as much
  as the suburbs** (79% within 1.5 km of Town Hall, 84% beyond 4 km; 89–94% at 8 pm).
  Pieces are per route shape, so a street served by ten routes had ten overlapping sets
  of pieces with about one bus each, and drawn worst-on-top it showed its worst single
  bus: a level worse than the pooled average at 28% of places where pieces overlap.
- **BOAM layers**: one value per stop-to-stop section (median 278 m). 76 sections over
  800 m (express routes on motorways and bridges, up to 4.9 km) are 5% of sections but
  24% of the coloured length.

Changes:

1. **Pooled along shared streets** (`engine/normalise/segments.py`): each piece's value
   pools every vehicle on pieces of the same street heading the same way: midpoints
   within 100 m along and 25 m across (parallel CBD streets are ~100 m apart), headings
   within 30°, checked along both pieces so the result doesn't depend on order. The two
   directions stay separate (inbound and outbound traffic differ); the worse is still
   drawn on top. The same-street relation is worked out once per piece (geometry never
   changes): ~110 ms once, then ~40 ms per snapshot at peak. At the same 8:00 peak, pieces
   resting on one bus fell to **11% in the CBD, 20% at 1.5–4 km, 32% beyond 4 km**.
2. **Faint when thin**: each layer's value carries how many vehicles it rests on
   (`congestion_n`, `crowding_n`, `derived_n`); pieces with fewer than 2 are drawn at
   35% opacity under the solid ones. So faint streets now cluster where data really is
   thin, and a solid colour means at least two buses agree.
3. **BOAM "Where buses lose time" leaves out sections over 800 m**: an average over that
   distance can't show where the time goes. **"Typical bus load" keeps them**: nobody
   boards or alights between stops, so the load really is constant along a section.
4. **Legend** says what pooling and faint mean, and that long BOAM sections are left out.

Earlier the same day, at the owner's request: **street layers lost their glow** (a 9 px,
25% opacity halo under a 3.5 px line). Shared streets stacked one halo per route, so
busy streets glowed brighter at the same level. Now one solid 4.5 px line. The halo had
also given some tolerance to calibration drift, so check alignment on the model.

Removing the feature was considered and rejected: the coloured streets are the
projection's main statement about congestion and crowding, and these changes address
the flaws found.

## 2026-11-04 — Optional background map: Plain (default), Detailed, Full

Asked for by the project owner, for a richer picture on a laptop or flat screen. This
**partly reverses the 25 Sep "no basemap" decision**, so the default stays Plain: on the
model the streets are already there, and anything drawn would compete with them.

- **OpenStreetMap, downloaded once and stored offline** (`scripts/fetch_basemap.py`,
  `data/basemap/basemap.json`, ~3 MB, gitignored), chosen with the owner over live map
  tiles: no internet needed at the showcase, and it's drawn as lines and polygons through
  the same projector as everything else, so it lines up after calibration. Tiles would
  have needed warping to match the calibrated projection.
- **Detailed**: roads (major and minor), rail, water and parks, kept faint so data stays on
  top. Hidden while the verification overlay is on.
- **Full**: Detailed plus 753 labels: suburbs, water, rail stations and wharves, landmarks,
  parks over 4 ha, neighbourhoods and street names (angled along the road, repeated about
  every 1 km). Placed greedily in that order of importance, skipping any that would overlap
  one already placed or not fit wholly on the model. Drawn above the street colours, under
  the vehicles.
- Calls made while checking it: **road tunnels are left out** (drawing the Cross City or
  Harbour Tunnel on the surface misleads on a physical model); **one label per wharf**
  ("Circular Quay", not one per berth); **bus interchanges and railway junctions aren't
  labelled** (they crowded out stations and places); each water/park ring is filled
  separately (filled together, overlapping rings cancelled out and the harbour vanished).
- The public Overpass servers failed several times while downloading (502/504), so the
  script retries, falls back to a second server, and keeps the raw download for
  `--cached` rebuilds.
- **Untested on the model.** At projector resolution the smallest labels (10 px street
  names) may be too small to read; Full is likely more useful on a screen.

## 2026-11-03 — The server restarts itself (launchd agent)

The Mac shut down some time after 11:30 on Tue 3 Nov (cause not found in the logs) and
came back at 16:00. The server had been started by hand, so it stayed down, and the
scheduled 16:25 recording didn't start until 18:28: most of that PM peak was lost.

- `scripts/install_server_service.py` installs `com.pulsecbd.server`: starts the server at
  login, restarts it within 30 s whenever it exits (tested: killed at 18:33:15, serving
  again at 18:33:30), under `caffeinate -i`. Same pattern as the recorder's agent.
- It's a user agent, so after a restart it starts only once someone logs in.
- `kill` no longer stops the server for good; `--uninstall` does.

## 2026-10-30 — Merged the teammate's `integration` work (branch `w12merge`)

`w12merge` = `yosephW12` + 0rangeGoose's 29–30 Oct commits: stop-visit history, the
placeholder-zero and full-trip fixes to trip-update delays, route filter, directional
arrows, Windows support. Their reasoning is in their individual report (repo root);
the calls that change what everyone sees:

- **An exact 0 s delay on a whole trip counts as "no live prediction"**, not on time. About
  70% of Sydney Trains now show grey ("no schedule data") instead of green, and Metro,
  light rail, ferries and NSW TrainLink now read the next stop's delay rather than the
  trip origin's (always 0). On-time percentages on the Stats tab change accordingly.
- **Two conflicts, both additive**: `feed.reset()` keeps both the travel-time reset and the
  stop-visit tracker; the renderer polls segments for every live line layer, so "Traffic
  (our measure)" still loads with the new route-focus code.
- **Stop visits are recorded only while live** (`engine/publish/feed.py`). The tracker sat
  in the engine code that replays also run through, so every replay, and every loop of
  one, wrote the recorded visits into the history again, with nothing to stop duplicates.
  Checked: 30 s of a 50× replay added 0 visits; live adds them again afterwards.
- Known, not fixed: `/stop_history` rescans the whole table at most once a minute; at
  ~586k visits (3 Nov) the first request after a refresh took ~13 s. It runs off the
  event loop, so nothing else stalls, but it grows with the history.

## 2026-10-30 — Replay reads the larger file when two writers recorded the same hour

The Thu 29 Oct PM peak showed 50% coverage, but nothing was missing: the server polled
the whole peak (~900 requests every 10 min). At 17:45 its heartbeat went briefly stale,
the scheduled recorder took over for one cycle and wrote its own `.rec.` files for that
hour (1 bus payload), and replay always preferred `rec` over `serve`, so the full
17:00–18:00 hour was replaced by that single cycle.

- **Fix (decided with the project owner):** per hour, replay and the coverage count read
  whichever writer's file is **larger**; the old `rec` → `serve` → untagged order only
  breaks ties (`engine/sources/replay.py`, `_pick_writer`). Since the recorder only polls
  when the server isn't, its file is the bigger one only when the server was down.
- Chosen over deleting the stray files because the same thing had happened on 22 Oct
  22:00 and 23 Oct 05:00 UTC (outside the peak windows), and will happen again whenever
  the server stalls mid-peak.
- Result: Thu 29 Oct PM 50% → **99%**; no other session changed. 145 tests pass.

## 2026-10-30 — Step 6, final: setup kept, two threshold sets, street-level result

**Trying to improve agreement** (`scripts/experiment_congestion_collect.py` and
`_evaluate.py`): one replay of the peaks, 8 nights and 6 middays, scoring each baseline and
stretch length on the **Thu–Fri** peaks with thresholds fitted on **Mon–Wed**:

| Baseline | Stretch | Has a baseline | Fitted thresholds (TTI) | Kappa per reading | Kappa per 10 min | Spearman per street, whole peak |
| --- | --- | --- | --- | --- | --- | --- |
| night | 300 m | 62% | 2.66 / 2.98 / 3.32 | 0.009 | 0.015 | 0.275 |
| night | 600 m | 61% | 2.42 / 2.7 / 3.01 | 0.003 | 0.012 | 0.104 |
| night | 1000 m | 62% | 2.3 / 2.59 / 2.89 | -0.009 | 0.003 | 0.181 |
| midday | 300 m | 73% | 1.91 / 2.12 / 2.34 | -0.022 | -0.018 | 0.182 |
| midday | 600 m | 73% | 1.7 / 1.87 / 2.05 | -0.025 | -0.016 | 0.038 |
| midday | 1000 m | 74% | 1.62 / 1.8 / 2.01 | -0.023 | -0.013 | 0.156 |

No setup agrees with TfNSW beyond chance per reading or per 10 minutes. The only real
signal is at street level over a whole peak, strongest for the setup already used
(night, 300m). A midday baseline adds coverage but weakens it; longer stretches don't help.

**Decisions**:
- **Keep night baseline, 300m stretches.**
- **Two threshold sets.** The model keeps **1.5 / 2 / 3×** ("how much slower than at
  night", readable for visitors). The analysis uses **2.66 / 2.98 / 3.32**
  (`analysis_thresholds_tti`), fitted so our levels occur as often as TfNSW's
  (77% / 6% / 4% / 13%), which is the fair basis for comparing levels.
- **Report step 6 at both scales**, final run (9 weekday peaks, 190,251 stretches):
  per reading, exact 63% but weighted kappa 0.01 (the exact
  figure is mostly the shared 77% "smooth"); per street over a whole peak, Spearman
  **0.242** across 1,184 streets. Against BOAM time lost: 0.081.
  Conclusion: TfNSW's per-bus label and a measured speed index capture different things;
  they only line up, weakly, when averaged per street.

## 2026-10-30 — Step 6: our own congestion measure, and how it compares with TfNSW's

**Method changed from A to B (decided with the project owner).**

- **A, time lost against the timetable** (planned 25 Sep), was tried first and dropped. On
  the 28 Oct AM peak the median bus *gained* 3–5 s per 100m, even where TfNSW reported
  severe congestion: bus timetables are padded (buses run ahead and wait at timing
  points), so the measure mostly measured the padding. Including or excluding stop time,
  and 100/300/500m stretches, didn't fix it.
- **B, a travel-time index (TTI)** (`engine/normalise/travel_time.py`): a bus's speed over
  each 300m+ stretch, stops included, against the same street's **free-flow speed**, the
  85th-percentile speed at night (22:00–05:00) from our own recordings
  (`scripts/build_freeflow.py`; 4,712 segments with their own baseline, 217 route shapes as
  a fallback, from 7 nights). TTI 1.0 = as fast as at night. Levels: ≤1.5 smooth, ≤2 slow,
  ≤3 congested, >3 severe (**thresholds proposed, awaiting approval**). Needs no
  timetable, so it works for every bus placed on a route.
- Live: "Traffic (our measure)" street layer, same colours as TfNSW's Traffic so the two
  can be switched between. Streets with no night baseline stay grey.

**Results** (`scripts/analyse_congestion.py`, 9 weekday peaks 26–30 Oct,
190,251 bus stretches, 62% with a baseline):

| | Per reading | Per segment, 10 min |
| --- | --- | --- |
| Pairs compared | 97,488 | 83,634 |
| Exact level agreement | 35% | 36% |
| Within one level | 60% | 60% |
| Weighted kappa | 0.056 | 0.068 |

Median TTI by TfNSW level: Running smoothly 1.75, Stop and go 2.03, Congested 2.02, Severely congested 1.93.
Spearman TTI vs TfNSW level 0.087; vs BOAM time lost
-0.001 (2,198 segments).

**Agreement is barely above chance.** Streets TfNSW calls smooth are faster on our measure
than the rest, but TfNSW's three congested levels are indistinguishable by speed, and most
of our "congested/severe" readings are on streets TfNSW calls smooth. Likely reasons, to
discuss in the report rather than tune away:
1. **The night baseline has fewer passengers**: peak buses dwell longer at stops, so every
   street reads slower at peak (median TTI 1.75 even where TfNSW says smooth).
2. **300m single-bus readings are noisy**: one long dwell or red light dominates.
3. **TfNSW's label is opaque and per vehicle**: we don't know what it measures, and it may
   reflect conditions ahead of the bus rather than behind it.
4. **BOAM time lost is timetable-relative**, so it shares method A's padding problem,
   and its days are school holidays.

## 2026-10-30 — Timetable refreshed

The static timetable from 19 Aug ran out on 5 Nov, the day before the showcase. Replaced
with TfNSW's current Complete GTFS (calendar 30 Oct 2026 – 28 Jan 2027: 546 routes, 2,905
stops, 3,135 shapes in the footprint). All 324 live vehicles matched it on restart; the
service-gap reference day moved to Tue 3 Nov. The old cache is kept as
`data/static_cache/gtfs_cbd.2026-08-19.db`.

## 2026-10-30 — Replays can be paused and scrubbed

- **Pause / play** (tablet, or Space on the laptop) holds the replay where it is; the
  projection freezes (positions and fade-outs use the paused moment) and resumes without
  a jump.
- **Tap the timeline** (or ±5 min, or ←/→ for a minute) to jump anywhere in the window.
  A seek restarts the replay at the target after feeding the **10 recorded minutes before
  it** through unpaced, so street layers and trip updates are already filled when it
  shows. `seek` is an action, not stored state; choosing a different recording un-pauses.
- Live polling (and the server's own recording) stays paused for as long as a replay is
  on, paused or not: switch back to Live after a demo.

## 2026-10-26 — Polling adapts to TfNSW's daily quota

Left running all weekend, the server was refused (429) from 7:06pm Sunday until midnight
and recorded almost nothing. The log shows why: **59,964 requests** from midnight to the
first refusal, and acceptance again just after the next midnight, i.e. a quota of
~60,000 requests a day per key, resetting at midnight Sydney time. Polling all 15
endpoints every 15s costs ~86,000 a day, so any full day would lose its evening, and a
weekday's PM peak with it.

- **One shared budget per key** (`engine/sources/budget.py`, `data/api_usage.json` under
  a file lock): the server and the scheduled recorder add to the same daily count.
  Budget 57,000 (5% margin), configurable under `api_budget`.
- **Peaks always at full rate** (weekdays 6:55–9:05 and 16:25–18:35, matching the
  recording schedule). Outside them, what the rest of today's peaks will need is held
  back and the remainder is spread evenly until midnight: e.g. Monday 1:30am → one cycle
  every ~34s. Budget spent → one cycle every 600s, so the live view thins out instead of
  going dark.
- **No double polling in peaks**: the server touches a heartbeat file each cycle; the
  scheduled recorder idles while it's fresh (< 60s) and takes over if the server stops
  (the server clears it on shutdown and when switching to a replay). Checked: with the
  server up, a recorder run received 0 payloads.
- **Network errors back off at most 30s** (was up to 300s, the same as a 429), so a
  dropped Wi-Fi connection costs about half a minute after it returns, not five.

## 2026-10-23 — BOAM: typical bus load, time lost, and a pressure input

TfNSW's Bus Opal Assignment Model gives every bus trip at every stop: scheduled and actual
arrival, bus capacity, and an occupancy band (0–20, 21–40, … passengers). Used three ways,
as decided:

1. **Two street layers**, per stop-to-stop section, for the AM (7–9) or PM (4:30–6:30)
   weekday peak: **typical bus load** (band midpoint ÷ total capacity, leaving each stop)
   and **where buses lose time** (change in delay between consecutive stops, per 100m).
2. **Measured reference for step 6**: the same time-lost figures, exported with
   `scripts/export_boam.py` to `data/boam/sections.csv`.
3. **A fifth pressure input**, `boam_load` (typical load of the sections in each area for
   the chosen peak), weight 1 by default; areas without BOAM sections get the neutral rank.

Calls and caveats:

- **Storage**: day files are 200–370MB; `scripts/import_boam.py` keeps the footprint rows
  and 14 columns (~2.5MB a day, gzipped) and deletes the original once the extract is
  read back with the same row count. Five days: 1.56GB → 12MB.
- **Joins on stop id, not trip id.** BOAM trip ids don't match the timetable's; its stop
  ids match 100%. Section geometry is the timetable shape of a trip where the two stops
  are consecutive (5 of 1,430 sections had none and are straight lines).
- **Weekdays only** in the averages; the weekend files are kept but unused. The first
  weekdays imported (28–30 Sep 2026) are almost certainly **NSW school holidays**, so
  loads read low; the legend says so. Adding term-time days is just another import.
- **Load bands are absolute but narrow** (15/30/45/60% of capacity): averaged over a
  2-hour peak, section loads rarely pass 50%, so 20%-wide bands left almost every street
  in the bottom colour.
- **Time lost isn't fully independent of the live feed**: since March 2026 BOAM's "actual"
  arrival times are TfNSW's GTFS-Realtime predictions. Step 6 has to say so when it
  compares against TfNSW's live congestion labels.
- Load is assumed to be the load *departing* each stop; BOAM's documentation doesn't say.
- Checked visually in Chrome: both street layers, the AM/PM switch and the legend.

## 2026-10-23 — Step 5: combined pressure layer

As agreed on 25 Sep: inputs traffic, crowding, service gap and Opal 2016 (jobs left out,
it's already inside the service gap), each percentile-ranked across areas, equal weights
by default, adjustable from the team panel (0–3, 0 = leave out), score 0–1. Details in
`engine/demand/pressure.py`. Two calls made while testing it on live data:

- **A missing live reading counts as neutral (rank 0.5), not as "leave it out".** The
  first version re-weighted each area over only the inputs it had. Live, the top of the
  ranking was then entirely areas with *no* buses at that moment, scored on two static
  inputs alone, ahead of areas with real readings. With the neutral fill, 14 of the top
  20 areas have live readings, and every area is averaged over the same four inputs. The
  tablet legend says so; `/pressure` reports which inputs were actually measured.
- **Colours are fifths of the scores, not fixed 0.2 bands.** An average of four ranks
  bunches around 0.5, so fixed bands only ever used the middle three colours.
- Live inputs reach an area through the street segments whose midpoint is in it; the
  score updates every ~10s (faster during a time-lapse). It's relative to the footprint
  at that moment: "where is it worst right now", not an absolute measure.
- Checked visually in Chrome: the layer draws in five shades, the weight sliders work.

## 2026-10-23 — Step 4: jobs, service gap and Opal 2016 on zones and grid

Built in full, as decided (all three layers, on both units). Details in the docstring of
`engine/demand/areas.py`; the calls that matter for the report:

- **Static, computed once.** Places don't change during a run, so layers build at startup
  in the background (~5s) and are cached until an input changes. Server start isn't
  delayed; the tablet greys out area buttons until they're ready.
- **Units**: TZ21 travel zones (405 in the footprint, kept whole for their values and
  clipped to the footprint for drawing) and 150m grid cells in UTM (3,204, square on the
  model). Grid jobs are an area-weighted share of the zones a cell overlaps.
- **Service = scheduled departures within 200m, 7:00–9:00, on a reference Tuesday**
  (27 Oct 2026, from the static timetable). It counts each trip once per stop it serves
  in reach, so it measures how much service a worker there can reach, not distinct trips.
- **Service gap = percentile rank of jobs − percentile rank of departures** among units
  with jobs, so it's relative to the rest of the footprint, not an absolute standard.
  First results look right: RPA Hospital, Australian Technology Park, Garden Island and
  Chippendale come out most underserved.
- **Opal 2016**: stations (train/light rail/ferry) are placed by matching Opal's names
  to today's stop names (base name + "Station"/"Wharf"/"Light Rail", since Opal writes
  "Circular Quay, No. 3 Wharf" where GTFS has "Circular Quay, Wharf 3"). Bus taps are
  only given by **postcode**, so each postcode's bus taps are scaled by the share of its
  area inside the footprint and split evenly across today's bus stops in it, using ABS
  POA 2021 boundaries. Busiest: Wynyard, Central, Town Hall, as expected.
- **Colours**: 0–4 bins. Quintiles for jobs and Opal; fixed bands at ±0.15/±0.4 for the
  gap (blue = more service than jobs, orange = underserved). Vehicles turn white while an
  area layer is on, as planned.
- **Drawing is clipped to the footprint.** Route corridors used to extend past the model
  edge (they're exported with 15% padding), which on the real rig lands on the table.
  Checked visually in Chrome: zones, grid, Opal and the tablet legend all render.
- Data: TZ21/TZP24 taken from the team branch via Git LFS; Opal Release 2 and ABS POA
  downloaded. `scripts/prepare_demand_data.py` cuts the 109MB statewide zones and the
  postcode shapefile down to ~1MB each; `pyshp` is an optional `[prep]` dependency.

## 2026-10-23 — Step 3: replay and time-lapse

- **Replay goes through the same engine as live.** `engine/sources/replay.py` merges the
  recorded feeds for a window into one stream ordered by fetch time and paces it at
  `speed` x real time; the engine treats each payload's fetch time as "now"
  (`feed.clock`), so 10-minute windows, adherence windows and stale-vehicle pruning all
  run on recorded time. Switching source or looping bumps an `epoch` in every frame so
  pages clear what they're showing.
- **What can be replayed**: each AM peak (7:00–9:00) and PM peak (16:30–18:30) Sydney
  time with at least 10% of expected bus updates, plus "latest 2 hours recorded" (for
  testing before the first scheduled peak). Coverage counts real updates, not hour
  files: the old server's September recordings have a file for every hour but only 1–3
  bus updates per hour (it was being rate-limited), so they're hidden. The only usable
  older sessions are weekends (6 Sep, 22 Aug), fine for testing, not for the showcase.
- **Defaults**: live on startup; replays loop; 25x (a 2h peak in under 5 minutes), with
  1x / 10x / 25x / 50x on the tablet. Live polling, and the server's own recording,
  pause during a replay; the scheduled recorder is a separate process and isn't
  affected.
- **Map-matching speed-up (affects live too).** Profiling the replay showed the
  nearest-route fallback took ~14ms per vehicle (92% of CPU; the live server sat at
  ~85% CPU): almost every cached shape's bounding box covers the CBD, so the spatial
  index couldn't rule any out. The index now holds 32-point pieces of each shape.
  Dense recorded data now processes at ~96x real time (was ~11x), so 25x/50x replays
  keep up — measured 50.0x effective at 50x.
- On the projector, interpolation, fade-out and street-colour polling scale with the
  replay speed. Which recording is playing, its clock and progress show on the tablet
  only (and on the projection's status line if the team turns it on).

## 2026-10-23 — Step 2: TfNSW traffic and crowding on route segments

- **Straight from TfNSW's fields, no estimation yet.** `congestion_level` and
  `occupancy_status` become ordinal levels (`engine/normalise/levels.py`): traffic
  0–3 (running smoothly → severe), crowding 0–3 (seats → crowded/full, with EMPTY
  folded into "seats" and the three "no room" states into one, so the projection needs
  four colours, not seven).
- **Traffic counts buses only.** TfNSW also reports it for Metro, but a metro line
  isn't in road traffic; it would paint rail corridors as "congested".
- **Train crowding = the median carriage** of the 1007 `consist` extension when there's
  no vehicle-level value, so one packed front car doesn't make a whole train "full".
- **Route segments are 200m sections of each shape** by distance along it. A section
  shows the mean level over the last 10 minutes, each vehicle counted once at its
  latest reading. Several routes share most CBD streets, so sections overlap; they're
  drawn worst-on-top. Deduplicating to physical street segments would fix that
  properly but needs a street network we don't have; revisit if it reads badly on the
  model.
- **Dot colour follows a lens** (schedule / traffic / crowding) and **one street layer**
  (off / traffic / crowding) at a time, both from the tablet or **L**/**K**. Dots with
  no reading under the current lens are grey ("not reported").
- First live check (Fri 23 Oct, ~10:30am): 164 of 269 buses on the model had a
  traffic level, all buses and Metro had crowding, Sydney Trains 26 of 56 (via
  carriages), light rail and ferries none. 633 sections with data; mostly "running
  smoothly" and "seats available" at that hour.

## 2026-10-23 — Step 1 implementation calls

Step 1 of the showcase plan (below) is built: new footprint, projector view with no
UI, tablet controller with keyboard backup, footprint-trimmed scheduled recording.
Calls made along the way:

- **Display state lives on the server** (`engine/publish/control.py`), sent over the
  existing WebSocket. Pages send `{"control": patch}` and only change when the merged
  state comes back, so the tablet, the laptop keyboard and the projector never
  disagree. Only the nudge persists across restarts (`data/projection_nudge.json`);
  everything else starts in the safe default (no HUD, not calibrating).
- **Nudge = shift + uniform scale about the screen centre**, applied after whichever
  projector is active. Switched off while calibrating, since crosshairs are placed in
  raw screen pixels, and reset when a calibration is saved.
- **The server now listens on 0.0.0.0** (`server.host`) so the tablet can reach it.
  Anyone on the same network can open the controller, hence the hotspot rule. The team
  PIN is checked in the browser only; it keeps visitors out, nothing more.
- **`/stats` redirects to `/control#stats`**; `renderer/stats.html` is gone. The
  theme setting moved from browser localStorage to the server's control state.
- **Recording is trimmed to a 10%-padded footprint.** Trip updates are kept if their
  vehicle was in the footprint in the last two polls *or* they stop at a footprint
  stop (Sydney Trains/Metro trip ids don't match the static bundle, so the first rule
  alone would drop them). Alerts pass through. The ~14GB of unfiltered recordings from
  August is left in place, untouched.
- **Writer tag in recording filenames** (`.serve.` / `.rec.`) so the server and the
  scheduled recorder never append to the same gzip file at once. Hours are UTC.
- **Requests are spaced ≥0.3s apart.** Polling fired ~22 requests in a burst every 15s
  and TfNSW rejected a large share with 429 (only 12 payloads in 45s). Spaced out:
  60 payloads/minute, no 429s.
- **Only the combined `/v2/gtfs/alerts/all` feed is polled.** Each alerts payload
  replaced the whole alert summary, so polling the 7 per-mode feeds as well meant the
  stats panel only ever showed whichever arrived last (usually `regionbuses`). Also
  saves 7 requests per cycle.
- **Scheduled recording**: launchd agent, weekdays 06:55 and 16:25 for 130 minutes,
  under `caffeinate -i`. Waking a sleeping Mac needs root, so
  `scripts/install_recording_schedule.py` writes `scripts/wake_schedule.sh` (one
  `pmset schedule wake` per peak up to 6 Nov) to run once with sudo.
- The static cache was rebuilt for the new footprint from the 19 Aug timetable
  bundle; the old cache is kept as `gtfs_cbd.pre-footprint.db`. Server startup now
  takes ~13s (corridor export over the bigger cache).

## 2026-09-25 — Showcase plan: merging the team's direction into pulse-cbd

Agreed in a design review (grilling session) ahead of the **6 Nov 2026** showcase. Unlike
the entries below, these were decided with the project owner, not made unilaterally.

### Context

- The team's branches (`group-repo/mid-project-demo`, a superset of `test-pipline`) are a
  separate codebase under `template-repository-main/`: a Leaflet dashboard plus pipeline
  with congestion / demand / routes layers, historical periods, and a keyboard-only
  projector page. It shares no history with `pulse-cbd`'s `test` branch.
- Their overall direction (a planning tool: congestion, demand, peak comparison) is the
  right one, but the way they calculate congestion and demand is not. pulse-cbd stays the
  single codebase; their ideas are rebuilt here properly rather than merged as-is.
- Live feed check (2026-09-25, one snapshot): TfNSW already publishes per-vehicle
  `congestion_level` (buses: 977 of 1,542 populated; metro: all; Sydney Trains: always
  unknown) and `occupancy_status` (all buses and metro; trains mostly per carriage via the
  1007 `consist` extension). Neither codebase used `congestion_level`.
- Problems identified in the team's congestion score: it applies a bus free-flow speed to
  trains/metro; treats absolute (upstream-accumulated) delay as local congestion; counts
  dwell at stops/lights as slowness; scores scheduled high-frequency headways as bunching;
  averages whatever vehicles are in a grid cell at that instant.
- The team's "demand" layer is TZP24 place-of-work employment: static land use, not
  transit demand.
- Their `dashboard/map-core.js` commits a CARTO API key in plain text.

### Decisions

**Footprint.** Default `bounding_box` becomes the team's surveyed `MODEL_CORNERS`
(NE −33.835123, 151.275993; SW −33.894723, 151.132966; ~13.2 km E-W × 6.6 km N-S, ~2:1).
Stays in config so it can be changed.

**Projection.** The projector mirrors the laptop screen, so the projected view shows map
content only: no on-screen UI, no hint overlay, hidden cursor. No basemap (keeps the
existing no-basemap decision; the team's CARTO basemap is not adopted). The rig (model +
projector) stays set up between weekly sessions: each session starts with the `V`
verification overlay; use a nudge/scale adjustment for small drift and full crosshair
recalibration only when needed.

**Controls.** Both the team and visitors operate it. A dedicated tablet by the model,
on the laptop's own hotspot (not venue Wi-Fi, which may block device-to-device traffic),
opens a controller page served by pulse-cbd that relays commands to the projected view
over the existing WebSocket. Three tabs:
- *Visitor view* (big buttons): layer toggles, zones/grid toggle, dot-colour lens,
  live / AM replay / PM replay, a short legend.
- *Team panel* (PIN or long-press): combined-score weights, nudge/scale, start
  calibration, recording controls, system health (feed status, vehicle counts).
- *Stats*: the existing `/stats` dashboard folds in here and stops being a separate page.

Keyboard shortcuts stay on the laptop as a team backup.

**Congestion.** Both, in this order:
1. TfNSW's `congestion_level`, shown directly (buses + metro). Quick to ship and a
   fallback if (2) runs late.
2. A derived metric: **delay gained per road segment**. The change in a vehicle's delay
   between consecutive observations is attributed to the stretch of road it covered.
   Buses only, dwell at stops excluded.
Validated with an **agreement table** (derived levels vs TfNSW's five categories across
the recorded data), plus a short write-up of where and why they disagree.

**Demand.** Four layers, each toggleable on its own:
- *Live crowding*: occupancy from the feeds (vehicle-level, plus per-carriage for trains).
- *Jobs*: TZP24 employment, labelled "jobs", not "demand".
- *Service gap*: jobs vs scheduled service frequency per zone.
- *Opal 2016*: Opal Tap On/Tap Off Release 2 (Nov 2016 weekday), 15-min bins by
  location, labelled as pre-Metro and pre-CBD light rail.

**Spatial units.** Things that move along routes (congestion, crowding) are drawn on
route segments. Fixed-place layers get a toggle between **TZ21 travel zones** and the
**150 m grid**, to compare them visually and pick one later.

**Merging.** Layers can be stacked, capped at one area layer plus one line layer at a time
(more is unreadable when projected). There is also one **combined "pressure" layer**:
- inputs: congestion, crowding, service gap, Opal 2016 (jobs excluded, since it is
  already inside service gap)
- each input is percentile-ranked across zones (not min–max, so one extreme zone can't
  flatten the rest)
- equal weights by default, adjustable from the team panel
- output 0–1, higher = more pressure on the network

**Vehicle dots.** Colour shows adherence by default; the controller's lens switches it to
congestion or crowding; dots go neutral white while an area layer is on. Interchange
pulses stay on in every view.

**Replay.** Live by default, plus recorded AM and PM peaks chosen from the controller,
played either real-time or as a **time-lapse** (e.g. a 2-hour peak in ~5 minutes). The
time-lapse is the showcase default. It also covers the team's AM/PM-peak comparison idea
with real recordings instead of averaged history, and protects the demo if Wi-Fi or the
TfNSW API fails.

**Recording.** Showcase time of day is unknown, so record both peaks, **7–9 am and
4:30–6:30 pm, every weekday**, starting as soon as step 1 lands. Runs on the Mac:
`launchd` starts the recorder, `pmset` wakes the machine beforehand (needs `sudo` once;
Mac must be on power). Only vehicles inside the footprint are stored; keep the best 3–4
days.

**Code location.** Local only, in `~/pulse-cbd` on the local `integration` branch
(`test` stays a clean mirror of `group-repo/test`). Nothing is pushed.

### Build order

Each step is usable on the model on its own:

1. New footprint; projector view with no UI; tablet controller + keyboard backup;
   start scheduled recording.
2. TfNSW congestion + live crowding on route segments; dot colour follows the lens.
3. Replay and time-lapse from recordings.
4. Jobs, service gap and Opal 2016 on zones and grid.
5. Combined layer and weights.
6. Derived congestion metric and agreement table.
7. Polish from model sessions, report figures; **feature freeze ~30 Oct**.

## 2026-08-19 — Bug caught live: one stuck client could hang the broadcast loop for everyone

Discovered while live-testing calibration: after the `alert()` freeze (previous entry), a
brand new WebSocket connection stopped receiving any frames at all -- not just the
frozen tab, every client, permanently, confirmed by opening a fresh test socket that
received nothing. `_broadcast_loop` sent to each connected client sequentially with
`await ws.send_str(frame)` and no timeout; the frozen tab's connection likely stalled at
the transport level while its (blocked) JS wasn't draining incoming data, and since the
loop awaits each send in turn before moving to the next client or looping back to
`asyncio.sleep`, one stuck socket wedges the loop forever for every other client too.
Fixed by sending to all clients concurrently (`asyncio.gather`) with a 2s
`asyncio.wait_for` timeout per socket, dropping any that time out or error rather than
blocking the rest.

## 2026-08-19 — Bug caught live: blocking alert() in the calibration panel

Live-testing the calibration flow (drag crosshairs, enter lat/lon, save) hit
`alert("Calibration saved.")` on success, which froze the whole page -- browser
JS dialogs block the event loop, including the WebSocket message handler, so
the live vehicle feed would visibly stall every time someone saved a calibration.
The exact failure mode called out for browser automation generally, but just as
real for a person operating the actual projector setup. Replaced all three
`alert()` calls in `renderer/index.html` with a small non-blocking status line
inside the calibration panel (`#cal-message`) instead.

## 2026-08-19 — Default crosshair positions were spawning behind the calibration panel

Also caught live: the default top-right/bottom-right crosshairs (`defaultCalibrationPoints`)
landed at 90% of canvas width, which sits directly under the calibration panel itself
(`#calibration-panel`, ~260px wide, fixed top-right) -- draggable but invisible until
moved. Right-side defaults now clamp to `canvas.width - 300` so they spawn clear of it.

## 2026-08-19 — Calibration uses equirectangular-at-box-centre, not UTM 56S

The brief offers either "UTM zone 56S or a simple equirectangular projection
anchored at the box centre" for the metric plane the homography is computed in.
Map matching (Phase 3) already uses pyproj's UTM 56S -- but the homography has to be
*applied* every frame in the renderer, in plain JS, with no pyproj available. Rather
than hand-porting UTM's Transverse Mercator series expansion into JavaScript (and
risking it drifting out of sync with the Python side), `engine/publish/calibration.py`
uses the simple equirectangular option instead: `x = (lon - center_lon) * cos(center_lat) * 111320`,
`y = (lat - center_lat) * 111320`. Identical four-line formula in both Python and
`renderer/index.html`, easy to keep in sync, and accurate enough over a ~5km box.
`center_lat`/`center_lon` are stored in the calibration record itself so the exact
anchor used is always reproducible.

## 2026-08-19 — Phase 4 renderer: no basemap image, straight-line interpolation, aiohttp

Three scoping calls for the flat-screen renderer:

- **No basemap tile/image at all.** The brief says any Phase 4 basemap must come from
  an offline source or open tiles, not a paid/hosted service -- but also says the goal
  is just to "get the visual language right." The route-corridor layer (drawn from our
  own clipped shapes, `engine/publish/shapes_export.py`) already gives enough spatial
  reference to judge the vehicle layer, so skipped sourcing a basemap image entirely
  rather than finding an offline tileset. Revisit if the corridor layer alone turns out
  to be disorienting without street/coastline context.
- **Straight-line interpolation between the last two known positions, not
  distance-along-shape.** The brief's ideal is interpolating along the map-matched
  shape using the distance-along-shape delta. Implemented instead: linear lerp between
  the previous and current lat/lon in the renderer (`renderer/index.html`). At a 15s
  poll interval, consecutive real positions are close enough together that a shape
  segment between them is close to straight, so the visual difference should be
  small in the common case; it would show up as a vehicle briefly cutting a corner on
  a sharp curve. Simpler to build and calibrate first, per Phase 4's own stated goal;
  along-shape interpolation is a drop-in upgrade later since the record already
  carries `distance_along_shape_m`.
- **aiohttp for the publish server**, not a separate WS library + bare HTTP server --
  one lightweight dependency instead of two, and the brief's "avoid heavy frameworks"
  is squarely about the renderer (React/Deck.gl-scale), not the backend transport.

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
