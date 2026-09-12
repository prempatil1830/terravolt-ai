"""
geoprocessing/diagnose_coastal_point.py
-----------------------------------------
Diagnostic-only script — does NOT modify fetch_slope.py or validate_slope.py.

Inspects the raw DEM and gradient internals at:
  - Tannirbhavi beach (12.8990N, 74.8000E)  — the current validation anchor
  - ~200m inland  (east, higher longitude)
  - ~200m seaward (west, lower longitude)

For each point prints:
  1. Raw 3x3 DEM elevation window (before any nodata masking)
  2. Explicit nodata/-32768/NaN flag for every pixel
  3. Exact dz_dx, dz_dy values at the centre pixel
  4. Resulting slope percentage

All intermediate values are shown; nothing is hidden.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import rowcol

# ---------------------------------------------------------------------------
# Paths — work from repo root or from geoprocessing/
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_TILE_PATH = _HERE / "data" / "dem_tiles" / "Copernicus_DSM_COG_10_N12_00_E074_00_DEM.tif"
_SLOPE_PATH = _HERE / "data" / "slope_karnataka.tif"

if not _TILE_PATH.exists():
    sys.exit(f"ERROR: raw DEM tile not found at {_TILE_PATH}\n"
             "Run validate_slope.py first to download tiles.")

# ---------------------------------------------------------------------------
# WGS-84 ellipsoid helpers (same as fetch_slope.py — duplicated to keep this
# script fully standalone and non-modifying)
# ---------------------------------------------------------------------------

def _metres_per_degree(lat_rad: float) -> tuple[float, float]:
    a  = 6_378_137.0
    b  = 6_356_752.314245
    e2 = 1.0 - (b / a) ** 2
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    M = a * (1 - e2) / (1 - e2 * sin_lat**2) ** 1.5
    N = a / (1 - e2 * sin_lat**2) ** 0.5
    return M * math.radians(1), N * cos_lat * math.radians(1)


def _divider(char: str = "-", width: int = 68) -> None:
    print(char * width)


# ---------------------------------------------------------------------------
# Core diagnostic for a single point
# ---------------------------------------------------------------------------

def diagnose_point(
    label: str,
    lat: float,
    lon: float,
    dem_ds: rasterio.DatasetReader,
    slope_ds: rasterio.DatasetReader,
) -> None:
    _divider("=")
    print(f"POINT: {label}")
    print(f"  Coordinates : {lat:.4f}N, {lon:.4f}E")
    _divider()

    # ---- 1. Locate pixel in the raw DEM tile --------------------------------
    row, col = rowcol(dem_ds.transform, lon, lat)
    print(f"  DEM tile row/col : row={row}, col={col}")
    print(f"  DEM pixel size   : {dem_ds.res[0]*111320:.1f} m (lat) x "
          f"{dem_ds.res[1]*111320*math.cos(math.radians(lat)):.1f} m (lon)  "
          f"[{dem_ds.res[0]*3600:.4f} arcsec]")
    print()

    # ---- 2. 3x3 raw DEM window ----------------------------------------------
    # Guard against being at the edge of the tile
    r0 = max(0, row - 1)
    c0 = max(0, col - 1)
    r1 = min(dem_ds.height - 1, row + 1)
    c1 = min(dem_ds.width  - 1, col + 1)
    win = rasterio.windows.Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)
    raw = dem_ds.read(1, window=win)   # INT16 — no masking yet

    print("  RAW 3x3 DEM elevation window (INT16, no masking):")
    print(f"  {'':6}  {'W (col-1)':>12}  {'Centre':>12}  {'E (col+1)':>12}")
    row_labels = ["N (row-1)", "Centre  ", "S (row+1)"]
    for i, rlabel in enumerate(row_labels):
        if raw.shape[0] <= i:
            print(f"  {rlabel}  (edge — outside tile bounds)")
            continue
        vals = []
        for j in range(raw.shape[1]):
            v = raw[i, j]
            vals.append(f"{int(v):>12}")
        print(f"  {rlabel}  {'  '.join(vals)}")

    print()

    # ---- 3. Nodata / sentinel check -----------------------------------------
    print("  Nodata / sentinel check for all 9 pixels:")
    DEM_NODATA = -32768
    nodata_found = False
    for i in range(raw.shape[0]):
        for j in range(raw.shape[1]):
            v = int(raw[i, j])
            flags = []
            if v == DEM_NODATA:
                flags.append("*** IS NODATA (-32768) ***")
                nodata_found = True
            if not np.isfinite(float(v)):
                flags.append("*** NOT FINITE ***")
            flag_str = "  ".join(flags) if flags else "ok"
            row_desc = ["N", "C", "S"][i]
            col_desc = ["W", "C", "E"][j]
            print(f"    [{row_desc},{col_desc}] = {v:6d}  {flag_str}")
    if not nodata_found:
        print("  -> No nodata sentinels in this window.")
    print()

    # ---- 4. Reproduce gradient at centre pixel, step by step ----------------
    # Use the same logic as fetch_slope.py: np.gradient on the 3x3 patch,
    # then read off the centre element.

    patch = raw.astype(np.float64)
    patch[patch == DEM_NODATA] = np.nan   # mask nodata first (as connector does)

    # Pixel spacing in degrees
    dx_deg = abs(dem_ds.transform.a)   # E-W
    dy_deg = abs(dem_ds.transform.e)   # N-S

    centre_lat_rad = math.radians(lat)
    m_per_deg_lat, m_per_deg_lon = _metres_per_degree(centre_lat_rad)
    dx_m = dx_deg * m_per_deg_lon   # metres per pixel E-W
    dy_m = dy_deg * m_per_deg_lat   # metres per pixel N-S

    print(f"  Pixel spacing (physical):")
    print(f"    dx_deg = {dx_deg:.8f}°  ->  dx_m = {dx_m:.4f} m  (E-W)")
    print(f"    dy_deg = {dy_deg:.8f}°  ->  dy_m = {dy_m:.4f} m  (N-S)")
    print()

    # np.gradient on the (possibly 1–3 row) patch
    if patch.shape[0] >= 2 and patch.shape[1] >= 2:
        dz_dy_patch, dz_dx_patch = np.gradient(patch, dy_m, dx_m)
        # Centre element index within the patch
        ci = min(1, patch.shape[0] - 1)   # row index of centre
        cj = min(1, patch.shape[1] - 1)   # col index of centre
        dz_dy_val = dz_dy_patch[ci, cj]
        dz_dx_val = dz_dx_patch[ci, cj]
    else:
        print("  WARNING: patch too small for gradient — at tile boundary.")
        dz_dy_val = float("nan")
        dz_dx_val = float("nan")

    print(f"  Gradient at centre pixel (after nodata-masking to NaN):")
    print(f"    dz_dy (N-S, m/m) = {dz_dy_val:+.6f}   "
          f"[{dz_dy_val * 100:+.4f} % grade N-S]")
    print(f"    dz_dx (E-W, m/m) = {dz_dx_val:+.6f}   "
          f"[{dz_dx_val * 100:+.4f} % grade E-W]")
    magnitude = math.sqrt(dz_dy_val**2 + dz_dx_val**2) if (
        np.isfinite(dz_dy_val) and np.isfinite(dz_dx_val)) else float("nan")
    slope_rad = math.atan(magnitude) if np.isfinite(magnitude) else float("nan")
    slope_pct = math.tan(slope_rad) * 100.0 if np.isfinite(slope_rad) else float("nan")
    print(f"    |gradient|       = {magnitude:.6f} m/m")
    print(f"    slope_rad        = {slope_rad:.6f} rad")
    print(f"    slope_pct        = {slope_pct:.4f} %")
    print()

    # ---- 5. Cross-check against the cached slope raster ---------------------
    if slope_ds is not None:
        sr, sc = rowcol(slope_ds.transform, lon, lat)
        if 0 <= sr < slope_ds.height and 0 <= sc < slope_ds.width:
            cached_val = slope_ds.read(1, window=rasterio.windows.Window(sc, sr, 1, 1))[0, 0]
            print(f"  Cached slope raster value : {float(cached_val):.4f} %"
                  f"  (row={sr}, col={sc} in merged raster)")
        else:
            print("  Cached slope raster value : outside raster extent")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print()
    _divider("=")
    print("COASTAL POINT DIAGNOSTIC — Tannirbhavi beach & ±200m transect")
    print("Raw DEM tile : Copernicus_DSM_COG_10_N12_00_E074_00_DEM.tif")
    _divider("=")
    print()

    # 200m displacement in degrees at ~12.9N
    lat_200m = 200 / 111_320         # ≈ 0.001797°
    lon_200m_at_12_9 = 200 / (111_320 * math.cos(math.radians(12.9)))  # ≈ 0.001841°

    # Karnataka coast here runs ~N-S; inland = east (+lon), seaward = west (-lon)
    BASE_LAT = 12.8990
    BASE_LON = 74.8000

    points = [
        (
            "Tannirbhavi beach — BASE POINT (12.8990N, 74.8000E)",
            BASE_LAT,
            BASE_LON,
        ),
        (
            f"~200m INLAND (east)  ({BASE_LAT:.4f}N, {BASE_LON + lon_200m_at_12_9:.4f}E)",
            BASE_LAT,
            BASE_LON + lon_200m_at_12_9,
        ),
        (
            f"~200m SEAWARD (west) ({BASE_LAT:.4f}N, {BASE_LON - lon_200m_at_12_9:.4f}E)",
            BASE_LAT,
            BASE_LON - lon_200m_at_12_9,
        ),
    ]

    with rasterio.open(_TILE_PATH) as dem_ds:
        print(f"DEM tile CRS    : {dem_ds.crs}")
        print(f"DEM tile bounds : {dem_ds.bounds}")
        print(f"DEM tile shape  : {dem_ds.height} rows x {dem_ds.width} cols")
        print(f"DEM nodata tag  : {dem_ds.nodata}")
        print(f"DEM dtype       : {dem_ds.dtypes[0]}")
        print()

        slope_ds = rasterio.open(_SLOPE_PATH) if _SLOPE_PATH.exists() else None

        try:
            for label, lat, lon in points:
                diagnose_point(label, lat, lon, dem_ds, slope_ds)
        finally:
            if slope_ds is not None:
                slope_ds.close()

    _divider("=")
    print("END OF DIAGNOSTIC")
    _divider("=")


if __name__ == "__main__":
    main()
