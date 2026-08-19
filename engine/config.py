"""Load config.yaml and .env into a single config object."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class BoundingBox:
    north: float
    south: float
    west: float
    east: float


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path

    @property
    def bounding_box(self) -> BoundingBox:
        bb = self.raw["bounding_box"]
        return BoundingBox(bb["north"], bb["south"], bb["west"], bb["east"])

    @property
    def static_gtfs(self) -> dict[str, Any]:
        return self.raw["static_gtfs"]

    def path(self, key: str) -> Path:
        """Resolve a static_gtfs.* path entry relative to the repo root."""
        return self.root / self.static_gtfs[key]

    @property
    def api_key(self) -> str | None:
        return os.environ.get("TFNSW_API_KEY") or None


def load_config(config_path: Path | None = None) -> Config:
    root = REPO_ROOT
    config_path = config_path or root / "config" / "config.yaml"
    _load_dotenv(root / ".env")
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return Config(raw=raw, root=root)
