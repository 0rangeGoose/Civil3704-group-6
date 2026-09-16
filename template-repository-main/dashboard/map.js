// Bare map-only view for the projector - no header/metrics/table, just the
// map. Layer switching is keyboard-only (no visible buttons to keep the
// projected output clean): 1 = Congestion, 2 = Demand, 3 = Priority.
import { LAYER_MODES, getActiveLayer, setActiveLayer, renderSnapshot, fetchSnapshot, loadDemandGrid, hasDemandData } from "./map-core.js";

const KEY_TO_LAYER = { "1": "congestion", "2": "demand", "3": "priority" };

function updateHint(note) {
  const hint = document.getElementById("layer-hint");
  const mode = LAYER_MODES[getActiveLayer()];
  const unavailable = mode !== LAYER_MODES.congestion && !hasDemandData() ? " (no demand data loaded)" : "";
  hint.innerHTML =
    `${mode.label}${unavailable}` +
    `<span class="keys">[1] Congestion&nbsp;&nbsp;[2] Demand&nbsp;&nbsp;[3] Priority</span>` +
    (note ? `<span class="stale">${note}</span>` : "");
}

window.addEventListener("keydown", (event) => {
  const layer = KEY_TO_LAYER[event.key];
  if (!layer) return;
  if (layer !== "congestion" && !hasDemandData()) return; // nothing to show, ignore the key
  setActiveLayer(layer);
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
