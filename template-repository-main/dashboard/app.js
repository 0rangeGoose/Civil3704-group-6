const map = L.map("map", { zoomControl: false }).setView([-33.865, 151.204], 12);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

const congestionLayer = L.layerGroup().addTo(map);
const markers = new Map();
let modelFitted = false;

// Frame the map on the physical scale-model footprint (sent in the snapshot
// as model_bounds) and outline it, so the view matches what the projector
// actually covers.
function applyModelBounds(b) {
  if (!b || modelFitted) return;
  const bounds = L.latLngBounds([[b.min_lat, b.min_lon], [b.max_lat, b.max_lon]]);
  map.fitBounds(bounds, { padding: [16, 16] });
  map.setMaxBounds(bounds.pad(0.2));
  L.rectangle(bounds, {
    color: "#17211f",
    weight: 2,
    dashArray: "6 4",
    fill: false,
    interactive: false
  }).addTo(map).bindTooltip("Scale model footprint", { sticky: true });
  modelFitted = true;
}

function statusFor(vehicle) {
  if (vehicle.cancelled) return ["cancelled", "Cancelled"];
  if (vehicle.delay_seconds == null) return ["unknown", "Unknown delay"];
  if (vehicle.delay_seconds <= 60) return ["on-time", "On time"];
  if (vehicle.delay_seconds <= 300) return ["late", "Late"];
  return ["very-late", "Very late"];
}

// Congestion score (0-1) -> colour ramp: clear teal -> amber -> red.
function congestionColor(score) {
  if (score >= 0.6) return "#cf5445";
  if (score >= 0.4) return "#e8722c";
  if (score >= 0.25) return "#e8a72c";
  if (score >= 0.12) return "#b6c34a";
  return "#087f74";
}

function setConnection(state, message) {
  const connection = document.getElementById("connection");
  connection.className = `connection ${state}`;
  document.getElementById("connection-text").textContent = message;
}

function formatTime(seconds) {
  if (!seconds) return "No update";
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function updateCongestion(congestion) {
  congestionLayer.clearLayers();
  const cells = (congestion && congestion.cells) || [];
  cells.forEach((cell) => {
    const b = cell.bounds;
    if (!b) return;
    const color = congestionColor(cell.score);
    const rect = L.rectangle(
      [[b.min_lat, b.min_lon], [b.max_lat, b.max_lon]],
      {
        color,
        weight: cell.hotspot ? 2.5 : 1,
        dashArray: cell.hotspot ? null : "3 4",
        fillColor: color,
        fillOpacity: 0.15 + Math.min(0.4, cell.score * 0.4)
      }
    );
    rect.bindTooltip(`${Math.round(cell.score * 100)}%`, {
      permanent: true,
      direction: "center",
      className: "cell-label"
    });
    rect.bindPopup(
      `<strong>Block ${cell.cell_id}</strong>${cell.hotspot ? ' <span class="pill">hotspot</span>' : ""}<br>` +
      `Congestion: ${(cell.score * 100).toFixed(0)}%<br>` +
      `Holding: ${cell.vehicle_count} bus${cell.vehicle_count === 1 ? "" : "es"}<br>` +
      `Routes: ${cell.routes && cell.routes.length ? cell.routes.join(", ") : "n/a"}<br>` +
      `~${congestion.cell_metres || 300} m square`
    );
    rect.addTo(congestionLayer);
  });
}

function updateMarkers(vehicles) {
  const active = new Set();
  vehicles.forEach((vehicle) => {
    if (typeof vehicle.latitude !== "number" || typeof vehicle.longitude !== "number") return;
    active.add(vehicle.vehicle_id);
    const [status, label] = statusFor(vehicle);
    const color = { "on-time": "#087f74", late: "#e8a72c", "very-late": "#cf5445", unknown: "#2874a6", cancelled: "#63716d" }[status];
    const icon = L.divIcon({ className: "vehicle-icon", html: `<div class="vehicle-marker" style="background:${color}"></div>`, iconSize: [16, 16], iconAnchor: [8, 8] });
    const congestionPct = vehicle.congestion_score == null ? "n/a" : `${Math.round(vehicle.congestion_score * 100)}%`;
    const popup = `<strong>${vehicle.route_short_name || vehicle.route_id || "Unassigned route"}</strong><br>${vehicle.mode} / ${label}<br>${vehicle.delay_seconds == null ? "Delay unavailable" : `${vehicle.delay_seconds}s delay`}<br>Congestion: ${congestionPct}`;
    if (markers.has(vehicle.vehicle_id)) markers.get(vehicle.vehicle_id).setLatLng([vehicle.latitude, vehicle.longitude]).setIcon(icon).setPopupContent(popup);
    else markers.set(vehicle.vehicle_id, L.marker([vehicle.latitude, vehicle.longitude], { icon }).addTo(map).bindPopup(popup));
  });
  markers.forEach((marker, id) => { if (!active.has(id)) { map.removeLayer(marker); markers.delete(id); } });
}

function updateRoutes(routes) {
  const entries = Object.entries(routes || {}).sort((a, b) => b[1].sample_count - a[1].sample_count);
  const table = document.getElementById("route-table");
  if (!entries.length) { table.innerHTML = '<tr><td colspan="4" class="muted">No route metrics yet</td></tr>'; return; }
  table.innerHTML = entries.map(([route, metric]) => `<tr><td>${route}</td><td>${metric.sample_count}</td><td>${metric.on_time_pct}%</td><td>${metric.late_pct}%</td></tr>`).join("");
}

function updateDashboard(snapshot) {
  const vehicles = snapshot.vehicles || [];
  const routes = snapshot.route_adherence || {};
  const congestion = snapshot.congestion || { cells: [], hotspot_count: 0 };
  const samples = Object.values(routes);
  const totalSamples = samples.reduce((sum, route) => sum + route.sample_count, 0);
  const onTimeSamples = samples.reduce((sum, route) => sum + route.on_time_pct * route.sample_count / 100, 0);
  const scored = vehicles.filter((v) => typeof v.congestion_score === "number");
  const avgCongestion = scored.length
    ? Math.round(scored.reduce((sum, v) => sum + v.congestion_score, 0) / scored.length * 100)
    : null;

  document.getElementById("vehicle-count").textContent = snapshot.vehicle_count ?? vehicles.length;
  document.getElementById("hotspots").textContent = congestion.hotspot_count ?? 0;
  document.getElementById("avg-congestion").textContent = avgCongestion == null ? "--" : `${avgCongestion}%`;
  document.getElementById("on-time").textContent = totalSamples ? `${Math.round(onTimeSamples / totalSamples * 100)}%` : "--";
  document.getElementById("last-update").textContent = formatTime(snapshot.updated_at);
  document.getElementById("feed-badge").textContent = vehicles.length ? "Live positions" : "No vehicles";
  document.getElementById("notice").textContent = vehicles.length
    ? `Updated ${formatTime(snapshot.updated_at)} / ${vehicles.length} bus${vehicles.length === 1 ? "" : "es"} in the CBD / ${congestion.hotspot_count} hotspot${congestion.hotspot_count === 1 ? "" : "s"}`
    : "Feed connected, but no vehicle positions are available in the CBD box.";

  applyModelBounds(snapshot.model_bounds);
  updateCongestion(congestion);
  updateMarkers(vehicles);
  updateRoutes(routes);
}

async function refresh() {
  try {
    const response = await fetch("/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    updateDashboard(await response.json());
    setConnection("live", "Live feed");
  } catch (error) {
    setConnection("error", "Feed unavailable");
    document.getElementById("notice").textContent = `Could not load /snapshot: ${error.message}`;
  }
}

refresh();
setInterval(refresh, 5000);
