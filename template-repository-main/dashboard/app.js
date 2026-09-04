const map = L.map("map", { zoomControl: false }).setView([-33.87, 151.21], 12);
L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors"
}).addTo(map);

const markers = new Map();
let hasFittedMap = false;

function statusFor(vehicle) {
  if (vehicle.cancelled) return ["cancelled", "Cancelled"];
  if (vehicle.delay_seconds == null) return ["unknown", "Unknown delay"];
  if (vehicle.delay_seconds <= 60) return ["on-time", "On time"];
  if (vehicle.delay_seconds <= 300) return ["late", "Late"];
  return ["very-late", "Very late"];
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

function updateMarkers(vehicles) {
  const active = new Set();
  const bounds = [];
  vehicles.forEach((vehicle) => {
    if (typeof vehicle.latitude !== "number" || typeof vehicle.longitude !== "number") return;
    active.add(vehicle.vehicle_id);
    const [status, label] = statusFor(vehicle);
    const color = { "on-time": "#087f74", late: "#e8a72c", "very-late": "#cf5445", unknown: "#2874a6", cancelled: "#63716d" }[status];
    const icon = L.divIcon({ className: "vehicle-icon", html: `<div class="vehicle-marker" style="background:${color}"></div>`, iconSize: [16, 16], iconAnchor: [8, 8] });
    const popup = `<strong>${vehicle.route_short_name || vehicle.route_id || "Unassigned route"}</strong><br>${vehicle.mode} / ${label}<br>${vehicle.delay_seconds == null ? "Delay unavailable" : `${vehicle.delay_seconds}s delay`}`;
    if (markers.has(vehicle.vehicle_id)) markers.get(vehicle.vehicle_id).setLatLng([vehicle.latitude, vehicle.longitude]).setIcon(icon).setPopupContent(popup);
    else markers.set(vehicle.vehicle_id, L.marker([vehicle.latitude, vehicle.longitude], { icon }).addTo(map).bindPopup(popup));
    bounds.push([vehicle.latitude, vehicle.longitude]);
  });
  markers.forEach((marker, id) => { if (!active.has(id)) { map.removeLayer(marker); markers.delete(id); } });
  if (bounds.length && !hasFittedMap) { map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 }); hasFittedMap = true; }
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
  const samples = Object.values(routes);
  const totalSamples = samples.reduce((sum, route) => sum + route.sample_count, 0);
  const onTimeSamples = samples.reduce((sum, route) => sum + route.on_time_pct * route.sample_count / 100, 0);
  document.getElementById("vehicle-count").textContent = snapshot.vehicle_count ?? vehicles.length;
  document.getElementById("route-count").textContent = Object.keys(routes).length;
  document.getElementById("on-time").textContent = totalSamples ? `${Math.round(onTimeSamples / totalSamples * 100)}%` : "--";
  document.getElementById("last-update").textContent = formatTime(snapshot.updated_at);
  document.getElementById("feed-badge").textContent = vehicles.length ? "Live positions" : "No vehicles";
  document.getElementById("notice").textContent = vehicles.length ? `Updated ${formatTime(snapshot.updated_at)} / ${vehicles.length} position${vehicles.length === 1 ? "" : "s"}` : "Feed connected, but no vehicle positions are available.";
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
