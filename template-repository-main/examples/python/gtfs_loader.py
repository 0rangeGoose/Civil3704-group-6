"""
Example: Loading GTFS Static Data

This script demonstrates how to load and explore GTFS static data files.
"""

import pandas as pd
from pathlib import Path
from typing import Dict
import sys


class GTFSLoader:
    """Load and manage GTFS static data"""
    
    def __init__(self, gtfs_path: str):
        """Initialize with path to GTFS directory"""
        self.gtfs_path = Path(gtfs_path)
        self.data: Dict[str, pd.DataFrame] = {}
        
    def load_all(self):
        """Load all GTFS files"""
        files = {
            'agency': 'agency.txt',
            'stops': 'stops.txt',
            'routes': 'routes.txt',
            'trips': 'trips.txt',
            'stop_times': 'stop_times.txt',
            'calendar': 'calendar.txt',
        }
        
        for name, filename in files.items():
            filepath = self.gtfs_path / filename
            if filepath.exists():
                print(f"Loading {filename}...")
                self.data[name] = pd.read_csv(filepath)
            else:
                print(f"Warning: {filename} not found")
    
    def get_stops_in_area(self, min_lat: float, max_lat: float, 
                          min_lon: float, max_lon: float) -> pd.DataFrame:
        """Get stops within a geographic bounding box"""
        stops = self.data.get('stops')
        if stops is None:
            return pd.DataFrame()
        
        return stops[
            (stops['stop_lat'] >= min_lat) &
            (stops['stop_lat'] <= max_lat) &
            (stops['stop_lon'] >= min_lon) &
            (stops['stop_lon'] <= max_lon)
        ]
    
    def get_routes_by_type(self, route_type: int) -> pd.DataFrame:
        """
        Get routes by type:
        0 - Tram/Light rail
        1 - Subway/Metro
        2 - Rail
        3 - Bus
        4 - Ferry
        """
        routes = self.data.get('routes')
        if routes is None:
            return pd.DataFrame()
        
        return routes[routes['route_type'] == route_type]
    
    def print_summary(self):
        """Print summary of loaded GTFS data"""
        print("\n" + "="*60)
        print("GTFS Data Summary")
        print("="*60)
        
        for name, df in self.data.items():
            print(f"{name:15s}: {len(df):6d} records")
        
        if 'stops' in self.data:
            stops = self.data['stops']
            print(f"\nGeographic bounds:")
            print(f"  Latitude:  {stops['stop_lat'].min():.4f} to {stops['stop_lat'].max():.4f}")
            print(f"  Longitude: {stops['stop_lon'].min():.4f} to {stops['stop_lon'].max():.4f}")


def main():
    # Path to GTFS data
    gtfs_path = Path(__file__).parent.parent.parent / 'data' / 'gtfs'
    
    if not gtfs_path.exists():
        print(f"Error: GTFS data not found at {gtfs_path}")
        print("Please download GTFS data to data/gtfs/ directory")
        sys.exit(1)
    
    # Load GTFS data
    loader = GTFSLoader(gtfs_path)
    loader.load_all()
    loader.print_summary()
    
    # Example: Find subway routes
    print("\n" + "="*60)
    print("Example: Subway/Metro Routes")
    print("="*60)
    subways = loader.get_routes_by_type(1)
    if not subways.empty:
        for _, route in subways.iterrows():
            print(f"  {route['route_short_name']:5s} - {route['route_long_name']}")
    else:
        print("  No subway routes found in this GTFS feed")


if __name__ == "__main__":
    main()
