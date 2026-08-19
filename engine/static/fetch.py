"""Fetch and cache the Transport for NSW static GTFS bundle.

Two ways a bundle gets into the cache:

- ``ensure_static_bundle`` downloads it fresh from the Open Data Hub when the
  cache is missing or older than ``refresh_interval_hours``, using
  ``TFNSW_API_KEY`` from the environment.
- ``seed_from_local`` points the cache at an already-downloaded extract (a
  directory of GTFS .txt files) without hitting the network. Useful for
  bootstrapping development, or when a bundle was fetched by hand.

Either way, ``data/static_cache/manifest.json`` records where the extracted
files live, when they were fetched, and a checksum, so repeated runs within
the refresh window don't refetch or reprocess anything.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from engine.config import Config

REQUIRED_FILES = (
    "agency.txt",
    "routes.txt",
    "trips.txt",
    "stops.txt",
    "stop_times.txt",
    "shapes.txt",
    "calendar.txt",
    "calendar_dates.txt",
)

_CHUNK = 8 * 1024 * 1024


class StaticBundleError(RuntimeError):
    pass


@dataclass
class Manifest:
    source: str
    fetched_at: str
    extracted_dir: str
    checksum: str

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(data: dict) -> "Manifest":
        return Manifest(**data)


def _manifest_path(cfg: Config) -> Path:
    return cfg.path("manifest_path")


def _read_manifest(cfg: Config) -> Manifest | None:
    path = _manifest_path(cfg)
    if not path.exists():
        return None
    try:
        return Manifest.from_json(json.loads(path.read_text()))
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _write_manifest(cfg: Config, manifest: Manifest) -> None:
    path = _manifest_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.to_json(), indent=2))


def _hash_directory(directory: Path) -> str:
    """Cheap-ish content checksum: sha256 of each required file, concatenated."""
    digest = hashlib.sha256()
    for name in REQUIRED_FILES:
        file_path = directory / name
        if not file_path.exists():
            continue
        digest.update(name.encode())
        file_digest = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(_CHUNK), b""):
                file_digest.update(chunk)
        digest.update(file_digest.hexdigest().encode())
    return digest.hexdigest()


def _is_fresh(cfg: Config, manifest: Manifest) -> bool:
    fetched_at = datetime.fromisoformat(manifest.fetched_at)
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
    return age_hours < cfg.static_gtfs["refresh_interval_hours"]


def _validate_extracted_dir(directory: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (directory / name).exists()]
    if missing:
        raise StaticBundleError(
            f"{directory} is missing required GTFS files: {', '.join(missing)}"
        )


def seed_from_local(cfg: Config, source_dir: Path) -> Path:
    """Point the cache at an already-downloaded GTFS extract, no network."""
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise StaticBundleError(f"seed directory does not exist: {source_dir}")
    _validate_extracted_dir(source_dir)

    checksum = _hash_directory(source_dir)
    manifest = Manifest(
        source=f"local-seed:{source_dir}",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        extracted_dir=str(source_dir),
        checksum=checksum,
    )
    _write_manifest(cfg, manifest)
    return source_dir


def _download(cfg: Config) -> Path:
    if not cfg.api_key:
        raise StaticBundleError(
            "TFNSW_API_KEY is not set. Add it to .env, or seed the cache from an "
            "already-downloaded bundle with seed_from_local()."
        )

    url = cfg.static_gtfs["download_url"]
    raw_zip_path = cfg.path("raw_zip_path")
    raw_zip_path.parent.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"apikey {cfg.api_key}"}
    try:
        with httpx.stream("GET", url, headers=headers, timeout=120, follow_redirects=True) as resp:
            if resp.status_code != 200:
                raise StaticBundleError(
                    f"GTFS static download failed: {resp.status_code} {resp.reason_phrase} for {url}"
                )
            with open(raw_zip_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=_CHUNK):
                    f.write(chunk)
    except httpx.HTTPError as exc:
        raise StaticBundleError(f"GTFS static download failed: {exc}") from exc

    extract_dir = cfg.path("cache_dir") / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw_zip_path) as zf:
        zf.extractall(extract_dir)

    _validate_extracted_dir(extract_dir)
    checksum = _hash_directory(extract_dir)
    manifest = Manifest(
        source=f"api:{url}",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        extracted_dir=str(extract_dir),
        checksum=checksum,
    )
    _write_manifest(cfg, manifest)
    return extract_dir


def ensure_static_bundle(cfg: Config, force_refresh: bool = False) -> Path:
    """Return a directory of GTFS .txt files, fetching only if cache is stale/missing."""
    manifest = _read_manifest(cfg)
    if manifest and not force_refresh:
        extracted_dir = Path(manifest.extracted_dir)
        if extracted_dir.is_dir() and _is_fresh(cfg, manifest):
            _validate_extracted_dir(extracted_dir)
            return extracted_dir

    return _download(cfg)
