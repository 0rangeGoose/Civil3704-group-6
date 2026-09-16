// Bare map-only view for the projector - no header/metrics/table, just the
// map. Layer switching is keyboard-only (no visible buttons to keep the
// projected output clean): 1/2/3 each independently TOGGLE Congestion/
// Demand/Priority on or off - any combination can be showing at once.
import { isLayerVisible, toggleLayer, renderSnapshot, fetchSnapshot, loadDemandGrid, hasDemandData } from "./map-core.js";

const LAYERS = [
  { key: "1", name: "congestion", label: "Congestion" },
  { key: "2", name: "demand", label: "Demand" },
  { key: "3", name: "priority", label: "Priority" }
];
const KEY_TO_LAYER = Object.fromEntries(LAYERS.map((l) => [l.key, l.name]));

function updateHint(note) {
  const hint = document.getElementById("layer-hint");
  const states = LAYERS.map(
    (l) => `[${isLayerVisible(l.name) ? "■" : "□"}${l.key}] ${l.label}`
  ).join("&nbsp;&nbsp;");
  const unavailable = hasDemandData() ? "" : " (no demand data loaded)";
  hint.innerHTML = `${states}<span class="keys">${unavailable}</span>` + (note ? `<span class="stale">${note}</span>` : "");
}

window.addEventListener("keydown", (event) => {
  const layer = KEY_TO_LAYER[event.key];
  if (!layer) return;
  if (layer !== "congestion" && !hasDemandData()) return; // nothing to show, ignore the key
  toggleLayer(layer);
  updateHint();
});

async function refresh() {
  try {
    const snapshot = await fetchSnapshot();
    renderSnapshot(snapshot);
    updateHint();
  } catch (error) {
    updateHint(`feed unavailable: ${error.message}`);
  }
}

updateHint();
loadDemandGrid().then(() => updateHint());
refresh();
setInterval(refresh, 5000);
