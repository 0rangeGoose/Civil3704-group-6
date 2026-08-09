"""
Unit tests for the AR Transport Installation

Add your tests here. Run with: make test
"""

import pytest
from pathlib import Path


def test_project_structure():
    """Test that required directories exist"""
    base_path = Path(__file__).parent.parent
    
    assert (base_path / 'src').exists()
    assert (base_path / 'data').exists()
    assert (base_path / 'docs').exists()
    assert (base_path / 'examples').exists()


def test_config_example_exists():
    """Test that config example file exists"""
    base_path = Path(__file__).parent.parent
    assert (base_path / 'config.example.json').exists()


def test_env_example_exists():
    """Test that .env example file exists"""
    base_path = Path(__file__).parent.parent
    assert (base_path / '.env.example').exists()


# Add your own tests below
# Example:
# def test_gtfs_loader():
#     from src.data_fetcher import GTFSLoader
#     loader = GTFSLoader('data/gtfs')
#     assert loader is not None
