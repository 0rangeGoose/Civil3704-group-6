"""
Transport Informatics AR Installation - Main Entry Point

Loads configuration/GTFS data, then hands off to the `pipeline` package
(ingestion -> normalisation -> analytics -> publish). See pipeline/run.py
for the two ways to run it: headless (no server, just logs each cycle - for
verifying the pipeline without a renderer attached) or serve (background
ingestion + HTTP/WebSocket snapshot for the projection renderer).
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from pipeline import run as pipeline_run

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transport Informatics AR Installation")
    parser.add_argument(
        "--mode",
        choices=["serve", "headless"],
        default="serve",
        help=(
            "serve: run ingestion in the background and publish a live snapshot "
            "over HTTP/WebSocket for the renderer (default). "
            "headless: just poll and log each cycle, no server - for verifying "
            "the pipeline works before touching the projector."
        ),
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="headless mode only: stop after N poll cycles instead of running forever.",
    )
    return parser.parse_args()


def main():
    """Main entry point for the AR installation"""
    args = parse_args()

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

    logger.info("GTFS data found")

    api_key = os.environ.get("TFNSW_API_KEY", "").strip()
    if not api_key:
        logger.error("TFNSW_API_KEY is missing. Add it to your .env file.")
        sys.exit(1)

    if args.mode == "headless":
        logger.info("Running headless (no server) - Ctrl+C to stop")
        pipeline_run.run_headless(api_key, max_cycles=args.max_cycles)
    else:
        logger.info("Running with live snapshot server - Ctrl+C to stop")
        pipeline_run.run_with_server(api_key)


if __name__ == "__main__":
    main()
