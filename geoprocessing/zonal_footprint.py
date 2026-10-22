"""
geoprocessing/zonal_footprint.py
--------------------------------
Computes zonal raster fractions and disk-to-polygon buffer distances
for candidate solar PV footprints (nominal 0.54 km radius ~ 91.6 hectares / ~226.4 acres,
representing 50 MWp at 4.5 acres/MWp design assumption).

Criteria reference: criteria.md
  - Criterion 3 (Slope <= 5.0% for utility-scale PV)
  - Criterion 4 (Land-use exclusions: Tree cover 10, Built-up 50, Water 80, Wetland 90, Mangroves 95)
  - Criterion 5 (Protected areas buffer 10 km, Water buffer 500 m)

Project Assumption:
  FOOTPRINT_RADIUS_KM = 0.54 (50 MWp x 4.5 acres/MWp rule of thumb = 225 acres -> r = ~0.5384 km ~= 0.54 km).
  Disk area = 226.37 acres.
  FOOTPRINT_MIN_USABLE = 0.80 (80% of footprint disk must satisfy slope <= 5%
  AND 80% must be in eligible land-use classes).
  Documented strictly as a project engineering assumption, not a literature value.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional, Tuple

import numpy as np
import rasterio
import rasterio.mask
import geopandas as gpd
from shapely.geometry import Point
from shapely.strtree import STRtree

try:
    from fetch_landuse import _get_dataset, _tile_id, CLASS_CATALOGUE
    from fetch_protected_buffer import _load_wdpa_data
except ImportError:
    from geoprocessing.fetch_landuse import _get_dataset, _tile_id, CLASS_CATALOGUE
    from geoprocessing.fetch_protected_buffer import _load_wdpa_data

# Design assumption: 50 MWp x 4.5 acres/MWp (unverified rule of thumb) = 225 acres
# Disk radius: r = sqrt(225 * 4046.8564224 / pi) / 1000 = ~0.53836 km ~= 0.54 km
# Disk area with r=0.54 km is pi * 0.54^2 = 0.91609 km^2 = 226.37 acres
FOOTPRINT_RADIUS_KM = 0.54
FOOTPRINT_RADIUS_M = 540.0
PA_BUFFER_M = 10000.0  # 10 km from disk edge
WATER_BUFFER_M = 500.0  # 500 m from disk edge
FOOTPRINT_MIN_USABLE = 0.80  # Project assumption: 80%

_SLOPE_PATH = os.path.join(os.path.dirname(__file__), "data", "slope_karnataka.tif")
_slope_ds = None
_slope_lock = threading.Lock()
_raster_lock = threading.Lock()
_excluded_codes = {k for k, v in CLASS_CATALOGUE.items() if v[1] == "excluded"}
_water_codes = {80, 90}

_water_setback_gdf = None
_water_setback_tree = None
_water_canal_gdf = None
_water_canal_tree = None
_water_lock = threading.Lock()


def _load_water_data():
    global _water_setback_gdf, _water_setback_tree, _water_canal_gdf, _water_canal_tree
    with _water_lock:
        if _water_setback_gdf is None:
            water_path = os.path.join(os.path.dirname(__file__), "data", "water_karnataka.geojson")
            if os.path.exists(water_path):
                gdf = gpd.read_file(water_path)
                gdf_utm = gdf.to_crs(epsg=32643)
                is_canal = gdf_utm["type"].isin(["canal", "stream"])
                _water_canal_gdf = gdf_utm[is_canal].reset_index(drop=True)
                _water_canal_tree = STRtree(_water_canal_gdf.geometry) if not _water_canal_gdf.empty else None
                _water_setback_gdf = gdf_utm[~is_canal].reset_index(drop=True)
                _water_setback_tree = STRtree(_water_setback_gdf.geometry) if not _water_setback_gdf.empty else None
            else:
                _water_canal_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:32643")
                _water_canal_tree = None
                _water_setback_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:32643")
                _water_setback_tree = None
    return _water_setback_gdf, _water_setback_tree, _water_canal_gdf, _water_canal_tree



def _get_slope_dataset():
    global _slope_ds
    if _slope_ds is None or _slope_ds.closed:
        if not os.path.exists(_SLOPE_PATH):
            raise FileNotFoundError(f"Slope raster not found at {_SLOPE_PATH}")
        _slope_ds = rasterio.open(_SLOPE_PATH)
    return _slope_ds



def evaluate_footprint_zonal(
    lat: float,
    lon: float,
    radius_km: float = FOOTPRINT_RADIUS_KM,
    pa_buffer_m: float = PA_BUFFER_M,
    water_buffer_m: float = WATER_BUFFER_M,
    min_usable_fraction: float = FOOTPRINT_MIN_USABLE,
) -> Dict[str, Any]:
    """
    Evaluates zonal slope and land-use fractions over a footprint disk of radius_km,
    and checks disk-to-polygon distance against protected areas and water buffers.
    """
    radius_m = radius_km * 1000.0
    pt_utm = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(epsg=32643).iloc[0]
    disk_utm = pt_utm.buffer(radius_m)
    disk_wgs84 = gpd.GeoSeries([disk_utm], crs="EPSG:32643").to_crs(epsg=4326).iloc[0]

    res: Dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "radius_km": radius_km,
        "min_usable_threshold": min_usable_fraction,
        "is_feasible": False,
        "tripped_constraints": [],
    }

    # 1. Protected-area disk-to-polygon distance (>= 10,000 m from disk edge)
    wdpa_gdf = _load_wdpa_data()
    min_pa_dist_m = float("inf")
    if not wdpa_gdf.empty and hasattr(wdpa_gdf, "sindex"):
        search_radius_m = radius_m + pa_buffer_m
        bbox_search = pt_utm.buffer(search_radius_m)
        possible_matches = list(wdpa_gdf.sindex.intersection(bbox_search.bounds))
        if possible_matches:
            polys = wdpa_gdf.geometry.iloc[possible_matches]
            dists = polys.distance(disk_utm)
            min_pa_dist_m = float(dists.min())
            if min_pa_dist_m < pa_buffer_m:
                res["tripped_constraints"].append(
                    f"Disk inside Protected Area buffer: disk distance is {min_pa_dist_m:.0f}m (< {pa_buffer_m:.0f}m)"
                )
    res["pa_dist_to_disk_km"] = round(min_pa_dist_m / 1000.0, 3) if min_pa_dist_m != float("inf") else None
    res["pa_buffer_passed"] = (min_pa_dist_m >= pa_buffer_m)

    # 2. Slope zonal fraction (Copernicus DEM 30m, slope <= 5.0%)
    slope_ds = _get_slope_dataset()
    try:
        with _raster_lock:
            out_slope, _ = rasterio.mask.mask(slope_ds, [disk_wgs84], crop=True)
        s_data = out_slope[0]
        valid_s = s_data[~np.isnan(s_data)]
        valid_s = valid_s[valid_s >= 0]
        if len(valid_s) > 0:
            usable_s_count = int(np.count_nonzero(valid_s <= 5.0))
            f_slope_usable = float(usable_s_count / len(valid_s))
            mean_slope = float(np.mean(valid_s))
            max_slope = float(np.max(valid_s))
        else:
            f_slope_usable = 0.0
            mean_slope = 0.0
            max_slope = 0.0
    except Exception as e:
        f_slope_usable = 0.0
        mean_slope = 0.0
        max_slope = 0.0
        res["slope_error"] = str(e)

    res["f_slope_usable"] = round(f_slope_usable, 4)
    res["mean_slope_pct"] = round(mean_slope, 2)
    res["max_slope_pct"] = round(max_slope, 2)
    res["slope_passed"] = (f_slope_usable >= min_usable_fraction)
    if not res["slope_passed"]:
        res["tripped_constraints"].append(
            f"Slope usable fraction ({f_slope_usable*100:.1f}%) < {min_usable_fraction*100:.0f}% requirement"
        )

    # 3. WorldCover zonal fraction (10m ESA WorldCover 2021)
    tile = _tile_id(lat, lon)
    wc_ds = _get_dataset(tile)
    try:
        with _raster_lock:
            out_wc, _ = rasterio.mask.mask(wc_ds, [disk_wgs84], crop=True)
        wc_data = out_wc[0]
        valid_wc = wc_data[wc_data > 0]
        total_wc_px = len(valid_wc)
        if total_wc_px > 0:
            excl_mask = np.isin(valid_wc, list(_excluded_codes))
            excl_count = int(np.count_nonzero(excl_mask))
            f_excl = float(excl_count / total_wc_px)
            f_lu_usable = float(1.0 - f_excl)
            water_count = int(np.count_nonzero(np.isin(valid_wc, list(_water_codes))))
            f_water = float(water_count / total_wc_px)
        else:
            f_excl = 1.0
            f_lu_usable = 0.0
            f_water = 0.0
            total_wc_px = 0
    except Exception as e:
        f_excl = 1.0
        f_lu_usable = 0.0
        f_water = 0.0
        total_wc_px = 0
        res["wc_error"] = str(e)

    res["f_lu_usable"] = round(f_lu_usable, 4)
    res["f_lu_excluded"] = round(f_excl, 4)
    res["f_water_disk"] = round(f_water, 4)
    res["lu_passed"] = (f_lu_usable >= min_usable_fraction)
    if not res["lu_passed"]:
        res["tripped_constraints"].append(
            f"Land-use usable fraction ({f_lu_usable*100:.1f}%) < {min_usable_fraction*100:.0f}% requirement"
        )

    # 4. Water buffer (Permanent water: WorldCover class 80 + OSM water bodies >= 1 ha & rivers/canals)
    # Applied to footprint disk edge (disk radius + 500m buffer)
    disk_water_utm = pt_utm.buffer(radius_m + water_buffer_m)
    disk_water_wgs84 = gpd.GeoSeries([disk_water_utm], crs="EPSG:32643").to_crs(epsg=4326).iloc[0]

    # 4a. WorldCover permanent water (class 80)
    water_px_500m = 0
    try:
        with _raster_lock:
            out_w, _ = rasterio.mask.mask(wc_ds, [disk_water_wgs84], crop=True)
        w_data = out_w[0]
        water_px_500m = int(np.count_nonzero(w_data == 80))
    except Exception:
        water_px_500m = 0
    res["wc_water_px_500m"] = water_px_500m
    res["water_px_within_500m_buffer"] = water_px_500m

    # 4b. OSM water bodies:
    # (i) Rivers, reservoirs, tanks/ponds >= 1 ha: excluded within water_buffer_m (500m) of disk edge
    # (ii) Canals and streams: excluded ONLY if they cross the disk itself (dist <= 0); otherwise distance recorded as context
    gdf_water_sb, tree_water_sb, gdf_canal, tree_canal = _load_water_data()
    min_osm_water_dist_m = float("inf")
    if tree_water_sb is not None and not gdf_water_sb.empty:
        cands_sb = list(tree_water_sb.query(disk_water_utm))
        if cands_sb:
            dists = gdf_water_sb.geometry.iloc[cands_sb].distance(disk_utm)
            min_osm_water_dist_m = float(dists.min())

    min_osm_canal_dist_m = float("inf")
    canal_crosses_disk = False
    if tree_canal is not None and not gdf_canal.empty:
        cands_canal = list(tree_canal.query(disk_water_utm))
        if cands_canal:
            dists_c = gdf_canal.geometry.iloc[cands_canal].distance(disk_utm)
            min_osm_canal_dist_m = float(dists_c.min())
            if min_osm_canal_dist_m <= 0.0:
                canal_crosses_disk = True

    res["osm_water_dist_to_disk_m"] = round(min_osm_water_dist_m, 1) if min_osm_water_dist_m != float("inf") else None
    res["osm_canal_dist_to_disk_m"] = round(min_osm_canal_dist_m, 1) if min_osm_canal_dist_m != float("inf") else None
    res["canal_crosses_disk"] = canal_crosses_disk

    # Hard constraint: No WorldCover permanent water (class 80) in buffer, OSM setback water >= 500m, no canal crossing disk
    water_passed = (water_px_500m == 0 and min_osm_water_dist_m >= water_buffer_m and not canal_crosses_disk)
    res["water_buffer_passed"] = water_passed
    if not water_passed:
        reasons = []
        if water_px_500m > 0:
            reasons.append(f"WorldCover permanent water ({water_px_500m} pixels) within 500m of disk")
        if min_osm_water_dist_m < water_buffer_m:
            reasons.append(f"OSM water body/river within {min_osm_water_dist_m:.0f}m of disk edge (< {water_buffer_m:.0f}m threshold)")
        if canal_crosses_disk:
            reasons.append("OSM canal/stream crosses footprint disk")
        res["tripped_constraints"].append("; ".join(reasons))

    # Feasibility verdict
    res["is_feasible"] = (
        res["pa_buffer_passed"]
        and res["slope_passed"]
        and res["lu_passed"]
        and res["water_buffer_passed"]
    )
    return res
