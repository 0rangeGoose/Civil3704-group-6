// Bare map-only view for the projector - no header/metrics/table, just the
// map. Layer switching is keyboard-only (no visible buttons to keep the
// projected output clean): 1/2/3/4 each independently TOGGLE Congestion/
// Demand/Priority/Routes on or off - any combination can be showing at
// once. Left/Right arrows cycle through time periods (Now / AM Peak /
// Midday / PM Peak / Evening).
import {
  isLayerVisible, toggleLayer, renderSnapshot, fetchSnapshot,
  loadDemandGrid, hasDemandData, loadRouteShapes,
  PERIODS, setPeriod, getActivePeriod
} from "./map-core.js";

const LAYERS = [
  { key: "1", name: "congestion", label: "Congestion" },
  { key: "2", name: "demand", label: "Demand" },
  { key: "3", name: "priority", label: "Priority" },
  { key: "4", name: "routes", label: "Routes" }
];
const KEY_TO_LAYER = Object.fromEntries(LAYERS.map((l) => [l.key, l.name]));
const NEEDS_DEMAND_DATA = new Set(["demand", "priority"]); // routes doesn't depend on demand data at all

function updateHint(note) {
  const hint = document.getElementById("layer-hint");
  const layerStates = LAYERS.map(
    (l) => `[${isLayerVisible(l.name) ? "■" : "□"}${l.key}] ${l.label}`
  ).join("&nbsp;&nbsp;");
  const periodLabel = PERIODS.find((p) => p.key === getActivePeriod())?.label || "Now";
  const unavailable = hasDemandData() ? "" : " (demand data not loaded)";
  hint.innerHTML =
    `${layerStates}&nbsp;&nbsp;&nbsp;&nbsp;[←/→] ${periodLabel}` +
    `<span class="keys">${unavailable}</span>` +
    (note ? `<span class="stale">${note}</span>` : "");
}

async function cyclePeriod(direction) {
  const index = PERIODS.findIndex((p) => p.key === getActivePeriod());
  const next = PERIODS[(index + direction + PERIODS.length) % PERIODS.length];
  await setPeriod(next.key);
  updateHint();
}

window.addEventListener("keydown", (event) => {
  if (event.key === "ArrowRight") { cyclePeriod(1); return; }
  if (event.key === "ArrowLeft") { cyclePeriod(-1); return; }

  const layer = KEY_TO_LAYER[event.key];
  if (!layer) return;
  if (NEEDS_DEMAND_DATA.has(layer) && !hasDemandData()) return; // nothing to show, ignore the key
  toggleLayer(layer);
  updateHint();
});

async function refresh() {
  try {
    const snapshot = await fetchSnapshot();
    renderSnapshot(snapshot); // no-ops the map draw internally while a historical period is active
    updateHint();
  } catch (error) {
    updateHint(`feed unavailable: ${error.message}`);
  }
}

updateHint();
loadDemandGrid().then(() => updateHint());

// Route geometry accumulates as new routes are seen through the day - poll
// it far less often than the 5s /snapshot cycle.
loadRouteShapes();
setInterval(loadRouteShapes, 30000);

refresh();
setInterval(refresh, 5000);
