# Python Example: GTFS Data Loading

This example demonstrates how to load and process GTFS data using Python.

## Setup

```bash
pip install pandas gtfs-realtime-bindings requests
```

## Files

- `gtfs_loader.py` - Load static GTFS data
- `gtfs_realtime.py` - Connect to GTFS-Realtime feed
- `example.py` - Complete example

## Usage

```bash
cd examples/python
python example.py
```

## Key Libraries

- **pandas**: Data manipulation and analysis
- **gtfs-realtime-bindings**: Parse GTFS-Realtime protobuf
- **requests**: HTTP requests for downloading feeds
- **geopandas**: Geographic data operations (optional)

## GTFS Data Structure

GTFS consists of several CSV files:

- `agency.txt` - Transit agencies
- `stops.txt` - Stop locations (lat/lon)
- `routes.txt` - Transit routes
- `trips.txt` - Individual trips on routes
- `stop_times.txt` - Times vehicles arrive/depart stops
- `calendar.txt` - Service schedules

## Example: Loading Stops

```python
import pandas as pd

# Load stops data
stops = pd.read_csv('data/gtfs/stops.txt')

# Filter to specific area
nearby_stops = stops[
    (stops['stop_lat'] > 37.7) & 
    (stops['stop_lat'] < 37.8) &
    (stops['stop_lon'] > -122.5) & 
    (stops['stop_lon'] < -122.4)
]

print(f"Found {len(nearby_stops)} stops in area")
```

## Example: Real-time Vehicle Positions

```python
from google.transit import gtfs_realtime_pb2
import requests

# Fetch GTFS-Realtime feed
feed_url = "https://your-agency.com/gtfs-realtime/vehicle-positions"
response = requests.get(feed_url)

# Parse protobuf
feed = gtfs_realtime_pb2.FeedMessage()
feed.ParseFromString(response.content)

# Extract vehicle positions
for entity in feed.entity:
    if entity.HasField('vehicle'):
        vehicle = entity.vehicle
        print(f"Vehicle {vehicle.vehicle.id}:")
        print(f"  Position: {vehicle.position.latitude}, {vehicle.position.longitude}")
        print(f"  Route: {vehicle.trip.route_id}")
```

See the complete examples in this directory!
