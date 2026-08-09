# Transport Informatics AR Project Template

A template repository for building augmented reality installations using public transit data (GTFS/GTFS-R).

## 📋 Project Overview

This template provides a standardized structure for developing AR-based transport informatics projects. Students can work in groups to create interactive installations that visualize real-time transit data.

### What is GTFS/GTFS-R?

- **GTFS (General Transit Feed Specification)**: Static transit data including routes, stops, schedules, and geographic information
- **GTFS-Realtime (GTFS-R)**: Live updates including vehicle positions, trip updates, and service alerts

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose (recommended for consistent environments)
- OR your preferred development environment (Python 3.8+, Node.js 18+, etc.)

### Getting Started

1. **Clone this repository**

   ```bash
   git clone <your-repo-url>
   cd template-repository
   ```

2. **Configure your project**

   ```bash
   cp config.example.json config.json
   cp .env.example .env
   # Edit config.json and .env with your transit feed URLs and API keys
   ```

3. **Build and run**

   ```bash
   make build    # Build the project
   make run      # Run the installation
   make test     # Run tests (if implemented)
   ```

## 📁 Project Structure

```
.
├── src/                    # Source code for your AR installation
│   ├── main.py            # Example entry point (rename as needed)
│   └── README.md          # Implementation details
├── data/                  # GTFS data files and cache
│   ├── gtfs/              # Static GTFS data
│   └── README.md          # Data documentation
├── docs/                  # Project documentation
│   └── ARCHITECTURE.md    # System design and architecture
├── examples/              # Example implementations
│   └── python/            # Python example
├── tests/                 # Test files
├── config.example.json    # Configuration template
├── .env.example           # Environment variables template
├── Dockerfile             # Container definition
├── docker-compose.yml     # Multi-service orchestration
├── Makefile               # Universal build commands
└── requirements.txt       # Python dependencies (if using Python)
```

## 🔧 Build System

This template uses a **Makefile** as a universal interface, regardless of your implementation language:

- `make build` - Build your project (installs dependencies, compiles if needed)
- `make run` - Run your AR installation
- `make test` - Run your test suite
- `make clean` - Clean build artifacts
- `make help` - Show all available commands

### Using Docker (Recommended)

Docker ensures your project runs consistently across all environments:

```bash
docker-compose up --build
```

### Without Docker

Update the Makefile with language-specific commands for your implementation.

## 🎯 Project Requirements

Your AR installation should:

1. **Fetch GTFS data** from a transit agency (static schedule data)
2. **Consume GTFS-R feeds** for real-time updates
3. **Visualize transit data** in an augmented reality format
4. **Document your approach** in `docs/ARCHITECTURE.md`
5. **Provide clear build/run instructions** in `src/README.md`

## 📊 Data Sources

### Finding GTFS Feeds

- [Mobility Database](https://database.mobilitydata.org/) - Catalog of 2000+ transit feeds
- [TransitFeeds](https://transitfeeds.com/) - Archive of GTFS data
- Local transit agencies (e.g., NYC MTA, BART, etc.)

### Example Feeds

- **NYC MTA**: Static GTFS and real-time feeds available
- **BART** (Bay Area): `https://www.bart.gov/schedules/developers/gtfs`
- **LA Metro**: Multiple feeds available via their developer portal

## 🛠️ Recommended Technologies

While you can use any programming language, here are some suggestions:

### Python (Recommended)

- `gtfs-realtime-bindings` - Parse GTFS-R protobuf
- `pandas` - Data manipulation
- `geopandas` - Geographic data
- AR frameworks: `PyOpenGL`, `Panda3D`, or web-based with `Flask/FastAPI`

### JavaScript/TypeScript

- `gtfs-realtime-bindings` (npm)
- AR.js, Three.js, A-Frame for web-based AR
- React/Vue for UI

### Other Options

- Unity (C#) for robust AR
- Processing/p5.js for creative coding
- Any language with protobuf support for GTFS-R

## 👥 Working in Groups

1. Use **feature branches** for development
2. Create **pull requests** for code review
3. Document decisions in `docs/`
4. Assign roles (data pipeline, AR visualization, testing, etc.)
5. Meet regularly and update your documentation

## 📝 Deliverables

- [ ] Working AR installation (runnable via `make run`)
- [ ] Documentation in `docs/ARCHITECTURE.md`
- [ ] Implementation in `src/` with clear README
- [ ] Test suite (basic coverage)
- [ ] Configuration files properly set up
- [ ] Demo video or screenshots (optional but recommended)

## 🆘 Troubleshooting

### Common Issues

1. **GTFS-R connection fails**: Check your API keys and feed URLs in `.env`
2. **Build fails**: Ensure all dependencies are listed in your language's dependency file
3. **Docker issues**: Try `docker-compose down` and rebuild

### Getting Help

- Review the examples in `examples/`
- Check the GTFS specification: <https://gtfs.org/>
- Ask your instructors or TAs

## 📚 Resources

- [GTFS Specification](https://gtfs.org/schedule/)
- [GTFS-Realtime Specification](https://gtfs.org/realtime/)
- [Mobility Database](https://database.mobilitydata.org/)
- [GTFS Best Practices](https://gtfs.org/schedule/best-practices/)

## 📄 License

This template is provided for educational purposes. Your project code is yours to license as you choose.
