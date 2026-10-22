"""
geoprocessing/fetch_boundary.py
-------------------------------
Provides point-in-polygon and footprint disk validation against
the official Karnataka state boundary (karnataka_boundary.geojson).

Criteria & Pipeline Rule:
1. Point-in-polygon test: The centroid coordinate must be strictly inside Karnataka.
2. Footprint disk test: The 0.54 km radius footprint disk (~226.4 acres, representing 225-acre 50 MWp design assumption) must lie
   at least 90% (FOOTPRINT_BOUNDARY_MIN = 0.90) inside the state boundary.
"""

import os
import threading
from typing import Optional, Tuple
import geopandas as gpd
from shapely.geometry import Point
from shapely.prepared import prep

_boundary_lock = threading.Lock()
_poly = None
_prepared_poly = None
_poly_utm = None
_inner_buffer_540m = None
_prep_inner_540m = None

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_BOUNDARY_GEOJSON = os.path.join(_DATA_DIR, "karnataka_boundary.geojson")


def _init_boundary():
    global _poly, _prepared_poly, _poly_utm, _inner_buffer_540m, _prep_inner_540m
    if _poly is not None:
        return

    with _boundary_lock:
        if _poly is not None:
            return

        if not os.path.exists(_BOUNDARY_GEOJSON):
            raise FileNotFoundError(f"Missing boundary file: {_BOUNDARY_GEOJSON}")

        gdf = gpd.read_file(_BOUNDARY_GEOJSON)
        _poly = gdf.geometry.iloc[0]
        _prepared_poly = prep(_poly)

        gdf_utm = gdf.to_crs(epsg=32643)
        _poly_utm = gdf_utm.geometry.iloc[0]
        _inner_buffer_540m = _poly_utm.buffer(-540.0)
        _prep_inner_540m = prep(_inner_buffer_540m)


def is_in_karnataka(lat: float, lon: float) -> bool:
    """Return True if (lat, lon) is strictly within Karnataka state boundary."""
    _init_boundary()
    pt = Point(lon, lat)
    return bool(_prepared_poly.contains(pt))


def get_boundary_disk_fraction(lat: float, lon: float, radius_km: float = 0.54) -> float:
    """
    Compute the fraction of the circular footprint disk (radius_km, default 0.54 km ~226.4 acres)
    that falls inside the Karnataka state boundary in EPSG:32643 UTM 43N.
    """
    _init_boundary()
    pt = Point(lon, lat)
    if not _prepared_poly.contains(pt) and not _poly.intersects(pt):
        # Far outside
        return 0.0

    radius_m = radius_km * 1000.0
    pt_gdf = gpd.GeoSeries([pt], crs="EPSG:4326").to_crs(epsg=32643)
    pt_utm = pt_gdf.iloc[0]

    # Fast path for standard 540m radius
    if abs(radius_m - 540.0) < 1e-3 and _prep_inner_540m.contains(pt_utm):
        return 1.0

    disk = pt_utm.buffer(radius_m)
    inter_area = disk.intersection(_poly_utm).area
    return float(inter_area / disk.area)


def check_boundary_constraint(
    lat: float,
    lon: float,
    radius_km: float = 0.54,
    min_disk_fraction: float = 0.90,
) -> Tuple[bool, float, Optional[str]]:
    """
    First filter in sweep:
    Returns (is_passed, disk_fraction, reason_if_tripped).
    """
    _init_boundary()
    pt = Point(lon, lat)
    if not _prepared_poly.contains(pt):
        return False, 0.0, "Outside Karnataka state boundary (point-in-polygon)"

    disk_frac = get_boundary_disk_fraction(lat, lon, radius_km=radius_km)
    if disk_frac < min_disk_fraction:
        return (
            False,
            round(disk_frac, 4),
            f"Footprint disk ({disk_frac * 100:.1f}%) < {min_disk_fraction * 100:.0f}% inside Karnataka boundary",
        )

    return True, round(disk_frac, 4), None
