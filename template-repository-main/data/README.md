# Data Directory

This directory stores GTFS data files and cache.

## Structure

```
data/
├── gtfs/              # Static GTFS data
│   ├── agency.txt
│   ├── routes.txt
│   ├── stops.txt
│   ├── trips.txt
│   ├── stop_times.txt
│   ├── calendar.txt
│   └── shapes.txt
├── cache/             # Cached processed data
└── README.md          # This file
```

## GTFS Data

Download GTFS data from your chosen transit agency and extract it to `data/gtfs/`.

### Required Files (Core GTFS)

- `agency.txt` - Transit agency information
- `stops.txt` - Stop/station locations
- `routes.txt` - Transit routes
- `trips.txt` - Individual trips
- `stop_times.txt` - Stop times for each trip

### Optional Files

- `calendar.txt` - Service schedules
- `calendar_dates.txt` - Service exceptions
- `shapes.txt` - Route geometries
- `frequencies.txt` - Headway-based service

## GTFS-Realtime

GTFS-Realtime data is fetched from APIs in real-time and should not be stored here.
Configure your feed URLs in `.env`:

```
GTFS_REALTIME_VEHICLE_POSITIONS_URL=https://...
GTFS_REALTIME_TRIP_UPDATES_URL=https://...
GTFS_REALTIME_SERVICE_ALERTS_URL=https://...
```

## Data Sources

Find GTFS feeds at:

- [Mobility Database](https://database.mobilitydata.org/)
- [TransitFeeds](https://transitfeeds.com/)
- Your local transit agency's developer portal

## .gitignore

Large data files are typically excluded from git. Add to `.gitignore`:

```
data/gtfs/*.txt
data/gtfs/*.zip
data/cache/*
```

Keep this README and directory structure in version control.
