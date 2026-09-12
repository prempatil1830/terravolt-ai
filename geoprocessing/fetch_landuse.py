"""
geoprocessing/fetch_landuse.py
-------------------------------
Downloads ESA WorldCover 2021 (v200) tiles covering Karnataka's bounding box,
caches them locally, and exposes three public functions:

    get_landuse_class(lat, lon) -> int
        Returns the WorldCover class code (10, 20, 30, ...) at a point.

    get_suitability(lat, lon) -> str
        Returns "eligible" | "eligible_penalised" | "excluded" per
        criteria.md § Criterion 3.  Unchanged from original — backward
        compatible; does NOT set low_confidence flags.

    get_suitability_full(lat, lon) -> LanduseResult
        Returns a NamedTuple with class_code, class_name, suitability,
        low_confidence (bool), and confidence_reason (str | None).
        For tree-cover pixels inside the documented Bellary/Hampi/
        Kalaburagi granite-scrub misclassification belt, sets
        low_confidence=True and a reason string citing the Hampi
        UNESCO/ICOMOS precedent and the FSI cross-check recommendation
        from criteria.md § Criterion 3.  Suitability is still returned
        as "excluded" — exclusion logic is unchanged; the low_confidence
        flag signals that this specific exclusion needs manual review
        before being accepted in the final candidate shortlist.

Data source (no auth required)
-------------------------------
ESA WorldCover 2021 v200 — AWS Open Data public S3 bucket.
Bucket  : esa-worldcover  (region: eu-central-1)
HTTPS   : https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/<tile>.tif
License : Creative Commons Attribution 4.0 International (CC BY 4.0)

Why this source over ESA worldcover2021.esa.int direct download:
  The ESA portal (worldcover2021.esa.int) requires a Terrascope account
  (free but mandatory) for bulk tile download.  The AWS Open Data mirror
  is byte-identical, publicly listed on registry.opendata.aws, and
  accessible via plain HTTPS GET — no credentials, no AWS account needed.
  The Google Earth Engine collection (ESA/WorldCover/v200) requires a GEE
  account and OAuth2 flow.

Tile naming convention:
    Tiles are 3 degrees x 3 degrees, named by lower-left corner.
    ESA_WorldCover_10m_2021_v200_N<LL>E<LLL>_Map.tif
    e.g. N12E075 covers 12-15N, 75-78E.

Class codes (WorldCover 2021, from criteria.md § Criterion 3):
    10  Tree cover          EXCLUDED
    20  Shrubland           eligible
    30  Grassland           eligible
    40  Cropland            eligible_penalised
    50  Built-up / Urban    EXCLUDED
    60  Bare / sparse veg   eligible
    80  Permanent water     EXCLUDED
    90  Herbaceous wetland  EXCLUDED
    95  Mangroves           EXCLUDED

Caching:
    Tiles cached in geoprocessing/data/worldcover_tiles/
    Individual tile files opened lazily; datasets kept open as a module-
    level dict for sub-millisecond repeat lookups.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import NamedTuple, Optional

import requests
import rasterio
from rasterio.transform import rowcol

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Karnataka bounding box — integer degree corners that define which tiles to
# download (tiles are 3x3°; we snap to 3° boundaries in _tile_id()).
_LAT_MIN = 11   # Karnataka southern extent ~11.5N
_LAT_MAX = 18   # Karnataka northern extent ~18.5N
_LON_MIN = 74   # Karnataka western extent ~74E
_LON_MAX = 78   # Karnataka eastern extent ~78.5E

# AWS Open Data HTTPS endpoint — no auth required.
_S3_BASE = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com"
    "/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)

# Local cache directory (relative to this file).
_HERE = Path(__file__).parent
_TILE_DIR = _HERE / "data" / "worldcover_tiles"

# HTTP settings.
_REQUEST_TIMEOUT = 300   # seconds — tiles are 76–128 MB each
_MAX_RETRIES = 3
_RETRY_BACKOFF_S = 5.0

# ---------------------------------------------------------------------------
# WorldCover class catalogue
# From criteria.md § Criterion 3 — Excluded Class Codes table.
# ---------------------------------------------------------------------------

# Map class code -> (human-readable name, suitability decision)
# Suitability: "eligible" | "eligible_penalised" | "excluded"
CLASS_CATALOGUE: dict[int, tuple[str, str]] = {
    10: ("Tree cover",              "excluded"),
    20: ("Shrubland",               "eligible"),
    30: ("Grassland",               "eligible"),
    40: ("Cropland",                "eligible_penalised"),
    50: ("Built-up / Urban",        "excluded"),
    60: ("Bare / sparse vegetation","eligible"),
    80: ("Permanent water bodies",  "excluded"),
    90: ("Herbaceous wetland",      "excluded"),
    95: ("Mangroves",               "excluded"),
}

# Sentinel for unknown/unlisted codes.
_UNKNOWN_LABEL = "Unknown / not in WorldCover spec"
_UNKNOWN_SUIT  = "excluded"   # conservative default: exclude if class unknown

# ---------------------------------------------------------------------------
# Misclassification risk zone
# ---------------------------------------------------------------------------
# The Bellary / Hampi / Kalaburagi / Bidar belt of the Deccan plateau is
# documented in criteria.md § Criterion 3 Known Limitations as having known
# WorldCover commission errors: open rocky granite scrubland is wrongly
# classified as Tree cover (code 10), which would silently hard-exclude
# viable solar sites.
#
# Empirical confirmation from validate_landuse.py:
#   Hampi/Anegundi (15.3200N, 76.4300E): UNESCO WHC 1986 nomination and
#   ICOMOS evaluation describe the landscape as "granite boulders with dry
#   thorny scrub" — yet WorldCover returns code 10 (Tree cover).
#   Daroji Sloth Bear Sanctuary environs (15.2000N, 76.6500E): Karnataka
#   Forest Dept sanctuary notification cites "open rocky scrub with boulders"
#   as defining habitat — yet WorldCover also returns code 10.
#
# Bounding box chosen to cover:
#   - Bellary district (granite scrub terrain, ~14.5-15.8N, 75.8-77.6E)
#   - Hampi / Hospet zone (~15.0-15.5N, 76.0-76.8E)
#   - Raichur / Yadgir transition zone (~15.8-16.9N, 76.0-77.5E)
#   - Kalaburagi district (~16.9-17.9N, 76.0-77.8E)
#   - Bidar district (~17.5-18.3N, 76.7-77.8E) — included despite being
#     at the northern edge because ISFR 2021 (Vol II, Karnataka district-wise
#     table) records Bidar forest cover at <2%, yet WorldCover shows
#     scattered tree-cover pixels on the laterite plateau.
#
# The bbox is deliberately wider than the confirmed misclassification points
# to act as a conservative screening zone.  It does NOT add a new exclusion
# — it adds a review flag to existing tree-cover exclusions only.
#
# These bounds can be tightened in Phase 2 once the FSI / Bhuvan LULC
# cross-check is completed (per criteria.md § Criterion 3 recommendation).

_MISCLASS_RISK_ZONE_LAT_MIN: float = 14.5
_MISCLASS_RISK_ZONE_LAT_MAX: float = 18.3   # includes Bidar northern edge
_MISCLASS_RISK_ZONE_LON_MIN: float = 75.8   # includes Hampi/Hospet western fringe
_MISCLASS_RISK_ZONE_LON_MAX: float = 77.8   # includes Bidar eastern edge

# Reason string attached to low-confidence results (kept in one place so it
# can be updated without touching function bodies).
_MISCLASS_RISK_REASON: str = (
    "Tree cover (code 10) in the documented Bellary/Hampi/Kalaburagi/Bidar "
    "granite-scrub misclassification belt (approx 14.5-18.3N, 75.8-77.8E). "
    "ESA WorldCover 2021 has known commission errors in this region, mapping "
    "open rocky granite scrubland as Tree cover. "
    "Confirmed instance: Hampi/Anegundi (15.3200N, 76.4300E) — UNESCO WHC 1986 "
    "nomination and ICOMOS evaluation describe the landscape as 'granite boulders "
    "with dry thorny scrub', yet WorldCover returns code 10 (Tree cover). "
    "Per criteria.md § Criterion 3 Known Limitations and § Compounding Data "
    "Uncertainty: cross-check this cell against FSI forest map (ISFR 2023) or "
    "NRSC Bhuvan LULC 50K before accepting the exclusion in the final candidate "
    "shortlist. Do not automatically drop this site from the candidate set."
)

# ---------------------------------------------------------------------------
# Tile ID helpers
# ---------------------------------------------------------------------------

def _tile_id(lat: float, lon: float) -> str:
    """
    Return the WorldCover tile identifier whose 3x3-degree cell contains
    (lat, lon).  Tiles are named by their lower-left (SW) corner, snapped
    to multiples of 3 degrees.

    Example: lat=12.42, lon=75.74  ->  N12E075
             lat=17.33, lon=76.84  ->  N15E075
    """
    tile_lat = math.floor(lat / 3) * 3
    tile_lon = math.floor(lon / 3) * 3
    ns = "N" if tile_lat >= 0 else "S"
    ew = "E" if tile_lon >= 0 else "W"
    return f"{ns}{abs(tile_lat):02d}{ew}{abs(tile_lon):03d}"


def _tile_url(tile: str) -> str:
    return _S3_BASE.format(tile=tile)


def _tile_local_path(tile: str) -> Path:
    return _TILE_DIR / f"ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_tile(tile: str) -> Path:
    """
    Download a WorldCover tile if not already cached. Returns local path.
    Raises RuntimeError after all retries on persistent failure.
    """
    local = _tile_local_path(tile)
    if local.exists():
        return local

    url = _tile_url(tile)
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT, stream=True)
            resp.raise_for_status()

            local.parent.mkdir(parents=True, exist_ok=True)
            bytes_written = 0
            with open(local, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=4 << 20):  # 4 MB chunks
                    fh.write(chunk)
                    bytes_written += len(chunk)

            print(f"  Downloaded: {tile}  ({bytes_written / 1e6:.0f} MB)")
            return local

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            if exc.response is not None and exc.response.status_code < 500:
                raise
            print(
                f"  [retry {attempt}/{_MAX_RETRIES}] HTTP {status} for {tile} -- "
                f"waiting {_RETRY_BACKOFF_S * attempt:.0f}s ..."
            )
            last_exc = exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            print(
                f"  [retry {attempt}/{_MAX_RETRIES}] Network error for {tile} -- "
                f"waiting {_RETRY_BACKOFF_S * attempt:.0f}s ..."
            )
            last_exc = exc

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_BACKOFF_S * attempt)

    raise RuntimeError(
        f"Failed to download WorldCover tile {tile} after {_MAX_RETRIES} attempts."
    ) from last_exc


def _required_tiles() -> list[str]:
    """Return sorted list of unique tile IDs covering Karnataka bbox."""
    seen: set[str] = set()
    for lat in range(_LAT_MIN, _LAT_MAX + 1):
        for lon in range(_LON_MIN, _LON_MAX + 1):
            seen.add(_tile_id(float(lat), float(lon)))
    return sorted(seen)


def download_all_tiles() -> None:
    """
    Download all WorldCover tiles covering Karnataka (if not already cached).
    Safe to call multiple times — skips already-downloaded tiles.
    """
    tiles = _required_tiles()
    print(f"=== Downloading ESA WorldCover 2021 v200 tiles ({len(tiles)} tiles) ===")
    for i, tile in enumerate(tiles, 1):
        local = _tile_local_path(tile)
        if local.exists():
            print(f"  [{i}/{len(tiles)}] {tile} already cached.")
        else:
            print(f"  [{i}/{len(tiles)}] {tile} downloading ...", end=" ", flush=True)
            _download_tile(tile)


# ---------------------------------------------------------------------------
# Open-dataset cache (lazy, module-level)
# ---------------------------------------------------------------------------

import threading

_thread_local = threading.local()
_download_lock = threading.Lock()


def _get_dataset(tile: str) -> rasterio.DatasetReader:
    """Lazily open a tile's dataset and keep it in the thread-local cache."""
    if not hasattr(_thread_local, "open_datasets"):
        _thread_local.open_datasets = {}
        
    if tile not in _thread_local.open_datasets or _thread_local.open_datasets[tile].closed:
        local = _tile_local_path(tile)
        if not local.exists():
            with _download_lock:
                if not local.exists():
                    print(f"  Tile {tile} not cached — downloading ...", flush=True)
                    _download_tile(tile)
        _thread_local.open_datasets[tile] = rasterio.open(local)
    return _thread_local.open_datasets[tile]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_landuse_class(lat: float, lon: float) -> int:
    """
    Return the ESA WorldCover 2021 class code at the given WGS-84 coordinate.

    Downloads the relevant tile on first access; subsequent calls for the
    same tile are sub-millisecond (dataset stays open in module cache).

    Parameters
    ----------
    lat : float  Latitude, WGS-84 decimal degrees.
    lon : float  Longitude, WGS-84 decimal degrees.

    Returns
    -------
    int
        WorldCover class code.  One of: 10, 20, 30, 40, 50, 60, 80, 90, 95.

    Raises
    ------
    ValueError
        If the pixel is nodata, or the coordinate is outside any downloaded tile.
    """
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Latitude out of range: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Longitude out of range: {lon}")

    tile = _tile_id(lat, lon)
    ds = _get_dataset(tile)

    row, col = rowcol(ds.transform, lon, lat)

    if not (0 <= row < ds.height and 0 <= col < ds.width):
        raise ValueError(
            f"Coordinate ({lat:.4f}N, {lon:.4f}E) maps outside tile {tile} bounds."
        )

    window = rasterio.windows.Window(col, row, 1, 1)
    value = int(ds.read(1, window=window)[0, 0])

    if ds.nodata is not None and value == int(ds.nodata):
        raise ValueError(
            f"Nodata pixel at ({lat:.4f}N, {lon:.4f}E) in tile {tile}."
        )

    return value


def get_class_name(class_code: int) -> str:
    """Return the human-readable class name for a WorldCover class code."""
    entry = CLASS_CATALOGUE.get(class_code)
    return entry[0] if entry else _UNKNOWN_LABEL


def get_suitability(lat: float, lon: float) -> str:
    """
    Return the solar-PV suitability decision for the given coordinate,
    based on criteria.md § Criterion 3 exclusion table.

    This function is backward-compatible and does NOT set low_confidence
    flags.  Use get_suitability_full() to get the full result including
    the misclassification-risk flag for tree-cover pixels in the
    Bellary/Hampi/Kalaburagi belt.

    Returns
    -------
    str
        One of:
        - "eligible"            -- suitable for utility-scale solar PV
        - "eligible_penalised"  -- eligible but carries AHP cost modifier
                                   (cropland: food-security / land-reform risk)
        - "excluded"            -- hard exclusion (forest, urban, water, wetland,
                                   mangroves, or unknown class)
    """
    code = get_landuse_class(lat, lon)
    entry = CLASS_CATALOGUE.get(code)
    return entry[1] if entry else _UNKNOWN_SUIT


# ---------------------------------------------------------------------------
# Risk-zone helper
# ---------------------------------------------------------------------------

def _in_misclass_risk_zone(lat: float, lon: float) -> bool:
    """Return True if (lat, lon) falls inside the documented granite-scrub
    misclassification belt where WorldCover tree-cover results are unreliable."""
    return (
        _MISCLASS_RISK_ZONE_LAT_MIN <= lat <= _MISCLASS_RISK_ZONE_LAT_MAX
        and _MISCLASS_RISK_ZONE_LON_MIN <= lon <= _MISCLASS_RISK_ZONE_LON_MAX
    )


# ---------------------------------------------------------------------------
# Full suitability result
# ---------------------------------------------------------------------------

class LanduseResult(NamedTuple):
    """
    Full suitability result returned by get_suitability_full().

    Fields
    ------
    class_code       : int   WorldCover class code at the queried pixel.
    class_name       : str   Human-readable class name.
    suitability      : str   "eligible" | "eligible_penalised" | "excluded".
                             Exclusion logic is unchanged from get_suitability();
                             tree cover is still "excluded" even in the risk zone.
    low_confidence   : bool  True when the suitability decision is flagged for
                             manual review before being accepted.  Currently set
                             only for tree-cover (code 10) pixels inside the
                             documented Bellary/Hampi/Kalaburagi risk zone.
    confidence_reason: str | None
                             Human-readable reason string when low_confidence is
                             True, citing the specific precedent and the
                             recommended cross-check source.  None otherwise.
    """
    class_code:        int
    class_name:        str
    suitability:       str
    low_confidence:    bool
    confidence_reason: Optional[str]


def get_suitability_full(lat: float, lon: float) -> LanduseResult:
    """
    Return the full land-use suitability result for the given WGS-84
    coordinate, including a low_confidence flag and reason string for
    tree-cover pixels in the documented Bellary/Hampi/Kalaburagi granite-
    scrub misclassification belt.

    Parameters
    ----------
    lat : float  Latitude, WGS-84 decimal degrees.
    lon : float  Longitude, WGS-84 decimal degrees.

    Returns
    -------
    LanduseResult
        NamedTuple with fields:
          class_code, class_name, suitability, low_confidence,
          confidence_reason.

        key behaviour:
          - suitability is ALWAYS "excluded" for tree cover (code 10),
            regardless of risk zone.  The exclusion logic is unchanged.
          - low_confidence=True is set only for code-10 pixels inside
            _MISCLASS_RISK_ZONE_*.  Outside that zone, tree cover is
            excluded with low_confidence=False (normal hard exclusion,
            no special review needed).
          - For all non-tree-cover codes, low_confidence=False always.

    Raises
    ------
    Same exceptions as get_landuse_class().
    """
    code = get_landuse_class(lat, lon)
    name = get_class_name(code)
    entry = CLASS_CATALOGUE.get(code)
    suit = entry[1] if entry else _UNKNOWN_SUIT

    if code == 10 and _in_misclass_risk_zone(lat, lon):
        return LanduseResult(
            class_code=code,
            class_name=name,
            suitability=suit,           # still "excluded" — logic unchanged
            low_confidence=True,
            confidence_reason=_MISCLASS_RISK_REASON,
        )

    return LanduseResult(
        class_code=code,
        class_name=name,
        suitability=suit,
        low_confidence=False,
        confidence_reason=None,
    )


# ---------------------------------------------------------------------------
# Module-level self-check
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    download_all_tiles()
    print("\nQuick spot-check (get_suitability — backward-compat):")
    pts = [
        ("Bengaluru central (urban)",     12.9716, 77.5946),
        ("Bandipur NP core (forest)",     11.6300, 76.6300),
        ("KRS reservoir (water)",         12.4244, 76.5735),
        ("Kalaburagi rural (scrub/crop)", 17.5000, 76.5000),
    ]
    for label, lat, lon in pts:
        code = get_landuse_class(lat, lon)
        name = get_class_name(code)
        suit = get_suitability(lat, lon)
        print(f"  {label}: code={code} ({name}) -> {suit}")

    print("\nRisk-zone check (get_suitability_full):")
    risk_pts = [
        ("Hampi/Anegundi granite scrub (confirmed misclassification)",
         15.3200, 76.4300),
        ("Bandipur forest (outside risk zone)",
         11.6300, 76.6300),
        ("Kalaburagi rural (inside zone, non-tree code)",
         17.5000, 76.5000),
    ]
    for label, lat, lon in risk_pts:
        r = get_suitability_full(lat, lon)
        lc_str = f"low_confidence=True  reason='{r.confidence_reason[:80]}...'" \
                 if r.low_confidence else "low_confidence=False"
        print(f"  {label}")
        print(f"    code={r.class_code} ({r.class_name})  suit={r.suitability}  {lc_str}")
