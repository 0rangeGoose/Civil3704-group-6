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
export const map = L.map("map", { zoomControl: false, preferCanvas: true }).setView([-33.865, 151.204], 12);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

// Four independent layers, any combination can be on at once:
//   congestion - live fill+label on wherever a bus currently is, rebuilt
//                every ~5s poll (a few hundred cells at most)
//   demand     - the comprehensive, bus-independent wash (thousands of
//                cells, covering genuine coverage gaps too) - built ONCE
//                from /demand_grid and never redrawn, since demand data
//                doesn't change during a run
//   priority   - outline-only markers (no fill of their own) stamped over
//                whichever *congestion* cells currently qualify; rebuilt
//                alongside congestion since priority requires a live bus
//   routes     - each active bus route drawn as its real street-shaped
//                line, coloured by that route's current average
//                congestion - an alternative to the grid that isn't cut up
//                by arbitrary square boundaries. Geometry is fetched once
//                per shape (from /route_shapes, a growing cache) and
//                reused; only the score is refreshed every poll.
// Demand is added first/stays visually underneath so the live congestion
// cells and priority outlines read as being "on top of" the wash.
const demandLayer = L.layerGroup();
const congestionLayer = L.layerGroup().addTo(map); // on by default
const priorityLayer = L.layerGroup(); // off by default, matches layerVisible below
const routesLayer = L.layerGroup();
let demandGridLoaded = false;
const shapeGeometry = new Map(); // shape_id -> [[lat, lon], ...]

const markers = new Map();
let modelFitted = false;
let modelBounds = null;
let lastCongestion = { cells: [], hotspot_count: 0, priority_count: 0, cell_metres: 150 };

const LAYER_GROUPS = { congestion: congestionLayer, demand: demandLayer, priority: priorityLayer, routes: routesLayer };
const layerVisible = { congestion: true, demand: false, priority: false, routes: false };

export function isLayerVisible(name) {
  return !!layerVisible[name];
}

// Flip a layer on/off; returns the new state. Demand fading in/out just
// shows/hides the pre-built static layer; congestion/priority are already
// kept current every poll regardless of visibility, so toggling them is
// just an add/removeLayer.
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
      // No permanent tooltip here (unlike the sparse congestion/priority
      // cells): at ~3,400 cells, a permanent label on every single one is
      // both unreadable (solid text) and the actual cause of a real
      // freeze - Leaflet has to create and continuously reposition one DOM
      // element per label the instant this layer is shown. Colour alone
      // carries the value; click a block for the exact number.
      L.rectangle([[b.min_lat, b.min_lon], [b.max_lat, b.max_lon]], {
        color,
        weight: 1,
        dashArray: "3 4",
        fillColor: color,
        fillOpacity: hasValue ? 0.15 + Math.min(0.4, value * 0.4) : 0.04
      })
        .bindPopup(
          `<strong>Block ${cell.cell_id}</strong><br>` +
          `Demand: ${hasValue ? `${Math.round(value * 100)}%` : "no data"}<br>` +
          `~${grid.cell_metres || 150} m square<br>` +
          `<span class="muted">Independent of current bus positions - includes coverage gaps.</span>`
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

// Fetch newly-seen route geometry. Safe/cheap to call repeatedly (e.g. on
// an interval) - only adds shapes not already cached, since geometry never
// changes once a shape has been seen. New bus routes can appear over time
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
  routesLayer.clearLayers();
  Object.entries(routeLines || {}).forEach(([shapeId, info]) => {
    const points = shapeGeometry.get(shapeId);
    if (!points) return; // geometry not fetched yet - appears once loadRouteShapes catches up
    const color = congestionColor(info.score);
    L.polyline(points, { color, weight: 4, opacity: 0.85 })
      .bindPopup(
        `<strong>${info.route_short_name || info.route_id || "Route"}</strong><br>` +
        `Congestion: ${Math.round(info.score * 100)}%<br>` +
        `${info.vehicle_count} bus${info.vehicle_count === 1 ? "" : "es"} on this line`
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
  const fitZoom = map.getBoundsZoom(modelBounds, false);
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
  // padding makes the footprint fill the panel edge-to-edge on both axes.
  map.fitBounds(modelBounds, { padding: [0, 0] });
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
  priorityLayer.clearLayers();
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
      : `Demand: ${(cell.demand_score * 100).toFixed(0)}%${cell.priority ? ' <span class="pill" style="background:#5b3fd6">priority</span>' : ""}`;
    rect.bindPopup(
      `<strong>Block ${cell.cell_id}</strong>${cell.hotspot ? ' <span class="pill">hotspot</span>' : ""}<br>` +
      `Congestion: ${(cell.score * 100).toFixed(0)}%<br>` +
      `${demandLine}<br>` +
      `Holding: ${cell.vehicle_count} bus${cell.vehicle_count === 1 ? "" : "es"}<br>` +
      `Routes: ${cell.routes && cell.routes.length ? cell.routes.join(", ") : "n/a"}<br>` +
      `~${congestion.cell_metres || 300} m square`
    );
    rect.addTo(congestionLayer);

    // Priority (high congestion AND high demand) is a separate, independently
    // toggleable outline-only layer - not baked into the congestion fill -
    // so it can be shown/hidden regardless of whether Congestion itself is on.
    if (cell.priority) {
      L.rectangle([[b.min_lat, b.min_lon], [b.max_lat, b.max_lon]], {
        color: "#5b3fd6",
        weight: 3,
        fill: false,
        interactive: false
      }).addTo(priorityLayer);
    }
  });
}

export function updateMarkers(vehicles) {
  const active = new Set();
  vehicles.forEach((vehicle) => {
    if (typeof vehicle.latitude !== "number" || typeof vehicle.longitude !== "number") return;
    active.add(vehicle.vehicle_id);
    const [status, label] = statusFor(vehicle);
    const color = { "on-time": "#087f74", late: "#e8a72c", "very-late": "#cf5445", unknown: "#2874a6", cancelled: "#63716d" }[status];
    const icon = L.divIcon({ className: "vehicle-icon", html: `<div class="vehicle-marker" style="background:${color}"></div>`, iconSize: [16, 16], iconAnchor: [8, 8] });
    const congestionPct = vehicle.congestion_score == null ? "n/a" : `${Math.round(vehicle.congestion_score * 100)}%`;
    const bunchingNote = vehicle.bunching_score == null ? "" : ` (bunching ${Math.round(vehicle.bunching_score * 100)}%)`;
    const popup = `<strong>${vehicle.route_short_name || vehicle.route_id || "Unassigned route"}</strong><br>${vehicle.mode} / ${label}<br>${vehicle.delay_seconds == null ? "Delay unavailable" : `${vehicle.delay_seconds}s delay`}<br>Congestion: ${congestionPct}${bunchingNote}`;
    if (markers.has(vehicle.vehicle_id)) markers.get(vehicle.vehicle_id).setLatLng([vehicle.latitude, vehicle.longitude]).setIcon(icon).setPopupContent(popup);
    else markers.set(vehicle.vehicle_id, L.marker([vehicle.latitude, vehicle.longitude], { icon }).addTo(map).bindPopup(popup));
  });
  markers.forEach((marker, id) => { if (!active.has(id)) { map.removeLayer(marker); markers.delete(id); } });
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
// bus positions overlaid on a past-hours average would be misleading.
export async function setPeriod(periodKey) {
  activePeriod = periodKey;
  if (periodKey === "now") {
    return null; // the next live poll repaints naturally
  }
  try {
    const response = await fetch(`/history?period=${encodeURIComponent(periodKey)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    updateCongestion(data.congestion || { cells: [], hotspot_count: 0, priority_count: 0 });
    updateRouteLines(data.route_lines || {});
    markers.forEach((marker) => map.removeLayer(marker));
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
  updateCongestion(snapshot.congestion || lastCongestion);
  updateRouteLines(snapshot.route_lines);
  updateMarkers(snapshot.vehicles || []);
}

export async function fetchSnapshot() {
  const response = await fetch("/snapshot", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
