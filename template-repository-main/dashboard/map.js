// Bare map-only view for the projector - no header/metrics/table, just the
// map. Layer switching is keyboard-only (no visible buttons to keep the
// projected output clean): 1/2/3/4 each independently TOGGLE Congestion/
// Demand/Importance/Routes on or off - any combination can be showing at
// once. 5/6/7 independently toggle which vehicle modes are drawn
// (Bus/Train/Metro). Left/Right arrows cycle through time periods (Now /
// AM Peak / Midday / PM Peak / Evening).
import {
  map, setZoomBoost, isLayerVisible, toggleLayer, renderSnapshot, fetchSnapshot,
  loadDemandGrid, hasDemandData, loadOpalGrid, hasOpalData, loadRouteShapes,
  PERIODS, setPeriod, getActivePeriod,
  MODES, isModeVisible, toggleMode
} from "./map-core.js";

// This view is projected onto a fixed physical model - it should never
// scroll, drag or zoom away from the model's own registered bounds, or the
// projection falls out of alignment with the real scale model underneath
// it. The dashboard (app.js) keeps the map interactive for the team; only
// this bare projector page locks it down.
map.scrollWheelZoom.disable();
map.doubleClickZoom.disable();
map.touchZoom.disable();
map.boxZoom.disable();
map.keyboard.disable();
map.dragging.disable();
if (map.tap) map.tap.disable();

// Locked to the exact edge-to-edge fit of the model's surveyed coordinates
// (config.MODEL_CORNERS) - what projection calibration onto the physical
// model needs: the visible area must match the model's real footprint
// exactly, not a cropped-in view of it.
setZoomBoost(0);

const LAYERS = [
  { key: "1", name: "congestion", label: "Congestion" },
  { key: "2", name: "demand", label: "Demand" },
  { key: "3", name: "opal", label: "Importance" },
  { key: "4", name: "routes", label: "Routes" }
];
const KEY_TO_LAYER = Object.fromEntries(LAYERS.map((l) => [l.key, l.name]));
// routes doesn't depend on any pre-loaded grid at all.
const NEEDS_DEMAND_DATA = new Set(["demand"]);
const NEEDS_OPAL_DATA = new Set(["opal"]);

// Mode toggles pick up where the layer keys leave off (5/6/7), same
// independent on/off pattern as 1/2/3/4 - just a different axis (which
// vehicles are drawn, not which analytics layer).
const MODE_KEYS = ["5", "6", "7"];
const KEY_TO_MODE = Object.fromEntries(MODES.map((m, i) => [MODE_KEYS[i], m.key]));

function updateHint(note) {
  const hint = document.getElementById("layer-hint");
  const layerStates = LAYERS.map(
    (l) => `[${isLayerVisible(l.name) ? "■" : "□"}${l.key}] ${l.label}`
  ).join("&nbsp;&nbsp;");
  const modeStates = MODES.map(
    (m, i) => `[${isModeVisible(m.key) ? "■" : "□"}${MODE_KEYS[i]}] ${m.label}`
  ).join("&nbsp;&nbsp;");
  const periodLabel = PERIODS.find((p) => p.key === getActivePeriod())?.label || "Now";
  const missing = [];
  if (!hasDemandData()) missing.push("demand");
  if (!hasOpalData()) missing.push("Opal importance");
  const unavailable = missing.length ? ` (${missing.join(", ")} data not loaded)` : "";
  hint.innerHTML =
    `${layerStates}&nbsp;&nbsp;&nbsp;&nbsp;${modeStates}&nbsp;&nbsp;&nbsp;&nbsp;[←/→] ${periodLabel}` +
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

  const mode = KEY_TO_MODE[event.key];
  if (mode) { toggleMode(mode); updateHint(); return; }

  const layer = KEY_TO_LAYER[event.key];
  if (!layer) return;
  if (NEEDS_DEMAND_DATA.has(layer) && !hasDemandData()) return; // nothing to show, ignore the key
  if (NEEDS_OPAL_DATA.has(layer) && !hasOpalData()) return;
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
loadOpalGrid().then(() => updateHint());

// Route geometry accumulates as new routes are seen through the day - poll
// it far less often than the 5s /snapshot cycle.
loadRouteShapes();
setInterval(loadRouteShapes, 30000);

refresh();
setInterval(refresh, 5000);
