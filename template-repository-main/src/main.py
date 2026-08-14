"""
Transport Informatics AR Installation - Main Entry Point

This is a sample Python implementation demonstrating GTFS data loading.
Students should customize or replace this with their own implementation.
"""

import os
import sys
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import requests
from google.transit import gtfs_realtime_pb2

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_GTFS_RT_URL = "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/buses"


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json"""
    repo_root = Path(__file__).parent.parent
    config_path = repo_root / 'config.json'
    example_path = repo_root / 'config.example.json'

    if config_path.exists():
        with open(config_path, 'r') as f:
            return json.load(f)

    if example_path.exists():
        logger.warning("config.json not found, loading config.example.json instead")
        with open(example_path, 'r') as f:
            return json.load(f)

    logger.warning("No config.json or config.example.json found; using empty config")
    return {}


def load_env():
    """Load environment variables from .env file"""
    repo_root = Path(__file__).parent.parent
    env_path = repo_root / '.env'
    alt_path = Path(__file__).parent / 'file.env'

    target = None
    if env_path.exists():
        target = env_path
    elif alt_path.exists():
        logger.info("Using src/file.env as environment file")
        target = alt_path
    else:
        logger.warning(".env file not found. Using defaults.")
        return

    with open(target, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                key, _, value = line.partition('=')
                os.environ[key.strip()] = value.strip()


def download_gtfs_realtime_feed(url: str, api_key: str) -> gtfs_realtime_pb2.FeedMessage:
    """Download and decode a GTFS-Realtime feed from the TfNSW Open Data Hub."""
    response = requests.get(
        url,
        headers={"Authorization": f"apikey {api_key}"},
        timeout=30,
    )
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)
    return feed


def log_vehicle_positions(feed: gtfs_realtime_pb2.FeedMessage) -> None:
    """Log up to ten vehicle positions from a decoded feed."""
    feed_time = datetime.fromtimestamp(feed.header.timestamp, timezone.utc)
    vehicles = [entity.vehicle for entity in feed.entity if entity.HasField("vehicle")]

    logger.info(f"Feed timestamp: {feed_time:%Y-%m-%d %H:%M:%S %Z}")
    logger.info(f"Vehicle positions: {len(vehicles)}")

    for vehicle in vehicles[:10]:
        label = vehicle.vehicle.label or vehicle.vehicle.id or "unknown"
        route = vehicle.trip.route_id or "unknown"
        latitude = vehicle.position.latitude
        longitude = vehicle.position.longitude
        logger.info(
            f"{label:>12}  route={route:<10}  lat={latitude:.5f}  lon={longitude:.5f}"
        )


def check_gtfs_data(config: Dict[str, Any]) -> bool:
    """Check if GTFS data is available"""
    gtfs_path = Path(__file__).parent.parent / 'data' / 'gtfs'
    
    if not gtfs_path.exists():
        gtfs_path.mkdir(parents=True, exist_ok=True)
        logger.warning(f"GTFS data directory created at {gtfs_path}")
        logger.warning("Please download GTFS data to data/gtfs/ directory")
        return False
    
    required_files = ['stops.txt', 'routes.txt', 'trips.txt']
    missing_files = [f for f in required_files if not (gtfs_path / f).exists()]
    
    if missing_files:
        logger.warning(f"Missing GTFS files: {', '.join(missing_files)}")
        logger.warning("Download GTFS data from your transit agency")
        return False
    
    return True


def main():
    """Main entry point for the AR installation"""
    logger.info("=" * 60)
    logger.info("Transport Informatics AR Installation")
    logger.info("=" * 60)
    
    # Load configuration
    load_env()
    config = load_config()
    
    logger.info(f"Project: {config.get('project_name', 'Unnamed')}")
    logger.info(f"Transit Agency: {config.get('transit_agency', 'Not specified')}")
    
    # Check for GTFS data
    if not check_gtfs_data(config):
        logger.error("Cannot proceed without GTFS data")
        logger.info("\nTo get started:")
        logger.info("1. Find a GTFS feed: https://database.mobilitydata.org/")
        logger.info("2. Download and extract to data/gtfs/")
        logger.info("3. Run 'make run' again")
        sys.exit(1)
    
    logger.info("✓ GTFS data found")

    # Example: Print some basic info if data exists
    gtfs_path = Path(__file__).parent.parent / 'data' / 'gtfs'
    stops_file = gtfs_path / 'stops.txt'

    if stops_file.exists():
        with open(stops_file, 'r') as f:
            lines = f.readlines()
            logger.info(f"\nFound {len(lines) - 1} stops in GTFS data")

    # Connect to the GTFS-Realtime feed
    api_key = os.environ.get("TFNSW_API_KEY", "").strip()
    if not api_key:
        logger.error("TFNSW_API_KEY is missing. Add it to your .env file.")
        sys.exit(1)

    feed_url = os.environ.get("TFNSW_GTFS_RT_URL", DEFAULT_GTFS_RT_URL).strip()
    update_interval = config.get("gtfs_realtime", {}).get("update_interval_seconds", 30)

    logger.info(f"\nConnecting to GTFS-Realtime feed: {feed_url}")
    logger.info("Press Ctrl+C to exit")

    try:
        while True:
            try:
                feed = download_gtfs_realtime_feed(feed_url, api_key)
                log_vehicle_positions(feed)
            except requests.HTTPError as error:
                status = error.response.status_code if error.response is not None else "unknown"
                logger.error(f"TfNSW returned HTTP {status}. Check your API key and feed URL.")
            except requests.RequestException as error:
                logger.error(f"Could not download the TfNSW feed: {error}")

            time.sleep(update_interval)
    except KeyboardInterrupt:
        logger.info("\nShutting down...")


if __name__ == "__main__":
    main()
