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
# Spatial scope: ~5km x 4km around the Sydney CBD.
# Narrow/adjust this once the group locks in the exact model footprint.
# ---------------------------------------------------------------------------
CBD_BBOX = {
    "min_lat": -33.895,
    "max_lat": -33.855,
    "min_lon": 151.185,
    "max_lon": 151.225,
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
# Publish layer
# ---------------------------------------------------------------------------
SNAPSHOT_HOST = "0.0.0.0"
SNAPSHOT_PORT = 8080
SNAPSHOT_PUSH_HZ = 2
