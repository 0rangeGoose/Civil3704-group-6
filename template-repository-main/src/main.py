"""
Transport Informatics AR Installation - Main Entry Point

This is a sample Python implementation demonstrating GTFS data loading.
Students should customize or replace this with their own implementation.
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config() -> Dict[str, Any]:
    """Load configuration from config.json"""
    config_path = Path(__file__).parent.parent / 'config.json'
    
    if not config_path.exists():
        logger.error("config.json not found. Run 'make setup' first.")
        sys.exit(1)
    
    with open(config_path, 'r') as f:
        return json.load(f)


def load_env():
    """Load environment variables from .env file"""
    env_path = Path(__file__).parent.parent / '.env'
    
    if not env_path.exists():
        logger.warning(".env file not found. Using defaults.")
        return
    
    with open(env_path, 'r') as f:
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
    
    # TODO: Students implement the following:
    # 1. Load GTFS static data
    # 2. Connect to GTFS-Realtime feed
    # 3. Initialize AR visualization
    # 4. Start the main application loop
    
    logger.info("\n" + "=" * 60)
    logger.info("TODO: Implement your AR installation here!")
    logger.info("=" * 60)
    logger.info("\nNext steps:")
    logger.info("1. Implement GTFS data loading in data_fetcher/")
    logger.info("2. Create AR visualization in visualization/")
    logger.info("3. Connect components in this main.py file")
    logger.info("4. Document your architecture in docs/ARCHITECTURE.md")
    
    # Example: Print some basic info if data exists
    gtfs_path = Path(__file__).parent.parent / 'data' / 'gtfs'
    stops_file = gtfs_path / 'stops.txt'
    
    if stops_file.exists():
        with open(stops_file, 'r') as f:
            lines = f.readlines()
            logger.info(f"\nFound {len(lines) - 1} stops in GTFS data")
    
    logger.info("\nPress Ctrl+C to exit")
    
    try:
        # Keep running until interrupted
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\nShutting down...")


if __name__ == "__main__":
    main()
