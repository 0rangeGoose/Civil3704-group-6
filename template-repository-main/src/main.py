"""
Transport Informatics AR Installation - Main Entry Point

This is a sample Python implementation demonstrating GTFS data loading.
Students should customize or replace this with their own implementation.
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from typing import Any, Dict

from pipeline.run import run_headless, run_with_server

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


def main():
    """Run the headless pipeline or its snapshot server."""
    parser = argparse.ArgumentParser(description="Run the Transport AR data pipeline")
    parser.add_argument(
        "--mode",
        choices=("serve", "headless"),
        default="serve",
        help="run the snapshot server or log-only pipeline",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="stop headless mode after this many polling cycles",
    )
    args = parser.parse_args()

    load_env()
    api_key = os.environ.get("TFNSW_API_KEY", "").strip()
    if not api_key:
        parser.error("TFNSW_API_KEY is missing; add it to .env or src/file.env")

    if args.mode == "headless":
        run_headless(api_key, max_cycles=args.max_cycles)
    else:
        run_with_server(api_key)


if __name__ == "__main__":
    main()
