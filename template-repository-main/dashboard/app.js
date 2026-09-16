import { formatTime, renderSnapshot, toggleLayer, isLayerVisible, fetchSnapshot, loadDemandGrid, hasDemandData, loadRouteShapes } from "./map-core.js";

// Each button is an independent on/off switch now (not a single active
// mode) - reflect their starting state, then just flip on click.
document.querySelectorAll(".layer-btn").forEach((btn) => {
  btn.classList.toggle("active", isLayerVisible(btn.dataset.layer));
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    btn.classList.toggle("active", toggleLayer(btn.dataset.layer));
  });
});

loadDemandGrid().then((loaded) => {
  document.querySelectorAll('.layer-btn[data-layer="demand"], .layer-btn[data-layer="priority"]').forEach((btn) => {
    btn.disabled = !loaded;
    btn.title = loaded ? "" : "No demand data loaded - see data/demand/ in the README";
  });
});

// Route geometry accumulates as new routes are seen through the day - poll
// it far less often than the 5s /snapshot cycle, just enough to pick up
// newly-appeared routes without hammering the endpoint for no reason.
loadRouteShapes();
setInterval(loadRouteShapes, 30000);

function setConnection(state, message) {
  const connection = document.getElementById("connection");
  connection.className = `connection ${state}`;
  document.getElementById("connection-text").textContent = message;
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
  const congestion = snapshot.congestion || { cells: [], hotspot_count: 0, priority_count: 0 };
  const samples = Object.values(routes);
  const totalSamples = samples.reduce((sum, route) => sum + route.sample_count, 0);
  const onTimeSamples = samples.reduce((sum, route) => sum + route.on_time_pct * route.sample_count / 100, 0);
  const scored = vehicles.filter((v) => typeof v.congestion_score === "number");
  const avgCongestion = scored.length
    ? Math.round(scored.reduce((sum, v) => sum + v.congestion_score, 0) / scored.length * 100)
    : null;

  document.getElementById("vehicle-count").textContent = snapshot.vehicle_count ?? vehicles.length;
  document.getElementById("hotspots").textContent = congestion.hotspot_count ?? 0;
  document.getElementById("priority-zones").textContent = hasDemandData() ? (congestion.priority_count ?? 0) : "no data";
  document.getElementById("avg-congestion").textContent = avgCongestion == null ? "--" : `${avgCongestion}%`;
  document.getElementById("on-time").textContent = totalSamples ? `${Math.round(onTimeSamples / totalSamples * 100)}%` : "--";
  document.getElementById("last-update").textContent = formatTime(snapshot.updated_at);
  document.getElementById("feed-badge").textContent = vehicles.length ? "Live positions" : "No vehicles";
  document.getElementById("notice").textContent = vehicles.length
    ? `Updated ${formatTime(snapshot.updated_at)} / ${vehicles.length} bus${vehicles.length === 1 ? "" : "es"} in the CBD / ${congestion.hotspot_count} hotspot${congestion.hotspot_count === 1 ? "" : "s"}`
    : "Feed connected, but no vehicle positions are available in the CBD box.";

  renderSnapshot(snapshot);
  updateRoutes(routes);
}

async function refresh() {
  try {
    updateDashboard(await fetchSnapshot());
    setConnection("live", "Live feed");
  } catch (error) {
    setConnection("error", "Feed unavailable");
    document.getElementById("notice").textContent = `Could not load /snapshot: ${error.message}`;
  }
}

refresh();
setInterval(refresh, 5000);
