import {
  formatTime, renderSnapshot, toggleLayer, isLayerVisible, fetchSnapshot,
  loadDemandGrid, loadRouteShapes, setPeriod, getActivePeriod
} from "./map-core.js";

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
  document.querySelectorAll('.layer-btn[data-layer="demand"]').forEach((btn) => {
    btn.disabled = !loaded;
    btn.title = loaded ? "" : "No demand data loaded - see data/demand/ in the README";
  });
});

// Route geometry accumulates as new routes are seen through the day - poll
// it far less often than the 5s /snapshot cycle, just enough to pick up
// newly-appeared routes without hammering the endpoint for no reason.
loadRouteShapes();
setInterval(loadRouteShapes, 30000);

// Period tabs: "Now" is the live poll (unchanged); anything else swaps the
// map to a historical hour-of-day average and freezes the headline metrics
// on that period's own numbers, so the tiles never contradict what the map
// is showing.
document.querySelectorAll(".period-btn").forEach((btn) => {
  btn.classList.toggle("active", btn.dataset.period === getActivePeriod());
  btn.addEventListener("click", async () => {
    document.querySelectorAll(".period-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    const data = await setPeriod(btn.dataset.period);
    if (data) applyHistoricalMetrics(data);
  });
});

function applyHistoricalMetrics(periodData) {
  const cells = (periodData.congestion && periodData.congestion.cells) || [];
  const avg = cells.length
    ? Math.round(cells.reduce((sum, c) => sum + c.score, 0) / cells.length * 100)
    : null;
  document.getElementById("vehicle-count").textContent = "—";
  document.getElementById("hotspots").textContent = periodData.congestion.hotspot_count ?? 0;
  document.getElementById("avg-congestion").textContent = avg == null ? "--" : `${avg}%`;
  document.getElementById("on-time").textContent = "--";
  document.getElementById("last-update").textContent = periodData.date || "--";
  document.getElementById("feed-badge").textContent = periodData.date ? `${periodData.label}, ${periodData.date}` : `${periodData.label} - no data yet`;
  document.getElementById("notice").textContent = cells.length
    ? `Showing ${periodData.label} from ${periodData.date} (the most recent day this window was logged).`
    : `No history logged yet for ${periodData.label} - check back after the server's been running during that window.`;
}

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
  renderSnapshot(snapshot); // no-ops the map draw internally while a historical period is active
  updateRoutes(snapshot.route_adherence || {});

  // A historical period's tiles were already set by applyHistoricalMetrics()
  // when it was selected - don't let the live poll clobber them.
  if (getActivePeriod() !== "now") return;

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
    ? `Updated ${formatTime(snapshot.updated_at)} / ${vehicles.length} vehicle${vehicles.length === 1 ? "" : "s"} in the CBD / ${congestion.hotspot_count} hotspot${congestion.hotspot_count === 1 ? "" : "s"}`
    : "Feed connected, but no vehicle positions are available in the CBD box.";
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
