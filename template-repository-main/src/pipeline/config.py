"""Central configuration for the transit data pipeline.

Everything that is TfNSW-specific or CBD-specific lives here so the rest of
the pipeline (ingestion / normalisation / analytics / publish) stays generic.

IMPORTANT: some endpoint URLs below are marked UNVERIFIED. TfNSW's Open Data
Hub API explorer requires a logged-in session to browse, so these were
sourced from public documentation pages and known-good community usage, not
independently tested. Confirm them against
https://opendata.transport.nsw.gov.au (log in -> APIs) before relying on
them, then delete the warning comment.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Spatial scope: the physical scale model's surveyed footprint.
# These are the model's NE and SW corner coordinates (from the projection
# rig's model config), so the pipeline covers exactly what the projector
# can paint - nothing off the edge of the model.
# ---------------------------------------------------------------------------
MODEL_CORNERS = {
    "ne": {"lat": -33.83512300658737, "lon": 151.27599316594075},
    "sw": {"lat": -33.894722633376766, "lon": 151.13296645445593},
}

CBD_BBOX = {
    "min_lat": MODEL_CORNERS["sw"]["lat"],
    "max_lat": MODEL_CORNERS["ne"]["lat"],
    "min_lon": MODEL_CORNERS["sw"]["lon"],
    "max_lon": MODEL_CORNERS["ne"]["lon"],
}

# ---------------------------------------------------------------------------
# Static GTFS
# ---------------------------------------------------------------------------
# Local directory main.py's check_gtfs_data() already expects/creates.
STATIC_GTFS_DIR = REPO_ROOT / "data" / "gtfs"

# UNVERIFIED: complete static GTFS bundle (all operators), per TfNSW Open
# Data Hub docs. Only used as a fallback if data/gtfs/*.txt isn't already
# present locally.
STATIC_GTFS_DOWNLOAD_URL = (
    "https://api.transport.nsw.gov.au/v1/publictransport/timetables/complete/gtfs"
)

STATIC_GTFS_CACHE_DB = REPO_ROOT / "data" / "cache" / "gtfs_static.sqlite"
STATIC_REFRESH_HOURS = 24

# ---------------------------------------------------------------------------
# GTFS-Realtime feeds, per mode.
#
# CONFIRMED (v1, still current for these modes):
#   vehicle_positions -> https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/{mode}
#   trip_updates       -> https://api.transport.nsw.gov.au/v1/gtfs/realtime/{mode}
# CONFIRMED (v2, all modes):
#   alerts              -> https://api.transport.nsw.gov.au/v2/gtfs/alerts/{mode}
#
# UNVERIFIED: sydneytrains / metro / lightrail vehicle_positions and
# trip_updates moved from v1 to v2 in May 2025. The v2 path below follows
# TfNSW's own versioning convention (same as the confirmed alerts v2 paths)
# but has not been independently confirmed - check before use.
# ---------------------------------------------------------------------------
REALTIME_FEEDS = {
    "buses": {
        "vehicle_positions": "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/buses",
        "trip_updates": "https://api.transport.nsw.gov.au/v1/gtfs/realtime/buses",
        "alerts": "https://api.transport.nsw.gov.au/v2/gtfs/alerts/buses",
    },
    "ferries": {
        "vehicle_positions": "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/ferries/sydneyferries",
        "trip_updates": "https://api.transport.nsw.gov.au/v1/gtfs/realtime/ferries/sydneyferries",
        "alerts": "https://api.transport.nsw.gov.au/v2/gtfs/alerts/ferries",
    },
    "nswtrains": {
        "vehicle_positions": "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/nswtrains",
        "trip_updates": "https://api.transport.nsw.gov.au/v1/gtfs/realtime/nswtrains",
        "alerts": "https://api.transport.nsw.gov.au/v2/gtfs/alerts/nswtrains",
    },
    "sydneytrains": {  # UNVERIFIED v2 path - confirm before use
        "vehicle_positions": "https://api.transport.nsw.gov.au/v2/gtfs/vehiclepos/sydneytrains",
        "trip_updates": "https://api.transport.nsw.gov.au/v2/gtfs/realtime/sydneytrains",
        "alerts": "https://api.transport.nsw.gov.au/v2/gtfs/alerts/sydneytrains",
    },
    "metro": {  # UNVERIFIED v2 path - confirm before use
        "vehicle_positions": "https://api.transport.nsw.gov.au/v2/gtfs/vehiclepos/metro",
        "trip_updates": "https://api.transport.nsw.gov.au/v2/gtfs/realtime/metro",
        "alerts": "https://api.transport.nsw.gov.au/v2/gtfs/alerts/metro",
    },
    "lightrail": {  # UNVERIFIED v2 path - confirm before use
        "vehicle_positions": "https://api.transport.nsw.gov.au/v2/gtfs/vehiclepos/lightrail/innerwest",
        "trip_updates": "https://api.transport.nsw.gov.au/v2/gtfs/realtime/lightrail/innerwest",
        "alerts": "https://api.transport.nsw.gov.au/v2/gtfs/alerts/lightrail",
    },
}

# Start with buses only while the pipeline is being built/tested - buses are
# the one mode with fully-confirmed URLs end to end. Add modes here once
# their endpoints are verified.
ACTIVE_MODES = ["buses"]

POLL_INTERVAL_SECONDS = {
    "vehicle_positions": 15,
    "trip_updates": 15,
    "alerts": 15,
}

# Vehicles whose last-reported timestamp is older than this are treated as
# stale ("ghost trails") and dropped from the published snapshot.
GHOST_TRAIL_STALE_SECONDS = 120

# Adherence scoring thresholds (seconds of delay).
ADHERENCE_THRESHOLDS = {
    "on_time": 60,       # <= 60s counts as on time
    "late": 300,         # <= 5 min counts as late; beyond is very_late
}

# How many recent samples to keep per route for rolling adherence %.
ROLLING_WINDOW_SIZE = 50

# ---------------------------------------------------------------------------
# Congestion scoring (prototype heuristic)
#
# Per-vehicle congestion score in [0, 1], blending three signals: how slowly
# the bus is moving, how far behind schedule it is, and how bunched up it is
# with the nearest bus on the same route shape. Aggregated onto a grid over
# the model footprint so the projector/dashboard can paint "where congestion
# is worst". This is a first-pass proxy, not a validated model - tune the
# constants against observed data.
# ---------------------------------------------------------------------------
FREE_FLOW_SPEED_MPS = 8.3            # ~30 km/h: CBD bus free-flow reference
CONGESTION_DELAY_CAP_SECONDS = 600   # delay at which the delay term saturates

# Bunching: gap (metres, measured along the route shape) to the nearest bus
# on the same shape. At/below CLOSE = fully bunched (1.0); at/above FAR = not
# bunched (0.0); linear in between. ~100m is a couple of bus-lengths apart;
# ~800m is roughly a light-traffic minute of running time.
BUNCHING_CLOSE_METRES = 100
BUNCHING_FAR_METRES = 800

# Weights when all three signals are available (they're re-normalised when
# one or more is missing - see vehicle_congestion_score).
CONGESTION_SPEED_WEIGHT = 0.4
CONGESTION_DELAY_WEIGHT = 0.3
CONGESTION_BUNCHING_WEIGHT = 0.3

# 150m: closer to an actual Sydney CBD block (~100-200m) than a flat 300m,
# which was blurring adjacent streets with different conditions together.
CONGESTION_GRID_CELL_METRES = 150
CONGESTION_HOTSPOT_THRESHOLD = 0.6   # cell score at/above this = a hotspot
CONGESTION_HOTSPOT_MIN_VEHICLES = 2  # ...and at least this many buses in the cell

# ---------------------------------------------------------------------------
# Publish layer
# ---------------------------------------------------------------------------
SNAPSHOT_HOST = "0.0.0.0"
SNAPSHOT_PORT = 8080
SNAPSHOT_PUSH_HZ = 2
