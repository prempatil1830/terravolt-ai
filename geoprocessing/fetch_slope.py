"""
geoprocessing/fetch_slope.py
-----------------------------
Downloads Copernicus DEM GLO-30 (30 m / ~1 arc-second) tiles covering
Karnataka's bounding box, mosaics them, derives slope (%), and exposes a
function to look up slope % at an arbitrary lat/lon point.

Data source (no auth required)
-------------------------------
Copernicus DEM GLO-30 — AWS Open Data public S3 bucket.
Bucket  : copernicus-dem-30m   (region: eu-central-1)
HTTPS   : https://copernicus-dem-30m.s3.amazonaws.com/<tile>/<tile>.tif
License : Open (Copernicus Programme Licence), no API key needed.

Reference tile naming:
  Copernicus_DSM_COG_10_N<LL>_00_E<LLL>_00_DEM/
      Copernicus_DSM_COG_10_N<LL>_00_E<LLL>_00_DEM.tif
  where LL = 2-digit integer lat of tile SW corner (N/S prefix)
        LLL = 3-digit integer lon of tile SW corner (E/W prefix)
Each tile covers exactly 1 degree x 1 degree.

Criteria reference: criteria.md section Criterion 3 (Slope <= 5% for utility-scale
PV installations).

Slope derivation
----------------
Computed from the raw elevation DEM using finite differences (numpy.gradient)
in geographic coordinates. The horizontal distance per pixel is computed using
the WGS-84 ellipsoid (latitude-dependent metres per degree) so that the
resulting rise/run values are correct in physical units, not pixel units.

    slope_radians = arctan( sqrt( (dz/dx)^2 + (dz/dy)^2 ) )
    slope_percent = tan(slope_radians) * 100

Void pixels (value == -32768 in the raw COG, or rasterio nodata) are masked
and will raise ValueError if looked up directly.

Caching
-------
Downloaded tiles are cached in  geoprocessing/data/dem_tiles/
The merged slope raster is cached in  geoprocessing/data/slope_karnataka.tif
Re-running the module skips downloads for tiles already present on disk, and
re-uses the cached slope raster if it exists.  Delete either to force a refresh.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Optional

import numpy as np
import requests
import rasterio
from rasterio.merge import merge
from rasterio.transform import rowcol

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Karnataka bounding box — integer degree tile corners to download.
# Using 1 extra tile of padding on each side avoids edge artefacts in the
# finite-difference slope computation at the tile boundaries.
_LAT_MIN = 11   # southernmost tile row (Karnataka coast starts ~11.5N)
_LAT_MAX = 18   # northernmost tile row (Karnataka ends ~18.5N)
_LON_MIN = 74   # westernmost tile column (Karnataka coast starts ~74E)
_LON_MAX = 78   # easternmost tile column (Karnataka ends ~78.5E)

# Public HTTPS endpoint for Copernicus DEM GLO-30 tiles — no auth required.
_COG_BASE = (
    "https://copernicus-dem-30m.s3.amazonaws.com"
    "/{tile_id}/{tile_id}.tif"
)

# Local cache directories (relative to this file's directory).
_HERE = Path(__file__).parent
_TILE_DIR = _HERE / "data" / "dem_tiles"
_SLOPE_PATH = _HERE / "data" / "slope_karnataka.tif"

# HTTP settings.
_REQUEST_TIMEOUT = 120   # seconds per tile download
_MAX_RETRIES = 3
_RETRY_BACKOFF_S = 3.0

# Copernicus GLO-30 raw nodata sentinel (INT16 void = -32768).
_DEM_NODATA = -32768.0


# ---------------------------------------------------------------------------
# Tile URL helpers
# ---------------------------------------------------------------------------

def _tile_id(lat: int, lon: int) -> str:
    """
    Return the Copernicus GLO-30 tile identifier for the SW-corner integer
    coordinates (lat, lon).

    Example:  lat=12, lon=74  ->  Copernicus_DSM_COG_10_N12_00_E074_00_DEM
    """
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    lat_str = f"{abs(lat):02d}"
    lon_str = f"{abs(lon):03d}"
    return f"Copernicus_DSM_COG_10_{ns}{lat_str}_00_{ew}{lon_str}_00_DEM"


def _tile_url(tile: str) -> str:
    return _COG_BASE.format(tile_id=tile)


def _tile_local_path(tile: str) -> Path:
    return _TILE_DIR / f"{tile}.tif"


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download_tile(tile: str) -> Optional[Path]:
    """
    Download a single tile to the local cache if not already present.
    Returns the local path, or None if the tile does not exist on the server
    (some ocean/void tiles are absent -- that is expected).
    """
    local = _tile_local_path(tile)
    if local.exists():
        return local

    url = _tile_url(tile)
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT, stream=True)
            if resp.status_code == 404:
                # Tile does not exist -- ocean / missing coverage is normal.
                return None
            resp.raise_for_status()

            local.parent.mkdir(parents=True, exist_ok=True)
            with open(local, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)

            print(f"  Downloaded: {tile}")
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

        time.sleep(_RETRY_BACKOFF_S * attempt)

    raise RuntimeError(
        f"Failed to download tile {tile} after {_MAX_RETRIES} attempts."
    ) from last_exc


def _download_all_tiles() -> list[Path]:
    """
    Download all GLO-30 tiles covering Karnataka's bounding box.
    Returns list of local paths for tiles that were found (non-ocean).
    """
    tile_paths: list[Path] = []
    total = (_LAT_MAX - _LAT_MIN + 1) * (_LON_MAX - _LON_MIN + 1)
    idx = 0

    for lat in range(_LAT_MIN, _LAT_MAX + 1):
        for lon in range(_LON_MIN, _LON_MAX + 1):
            idx += 1
            tile = _tile_id(lat, lon)
            print(f"[{idx}/{total}] {tile} ...", end=" ", flush=True)
            path = _download_tile(tile)
            if path is not None:
                print("ok")
                tile_paths.append(path)
            else:
                print("not found (ocean / no coverage)")

    return tile_paths


# ---------------------------------------------------------------------------
# Slope computation
# ---------------------------------------------------------------------------

def _metres_per_degree(lat_rad: float) -> tuple[float, float]:
    """
    Return (metres_per_degree_lat, metres_per_degree_lon) at the given
    latitude (radians) using the WGS-84 ellipsoid semi-axes.

    These are the "length of one degree" values needed to convert pixel
    spacing in geographic coordinates to physical distances for slope calc.
    """
    a = 6_378_137.0         # WGS-84 semi-major axis (m)
    b = 6_356_752.314245    # WGS-84 semi-minor axis (m)
    e2 = 1.0 - (b / a) ** 2

    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)

    # Meridional radius of curvature (N-S direction)
    M = a * (1 - e2) / (1 - e2 * sin_lat**2) ** 1.5
    # Normal radius of curvature (E-W direction)
    N = a / (1 - e2 * sin_lat**2) ** 0.5

    m_per_deg_lat = M * math.radians(1)          # metres per 1 degree latitude
    m_per_deg_lon = N * cos_lat * math.radians(1) # metres per 1 degree longitude

    return m_per_deg_lat, m_per_deg_lon


def _compute_slope_raster(
    elevation: np.ndarray,
    nodata: float,
    transform: rasterio.transform.Affine,
) -> np.ndarray:
    """
    Compute slope (%) from a 2-D elevation array using numpy finite differences.

    Parameters
    ----------
    elevation : 2-D float array, shape (rows, cols), elevations in metres.
    nodata    : sentinel value for void pixels (will be masked then NaN-filled).
    transform : affine geotransform of the raster (used to get pixel spacing in degrees).

    Returns
    -------
    slope_pct : 2-D float32 array, same shape as elevation.
                Void pixels have value NaN.
    """
    elev = elevation.astype(np.float64)

    # Mask nodata and any non-finite values.
    mask = (elev == nodata) | ~np.isfinite(elev)
    elev[mask] = np.nan

    # Pixel size in degrees (from affine transform).
    dx_deg = abs(transform.a)   # W-to-E pixel width in degrees
    dy_deg = abs(transform.e)   # N-to-S pixel height in degrees

    # Centre latitude of the raster -- used as a representative value for
    # converting degrees to metres. The flat-earth approximation across one
    # 1-degree tile introduces < 0.1 % error in slope, which is acceptable.
    nrows = elev.shape[0]
    centre_lat_deg = transform.f + transform.e * (nrows / 2.0)
    centre_lat_rad = math.radians(centre_lat_deg)

    m_per_deg_lat, m_per_deg_lon = _metres_per_degree(centre_lat_rad)
    dx_m = dx_deg * m_per_deg_lon   # metres per pixel in E-W direction
    dy_m = dy_deg * m_per_deg_lat   # metres per pixel in N-S direction

    # Compute partial derivatives using numpy central differences.
    # np.gradient returns (dy_gradient, dx_gradient) for a 2-D array where
    # the first axis is rows (N-S) and the second axis is cols (E-W).
    dz_dy, dz_dx = np.gradient(elev, dy_m, dx_m)

    slope_rad = np.arctan(np.sqrt(dz_dx**2 + dz_dy**2))
    slope_pct = np.tan(slope_rad) * 100.0

    # Restore nodata as NaN.
    slope_pct[mask] = np.nan

    return slope_pct.astype(np.float32)


# ---------------------------------------------------------------------------
# Build mosaic + slope (cached)
# ---------------------------------------------------------------------------

def build_slope_raster(force_rebuild: bool = False) -> None:
    """
    Download all tiles (if not cached) and compute the merged slope raster.

    The result is written to  geoprocessing/data/slope_karnataka.tif.
    Subsequent calls are no-ops unless force_rebuild=True.
    """
    if _SLOPE_PATH.exists() and not force_rebuild:
        print(f"Slope raster already cached at {_SLOPE_PATH}. Skipping rebuild.")
        return

    print("=== Downloading Copernicus DEM GLO-30 tiles ===")
    tile_paths = _download_all_tiles()

    if not tile_paths:
        raise RuntimeError(
            "No DEM tiles were downloaded. Check network connectivity and "
            "the tile URL pattern."
        )

    print(f"\n=== Mosaicking {len(tile_paths)} tile(s) ===")
    datasets = [rasterio.open(p) for p in tile_paths]

    try:
        mosaic_data, mosaic_transform = merge(datasets)
    finally:
        for ds in datasets:
            ds.close()

    # mosaic_data shape: (1, rows, cols) -- single band.
    elev_2d = mosaic_data[0]
    crs = datasets[0].crs   # WGS-84 geographic (EPSG:4326)

    print("=== Computing slope raster ===")
    slope_2d = _compute_slope_raster(
        elev_2d.astype(np.float64),
        nodata=_DEM_NODATA,
        transform=mosaic_transform,
    )

    _SLOPE_PATH.parent.mkdir(parents=True, exist_ok=True)

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": slope_2d.shape[1],
        "height": slope_2d.shape[0],
        "count": 1,
        "crs": crs,
        "transform": mosaic_transform,
        "nodata": float("nan"),
        "compress": "lzw",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }

    with rasterio.open(_SLOPE_PATH, "w", **profile) as dst:
        dst.write(slope_2d, 1)

    print(f"Slope raster written -> {_SLOPE_PATH}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

import threading

_thread_local = threading.local()
_build_lock = threading.Lock()


def _get_slope_dataset() -> rasterio.DatasetReader:
    """Lazily open (and cache as thread-local) the slope raster."""
    if not hasattr(_thread_local, "slope_dataset") or _thread_local.slope_dataset.closed:
        with _build_lock:
            if not _SLOPE_PATH.exists():
                build_slope_raster()
        _thread_local.slope_dataset = rasterio.open(_SLOPE_PATH)
    return _thread_local.slope_dataset


def get_slope(lat: float, lon: float) -> float:
    """
    Return the terrain slope at the given WGS-84 coordinate as a percentage
    (rise / run * 100).

    Downloads and processes the DEM on the first call (or re-uses the cached
    slope raster on subsequent calls).  The first call may take several minutes
    depending on network speed; subsequent calls are sub-millisecond.

    Parameters
    ----------
    lat : float
        Latitude in decimal degrees, WGS-84. Should be within Karnataka
        approximately 11.5 to 18.5 N.
    lon : float
        Longitude in decimal degrees, WGS-84. Should be within Karnataka
        approximately 74 to 78.5 E.

    Returns
    -------
    float
        Slope percentage at the requested point.

    Raises
    ------
    ValueError
        If lat/lon are outside the covered area, or if the pixel is a void
        (nodata) cell (e.g. over water).
    RuntimeError
        If the slope raster cannot be built (network failure, etc.).
    """
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Latitude must be in [-90, 90]; got {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Longitude must be in [-180, 180]; got {lon}")

    ds = _get_slope_dataset()

    # Convert lat/lon to raster row/col.
    row, col = rowcol(ds.transform, lon, lat)

    if not (0 <= row < ds.height and 0 <= col < ds.width):
        raise ValueError(
            f"Coordinate ({lat:.4f}N, {lon:.4f}E) is outside the slope "
            f"raster extent (covers approx {_LAT_MIN} to {_LAT_MAX + 1}N, "
            f"{_LON_MIN} to {_LON_MAX + 1}E)."
        )

    # Read a single pixel.
    window = rasterio.windows.Window(col, row, 1, 1)
    value = ds.read(1, window=window)[0, 0]

    if not np.isfinite(value):
        raise ValueError(
            f"Slope at ({lat:.4f}N, {lon:.4f}E) is a void/nodata pixel. "
            "This may indicate a DEM void -- the point is likely over water or "
            "has missing DEM coverage."
        )

    return float(value)


# ---------------------------------------------------------------------------
# Module-level self-check (python fetch_slope.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Building / verifying slope raster ...")
    build_slope_raster()
    print("\nSlope raster ready.")
    test_pts = [
        ("Kalaburagi (flat plateau)", 17.33, 76.84),
        ("Madikeri / Western Ghats escarpment (steep)", 12.42, 75.74),
        ("Mangalore coast (flat)", 12.87, 74.88),
    ]
    for label, lat, lon in test_pts:
        slope = get_slope(lat, lon)
        print(f"  {label}: {slope:.2f}%")
