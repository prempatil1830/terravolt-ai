"""
geoprocessing/fetch_nasa_power.py
----------------------------------
Fetches 20-year climatological averages from the NASA POWER API for a
single lat/lon point.

Criteria reference: criteria.md § Criterion 1 (GHI) and § Criterion 7
(Temperature derating).

API endpoint (no auth required):
  https://power.larc.nasa.gov/api/temporal/climatology/point
  Temporal coverage: 2001–2020 (20-year climatological average, per API header)
  Spatial resolution of source data: ~0.5° × 0.5° (~55 km grid)

Returned parameters
-------------------
ALLSKY_SFC_SW_DWN  : GHI — all-sky surface shortwave downward irradiance
                     Unit: kWh/m²/day (daily mean annual average)
T2M                : Temperature at 2 m — annual mean
                     Unit: °C
T2M_MAX            : Temperature at 2 m — daily maximum, annual mean
                     Unit: °C
"""

import threading
import time
from typing import NamedTuple, Callable

import requests


# ---------------------------------------------------------------------------
# Exceptions & Rate Limiting
# ---------------------------------------------------------------------------

class NASAPowerRateLimitExceeded(requests.HTTPError):
    """Raised when NASA POWER API returns HTTP 429 Too Many Requests."""
    pass


class GlobalRateLimiter:
    """
    Thread-safe global rate limiter capping calls at `max_per_minute` (default 50.0).
    Enforces a minimum spacing of (60.0 / max_per_minute) seconds between requests across all threads.
    """
    def __init__(
        self,
        max_per_minute: float = 50.0,
        time_func: Callable[[], float] = time.time,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        self.max_per_minute = max_per_minute
        self.min_interval = 60.0 / max_per_minute  # 1.2s for 50/min
        self.lock = threading.Lock()
        self.last_call_time = 0.0
        self.time_func = time_func
        self.sleep_func = sleep_func

    def acquire(self) -> float:
        """
        Wait if necessary to ensure at least `min_interval` seconds since the previous call across all threads.
        Returns the timestamp when acquired.
        """
        with self.lock:
            now = self.time_func()
            elapsed = now - self.last_call_time
            if elapsed < self.min_interval:
                sleep_s = self.min_interval - elapsed
                self.sleep_func(sleep_s)
            self.last_call_time = self.time_func()
            return self.last_call_time


# Module-level shared rate limiter across all worker threads
_rate_limiter = GlobalRateLimiter(max_per_minute=50.0)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"

# Parameters requested from the API (comma-separated in the query string).
_PARAMETERS = "ALLSKY_SFC_SW_DWN,T2M,T2M_MAX"

# The API returns the "ANN" (annual) key inside each parameter's data dict.
_ANNUAL_KEY = "ANN"

# Timeout for each HTTP request (seconds).
_REQUEST_TIMEOUT = 30

# Simple retry settings — NASA POWER can occasionally return 5xx transiently.
_MAX_RETRIES = 3
_RETRY_BACKOFF_S = 2.0


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class NASAPowerResult(NamedTuple):
    """Climatological annual averages for a single lat/lon point."""
    latitude: float
    longitude: float
    ghi_kwh_m2_day: float   # ALLSKY_SFC_SW_DWN — kWh/m²/day
    t2m_celsius: float      # T2M annual mean — °C
    t2m_max_celsius: float  # T2M_MAX annual mean — °C


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# In-memory cache for NASA POWER results
_cache: dict[tuple[float, float], NASAPowerResult] = {}
_cache_lock = threading.Lock()


def fetch_climatology(lat: float, lon: float) -> NASAPowerResult:
    """
    Query the NASA POWER climatology endpoint for the given point and
    return GHI, T2M, and T2M_MAX annual averages.
    Results are cached in-memory by rounded coordinate.
    """
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Latitude must be in [-90, 90]; got {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Longitude must be in [-180, 180]; got {lon}")

    key = (round(lat, 4), round(lon, 4))
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    params = {
        "parameters": _PARAMETERS,
        "community": "RE",          # Renewable Energy community dataset
        "longitude": lon,
        "latitude": lat,
        "format": "JSON",
    }

    response = _get_with_retry(_BASE_URL, params)
    result = _parse_response(response, lat, lon)

    with _cache_lock:
        _cache[key] = result

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Thread-safe counter for live NASA POWER API calls
_live_nasa_calls = 0
_live_nasa_calls_lock = threading.Lock()

def reset_live_nasa_calls() -> None:
    global _live_nasa_calls
    with _live_nasa_calls_lock:
        _live_nasa_calls = 0

def get_live_nasa_calls() -> int:
    with _live_nasa_calls_lock:
        return _live_nasa_calls


def _get_with_retry(url: str, params: dict) -> dict:
    """GET with thread-safe rate-limiting, raw-output caching, and simple exponential-backoff retry on 5xx responses."""
    import urllib.parse
    try:
        from raw_cache import get_cached, save_cached, is_replay_mode
    except ImportError:
        from geoprocessing.raw_cache import get_cached, save_cached, is_replay_mode

    req_key = f"NASA_POWER: url={url}?{urllib.parse.urlencode(sorted(params.items()))}"
    cached = get_cached(req_key)
    if is_replay_mode():
        if cached is not None:
            return cached
        raise RuntimeError(f"REPLAY mode active: missing cache for {req_key}")

    if cached is not None:
        return cached

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            # Enforce global rate limiter across all threads (< 50 req/min)
            _rate_limiter.acquire()

            global _live_nasa_calls
            with _live_nasa_calls_lock:
                _live_nasa_calls += 1

            resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)

            # Explicit 429 handling: do not swallow or silently substitute
            if resp.status_code == 429:
                raise NASAPowerRateLimitExceeded(
                    f"NASA POWER API rate limit exceeded (HTTP 429) for {url}: {resp.text[:200]}"
                )

            resp.raise_for_status()
            data = resp.json()
            save_cached(req_key, data, {"service": "NASA_POWER", "url": url, "params": params})
            return data

        except NASAPowerRateLimitExceeded:
            # Explicitly propagate 429 — must never silently substitute fake data
            raise

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            if exc.response is not None and exc.response.status_code == 429:
                raise NASAPowerRateLimitExceeded(
                    f"NASA POWER API rate limit exceeded (HTTP 429) for {url}: {exc}"
                ) from exc
            if exc.response is not None and exc.response.status_code < 500:
                # 4xx — not transient; raise immediately
                raise
            print(f"  [retry {attempt}/{_MAX_RETRIES}] HTTP {status} from NASA POWER — "
                  f"waiting {_RETRY_BACKOFF_S * attempt:.1f}s …")
            last_exc = exc
        except requests.ConnectionError as exc:
            print(f"  [retry {attempt}/{_MAX_RETRIES}] Connection error — "
                  f"waiting {_RETRY_BACKOFF_S * attempt:.1f}s …")
            last_exc = exc
        except requests.Timeout as exc:
            print(f"  [retry {attempt}/{_MAX_RETRIES}] Timeout — "
                  f"waiting {_RETRY_BACKOFF_S * attempt:.1f}s …")
            last_exc = exc

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_BACKOFF_S * attempt)

    raise RuntimeError(
        f"NASA POWER API unreachable after {_MAX_RETRIES} attempts."
    ) from last_exc


def _parse_response(data: dict, lat: float, lon: float) -> NASAPowerResult:
    """Extract the three annual-average values from a NASA POWER JSON response."""
    try:
        props = data["properties"]["parameter"]
    except KeyError as exc:
        raise KeyError(
            f"Unexpected NASA POWER response structure — missing 'properties.parameter'. "
            f"Keys received: {list(data.keys())}"
        ) from exc

    def _extract(param_name: str) -> float:
        if param_name not in props:
            raise KeyError(
                f"Parameter '{param_name}' not found in NASA POWER response. "
                f"Available parameters: {list(props.keys())}"
            )
        annual_value = props[param_name].get(_ANNUAL_KEY)
        if annual_value is None:
            raise KeyError(
                f"Annual key '{_ANNUAL_KEY}' missing for parameter '{param_name}'. "
                f"Available keys: {list(props[param_name].keys())}"
            )
        if annual_value == -999.0:
            # NASA POWER uses -999 as a fill/missing-data sentinel
            raise ValueError(
                f"NASA POWER returned fill value (-999) for '{param_name}' at "
                f"({lat}, {lon}). The point may be over ocean or outside data coverage."
            )
        return float(annual_value)

    ghi     = _extract("ALLSKY_SFC_SW_DWN")
    t2m     = _extract("T2M")
    t2m_max = _extract("T2M_MAX")

    return NASAPowerResult(
        latitude=lat,
        longitude=lon,
        ghi_kwh_m2_day=ghi,
        t2m_celsius=t2m,
        t2m_max_celsius=t2m_max,
    )
