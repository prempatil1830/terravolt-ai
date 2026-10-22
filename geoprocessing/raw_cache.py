"""
geoprocessing/raw_cache.py
--------------------------
Raw response cache for external geospatial and meteorological connectors:
- NASA POWER API
- OpenStreetMap Overpass API (substations, water bodies, land-cost proxy)

Requirements:
- Every NASA POWER and Overpass response is saved to geoprocessing/data/raw_cache/
  keyed by request (SHA-256 hash of URL/query), with timestamp.
- REPLAY mode (env var REPLAY=1): connectors read ONLY from this cache and never
  call the network.
"""

import os
import json
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


import threading

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw_cache")

_write_counter_lock = threading.Lock()
_cache_entries_written = 0

def reset_cache_write_counter() -> None:
    global _cache_entries_written
    with _write_counter_lock:
        _cache_entries_written = 0

def get_cache_entries_written() -> int:
    with _write_counter_lock:
        return _cache_entries_written

def is_replay_mode() -> bool:
    """Return True if REPLAY=1 environment variable is set."""
    val = os.environ.get("REPLAY", "").strip().lower()
    return val in ("1", "true", "yes")


def get_cache_dir() -> str:
    """Ensure and return the raw cache directory path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    return CACHE_DIR


def hash_request(request_key: str) -> str:
    """Return SHA-256 hex digest of the normalized request string."""
    normalized = " ".join(request_key.strip().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def get_cached(request_key: str) -> Optional[Any]:
    """
    Retrieve cached response for request_key.
    Returns the parsed response object if present, else None.
    """
    record = get_cache_record(request_key)
    return record.get("response") if record else None


def get_cache_record(request_key: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve entire cached record (request, request_hash, cached_at, metadata, response).
    """
    req_hash = hash_request(request_key)
    path = os.path.join(get_cache_dir(), f"{req_hash}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[raw_cache] Warning: failed to read cache {path}: {e}")
            return None
    return None


def save_cached(request_key: str, response_data: Any, metadata: Optional[Dict[str, Any]] = None) -> str:
    """
    Save raw response data to cache keyed by SHA-256 hash of request_key.
    Returns the cache file path.
    """
    req_hash = hash_request(request_key)
    path = os.path.join(get_cache_dir(), f"{req_hash}.json")
    record = {
        "request": request_key,
        "request_hash": req_hash,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
        "response": response_data,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    global _cache_entries_written
    with _write_counter_lock:
        _cache_entries_written += 1
    return path
