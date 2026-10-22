"""
geoprocessing/bulk_download_substations.py
-------------------------------------------
Bulk downloader and spatial indexer for Karnataka electrical grid infrastructure.
Per Step 3:
- Downloads all power=substation and power=line with voltage >= 33 kV for Karnataka
  plus a 25 km margin, in 0.5 degree tiles, with retries and mirrors.
- Saves each raw response to raw_cache.
- Builds local spatial indexes (STRtree in EPSG:32643).
- Computes substation distance for EVERY feasible point locally, keeping existing rules:
    * untagged substations excluded from qualifying distance
    * generation-type substations flagged low confidence
    * lines reported separately
- Reports: tile count, failed tiles (if any), and number of substations and lines loaded.
"""

from __future__ import annotations

import os
import sys
import re
import time
import math
import json
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple

import requests
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon, MultiPolygon, box
from shapely.strtree import STRtree

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from raw_cache import get_cached, save_cached
from fetch_substation_distance import parse_osm_voltage_v, haversine

OVERPASS_MIRRORS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def get_tiles(margin_km: float = 25.0, tile_size: float = 0.5) -> List[Tuple[float, float, float, float]]:
    """Generate 0.5-degree tiles covering Karnataka + margin_km buffer."""
    kar_path = os.path.join(_HERE, "data", "karnataka_boundary.geojson")
    kar = gpd.read_file(kar_path)
    kar_utm = kar.to_crs(epsg=32643)
    kar_buf_utm = kar_utm.buffer(margin_km * 1000.0)
    kar_buf_wgs84 = kar_buf_utm.to_crs(epsg=4326).iloc[0]

    minx, miny, maxx, maxy = kar_buf_wgs84.bounds

    lats = np.arange(np.floor(miny / tile_size) * tile_size, np.ceil(maxy / tile_size) * tile_size, tile_size)
    lons = np.arange(np.floor(minx / tile_size) * tile_size, np.ceil(maxx / tile_size) * tile_size, tile_size)

    tiles = []
    for lat in lats:
        for lon in lons:
            b = box(lon, lat, lon + tile_size, lat + tile_size)
            if b.intersects(kar_buf_wgs84):
                tiles.append((round(lat, 2), round(lon, 2), round(lat + tile_size, 2), round(lon + tile_size, 2)))
    return tiles


def query_tile(tile: Tuple[float, float, float, float], max_retries: int = 5) -> Tuple[Tuple[float, float, float, float], Optional[Dict[str, Any]], Optional[str]]:
    s, w, n, e = tile
    bbox = f"{s},{w},{n},{e}"
    query = f"""[out:json][timeout:35][maxsize:536870912];
(
  node["power"="substation"]({bbox});
  way["power"="substation"]({bbox});
  relation["power"="substation"]({bbox});
  way["power"="line"](if:!t["voltage"] || number(t["voltage"])>=33000)({bbox});
);
out geom;"""
    req_key = f"OVERPASS_GRID_TILE: bbox={bbox} query={query.strip()}"

    cached = get_cached(req_key)
    if cached is not None and "elements" in cached:
        return tile, cached, None

    last_err = None
    for attempt in range(max_retries):
        url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            headers = {"User-Agent": f"SolarPVKarnatakaProject/1.3 (tile_{s}_{w})"}
            resp = requests.post(url, data={"data": query}, headers=headers, timeout=35)
            if resp.status_code == 200:
                data = resp.json()
                save_cached(req_key, data, {"service": "OVERPASS_GRID_TILE", "bbox": bbox, "tile": tile})
                return tile, data, None
            else:
                last_err = f"HTTP {resp.status_code} from {url}"
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0 + attempt * 1.5)

    return tile, None, last_err


def download_all_tiles(tiles: List[Tuple[float, float, float, float]], max_workers: int = 3) -> Tuple[List[Dict[str, Any]], List[Tuple[float, float, float, float]]]:
    print(f"Checking & downloading {len(tiles)} tiles using up to {max_workers} concurrent workers across Overpass mirrors...")
    results = []
    failed = []

    # First check how many are already cached
    cached_count = 0
    to_download = []
    for t in tiles:
        s, w, n, e = t
        bbox = f"{s},{w},{n},{e}"
        query = f"""[out:json][timeout:35][maxsize:536870912];
(
  node["power"="substation"]({bbox});
  way["power"="substation"]({bbox});
  relation["power"="substation"]({bbox});
  way["power"="line"](if:!t["voltage"] || number(t["voltage"])>=33000)({bbox});
);
out geom;"""
        req_key = f"OVERPASS_GRID_TILE: bbox={bbox} query={query.strip()}"
        c = get_cached(req_key)
        if c is not None and "elements" in c:
            results.append((t, c, None))
            cached_count += 1
        else:
            to_download.append(t)

    print(f"  {cached_count}/{len(tiles)} tiles already present in raw_cache. {len(to_download)} to fetch.")

    if to_download:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_tile = {executor.submit(query_tile, t): t for t in to_download}
            done_count = 0
            for future in concurrent.futures.as_completed(future_to_tile):
                t, data, err = future.result()
                done_count += 1
                if data is not None:
                    results.append((t, data, None))
                    n_el = len(data.get("elements", []))
                    if done_count % 10 == 0 or done_count == len(to_download):
                        print(f"  [{done_count}/{len(to_download)}] Tile {t} -> {n_el} elements")
                else:
                    failed.append(t)
                    print(f"  FAILED Tile {t}: {err}")

    raw_responses = [d for _, d, _ in results if d is not None]
    return raw_responses, failed


CAPTIVE_POOLING_PATTERNS = [
    r"\bcaptive\b",
    r"\bcpp\b",
    r"\bco-?gen\b",
    r"\bpooling\b",
    r"\bpss\b",
    r"\bdeveloper\b",
    r"\bsolar\s+park\b",
    r"\bwind\s+park\b",
    r"\bsolar\s+farm\b",
    r"\bwind\s+farm\b",
    r"\bpower\s+plant\b",
    r"\bthermal\s+power\b",
    r"\bhydroelectric\b",
    r"\bhydro\s+electric\b",
    r"\bhydel\b",
    r"\bgenerating\s+station\b",
    r"\bpower\s+station\b",
    r"\bpower\s+house\b",
    r"\bpower\s+generation\b",
    r"\bgreen\s+infra\b",
    r"\bserentica\b",
    r"\bsolar\b",
    r"\bwind\b",
    r"\belectrode\b",
    r"^s/s-\d+$",
]
_COMPILED_CAPTIVE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in CAPTIVE_POOLING_PATTERNS]
_UTILITY_OPERATOR_PATTERN = re.compile(r"\b(kptcl|msetcl|tangedco|powergrid)\b", re.IGNORECASE)


def load_power_plant_polygons_utm() -> gpd.GeoDataFrame:
    """Loads power plant polygons from geojson and projects to EPSG:32643 with 50m buffer."""
    plant_path = os.path.join(_HERE, "data", "power_plants_karnataka.geojson")
    if not os.path.exists(plant_path):
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:32643")
    gdf = gpd.read_file(plant_path)
    if gdf.empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:32643")
    gdf_utm = gdf.to_crs(epsg=32643)
    # Apply 50m buffer in UTM to capture touching or perimeter substations
    gdf_buf = gpd.GeoDataFrame(gdf_utm.drop(columns=["geometry"]), geometry=gdf_utm.geometry.buffer(50.0), crs="EPSG:32643")
    return gdf_buf


def check_is_generation(tags: Dict[str, Any], geom_utm: Any, plant_gdf_utm: Optional[gpd.GeoDataFrame] = None) -> Tuple[bool, Optional[str]]:
    """
    Determines if a substation is a generation, captive, or developer pooling facility:
    1. substation=generation, industrial, traction, or explicit generator tags.
    2. Touches or lies inside any power=plant polygon (with 50m buffer),
       with plant-touch recovery for public utility operators (KPTCL, MSETCL, TANGEDCO, POWERGRID)
       or substation=transmission unless captive/developer tags are present.
    3. Name or operator matches captive/developer pooling keywords or electrode grounding stations.
    """
    sub_kind = str(tags.get("substation") or "").lower()
    plant = str(tags.get("plant:source") or tags.get("generator:source") or "").lower()
    name = str(tags.get("name") or tags.get("operator") or tags.get("ref") or "").strip()

    # 1. Tag-level generation indicators
    if sub_kind in ["generation", "industrial", "traction"]:
        return True, f"substation={sub_kind}"
    if "solar" in plant or "wind" in plant or "generator" in tags:
        return True, "generation_tag"

    # 2. Captive / developer / electrode pattern match
    has_captive_pattern = False
    captive_match_reason = None
    if name:
        for pat in _COMPILED_CAPTIVE_PATTERNS:
            if pat.search(name):
                has_captive_pattern = True
                captive_match_reason = f"name_matches:{pat.pattern}"
                break

    # If captive/developer pattern matches, exclude regardless of plant touch
    if has_captive_pattern:
        return True, captive_match_reason

    # 3. Inside or touching power=plant polygon
    if plant_gdf_utm is not None and not plant_gdf_utm.empty and geom_utm is not None:
        possible = list(plant_gdf_utm.sindex.intersection(geom_utm.bounds))
        for p_idx in possible:
            if geom_utm.intersects(plant_gdf_utm.geometry.iloc[p_idx]) or geom_utm.touches(plant_gdf_utm.geometry.iloc[p_idx]):
                p_name = plant_gdf_utm.iloc[p_idx].get("name") or "unnamed"
                # Plant-touch recovery:
                # If operator matches KPTCL/MSETCL/TANGEDCO/POWERGRID or substation tag is "transmission",
                # recover it as a primary public utility substation.
                op_name = str(tags.get("operator") or tags.get("name") or "")
                is_utility = bool(_UTILITY_OPERATOR_PATTERN.search(op_name) or sub_kind == "transmission")
                if is_utility:
                    pass  # Recovered!
                else:
                    return True, f"touches_or_inside_power_plant:{p_name}"

    return False, None


def build_spatial_indexes(raw_responses: List[Dict[str, Any]]):
    """
    Parses all OSM elements from responses, deduplicates them, and builds
    GeoDataFrames and STRtree spatial indexes in EPSG:32643.
    Enforces Criterion 4 Substation Rule:
    - Primary qualifying: >=33 kV, non-untagged, AND non-generation.
    - Generation substations kept separately as context only.
    """
    plant_gdf_utm = load_power_plant_polygons_utm()

    substations_qualifying = []
    substations_generation = []
    substations_all = []
    lines = []

    seen_subs = set()
    seen_lines = set()

    for resp in raw_responses:
        for el in resp.get("elements", []):
            el_type = el.get("type")
            el_id = el.get("id")
            uid = (el_type, el_id)
            tags = el.get("tags", {})
            power = tags.get("power")

            # Extract geometry
            geom = None
            if el_type == "node" and "lat" in el and "lon" in el:
                geom = Point(el["lon"], el["lat"])
            elif "geometry" in el and len(el["geometry"]) >= 2:
                coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
                if power == "substation" and len(coords) >= 4 and coords[0] == coords[-1]:
                    geom = Polygon(coords)
                else:
                    geom = LineString(coords)
            elif "geometry" in el and len(el["geometry"]) == 1:
                pt = el["geometry"][0]
                geom = Point(pt["lon"], pt["lat"])

            if geom is None or geom.is_empty:
                continue

            voltage_v = parse_osm_voltage_v(tags.get("voltage"))
            sub_kind = tags.get("substation", "").lower()

            if power == "substation":
                if uid in seen_subs:
                    continue
                seen_subs.add(uid)

                # Convert single geom to UTM for accurate spatial test against plants
                geom_utm = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(epsg=32643).iloc[0]
                is_generation, gen_reason = check_is_generation(tags, geom_utm, plant_gdf_utm)

                # Qualifying test: voltage >= 33,000 V or substation=transmission
                is_qualifying = False
                if voltage_v is not None and voltage_v >= 33000.0:
                    is_qualifying = True
                elif sub_kind == "transmission":
                    is_qualifying = True

                record = {
                    "osm_id": f"{el_type}/{el_id}",
                    "name": tags.get("name"),
                    "name_tag": tags.get("name"),
                    "operator": tags.get("operator"),
                    "operator_tag": tags.get("operator"),
                    "voltage_tag": tags.get("voltage"),
                    "voltage_v": voltage_v,
                    "substation_kind": sub_kind,
                    "is_generation": is_generation,
                    "generation_reason": gen_reason,
                    "is_qualifying": is_qualifying,
                    "raw_tags": json.dumps(tags),
                    "geometry": geom,
                }
                substations_all.append(record)

                if is_generation:
                    substations_generation.append(record)
                elif is_qualifying:
                    # Primary qualifying MUST be non-generation
                    substations_qualifying.append(record)

            elif power == "line":
                if uid in seen_lines:
                    continue
                seen_lines.add(uid)

                if voltage_v is None or voltage_v >= 33000.0:
                    lines.append({
                        "osm_id": f"{el_type}/{el_id}",
                        "name": tags.get("name"),
                        "name_tag": tags.get("name"),
                        "operator": tags.get("operator"),
                        "operator_tag": tags.get("operator"),
                        "voltage_tag": tags.get("voltage"),
                        "voltage_v": voltage_v,
                        "raw_tags": json.dumps(tags),
                        "geometry": geom,
                    })

    # Convert to GeoDataFrames in EPSG:32643
    gdf_qualifying = gpd.GeoDataFrame(substations_qualifying, crs="EPSG:4326").to_crs(epsg=32643) if substations_qualifying else gpd.GeoDataFrame()
    gdf_generation = gpd.GeoDataFrame(substations_generation, crs="EPSG:4326").to_crs(epsg=32643) if substations_generation else gpd.GeoDataFrame()
    gdf_all_subs = gpd.GeoDataFrame(substations_all, crs="EPSG:4326").to_crs(epsg=32643) if substations_all else gpd.GeoDataFrame()
    gdf_lines = gpd.GeoDataFrame(lines, crs="EPSG:4326").to_crs(epsg=32643) if lines else gpd.GeoDataFrame()

    tree_qualifying = STRtree(gdf_qualifying.geometry) if not gdf_qualifying.empty else None
    tree_generation = STRtree(gdf_generation.geometry) if not gdf_generation.empty else None
    tree_all_subs = STRtree(gdf_all_subs.geometry) if not gdf_all_subs.empty else None
    tree_lines = STRtree(gdf_lines.geometry) if not gdf_lines.empty else None

    return {
        "gdf_qualifying": gdf_qualifying,
        "gdf_generation": gdf_generation,
        "gdf_all_subs": gdf_all_subs,
        "gdf_lines": gdf_lines,
        "tree_qualifying": tree_qualifying,
        "tree_generation": tree_generation,
        "tree_all_subs": tree_all_subs,
        "tree_lines": tree_lines,
    }


def query_grid_locally(lat: float, lon: float, index_bundle: Dict[str, Any], max_search_dist_km: float = 60.0) -> Dict[str, Any]:
    """Queries grid proximity using the local spatial index in microseconds."""
    pt_utm = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(epsg=32643).iloc[0]
    search_radius_m = max_search_dist_km * 1000.0
    search_geom = pt_utm.buffer(search_radius_m)

    gdf_q = index_bundle["gdf_qualifying"]
    tree_q = index_bundle["tree_qualifying"]
    gdf_gen = index_bundle.get("gdf_generation", gpd.GeoDataFrame())
    tree_gen = index_bundle.get("tree_generation")
    gdf_all = index_bundle["gdf_all_subs"]
    tree_all = index_bundle["tree_all_subs"]
    gdf_lines = index_bundle["gdf_lines"]
    tree_lines = index_bundle["tree_lines"]

    # 1. Qualifying substation (>=33 kV, non-generation)
    best_q_dist_km = None
    best_q_row = None
    if tree_q is not None and not gdf_q.empty:
        cands = list(tree_q.query(search_geom))
        if cands:
            cands_geom = gdf_q.geometry.iloc[cands]
            dists = cands_geom.distance(pt_utm)
            min_idx = dists.idxmin()
            best_q_dist_km = round(float(dists[min_idx]) / 1000.0, 3)
            best_q_row = gdf_q.loc[min_idx]

    # 2. Nearest generation substation (context only)
    best_gen_dist_km = None
    best_gen_row = None
    if tree_gen is not None and not gdf_gen.empty:
        cands_gen = list(tree_gen.query(search_geom))
        if cands_gen:
            cands_geom = gdf_gen.geometry.iloc[cands_gen]
            dists = cands_geom.distance(pt_utm)
            min_idx = dists.idxmin()
            best_gen_dist_km = round(float(dists[min_idx]) / 1000.0, 3)
            best_gen_row = gdf_gen.loc[min_idx]

    # 3. Nearest any non-generation substation (including untagged, for confidence check)
    best_any_dist_km = None
    best_any_row = None
    if tree_all is not None and not gdf_all.empty:
        gdf_non_gen = gdf_all[gdf_all.get("is_generation", False) != True]
        if not gdf_non_gen.empty:
            tree_non_gen = STRtree(gdf_non_gen.geometry)
            cands_all = list(tree_non_gen.query(search_geom))
            if cands_all:
                cands_geom = gdf_non_gen.geometry.iloc[cands_all]
                dists = cands_geom.distance(pt_utm)
                min_idx = dists.idxmin()
                best_any_dist_km = round(float(dists[min_idx]) / 1000.0, 3)
                best_any_row = gdf_non_gen.loc[min_idx]

    # 4. Transmission lines (>=33 kV)
    best_line_dist_km = None
    best_line_row = None
    if tree_lines is not None and not gdf_lines.empty:
        cands_l = list(tree_lines.query(search_geom))
        if cands_l:
            cands_geom = gdf_lines.geometry.iloc[cands_l]
            dists = cands_geom.distance(pt_utm)
            min_idx = dists.idxmin()
            best_line_dist_km = round(float(dists[min_idx]) / 1000.0, 3)
            best_line_row = gdf_lines.loc[min_idx]

    # Apply existing confidence rules:
    low_confidence = False
    reasons = []

    if best_q_dist_km is None:
        low_confidence = True
        reasons.append(f"No confirmed >=33kV non-generation substation found within {max_search_dist_km} km")
    else:
        # Check if untagged substation is closer
        if best_any_dist_km is not None and best_any_dist_km < best_q_dist_km:
            low_confidence = True
            reasons.append(
                f"Untagged substation at {best_any_dist_km:.2f} km is closer than nearest confirmed >=33kV ({best_q_dist_km:.2f} km)"
            )

    res = {
        "substation_dist_km": best_q_dist_km,
        "nearest_generation_substation_dist_km": best_gen_dist_km,
        "nearest generation substation (context only)": best_gen_dist_km,
        "generation_substation_name": best_gen_row.get("name_tag") if (best_gen_row is not None and str(best_gen_row.get("name_tag")) != "nan") else None,
        "generation_substation_osm_id": best_gen_row.get("osm_id") if best_gen_row is not None else None,
        "transmission_line_dist_km": best_line_dist_km,
        "nearest_any_substation_dist_km": best_any_dist_km,
        "substation_name": best_q_row.get("name_tag") if (best_q_row is not None and best_q_row.get("name_tag") is not None and str(best_q_row.get("name_tag")) != "nan") else None,
        "substation_operator": best_q_row.get("operator_tag") if (best_q_row is not None and best_q_row.get("operator_tag") is not None and str(best_q_row.get("operator_tag")) != "nan") else None,
        "substation_voltage_tag": best_q_row.get("voltage_tag") if (best_q_row is not None and best_q_row.get("voltage_tag") is not None and str(best_q_row.get("voltage_tag")) != "nan") else None,
        "substation_voltage_v": best_q_row.get("voltage_v") if (best_q_row is not None and not np.isnan(best_q_row.get("voltage_v", np.nan))) else None,
        "substation_osm_id": best_q_row.get("osm_id") if best_q_row is not None else None,
        "transmission_line_osm_id": best_line_row.get("osm_id") if best_line_row is not None else None,
        "low_confidence": low_confidence,
        "confidence_reason": "; ".join(reasons) if reasons else None,
    }

    if low_confidence and best_any_row is not None:
        centroid_wgs = gpd.GeoSeries([best_any_row.geometry.centroid], crs="EPSG:32643").to_crs(epsg=4326).iloc[0]
        raw_tags_val = best_any_row.get("raw_tags")
        if isinstance(raw_tags_val, str):
            try:
                raw_tags_dict = json.loads(raw_tags_val)
            except Exception:
                raw_tags_dict = {}
        elif isinstance(raw_tags_val, dict):
            raw_tags_dict = raw_tags_val
        else:
            raw_tags_dict = {}
        res["closer_untagged_osm_id"] = best_any_row.get("osm_id")
        res["closer_untagged_lat"] = round(float(centroid_wgs.y), 5)
        res["closer_untagged_lon"] = round(float(centroid_wgs.x), 5)
        res["closer_untagged_dist_km"] = best_any_dist_km
        res["closer_untagged_tags"] = raw_tags_dict

    return res


if __name__ == "__main__":
    t0 = time.time()
    tiles = get_tiles(margin_km=25.0, tile_size=0.5)
    print(f"Generated {len(tiles)} 0.5-degree tiles covering Karnataka + 25 km margin.")

    raw_responses, failed = download_all_tiles(tiles, max_workers=4)
    print(f"\nDownload summary: {len(raw_responses)} successful tiles, {len(failed)} failed tiles.")

    print("\nBuilding local spatial indexes...")
    index_bundle = build_spatial_indexes(raw_responses)
    n_qual = len(index_bundle["gdf_qualifying"])
    n_gen = len(index_bundle["gdf_generation"])
    n_all = len(index_bundle["gdf_all_subs"])
    n_lines = len(index_bundle["gdf_lines"])
    print(f"Loaded {n_all} total substations ({n_qual} primary qualifying >=33 kV, {n_gen} generation/captive/pooling) and {n_lines} transmission lines.")

    # Save to geojson cache for instant future loads
    out_subs_path = os.path.join(_HERE, "data", "substations_karnataka.geojson")
    out_lines_path = os.path.join(_HERE, "data", "lines_karnataka.geojson")
    index_bundle["gdf_all_subs"].to_crs(epsg=4326).to_file(out_subs_path, driver="GeoJSON")
    index_bundle["gdf_lines"].to_crs(epsg=4326).to_file(out_lines_path, driver="GeoJSON")
    print(f"Saved local grid layers to {out_subs_path} and {out_lines_path}")
    print(f"Total time elapsed: {time.time()-t0:.1f}s")
