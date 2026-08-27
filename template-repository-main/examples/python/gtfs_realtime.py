"""
Example: GTFS-Realtime Vehicle Positions

This script demonstrates how to fetch and parse GTFS-Realtime vehicle positions.
Install: pip install gtfs-realtime-bindings requests
"""

import os
import sys
from datetime import datetime
from google.transit import gtfs_realtime_pb2
import requests


class GTFSRealtime:
    """Connect to and parse GTFS-Realtime feeds"""
    
    def __init__(self, feed_url: str, api_key: str = None):
        """
        Initialize GTFS-Realtime consumer
        
        Args:
            feed_url: URL to GTFS-Realtime feed
            api_key: Optional API key (some feeds require authentication)
        """
        self.feed_url = feed_url
        self.api_key = api_key
        self.headers = {}
        
        if api_key:
            self.headers['Authorization'] = f'Bearer {api_key}'
    
    def fetch_vehicle_positions(self):
        """Fetch and parse vehicle positions"""
        try:
            response = requests.get(self.feed_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            
            vehicles = []
            for entity in feed.entity:
                if entity.HasField('vehicle'):
                    vehicle = entity.vehicle
                    vehicles.append({
                        'id': vehicle.vehicle.id if vehicle.vehicle.HasField('id') else 'unknown',
                        'route_id': vehicle.trip.route_id if vehicle.trip.HasField('route_id') else None,
                        'trip_id': vehicle.trip.trip_id if vehicle.trip.HasField('trip_id') else None,
                        'latitude': vehicle.position.latitude if vehicle.position.HasField('latitude') else None,
                        'longitude': vehicle.position.longitude if vehicle.position.HasField('longitude') else None,
                        'bearing': vehicle.position.bearing if vehicle.position.HasField('bearing') else None,
                        'speed': vehicle.position.speed if vehicle.position.HasField('speed') else None,
                        'timestamp': vehicle.timestamp if vehicle.HasField('timestamp') else None,
                    })
            
            return vehicles
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching GTFS-Realtime feed: {e}")
            return []
        except Exception as e:
            print(f"Error parsing GTFS-Realtime data: {e}")
            return []
    
    def fetch_trip_updates(self):
        """Fetch and parse trip updates (delays, cancellations)"""
        try:
            response = requests.get(self.feed_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            
            updates = []
            for entity in feed.entity:
                if entity.HasField('trip_update'):
                    trip_update = entity.trip_update
                    updates.append({
                        'trip_id': trip_update.trip.trip_id if trip_update.trip.HasField('trip_id') else None,
                        'route_id': trip_update.trip.route_id if trip_update.trip.HasField('route_id') else None,
                        'delay': trip_update.delay if trip_update.HasField('delay') else 0,
                    })
            
            return updates
            
        except Exception as e:
            print(f"Error fetching trip updates: {e}")
            return []


def main():
    # Get feed URL from environment variable
    feed_url = os.environ.get('GTFS_REALTIME_VEHICLE_POSITIONS_URL')
    
    if not feed_url:
        print("Error: GTFS_REALTIME_VEHICLE_POSITIONS_URL not set")
        print("\nSet it in your .env file:")
        print("GTFS_REALTIME_VEHICLE_POSITIONS_URL=https://your-agency.com/gtfs-rt/vehicle-positions")
        sys.exit(1)
    
    # Initialize GTFS-Realtime consumer
    api_key = os.environ.get('GTFS_API_KEY')
    gtfs_rt = GTFSRealtime(feed_url, api_key)
    
    print("="*60)
    print("GTFS-Realtime Vehicle Positions")
    print("="*60)
    print(f"Feed URL: {feed_url}")
    print(f"Fetching data...\n")
    
    # Fetch vehicle positions
    vehicles = gtfs_rt.fetch_vehicle_positions()
    
    if not vehicles:
        print("No vehicles found or error fetching data")
        return
    
    print(f"Found {len(vehicles)} vehicles\n")
    
    # Display first 10 vehicles
    for i, vehicle in enumerate(vehicles[:10], 1):
        print(f"Vehicle {i}:")
        print(f"  ID: {vehicle['id']}")
        print(f"  Route: {vehicle['route_id']}")
        print(f"  Position: ({vehicle['latitude']:.4f}, {vehicle['longitude']:.4f})")
        if vehicle['bearing'] is not None:
            print(f"  Bearing: {vehicle['bearing']:.1f}°")
        if vehicle['speed'] is not None:
            print(f"  Speed: {vehicle['speed']:.1f} m/s")
        if vehicle['timestamp']:
            dt = datetime.fromtimestamp(vehicle['timestamp'])
            print(f"  Last update: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
        print()
    
    if len(vehicles) > 10:
        print(f"... and {len(vehicles) - 10} more vehicles")


if __name__ == "__main__":
    main()
