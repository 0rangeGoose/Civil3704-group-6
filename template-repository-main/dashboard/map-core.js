// Shared map rendering logic for both the full dashboard (app.js) and the
// bare map-only view for the projector (map.js) - one Leaflet setup, one
// congestion/vehicle renderer, so a fix here (e.g. the zoom-lock fix) only
// ever has to be made in one place. Both pages provide their own <div
// id="map"> and poll /snapshot on their own schedule/UI; this module owns
// everything about drawing onto the map itself.

// preferCanvas: the demand grid alone is ~3,400 rectangles - canvas draws
// shapes as pixels on one element instead of one DOM node per shape, which
// matters a lot at that count (SVG, Leaflet's default, was visibly
// stuttering/freezing the tab when that layer got switched on).
export const map = L.map("map", { zoomControl: false, preferCanvas: true, zoomSnap: 0.25 }).setView([-33.865, 151.204], 12);
L.control.zoom({ position: "bottomright" }).addTo(map);

// CARTO's basemap, not tile.openstreetmap.org directly - that server is the
// OSM project's own low-capacity demo endpoint, and its usage policy
// explicitly forbids exactly what we're doing (an app polled by multiple
// people). It enforces this by serving "Access blocked" tiles once it
// notices, which is what a teammate hit. CARTO's tiles are OSM data
// (attribution required either way) served from infrastructure meant for
// this kind of use. Keyed via CARTO_API_KEY (carto.com/basemaps/apikey) so
// requests aren't rate-limited as anonymous traffic.
const CARTO_API_KEY = "cb1_3oi7_1_bd8314ed27760b5a31f22e04";
L.tileLayer(`https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=${CARTO_API_KEY}`, {
  maxZoom: 20,
  subdomains: "abcd",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
}).addTo(map);

// Four independent layers, any combination can be on at once:
//   congestion - live fill+label on wherever a vehicle currently is, rebuilt
//                every ~5s poll (a few hundred cells at most)
//   demand     - the comprehensive, vehicle-independent wash (thousands of
//                cells, covering genuine coverage gaps too) - built ONCE
//                from /demand_grid and never redrawn, since demand data
//                doesn't change during a run
//   boam       - real per-STOP peak bus occupancy for one day (see
//                ingestion.boam_stops) - point markers at each stop's own
//                coordinate, not a rasterized grid like demand, since
//                there's no travel-zone polygon in the way here. Built ONCE
//                from /boam_stops, same lifecycle as demand.
//   routes     - each active route (bus, train, metro) drawn as its real street-shaped
//                line, coloured by that route's current average
//                congestion - an alternative to the grid that isn't cut up
//                by arbitrary square boundaries. Geometry is fetched once
//                per shape (from /route_shapes, a growing cache) and
//                reused; only the score is refreshed every poll.
// Demand/BOAM are added first/stay visually underneath so the live
// congestion cells read as being "on top of" the wash.
//
// There used to be a 5th layer here ("Importance" - real Opal tap on/off
// activity per travel zone, z-scored; see ingestion.opal_importance).
// Pulled out for now: none of the layers were showcasing real-time data,
// and that one was the least live of the three enrichment layers (a single
// 2020 snapshot week, vs. BOAM's one real day and demand's structural
// job-density data). The backend module/tests are untouched - importanceColor()
// below went with it since nothing else used it.
const demandLayer = L.layerGroup();
const boamLayer = L.layerGroup();
const congestionLayer = L.layerGroup().addTo(map); // on by default
const routesLayer = L.layerGroup();
let demandGridLoaded = false;
let boamStopsLoaded = false;
const shapeGeometry = new Map(); // shape_id -> [[lat, lon], ...]

// vehicle_id -> { marker, missed }. `missed` is a grace counter, not just
// on/off: a vehicle absent from a single poll (a transient feed gap, not
// actually gone - TfNSW's live feed drops individual vehicles from one
// cycle to the next fairly often) used to make its marker vanish and pop
// back in a cycle or two later. Now it fades instead and only gets removed
// after MARKER_GRACE_MISSES consecutive misses.
const markers = new Map();
const MARKER_GRACE_MISSES = 2;
let modelFitted = false;
let modelBounds = null;

// Extra zoom applied on top of the exact model-bounds fit (see setZoomBoost)
// - 0 by default (the exact 1:1 footprint fit used for projection
// calibration). Only the projector page, which disables all zoom
// interaction, sets this to pick a closer default view than the bare
// edge-to-edge fit.
let zoomBoost = 0;

export function setZoomBoost(delta) {
  zoomBoost = delta;
  if (modelBounds) {
    map.setView(modelBounds.getCenter(), map.getBoundsZoom(modelBounds, false) + zoomBoost);
    lockZoomToModel();
  }
}

let lastCongestion = { cells: [], hotspot_count: 0, cell_metres: 150 };
let lastServerCongestion = { cells: [], hotspot_count: 0, cell_metres: 150 }; // the server's real grid, kept separate from lastCongestion (which toggleMode may overwrite with a client-recomputed one)
let lastVehicles = []; // so toggleMode() can re-filter instantly, without waiting for the next poll
let lastRouteLines = {};

const LAYER_GROUPS = { congestion: congestionLayer, demand: demandLayer, boam: boamLayer, routes: routesLayer };
const layerVisible = { congestion: true, demand: false, boam: false, routes: false };

// Which vehicle modes actually get drawn - independent of the layer toggles
// above (those pick which analytics layer shows; this picks which vehicles
// show at all). All on by default. Kept as its own axis since a mode isn't
// a layer - it's a property of each vehicle marker, same modes MODE_LABEL/
// MODE_SHAPE_CLASS below already know about.
export const MODES = [
  { key: "buses", label: "Bus" },
  { key: "sydneytrains", label: "Train" },
  { key: "metro", label: "Metro" }
];
const modeVisible = { buses: true, sydneytrains: true, metro: true };

export function isModeVisible(mode) {
  return modeVisible[mode] !== false; // unknown modes default to visible
}

function allModesVisible() {
  return MODES.every((m) => isModeVisible(m.key));
}

// Mirrors config.CONGESTION_HOTSPOT_THRESHOLD/_MIN_VEHICLES on the backend -
// kept in sync manually (same as PERIODS further down). Only used for the
// client-recomputed grid below; the server's own grid already applies these.
const HOTSPOT_THRESHOLD = 0.6;
const HOTSPOT_MIN_VEHICLES = 2;

// Mirrors pipeline.gridmath's cell math exactly, so a client-recomputed
// cell lines up on the map with the server's own grid cells.
function gridSteps(refLat, cellMetres) {
  const latStep = cellMetres / 111320.0;
  const lonStep = cellMetres / (111320.0 * Math.cos((refLat * Math.PI) / 180));
  return [latStep, lonStep];
}

// The Congestion layer normally just shows the server's pre-computed grid
// (full fidelity: demand_score, real route lists). But that grid blends
// every mode into one score per cell - a metro reading and a bus reading
// sharing a block get averaged together with nothing on the map showing it
// happened. There's no way to un-blend an already-averaged cell, so once a
// mode is toggled off, the grid is rebuilt here instead, straight from the
// live per-vehicle list the frontend already has (which does carry mode).
// Traded away in this path: demand_score (needs the backend's TZP24/TZ21
// zone data, not available client-side) and, since scoring happens here
// rather than in pipeline.analytics, this only ever applies to the live
// "Now" view - historical periods keep using whatever the history database
// returns, same caveat as the Routes layer's mode filtering.
function recomputeCongestionGrid(vehicles, cellMetres) {
  if (!modelBounds) return { cells: [], hotspot_count: 0, cell_metres: cellMetres };
  const refLat = (modelBounds.getSouth() + modelBounds.getNorth()) / 2;
  const [latStep, lonStep] = gridSteps(refLat, cellMetres);
  const buckets = new Map();

  vehicles.forEach((v) => {
    if (typeof v.congestion_score !== "number") return;
    if (!isModeVisible(v.mode)) return;
    const row = Math.floor(v.latitude / latStep);
    const col = Math.floor(v.longitude / lonStep);
    const key = `${row}c${col}`;
    if (!buckets.has(key)) buckets.set(key, { row, col, members: [] });
    buckets.get(key).members.push(v);
  });

  let hotspotCount = 0;
  const cells = [];
  buckets.forEach(({ row, col, members }) => {
    const meanScore = members.reduce((sum, m) => sum + m.congestion_score, 0) / members.length;
    const minLat = row * latStep, maxLat = (row + 1) * latStep;
    const minLon = col * lonStep, maxLon = (col + 1) * lonStep;
    const isHotspot = meanScore >= HOTSPOT_THRESHOLD && members.length >= HOTSPOT_MIN_VEHICLES;
    if (isHotspot) hotspotCount++;
    const routes = [...new Set(members.map((m) => m.route_short_name || m.route_id || "?"))].sort();
    cells.push({
      cell_id: `r${row}c${col}`,
      bounds: { min_lat: minLat, max_lat: maxLat, min_lon: minLon, max_lon: maxLon },
      center: { lat: (minLat + maxLat) / 2, lon: (minLon + maxLon) / 2 },
      score: Math.round(meanScore * 1000) / 1000,
      vehicle_count: members.length,
      routes,
      hotspot: isHotspot,
      demand_score: null
    });
  });

  return { cells, hotspot_count: hotspotCount, cell_metres: cellMetres };
}

export function toggleMode(mode) {
  if (!(mode in modeVisible)) return true;
  modeVisible[mode] = !modeVisible[mode];
  // Re-apply immediately against the last-seen live data - otherwise a
  // hidden mode's markers/routes/grid linger until the next ~5s poll. Only
  // while "Now" is active: lastVehicles/lastRouteLines are stale live data
  // once a historical period is on screen (renderSnapshot stops touching
  // them then) - reapplying here would put live vehicles back on top of a
  // historical view.
  if (activePeriod === "now") {
    updateMarkers(lastVehicles);
    updateRouteLines(lastRouteLines);
    updateCongestion(allModesVisible() ? lastServerCongestion : recomputeCongestionGrid(lastVehicles, lastServerCongestion.cell_metres || 150));
  }
  return modeVisible[mode];
}

export function isLayerVisible(name) {
  return !!layerVisible[name];
}

// Flip a layer on/off; returns the new state. Demand fading in/out just
// shows/hides the pre-built static layer; congestion is already kept
// current every poll regardless of visibility, so toggling it is just an
// add/removeLayer.
export function toggleLayer(name) {
  const group = LAYER_GROUPS[name];
  if (!group) return false;
  layerVisible[name] = !layerVisible[name];
  if (layerVisible[name]) {
    if (!map.hasLayer(group)) map.addLayer(group);
  } else {
    map.removeLayer(group);
  }
  return layerVisible[name];
}

export function hasDemandData() {
  return demandGridLoaded;
}

// Fetch and draw the comprehensive demand grid once. Safe to call more than
// once (e.g. from both app.js and map.js, or on a retry) - only does the
// work the first time it actually succeeds.
export async function loadDemandGrid() {
  if (demandGridLoaded) return true;
  try {
    const response = await fetch("/demand_grid", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const grid = await response.json();
    const cells = grid.cells || [];
    cells.forEach((cell) => {
      const b = cell.bounds;
      if (!b) return;
      const value = cell.demand_score;
      const hasValue = typeof value === "number";
      const color = hasValue ? congestionColor(value) : "#9aa39d";
      // No permanent tooltip here (unlike the sparse congestion cells):
      // at ~3,400 cells, a permanent label on every single one is
      // both unreadable (solid text) and the actual cause of a real
      // freeze - Leaflet has to create and continuously reposition one DOM
      // element per label the instant this layer is shown. Colour alone
      // carries the value; click a block for the exact number.
      L.rectangle([[b.min_lat, b.min_lon], [b.max_lat, b.max_lon]], {
        stroke: false,
        fillColor: color,
        fillOpacity: hasValue ? 0.15 + Math.min(0.4, value * 0.4) : 0.04
      })
        .bindPopup(
          `<strong>Block ${cell.cell_id}</strong><br>` +
          `Demand: ${hasValue ? `${Math.round(value * 100)}%` : "no data"}<br>` +
          `~${grid.cell_metres || 150} m square<br>` +
          `<span class="muted">Independent of current vehicle positions - includes coverage gaps.</span>`
        )
        .addTo(demandLayer);
    });
    demandGridLoaded = cells.length > 0;
    // In case the Demand toggle was already flipped on before this finished
    // loading (unlikely given how fast the fetch is, but cheap to guard).
    if (demandGridLoaded && layerVisible.demand && !map.hasLayer(demandLayer)) {
      map.addLayer(demandLayer);
    }
    return demandGridLoaded;
  } catch (error) {
    console.warn("Could not load /demand_grid:", error.message);
    return false;
  }
}

export function hasBoamData() {
  return boamStopsLoaded;
}

// Fetch and draw the BOAM peak-occupancy stop markers once. Unlike
// demand (a rasterized grid), every stop already has its own exact
// coordinate, so these are plain circle markers, not rectangles - coloured
// with the same sequential congestionColor ramp congestion uses, since
// "how crowded has this stop gotten" is a magnitude, not a signed
// deviation from an average.
export async function loadBoamStops() {
  if (boamStopsLoaded) return true;
  try {
    const response = await fetch("/boam_stops", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const stops = data.stops || [];
    stops.forEach((stop) => {
      const color = congestionColor(stop.peak_rank / 5);
      L.circleMarker([stop.lat, stop.lon], {
        radius: 5,
        weight: 1,
        color: "#fff",
        fillColor: color,
        fillOpacity: 0.85
      })
        .bindPopup(
          `<strong>${stop.description || stop.stop_id}</strong><br>` +
          `${stop.suburb || ""}<br>` +
          `Peak occupancy: ${stop.peak_label}% of capacity<br>` +
          `${stop.visit_count} bus visit${stop.visit_count === 1 ? "" : "s"} logged that day<br>` +
          `<span class="muted">Worst single reading that day, not an average - see the Importance layer for zone-level activity.</span>`
        )
        .addTo(boamLayer);
    });
    boamStopsLoaded = stops.length > 0;
    if (boamStopsLoaded && layerVisible.boam && !map.hasLayer(boamLayer)) {
      map.addLayer(boamLayer);
    }
    return boamStopsLoaded;
  } catch (error) {
    console.warn("Could not load /boam_stops:", error.message);
    return false;
  }
}

// Fetch newly-seen route geometry. Safe/cheap to call repeatedly (e.g. on
// an interval) - only adds shapes not already cached, since geometry never
// changes once a shape has been seen. New routes can appear over time
// as service patterns shift through the day, so unlike loadDemandGrid()
// this one is meant to be re-polled, just far less often than /snapshot.
export async function loadRouteShapes() {
  try {
    const response = await fetch("/route_shapes", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const shapes = await response.json();
    Object.entries(shapes).forEach(([shapeId, info]) => {
      if (!shapeGeometry.has(shapeId) && info.points && info.points.length >= 2) {
        shapeGeometry.set(shapeId, info.points);
      }
    });
  } catch (error) {
    console.warn("Could not load /route_shapes:", error.message);
  }
}

export function updateRouteLines(routeLines) {
  lastRouteLines = routeLines || {};
  routesLayer.clearLayers();
  Object.entries(routeLines || {}).forEach(([shapeId, info]) => {
    // info.mode is absent on historical (time-period) route lines - the
    // history database doesn't store it yet, so those always show
    // regardless of the mode toggles rather than being wrongly hidden.
    if (info.mode && !isModeVisible(info.mode)) return;
    const points = shapeGeometry.get(shapeId);
    if (!points) return; // geometry not fetched yet - appears once loadRouteShapes catches up
    const color = congestionColor(info.score);
    L.polyline(points, { color, weight: 4, opacity: 0.85 })
      .bindPopup(
        `<strong>${info.route_short_name || info.route_id || "Route"}</strong><br>` +
        `Congestion: ${Math.round(info.score * 100)}%<br>` +
        `${info.vehicle_count} vehicle${info.vehicle_count === 1 ? "" : "s"} on this line`
      )
      .addTo(routesLayer);
  });
}

// The zoom level that makes `modelBounds` exactly fill the map panel at its
// CURRENT on-screen size. Recomputed (not cached) so it stays correct if the
// panel's pixel size changes - a stale value was letting the "-" zoom
// control zoom out past the footprint.
function lockZoomToModel() {
  if (!modelBounds) return;
  const fitZoom = map.getBoundsZoom(modelBounds, false) + zoomBoost;
  map.setMinZoom(fitZoom);
  if (map.getZoom() < fitZoom) map.setZoom(fitZoom);
}
window.addEventListener("load", lockZoomToModel);
window.addEventListener("resize", lockZoomToModel);

// Frame the map on the physical scale-model footprint (sent in the snapshot
// as model_bounds), mask out everything beyond it, and lock panning/zooming
// so only the model's actual coverage area is ever visible.
export function applyModelBounds(b) {
  if (!b || modelFitted) return;
  modelBounds = L.latLngBounds([[b.min_lat, b.min_lon], [b.max_lat, b.max_lon]]);

  // Dim/hide everything outside the model footprint: one giant rectangle
  // covering the whole map, with the model bounds cut out as a hole.
  const world = [[-89, -180], [-89, 180], [89, 180], [89, -180]];
  const hole = [
    [b.min_lat, b.min_lon], [b.min_lat, b.max_lon],
    [b.max_lat, b.max_lon], [b.max_lat, b.min_lon]
  ];
  L.polygon([world, hole], {
    stroke: false,
    fill: true,
    fillColor: "#0b0f0e",
    fillOpacity: 0.55,
    interactive: false
  }).addTo(map);

  L.rectangle(modelBounds, {
    color: "#17211f",
    weight: 2,
    dashArray: "6 4",
    fill: false,
    interactive: false
  }).addTo(map).bindTooltip("Scale model footprint", { sticky: true });

  map.setMaxBounds(modelBounds);
  lockZoomToModel();
  // No padding: the map panel is CSS-locked to the model's own aspect
  // ratio (2:1, matching the physical scale model), so fitting with zero
  // padding makes the footprint fill the panel edge-to-edge on both axes
  // (before any zoomBoost is applied on top, for pages that set one).
  map.setView(modelBounds.getCenter(), map.getBoundsZoom(modelBounds, false) + zoomBoost);
  modelFitted = true;
}

export function statusFor(vehicle) {
  if (vehicle.cancelled) return ["cancelled", "Cancelled"];
  if (vehicle.delay_seconds == null) return ["unknown", "Unknown delay"];
  if (vehicle.delay_seconds <= 60) return ["on-time", "On time"];
  if (vehicle.delay_seconds <= 300) return ["late", "Late"];
  return ["very-late", "Very late"];
}

// Congestion score (0-1) -> colour ramp: clear teal -> amber -> red.
export function congestionColor(value) {
  if (value >= 0.6) return "#cf5445";
  if (value >= 0.4) return "#e8722c";
  if (value >= 0.25) return "#e8a72c";
  if (value >= 0.12) return "#b6c34a";
  return "#087f74";
}

export function formatTime(seconds) {
  if (!seconds) return "No update";
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function updateCongestion(congestion) {
  lastCongestion = congestion;
  congestionLayer.clearLayers();
  const cells = (congestion && congestion.cells) || [];

  cells.forEach((cell) => {
    const b = cell.bounds;
    if (!b) return;
    const value = cell.score;
    const hasValue = typeof value === "number";
    const color = hasValue ? congestionColor(value) : "#9aa39d";

    const rect = L.rectangle(
      [[b.min_lat, b.min_lon], [b.max_lat, b.max_lon]],
      {
        color,
        weight: hasValue && cell.hotspot ? 2.5 : 1,
        dashArray: hasValue && cell.hotspot ? null : "3 4",
        fillColor: color,
        fillOpacity: hasValue ? 0.15 + Math.min(0.4, value * 0.4) : 0.04
      }
    );
    rect.bindTooltip(hasValue ? `${Math.round(value * 100)}%` : "no data", {
      permanent: true,
      direction: "center",
      className: "cell-label"
    });
    const demandLine = cell.demand_score == null
      ? "Demand: no data loaded"
      : `Demand: ${(cell.demand_score * 100).toFixed(0)}%`;
    rect.bindPopup(
      `<strong>Block ${cell.cell_id}</strong>${cell.hotspot ? ' <span class="pill">hotspot</span>' : ""}<br>` +
      `Congestion: ${(cell.score * 100).toFixed(0)}%<br>` +
      `${demandLine}<br>` +
      `Holding: ${cell.vehicle_count} vehicle${cell.vehicle_count === 1 ? "" : "s"}<br>` +
      `Routes: ${cell.routes && cell.routes.length ? cell.routes.join(", ") : "n/a"}<br>` +
      `~${congestion.cell_metres || 300} m square`
    );
    rect.addTo(congestionLayer);
  });
}

// Mode -> marker shape, kept independent of the status colour so the two
// signals (what it is / how it's running) can be read separately at a
// glance. Anything outside these three (nswtrains, ferries, lightrail) is
// never in ACTIVE_MODES today, but falls back to the plain circle rather
// than disappearing if that changes.
const MODE_SHAPE_CLASS = { buses: "mode-bus", sydneytrains: "mode-train", metro: "mode-metro" };
const MODE_LABEL = { buses: "Bus", sydneytrains: "Train", metro: "Metro" };

// Human-readable form of normalise.OCCUPANCY_LABELS' enum names.
const OCCUPANCY_LABEL = {
  EMPTY: "Empty",
  MANY_SEATS_AVAILABLE: "Many seats available",
  FEW_SEATS_AVAILABLE: "Few seats available",
  STANDING_ROOM_ONLY: "Standing room only",
  CRUSHED_STANDING_ROOM_ONLY: "Crushed standing room only",
  FULL: "Full",
  NOT_ACCEPTING_PASSENGERS: "Not accepting passengers",
  NOT_BOARDABLE: "Not boardable",
};

export function updateMarkers(vehicles) {
  lastVehicles = vehicles;
  const active = new Set();
  vehicles.forEach((vehicle) => {
    if (typeof vehicle.latitude !== "number" || typeof vehicle.longitude !== "number") return;
    if (!isModeVisible(vehicle.mode)) return; // marker stays out of `active`, so the cleanup pass below removes it if it was showing
    active.add(vehicle.vehicle_id);
    const [status, label] = statusFor(vehicle);
    const color = { "on-time": "#087f74", late: "#e8a72c", "very-late": "#cf5445", unknown: "#2874a6", cancelled: "#63716d" }[status];
    const modeClass = MODE_SHAPE_CLASS[vehicle.mode] || "mode-bus";
    const icon = L.divIcon({ className: "vehicle-icon", html: `<div class="vehicle-marker ${modeClass}" style="background:${color}"></div>`, iconSize: [16, 16], iconAnchor: [8, 8] });
    const congestionPct = vehicle.congestion_score == null ? "n/a" : `${Math.round(vehicle.congestion_score * 100)}%`;
    const modeLabel = MODE_LABEL[vehicle.mode] || vehicle.mode;
    // The three raw signals behind the blended score above - not just the
    // final number, so "why is this 80%?" has an answer on click. Any
    // signal can be missing independently (see vehicle_congestion_score on
    // the backend) - each line says so plainly rather than going blank.
    const speedLine = typeof vehicle.speed === "number"
      ? `${Math.round(vehicle.speed * 3.6)} km/h`
      : "unavailable";
    const latenessLine = vehicle.delay_seconds == null
      ? "unavailable"
      : vehicle.delay_seconds <= 0
        ? `${Math.abs(vehicle.delay_seconds)}s early`
        : `${vehicle.delay_seconds}s late`;
    const bunchingLine = vehicle.bunching_score == null
      ? "no nearby vehicle on this route"
      : `${Math.round(vehicle.bunching_score * 100)}%`;
    // Live occupancy, straight from the feed's own occupancy_status - not
    // derived like the three lines above. OCCUPANCY_LABEL covers every
    // value VehicleRecord can carry (see normalise.OCCUPANCY_LABELS on the
    // backend); "unavailable" means the feed left it unset for this vehicle.
    const occupancyLine = OCCUPANCY_LABEL[vehicle.occupancy] || "unavailable";
    const popup =
      `<strong>${vehicle.route_short_name || vehicle.route_id || "Unassigned route"}</strong><br>` +
      `${modeLabel} / ${label}<br>` +
      `Speed: ${speedLine}<br>` +
      `Lateness: ${latenessLine}<br>` +
      `Bunching: ${bunchingLine}<br>` +
      `Occupancy: ${occupancyLine}<br>` +
      `Congestion: ${congestionPct}`;
    const entry = markers.get(vehicle.vehicle_id);
    if (entry) {
      entry.marker.setLatLng([vehicle.latitude, vehicle.longitude]).setIcon(icon).setPopupContent(popup);
      entry.marker.setOpacity(1); // back from a missed cycle, if it was faded
      entry.missed = 0;
    } else {
      const marker = L.marker([vehicle.latitude, vehicle.longitude], { icon }).addTo(map).bindPopup(popup);
      markers.set(vehicle.vehicle_id, { marker, missed: 0 });
    }
  });
  markers.forEach((entry, id) => {
    if (active.has(id)) return;
    entry.missed += 1;
    if (entry.missed > MARKER_GRACE_MISSES) {
      map.removeLayer(entry.marker);
      markers.delete(id);
    } else {
      entry.marker.setOpacity(0.4); // still on screen, but flagged as unconfirmed this cycle
    }
  });
}

// Named time periods (see config.TIME_PERIODS on the backend - kept in sync
// manually since these are just fixed real-world period names). "now" means
// "show the live poll", same as before this feature existed.
export const PERIODS = [
  { key: "now", label: "Now" },
  { key: "am_peak", label: "AM Peak" },
  { key: "midday", label: "Midday" },
  { key: "pm_peak", label: "PM Peak" },
  { key: "evening", label: "Evening" }
];
let activePeriod = "now";

export function getActivePeriod() {
  return activePeriod;
}

// Switch between live data and a historical hour-of-day average. Reuses
// updateCongestion()/updateRouteLines() directly - a historical period is
// just a different source of the exact same {cells, route_lines} shape the
// live snapshot already produces, so there's no second rendering path to
// maintain. Live vehicle markers are cleared while viewing history: current
// vehicle positions overlaid on a past-hours average would be misleading.
export async function setPeriod(periodKey) {
  activePeriod = periodKey;
  if (periodKey === "now") {
    return null; // the next live poll repaints naturally
  }
  try {
    const response = await fetch(`/history?period=${encodeURIComponent(periodKey)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    updateCongestion(data.congestion || { cells: [], hotspot_count: 0 });
    updateRouteLines(data.route_lines || {});
    markers.forEach(({ marker }) => map.removeLayer(marker));
    markers.clear();
    return data;
  } catch (error) {
    console.warn("Could not load /history:", error.message);
    return null;
  }
}

// Does the three map-only updates (bounds/blocks/vehicles) both pages need
// on every poll. The full dashboard additionally updates its own metric
// tiles/table from the same snapshot; this doesn't touch any of that.
export function renderSnapshot(snapshot) {
  applyModelBounds(snapshot.model_bounds); // safe to always run - only takes effect once
  if (activePeriod !== "now") return; // a historical period is on screen - don't let the live poll overwrite it
  lastServerCongestion = snapshot.congestion || lastServerCongestion;
  const vehicles = snapshot.vehicles || [];
  updateCongestion(allModesVisible() ? lastServerCongestion : recomputeCongestionGrid(vehicles, lastServerCongestion.cell_metres || 150));
  updateRouteLines(snapshot.route_lines);
  updateMarkers(vehicles);
}

export async function fetchSnapshot() {
  const response = await fetch("/snapshot", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
