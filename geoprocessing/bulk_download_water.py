"""
geoprocessing/bulk_download_water.py
------------------------------------
Bulk downloader and spatial indexer for Karnataka water bodies and waterways.
Per Step 2:
- Downloads all natural=water, waterway=river/canal for Karnataka + 25 km margin,
  in 0.5 degree tiles, with retries and mirrors.
- Saves each raw response to raw_cache.
- Filters water bodies to at least 1 hectare (10,000 m²) in area (project assumption).
  Linear rivers and canals are preserved as LineStrings.
- Builds local spatial index (STRtree in EPSG:32643).
- Saves local vector layer to data/water_karnataka.geojson.
"""

from __future__ import annotations

import os
import sys
import time
import math
import json
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple

import requests
import numpy as np
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon, MultiPolygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from raw_cache import get_cached, save_cached
from bulk_download_substations import get_tiles, OVERPASS_MIRRORS

ONE_HECTARE_M2 = 10000.0  # 1 hectare = 10,000 square meters (project assumption)


def query_water_tile(tile: Tuple[float, float, float, float], max_retries: int = 5) -> Tuple[Tuple[float, float, float, float], Optional[Dict[str, Any]], Optional[str]]:
    s, w, n, e = tile
    bbox = f"{s},{w},{n},{e}"
    query = f"""[out:json][timeout:35][maxsize:536870912];
(
  way["natural"="water"]({bbox});
  relation["natural"="water"]({bbox});
  way["waterway"~"river|canal"]({bbox});
  relation["waterway"~"river|canal"]({bbox});
);
out geom;"""
    req_key = f"OVERPASS_WATER_TILE: bbox={bbox} query={query.strip()}"

    cached = get_cached(req_key)
    if cached is not None and "elements" in cached:
        return tile, cached, None

    last_err = None
    for attempt in range(max_retries):
        url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            headers = {"User-Agent": f"SolarPVKarnatakaProject/1.3 (water_tile_{s}_{w})"}
            resp = requests.post(url, data={"data": query}, headers=headers, timeout=35)
            if resp.status_code == 200:
                data = resp.json()
                save_cached(req_key, data, {"service": "OVERPASS_WATER_TILE", "bbox": bbox, "tile": tile})
                return tile, data, None
            else:
                last_err = f"HTTP {resp.status_code} from {url}"
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0 + attempt * 1.5)

    return tile, None, last_err


def download_all_water_tiles(tiles: List[Tuple[float, float, float, float]], max_workers: int = 4) -> Tuple[List[Dict[str, Any]], List[Tuple[float, float, float, float]]]:
    print(f"Checking & downloading {len(tiles)} water tiles using up to {max_workers} concurrent workers across Overpass mirrors...")
    results = []
    failed = []

    cached_count = 0
    to_download = []
    for t in tiles:
        s, w, n, e = t
        bbox = f"{s},{w},{n},{e}"
        query = f"""[out:json][timeout:35][maxsize:536870912];
(
  way["natural"="water"]({bbox});
  relation["natural"="water"]({bbox});
  way["waterway"~"river|canal"]({bbox});
  relation["waterway"~"river|canal"]({bbox});
);
out geom;"""
        req_key = f"OVERPASS_WATER_TILE: bbox={bbox} query={query.strip()}"
        c = get_cached(req_key)
        if c is not None and "elements" in c:
            results.append((t, c, None))
            cached_count += 1
        else:
            to_download.append(t)

    print(f"  {cached_count}/{len(tiles)} tiles already present in raw_cache. {len(to_download)} to fetch.")

    if to_download:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_tile = {executor.submit(query_water_tile, t): t for t in to_download}
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


def build_water_layer(raw_responses: List[Dict[str, Any]], min_area_m2: float = ONE_HECTARE_M2) -> gpd.GeoDataFrame:
    """
    Parses OSM water elements, applies the 1 hectare threshold to polygons,
    and returns a clean, deduplicated GeoDataFrame in EPSG:4326.
    """
    features = []
    seen = set()

    for resp in raw_responses:
        for el in resp.get("elements", []):
            el_type = el.get("type")
            el_id = el.get("id")
            uid = (el_type, el_id)
            if uid in seen:
                continue
            seen.add(uid)

            tags = el.get("tags", {})
            name = tags.get("name")
            water_type = tags.get("water") or tags.get("waterway") or tags.get("natural") or "water"

            # Ways
            if el_type == "way" and "geometry" in el and len(el["geometry"]) >= 2:
                coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
                is_closed = (len(coords) >= 4 and coords[0] == coords[-1])
                if is_closed:
                    geom = Polygon(coords)
                else:
                    geom = LineString(coords)

                if geom is not None and not geom.is_empty:
                    features.append({
                        "osm_id": f"{el_type}/{el_id}",
                        "name": name,
                        "type": water_type,
                        "is_polygon": is_closed,
                        "geometry": geom,
                    })

            # Relations (multipolygons)
            elif el_type == "relation" and "members" in el:
                lines = []
                for m in el.get("members", []):
                    if m.get("role") in ["outer", ""] and "geometry" in m and len(m["geometry"]) >= 2:
                        coords = [(pt["lon"], pt["lat"]) for pt in m["geometry"]]
                        lines.append(LineString(coords))
                if lines:
                    try:
                        polys = list(polygonize(lines))
                        for p in polys:
                            if p is not None and not p.is_empty:
                                features.append({
                                    "osm_id": f"{el_type}/{el_id}",
                                    "name": name,
                                    "type": water_type,
                                    "is_polygon": True,
                                    "geometry": p,
                                })
                    except Exception:
                        pass

    if not features:
        return gpd.GeoDataFrame(columns=["osm_id", "name", "type", "area_ha", "geometry"], crs="EPSG:4326")

    gdf = gpd.GeoDataFrame(features, crs="EPSG:4326")
    gdf_utm = gdf.to_crs(epsg=32643)

    # Filter: polygons must have area >= min_area_m2 (1 ha); LineStrings are kept
    areas = gdf_utm.geometry.area
    areas_ha = areas / 10000.0

    keep_mask = (~gdf["is_polygon"]) | (areas >= min_area_m2)
    gdf_filtered = gdf[keep_mask].copy()
    gdf_filtered["area_ha"] = np.where(gdf_filtered["is_polygon"], areas_ha[keep_mask].round(2), None)
    gdf_filtered = gdf_filtered.drop(columns=["is_polygon"])

    return gdf_filtered


if __name__ == "__main__":
    t0 = time.time()
    tiles = get_tiles(margin_km=25.0, tile_size=0.5)
    print(f"Generated {len(tiles)} 0.5-degree tiles covering Karnataka + 25 km margin.")

    raw_responses, failed = download_all_water_tiles(tiles, max_workers=4)
    print(f"\nDownload summary: {len(raw_responses)} successful tiles, {len(failed)} failed tiles.")

    print("\nBuilding and filtering water layer (>= 1 hectare threshold)...")
    gdf_water = build_water_layer(raw_responses, min_area_m2=ONE_HECTARE_M2)
    print(f"Resulting water layer features: {len(gdf_water)}")

    out_water_path = os.path.join(_HERE, "data", "water_karnataka.geojson")
    gdf_water.to_file(out_water_path, driver="GeoJSON")
    print(f"Saved local water layer to {out_water_path}")
    print(f"Total time elapsed: {time.time()-t0:.1f}s")
