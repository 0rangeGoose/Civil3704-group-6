# Source Code Directory

This directory contains your AR installation implementation.

## Getting Started

1. Implement your main entry point (e.g., `main.py`, `index.js`, `main.go`)
2. Organize your code into logical modules
3. Document your architecture in `../docs/ARCHITECTURE.md`

## Recommended Structure

```
src/
├── main.py                 # Entry point
├── data_fetcher/          # GTFS/GTFS-R data fetching
│   ├── __init__.py
│   ├── gtfs_loader.py     # Static GTFS loading
│   └── gtfs_realtime.py   # Real-time feed consumer
├── visualization/         # AR visualization logic
│   ├── __init__.py
│   ├── renderer.py        # Main rendering engine
│   └── ar_components.py   # AR-specific components
├── utils/                 # Helper functions
│   ├── __init__.py
│   ├── config.py          # Configuration loader
│   └── logger.py          # Logging setup
└── tests/                 # Unit tests
    └── __init__.py
```

## Key Components to Implement

### 1. Data Fetcher

- Load static GTFS data (routes, stops, schedules)
- Connect to GTFS-Realtime feed
- Parse and validate transit data
- Cache data appropriately

### 2. Data Processor

- Transform GTFS data into useful formats
- Calculate positions and timings
- Handle real-time updates
- Manage state

### 3. AR Visualization

- Render transit information in AR
- Handle user interactions
- Update display in real-time
- Manage AR markers/anchors

### 4. Configuration

- Load settings from `config.json` and `.env`
- Validate required parameters
- Provide sensible defaults

## Example Entry Points

### Python

```python
# main.py
from data_fetcher import GTFSLoader, GTFSRealtime
from visualization import ARRenderer
from utils import load_config

def main():
    config = load_config()
    gtfs = GTFSLoader(config['gtfs_url'])
    realtime = GTFSRealtime(config['gtfs_realtime_url'])
    renderer = ARRenderer()
    
    # Your AR installation logic here
    renderer.start()

if __name__ == "__main__":
    main()
```

### JavaScript/Node.js

```javascript
// index.js
const { GTFSLoader } = require('./data_fetcher');
const { ARRenderer } = require('./visualization');
const config = require('../config.json');

async function main() {
    const gtfs = new GTFSLoader(config.gtfs_url);
    const renderer = new ARRenderer();
    
    await gtfs.load();
    renderer.start();
}

main().catch(console.error);
```

## Testing

Add tests in the `tests/` directory. Run with:

```bash
make test
```

## Documentation

Document your implementation decisions, API endpoints, and any special considerations in the main documentation directory.
