#!/usr/bin/env python3
"""Phase 4 entry point: starts the live feed + WebSocket/HTTP server and
serves the renderer. One command, per the brief's definition of done.

Requires TFNSW_API_KEY in .env and the Phase 1 SQLite cache.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.publish.server import main

if __name__ == "__main__":
    main()
