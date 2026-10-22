"""
geoprocessing/build_feature_points.py
--------------------------------------
Builds a per-point feature table for interactive search across Karnataka
using only local vector/raster datasets and the raw cache.

Points: Every 0.1 degree grid point inside Karnataka boundary (~1,603 points).
Features:
- Identification: lat, lon, district
- Radii [0.25, 0.40, 0.54, 0.70, 0.85, 1.00, 1.10] km:
  * fraction of disk inside Karnataka boundary
  * slope shares at or under 3%, 5%, and 8%
  * WorldCover eligible shares with cropland allowed and cropland excluded
- Per point features:
  * GHI and T2M from NASA POWER raw cache
  * land-cost proxy score
  * distance to nearest protected-area polygon
  * distance to nearest water body (rivers, reservoirs, ponds >= 1 ha, WorldCover water)
  * distance to nearest canal or stream line
  * known-gap review flag and reasons
  * for min voltages 33, 66, and 110 kV:
    nearest primary substation (OSM id, name, operator, voltage tag, distance km)
    with flags: untagged closer than confirmed, voltage tag missing.

Outputs:
- geoprocessing/data/feature_points (and feature_points.json)
- MongoDB collection: feature_points
"""

from __future__ import annotations

import os
import sys
import json
import time
import math
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import geopandas as gpd
import rasterio
import rasterio.windows
import rasterio.features
from shapely.geometry import Point
from shapely.strtree import STRtree
from shapely.prepared import prep

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from generate_candidates import build_grid
import fetch_boundary as fb
from fetch_boundary import (
    is_in_karnataka,
    get_boundary_disk_fraction,
)
from fetch_district import get_district_name
from fetch_protected_buffer import (
    _load_wdpa_data,
    check_known_gaps_review,
)
from zonal_footprint import (
    _load_water_data,
    _get_slope_dataset,
)
from fetch_landuse import (
    _get_dataset as _get_wc_dataset,
    _tile_id as _wc_tile_id,
    CLASS_CATALOGUE,
)
from fetch_nasa_power import fetch_climatology
from fetch_landcost_proxy import haversine
from backend.db import get_feature_points_col
from bulk_download_roads import load_roads_and_index

RADII_KM = [0.25, 0.40, 0.54, 0.70, 0.85, 1.00, 1.10]
LARGEST_RADIUS_KM = 1.10
LARGEST_RADIUS_M = 1100.0

DATA_DIR = os.path.join(_HERE, "data")
OUTPUT_FILE_NO_EXT = os.path.join(DATA_DIR, "feature_points")
OUTPUT_FILE_JSON = os.path.join(DATA_DIR, "feature_points.json")

_EXCLUDED_CODES = {k for k, v in CLASS_CATALOGUE.items() if v[1] == "excluded"}


def compute_inputs_hash() -> str:
    """Compute a deterministic hash of all raw local input datasets."""
    files_to_hash = [
        os.path.join(DATA_DIR, "karnataka_boundary.geojson"),
        os.path.join(DATA_DIR, "karnataka_districts.geojson"),
        os.path.join(DATA_DIR, "substations_karnataka.geojson"),
        os.path.join(DATA_DIR, "lines_karnataka.geojson"),
        os.path.join(DATA_DIR, "water_karnataka.geojson"),
        os.path.join(DATA_DIR, "slope_karnataka.tif"),
        os.path.join(DATA_DIR, "wdpa", "wdpa_karnataka_filtered.geojson"),
        os.path.join(DATA_DIR, "roads_karnataka.geojson"),
        os.path.join(DATA_DIR, "power_plants_karnataka.geojson"),
    ]
    h = hashlib.sha256()
    for fp in sorted(files_to_hash):
        if os.path.exists(fp):
            stat = os.stat(fp)
            h.update(f"{os.path.basename(fp)}:{stat.st_size}:{int(stat.st_mtime)}".encode("utf-8"))
    return h.hexdigest()[:16]


def load_cached_cities_towns() -> Dict[int, Tuple[float, float]]:
    """Extract all unique city and town nodes from raw_cache OVERPASS_LANDCOST responses."""
    import glob
    cache_dir = os.path.join(DATA_DIR, "raw_cache")
    cities: Dict[int, Tuple[float, float]] = {}
    for cf in glob.glob(os.path.join(cache_dir, "*.json")):
        try:
            with open(cf, "r", encoding="utf-8") as fp:
                d = json.load(fp)
            if d.get("metadata", {}).get("service") == "OVERPASS_LANDCOST":
                for el in d.get("response", {}).get("elements", []):
                    if el.get("type") == "node":
                        cid = el["id"]
                        if cid not in cities:
                            cities[cid] = (float(el["lat"]), float(el["lon"]))
        except Exception:
            pass
    return cities


def load_substation_spatial_indexes() -> Dict[str, Any]:
    """Prepare spatial indexes for minimum voltages 33, 66, and 110 kV."""
    subs_path = os.path.join(DATA_DIR, "substations_karnataka.geojson")
    gdf = gpd.read_file(subs_path).to_crs(epsg=32643)

    # Filter out generation
    non_gen = gdf[gdf.get("is_generation", False) != True].copy()
    untagged = non_gen[non_gen["is_qualifying"] != True].copy()
    tree_untagged = STRtree(untagged.geometry) if not untagged.empty else None

    sub_bundle = {
        "untagged": untagged,
        "tree_untagged": tree_untagged,
        "voltages": {},
    }

    for v_min in [33, 66, 110]:
        v_thresh = v_min * 1000.0
        cond = (non_gen["voltage_v"] >= v_thresh) | (
            non_gen["voltage_v"].isna() & (non_gen["substation_kind"] == "transmission")
        )
        q_gdf = non_gen[cond].copy().reset_index(drop=True)
        tree_q = STRtree(q_gdf.geometry) if not q_gdf.empty else None
        sub_bundle["voltages"][str(v_min)] = {
            "gdf": q_gdf,
            "tree": tree_q,
        }

    return sub_bundle


def query_substation_for_voltage(
    pt_utm: Point,
    v_min: int,
    sub_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """Query nearest qualifying primary substation and check flags."""
    v_data = sub_bundle["voltages"][str(v_min)]
    q_gdf = v_data["gdf"]
    tree_q = v_data["tree"]
    untagged_gdf = sub_bundle["untagged"]
    tree_untagged = sub_bundle["tree_untagged"]

    if tree_q is None or q_gdf.empty:
        return {
            "osm_id": None,
            "name": None,
            "operator": None,
            "voltage_tag": None,
            "distance_km": None,
            "untagged_closer_than_confirmed": False,
            "voltage_tag_missing": False,
        }

    q_idx = tree_q.nearest(pt_utm)
    q_row = q_gdf.iloc[q_idx]
    q_dist_m = float(pt_utm.distance(q_row.geometry))
    q_dist_km = round(q_dist_m / 1000.0, 3)

    # Check if an untagged substation is closer
    untagged_closer = False
    if tree_untagged is not None and not untagged_gdf.empty:
        u_idx = tree_untagged.nearest(pt_utm)
        u_dist_m = float(pt_utm.distance(untagged_gdf.geometry.iloc[u_idx]))
        if u_dist_m < q_dist_m:
            untagged_closer = True

    v_tag = q_row.get("voltage_tag")
    v_tag_str = str(v_tag).strip() if v_tag is not None else ""
    is_missing = v_tag is None or v_tag_str.lower() in ["none", "nan", "", "no voltage tag"]

    name_val = q_row.get("name_tag")
    name_str = str(name_val).strip() if name_val is not None and str(name_val) != "nan" else None
    op_val = q_row.get("operator_tag")
    op_str = str(op_val).strip() if op_val is not None and str(op_val) != "nan" else None

    return {
        "osm_id": q_row.get("osm_id"),
        "name": name_str,
        "operator": op_str,
        "voltage_tag": v_tag_str if not is_missing else None,
        "distance_km": q_dist_km,
        "untagged_closer_than_confirmed": untagged_closer,
        "voltage_tag_missing": is_missing,
    }


def build_feature_table(verbose: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build the per-point feature table across all ~1,600 Karnataka points."""
    t0 = time.time()
    build_id = compute_inputs_hash()
    build_date = datetime.now(timezone.utc).isoformat()

    if verbose:
        print("=" * 70)
        print("BUILDING PER-POINT FEATURE TABLE FOR KARNATAKA SOLAR PV")
        print(f"  Build ID   : {build_id}")
        print(f"  Build Date : {build_date}")
        print("=" * 70)

    # 1. Base Grid Points
    if verbose:
        print("\n1. Initializing grid points inside Karnataka boundary...")
    fb._init_boundary()
    inner_1100 = fb._poly_utm.buffer(-LARGEST_RADIUS_M)
    prep_inner_1100 = prep(inner_1100)

    raw_grid = build_grid(step=0.1)
    points = [(lat, lon) for lat, lon in raw_grid if is_in_karnataka(lat, lon)]
    total_pts = len(points)
    if verbose:
        print(f"   Found {total_pts} points inside Karnataka boundary (0.1 degree grid).")

    # 2. Local Layers
    if verbose:
        print("\n2. Loading local layers (WDPA, Water, Substations, Cities/Towns, Roads, Lines, Solar Plants, DEM, WorldCover)...")
    wdpa_gdf = _load_wdpa_data()
    tree_wdpa = STRtree(wdpa_gdf.geometry) if not wdpa_gdf.empty else None

    gdf_water_sb, tree_water_sb, gdf_canal, tree_canal = _load_water_data()
    sub_bundle = load_substation_spatial_indexes()
    cities_towns = load_cached_cities_towns()
    slope_ds = _get_slope_dataset()

    # Roads layer and spatial index
    gdf_roads_utm, tree_roads, tile_count, failed_tiles = load_roads_and_index()

    # Transmission lines (>= 33 kV)
    lines_path = os.path.join(DATA_DIR, "lines_karnataka.geojson")
    gdf_lines = gpd.read_file(lines_path).to_crs(epsg=32643)
    tree_lines = STRtree(gdf_lines.geometry) if not gdf_lines.empty else None

    # Solar power plants (plant_source == 'solar')
    plants_path = os.path.join(DATA_DIR, "power_plants_karnataka.geojson")
    gdf_plants = gpd.read_file(plants_path)
    gdf_solar = gdf_plants[gdf_plants["plant_source"] == "solar"].to_crs(epsg=32643).reset_index(drop=True)
    tree_solar = STRtree(gdf_solar.geometry) if not gdf_solar.empty else None

    if verbose:
        print(f"   WDPA polygons loaded: {len(wdpa_gdf)}")
        print(f"   Water bodies: {len(gdf_water_sb)}, Canal/stream lines: {len(gdf_canal)}")
        print(f"   Cities/towns in cache: {len(cities_towns)}")
        print(f"   Roads indexed: {len(gdf_roads_utm)} ways (tiles: {tile_count}, failed: {len(failed_tiles)})")
        print(f"   Transmission lines indexed: {len(gdf_lines)} lines")
        print(f"   Solar plant polygons indexed: {len(gdf_solar)} plants")

    # Pre-generate concentric disk shapes in UTM
    # Radii in meters
    radii_m = [r * 1000.0 for r in RADII_KM]

    feature_rows: List[Dict[str, Any]] = []

    if verbose:
        print(f"\n3. Extracting features for {total_pts} points...")

    for idx, (lat, lon) in enumerate(points, 1):
        pt_wgs = Point(lon, lat)
        pt_utm = gpd.GeoSeries([pt_wgs], crs="EPSG:4326").to_crs(epsg=32643).iloc[0]

        # District
        district = get_district_name(lat, lon)

        # Precompute concentric disks
        disks_utm = [pt_utm.buffer(rm) for rm in radii_m]
        disks_wgs = [gpd.GeoSeries([d_utm], crs="EPSG:32643").to_crs(epsg=4326).iloc[0] for d_utm in disks_utm]
        disk_max_wgs = disks_wgs[-1]

        # Fraction of disk inside Karnataka
        is_deep_inside = prep_inner_1100.contains(pt_utm)
        disk_karnataka_fractions: Dict[str, float] = {}
        for r_km, disk_utm in zip(RADII_KM, disks_utm):
            r_str = f"{r_km:.2f}"
            if is_deep_inside:
                disk_karnataka_fractions[r_str] = 1.0
            else:
                inter_area = disk_utm.intersection(fb._poly_utm).area
                disk_karnataka_fractions[r_str] = round(float(inter_area / disk_utm.area), 4)

        # Slope extraction (read 1.10 km window ONCE)
        win_slope = rasterio.windows.from_bounds(*disk_max_wgs.bounds, slope_ds.transform).round_offsets().round_lengths()
        slope_data = slope_ds.read(1, window=win_slope)
        win_slope_trans = rasterio.windows.transform(win_slope, slope_ds.transform)

        slope_shares: Dict[str, Dict[str, float]] = {}
        for r_km, disk_wgs in zip(RADII_KM, disks_wgs):
            r_str = f"{r_km:.2f}"
            m_s = rasterio.features.rasterize([(disk_wgs, 1)], out_shape=slope_data.shape, transform=win_slope_trans, fill=0, default_value=1)
            v_s = slope_data[(m_s == 1) & ~np.isnan(slope_data) & (slope_data >= 0)]
            if len(v_s) > 0:
                s3 = float(np.count_nonzero(v_s <= 3.0) / len(v_s))
                s5 = float(np.count_nonzero(v_s <= 5.0) / len(v_s))
                s8 = float(np.count_nonzero(v_s <= 8.0) / len(v_s))
            else:
                s3, s5, s8 = 0.0, 0.0, 0.0
            slope_shares[r_str] = {
                "slope_le_3pct": round(s3, 4),
                "slope_le_5pct": round(s5, 4),
                "slope_le_8pct": round(s8, 4),
            }

        # WorldCover extraction (read 1.10 km window ONCE)
        wc_tile = _wc_tile_id(lat, lon)
        wc_ds = _get_wc_dataset(wc_tile)
        win_wc = rasterio.windows.from_bounds(*disk_max_wgs.bounds, wc_ds.transform).round_offsets().round_lengths()
        wc_data = wc_ds.read(1, window=win_wc, boundless=True, fill_value=0)
        win_wc_trans = rasterio.windows.transform(win_wc, wc_ds.transform)

        # Check for WorldCover permanent water (class 80) in the 1.1km window
        wc_water_coords = np.argwhere(wc_data == 80)
        min_wc_water_dist_m = float("inf")
        if len(wc_water_coords) > 0:
            # Transform pixel row,col to UTM coordinates
            pixel_xs, pixel_ys = rasterio.transform.xy(win_wc_trans, wc_water_coords[:, 0], wc_water_coords[:, 1])
            pixel_pts_wgs = gpd.points_from_xy(pixel_xs, pixel_ys, crs="EPSG:4326").to_crs(epsg=32643)
            dists_wc_water = np.hypot(pixel_pts_wgs.x - pt_utm.x, pixel_pts_wgs.y - pt_utm.y)
            min_wc_water_dist_m = float(dists_wc_water.min())

        lu_shares: Dict[str, Dict[str, float]] = {}
        for r_km, disk_wgs in zip(RADII_KM, disks_wgs):
            r_str = f"{r_km:.2f}"
            m_wc = rasterio.features.rasterize([(disk_wgs, 1)], out_shape=wc_data.shape, transform=win_wc_trans, fill=0, default_value=1)
            v_wc = wc_data[(m_wc == 1) & (wc_data > 0)]
            if len(v_wc) > 0:
                excl_crop_allowed = int(np.count_nonzero(np.isin(v_wc, list(_EXCLUDED_CODES))))
                excl_crop_excluded = int(np.count_nonzero(np.isin(v_wc, list(_EXCLUDED_CODES) + [40])))
                f_allowed = float((len(v_wc) - excl_crop_allowed) / len(v_wc))
                f_excluded = float((len(v_wc) - excl_crop_excluded) / len(v_wc))
            else:
                f_allowed, f_excluded = 0.0, 0.0
            lu_shares[r_str] = {
                "lu_cropland_allowed": round(f_allowed, 4),
                "lu_cropland_excluded": round(f_excluded, 4),
            }

        # Build radii composite dict
        radii_dict: Dict[str, Any] = {}
        for r_km in RADII_KM:
            r_str = f"{r_km:.2f}"
            c_share = round(lu_shares[r_str]["lu_cropland_allowed"] - lu_shares[r_str]["lu_cropland_excluded"], 4)
            radii_dict[r_str] = {
                "radius_km": r_km,
                "disk_karnataka_fraction": disk_karnataka_fractions[r_str],
                "karnataka_fraction": disk_karnataka_fractions[r_str],
                "slope_le_3pct": slope_shares[r_str]["slope_le_3pct"],
                "slope_le_5pct": slope_shares[r_str]["slope_le_5pct"],
                "slope_le_8pct": slope_shares[r_str]["slope_le_8pct"],
                "share_slope_le_3": slope_shares[r_str]["slope_le_3pct"],
                "share_slope_le_5": slope_shares[r_str]["slope_le_5pct"],
                "share_slope_le_8": slope_shares[r_str]["slope_le_8pct"],
                "lu_cropland_allowed": lu_shares[r_str]["lu_cropland_allowed"],
                "lu_cropland_excluded": lu_shares[r_str]["lu_cropland_excluded"],
                "share_cropland_allowed": lu_shares[r_str]["lu_cropland_allowed"],
                "share_cropland_excluded": lu_shares[r_str]["lu_cropland_excluded"],
                "cropland_share": c_share,
            }

        # GHI and T2M from NASA POWER raw cache
        try:
            clima = fetch_climatology(lat, lon)
            ghi = round(float(clima.ghi_kwh_m2_day), 4)
            t2m = round(float(clima.t2m_celsius), 2)
        except Exception:
            ghi, t2m = None, None

        # Distance to nearest OSM city or town & Land-cost proxy score
        if cities_towns:
            min_city_dist = min(haversine(lat, lon, clat, clon) for clat, clon in cities_towns.values())
            dist_town_km = round(float(min_city_dist), 3)
            landcost_proxy = round(float(min(min_city_dist, 50.0) / 50.0), 4)
        else:
            dist_town_km = None
            landcost_proxy = None

        # Distance to nearest OSM highway (motorway, trunk, primary, secondary)
        if tree_roads is not None and not gdf_roads_utm.empty:
            r_idx = tree_roads.nearest(pt_utm)
            dist_road_m = float(pt_utm.distance(gdf_roads_utm.geometry.iloc[r_idx]))
            dist_road_km = round(dist_road_m / 1000.0, 3)
        else:
            dist_road_km = None

        # Distance to nearest transmission line (>= 33 kV)
        if tree_lines is not None and not gdf_lines.empty:
            l_idx = tree_lines.nearest(pt_utm)
            dist_line_m = float(pt_utm.distance(gdf_lines.geometry.iloc[l_idx]))
            dist_line_km = round(dist_line_m / 1000.0, 3)
        else:
            dist_line_km = None

        # Distance to nearest solar power plant polygon
        if tree_solar is not None and not gdf_solar.empty:
            s_idx = tree_solar.nearest(pt_utm)
            dist_solar_m = float(pt_utm.distance(gdf_solar.geometry.iloc[s_idx]))
            dist_solar_plant_km = round(dist_solar_m / 1000.0, 3)
        else:
            dist_solar_plant_km = None

        # Distance to nearest protected area polygon
        if tree_wdpa is not None and not wdpa_gdf.empty:
            w_idx = tree_wdpa.nearest(pt_utm)
            dist_pa_m = float(pt_utm.distance(wdpa_gdf.geometry.iloc[w_idx]))
            dist_pa_km = round(dist_pa_m / 1000.0, 3)
        else:
            dist_pa_m = float("inf")
            dist_pa_km = None

        # Distance to nearest water body (rivers, reservoirs, ponds >= 1 ha, WorldCover water)
        if tree_water_sb is not None and not gdf_water_sb.empty:
            ws_idx = tree_water_sb.nearest(pt_utm)
            dist_osm_water_m = float(pt_utm.distance(gdf_water_sb.geometry.iloc[ws_idx]))
        else:
            dist_osm_water_m = float("inf")

        dist_water_m = min(dist_osm_water_m, min_wc_water_dist_m)
        dist_water_km = round(dist_water_m / 1000.0, 3) if dist_water_m != float("inf") else None

        # Distance to nearest canal or stream line
        if tree_canal is not None and not gdf_canal.empty:
            c_idx = tree_canal.nearest(pt_utm)
            dist_canal_m = float(pt_utm.distance(gdf_canal.geometry.iloc[c_idx]))
            dist_canal_km = round(dist_canal_m / 1000.0, 3)
        else:
            dist_canal_m = float("inf")
            dist_canal_km = None

        # Known-gap review flag
        gap_reasons = check_known_gaps_review(lat, lon, review_dist_km=25.0)
        known_gap_review = (len(gap_reasons) > 0)

        # Substations for 33, 66, and 110 kV
        sub_33 = query_substation_for_voltage(pt_utm, 33, sub_bundle)
        sub_66 = query_substation_for_voltage(pt_utm, 66, sub_bundle)
        sub_110 = query_substation_for_voltage(pt_utm, 110, sub_bundle)

        row: Dict[str, Any] = {
            "point_id": f"{lat:.4f}_{lon:.4f}",
            "lat": lat,
            "lon": lon,
            "latitude": lat,
            "longitude": lon,
            "district": district,
            "radii": radii_dict,
            "ghi": ghi,
            "t2m": t2m,
            "landcost_proxy": landcost_proxy,
            "dist_town_km": dist_town_km,
            "dist_road_km": dist_road_km,
            "dist_line_km": dist_line_km,
            "dist_solar_plant_km": dist_solar_plant_km,
            "cropland_share": radii_dict["0.54"]["cropland_share"],
            "dist_protected_area_km": dist_pa_km,
            "dist_protected_area_m": round(dist_pa_m, 1) if dist_pa_m != float("inf") else None,
            "dist_water_body_km": dist_water_km,
            "dist_water_body_m": round(dist_water_m, 1) if dist_water_m != float("inf") else None,
            "dist_canal_stream_km": dist_canal_km,
            "dist_canal_stream_m": round(dist_canal_m, 1) if dist_canal_m != float("inf") else None,
            "known_gap_review": known_gap_review,
            "known_gap_reasons": gap_reasons,
            "substations": {
                "33": sub_33,
                "66": sub_66,
                "110": sub_110,
            },
            "substation_33kv": sub_33,
            "substation_66kv": sub_66,
            "substation_110kv": sub_110,
            "build_id": build_id,
            "build_date": build_date,
        }
        feature_rows.append(row)

        if verbose and (idx % 400 == 0 or idx == total_pts):
            print(f"   [{idx:>4}/{total_pts}] ({idx/total_pts*100:5.1f}%) elapsed: {time.time()-t0:.1f}s")

    t1 = time.time()
    build_time = round(t1 - t0, 2)
    meta = {
        "build_id": build_id,
        "build_date": build_date,
        "row_count": len(feature_rows),
        "build_time_seconds": build_time,
        "tile_count": tile_count,
        "failed_tiles": failed_tiles,
    }

    if verbose:
        print("\n" + "=" * 70)
        print("BUILD SUMMARY")
        print(f"  Row Count  : {len(feature_rows)}")
        print(f"  Build Time : {build_time}s")
        print(f"  Build ID   : {build_id}")
        print("=" * 70)

    return feature_rows, meta


def save_and_load(feature_rows: List[Dict[str, Any]], meta: Dict[str, Any], verbose: bool = True) -> None:
    """Save feature points to data file and load into MongoDB."""
    os.makedirs(DATA_DIR, exist_ok=True)

    # Save to JSON
    with open(OUTPUT_FILE_JSON, "w", encoding="utf-8") as f:
        json.dump({"metadata": meta, "feature_points": feature_rows}, f, indent=2)

    # Save to feature_points (without extension) as well
    with open(OUTPUT_FILE_NO_EXT, "w", encoding="utf-8") as f:
        json.dump({"metadata": meta, "feature_points": feature_rows}, f)

    if verbose:
        print(f"Saved local file to {OUTPUT_FILE_NO_EXT} and {OUTPUT_FILE_JSON}")

    # Load into MongoDB collection feature_points
    col = get_feature_points_col()
    col.delete_many({})

    # Prepare documents with _id = point_id
    docs = []
    for r in feature_rows:
        doc = dict(r)
        doc["_id"] = r["point_id"]
        docs.append(doc)

    if docs:
        col.insert_many(docs)
        col.create_index("point_id", unique=True)
        col.create_index([("lat", 1), ("lon", 1)])
        col.create_index("district")

    if verbose:
        print(f"Loaded {len(docs)} documents into MongoDB collection 'feature_points'.")


def print_sample_rows(feature_rows: List[Dict[str, Any]], count: int = 5) -> None:
    """Print sample rows in a clean, human-readable format."""
    print("\n" + "=" * 70)
    print(f"SAMPLE {count} ROWS (from {len(feature_rows)} total points):")
    print("=" * 70)
    indices = [0, len(feature_rows) // 4, len(feature_rows) // 2, 3 * len(feature_rows) // 4, -1]
    for i, idx in enumerate(indices[:count], 1):
        r = feature_rows[idx]
        print(f"\n--- Sample Row #{i}: Point ({r['lat']}, {r['lon']}) - {r['district']} ---")
        print(f"  GHI: {r['ghi']} kWh/m²/d | T2M: {r['t2m']}°C | Land-Cost Proxy: {r['landcost_proxy']}")
        print(f"  New Distances (km): Town: {r['dist_town_km']} | Road: {r['dist_road_km']} | Line: {r['dist_line_km']} | Solar Plant: {r['dist_solar_plant_km']}")
        print(f"  Cropland Share (0.54 km): {r['cropland_share']*100:.1f}%")
        print(f"  Distances (km): Protected Area: {r['dist_protected_area_km']} | Water Body: {r['dist_water_body_km']} | Canal/Stream: {r['dist_canal_stream_km']}")
        print(f"  Known Gap Review Flag: {r['known_gap_review']} ({r['known_gap_reasons'] if r['known_gap_review'] else 'None'})")
        sub33 = r['substations']['33']
        sub110 = r['substations']['110']
        print(f"  Substation 33kV : {sub33['osm_id']} ({sub33['distance_km']} km, v_tag={sub33['voltage_tag']}, untagged_closer={sub33['untagged_closer_than_confirmed']}, v_missing={sub33['voltage_tag_missing']})")
        print(f"  Substation 110kV: {sub110['osm_id']} ({sub110['distance_km']} km, v_tag={sub110['voltage_tag']}, untagged_closer={sub110['untagged_closer_than_confirmed']}, v_missing={sub110['voltage_tag_missing']})")
        r54 = r['radii']['0.54']
        print(f"  Radius 0.54km   : KA disk frac: {r54['disk_karnataka_fraction']} | Slope <=5%: {r54['slope_le_5pct']*100:.1f}% | Cropland share: {r54['cropland_share']*100:.1f}%")


def print_column_stats(feature_rows: List[Dict[str, Any]], tile_count: int, failed_tiles: List[Any]) -> None:
    """Print the tile count, failed tiles, and for each new column the min, median, max and null count."""
    print("\n" + "=" * 75)
    print("ROAD TILE DOWNLOAD & CACHE STATUS")
    print(f"  Total Tile Count : {tile_count}")
    print(f"  Failed Tiles     : {len(failed_tiles)} ({failed_tiles if failed_tiles else 'None'})")
    print("=" * 75)

    columns = [
        ("dist_town_km", [r["dist_town_km"] for r in feature_rows]),
        ("dist_road_km", [r["dist_road_km"] for r in feature_rows]),
        ("dist_line_km", [r["dist_line_km"] for r in feature_rows]),
        ("dist_solar_plant_km", [r["dist_solar_plant_km"] for r in feature_rows]),
        ("cropland_share", [r["cropland_share"] for r in feature_rows]),
    ]

    print("\n" + "=" * 75)
    print("NEW COLUMNS SUMMARY STATISTICS (1,603 POINTS)")
    print(f"{'Column':<22} | {'Min':>10} | {'Median':>10} | {'Max':>10} | {'Null Count':>12}")
    print("-" * 75)
    for col_name, vals in columns:
        null_count = sum(1 for v in vals if v is None)
        valid = [v for v in vals if v is not None]
        min_val = min(valid) if valid else "N/A"
        med_val = round(float(np.median(valid)), 4) if valid else "N/A"
        max_val = max(valid) if valid else "N/A"
        print(f"{col_name:<22} | {str(min_val):>10} | {str(med_val):>10} | {str(max_val):>10} | {null_count:>12}")
    print("=" * 75)


def main():
    rows, meta = build_feature_table(verbose=True)
    save_and_load(rows, meta, verbose=True)
    print_sample_rows(rows, count=5)
    print_column_stats(rows, meta.get("tile_count", 115), meta.get("failed_tiles", []))


if __name__ == "__main__":
    main()
