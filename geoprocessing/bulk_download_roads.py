"""
geoprocessing/bulk_download_roads.py
------------------------------------
Bulk downloader and spatial indexer for Karnataka major road infrastructure.
Downloads nearest OSM highways of type motorway, trunk, primary, or secondary
for Karnataka plus a 25 km margin in 0.5 degree tiles with retries and mirrors,
caches every raw response in raw_cache, and builds a local spatial index.
If a tile fails after retries, records it and fails loudly (never uses fallback values).
"""

from __future__ import annotations

import os
import sys
import time
import json
import concurrent.futures
from typing import Any, Dict, List, Optional, Tuple

import requests
import numpy as np
import geopandas as gpd
from shapely.geometry import LineString, box
from shapely.strtree import STRtree

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from raw_cache import get_cached, save_cached
from bulk_download_substations import get_tiles, OVERPASS_MIRRORS

DATA_DIR = os.path.join(_HERE, "data")
ROADS_GEOJSON = os.path.join(DATA_DIR, "roads_karnataka.geojson")

OVERPASS_ROAD_MIRRORS = [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def format_road_query(bbox: str) -> str:
    return f"""[out:json][timeout:35][maxsize:536870912];
(
  way["highway"~"^(motorway|trunk|primary|secondary)"]({bbox});
);
out geom;"""


def query_road_tile(
    tile: Tuple[float, float, float, float],
    max_retries: int = 5,
) -> Tuple[Tuple[float, float, float, float], Optional[Dict[str, Any]], Optional[str]]:
    s, w, n, e = tile
    bbox = f"{s},{w},{n},{e}"
    query = format_road_query(bbox)
    req_key = f"OVERPASS_ROAD_TILE: bbox={bbox} query={query.strip()}"

    cached = get_cached(req_key)
    if cached is not None and "elements" in cached:
        return tile, cached, None

    last_err = None
    for attempt in range(max_retries):
        url = OVERPASS_ROAD_MIRRORS[attempt % len(OVERPASS_ROAD_MIRRORS)]
        try:
            headers = {"User-Agent": f"SolarPVKarnatakaProject/1.3 (road_tile_{s}_{w})"}
            resp = requests.post(url, data={"data": query}, headers=headers, timeout=35)
            if resp.status_code == 200:
                data = resp.json()
                save_cached(req_key, data, {"service": "OVERPASS_ROAD_TILE", "bbox": bbox, "tile": tile})
                return tile, data, None
            else:
                last_err = f"HTTP {resp.status_code} from {url}"
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0 + attempt * 1.5)

    return tile, None, last_err


def download_all_road_tiles(
    tiles: List[Tuple[float, float, float, float]],
    max_workers: int = 3,
) -> Tuple[List[Dict[str, Any]], List[Tuple[float, float, float, float]]]:
    """
    Downloads road tiles across Karnataka + 25km margin with caching and retries.
    Fails loudly if any tile fails after retries.
    """
    print(f"Checking & downloading {len(tiles)} road tiles using up to {max_workers} concurrent workers...")
    results: List[Tuple[Tuple[float, float, float, float], Dict[str, Any], Optional[str]]] = []
    failed: List[Tuple[float, float, float, float]] = []

    cached_count = 0
    to_download = []
    for t in tiles:
        s, w, n, e = t
        bbox = f"{s},{w},{n},{e}"
        query = format_road_query(bbox)
        req_key = f"OVERPASS_ROAD_TILE: bbox={bbox} query={query.strip()}"
        c = get_cached(req_key)
        if c is not None and "elements" in c:
            results.append((t, c, None))
            cached_count += 1
        else:
            to_download.append(t)

    print(f"  {cached_count}/{len(tiles)} tiles already present in raw_cache. {len(to_download)} to fetch.")

    if to_download:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_tile = {executor.submit(query_road_tile, t): t for t in to_download}
            done_count = 0
            for future in concurrent.futures.as_completed(future_to_tile):
                t, data, err = future.result()
                done_count += 1
                if data is not None:
                    results.append((t, data, None))
                    n_el = len(data.get("elements", []))
                    if done_count % 10 == 0 or done_count == len(to_download):
                        print(f"  [{done_count}/{len(to_download)}] Tile {t} -> {n_el} road ways")
                else:
                    failed.append(t)
                    print(f"  FAILED Tile {t}: {err}")

    raw_responses = [d for _, d, _ in results if d is not None]

    if failed:
        raise RuntimeError(
            f"Road download failed for {len(failed)}/{len(tiles)} tiles after retries: {failed}. "
            f"Never using fallback values per specification."
        )

    return raw_responses, failed


def build_road_layer(raw_responses: List[Dict[str, Any]]) -> gpd.GeoDataFrame:
    """
    Parses OSM road elements (motorway, trunk, primary, secondary) into a deduplicated GeoDataFrame.
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
            highway_type = tags.get("highway")
            if highway_type not in ("motorway", "trunk", "primary", "secondary"):
                continue

            name = tags.get("name")
            ref = tags.get("ref")

            if el_type == "way" and "geometry" in el and len(el["geometry"]) >= 2:
                coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
                geom = LineString(coords)
                if geom is not None and not geom.is_empty:
                    features.append({
                        "osm_id": f"{el_type}/{el_id}",
                        "name": name,
                        "ref": ref,
                        "highway": highway_type,
                        "geometry": geom,
                    })

    gdf = gpd.GeoDataFrame(features, crs="EPSG:4326")
    os.makedirs(DATA_DIR, exist_ok=True)
    gdf.to_file(ROADS_GEOJSON, driver="GeoJSON")
    print(f"Saved {len(gdf)} unique highway ways to {ROADS_GEOJSON}")
    return gdf


def load_roads_and_index() -> Tuple[gpd.GeoDataFrame, STRtree, int, List[Tuple[float, float, float, float]]]:
    """
    Downloads/loads road tiles, caches responses, builds local GeoDataFrame and STRtree in EPSG:32643.
    Returns (gdf_roads_utm, tree_roads, tile_count, failed_tiles).
    """
    tiles = get_tiles(margin_km=25.0, tile_size=0.5)
    tile_count = len(tiles)

    raw_responses, failed_tiles = download_all_road_tiles(tiles, max_workers=3)
    gdf_wgs = build_road_layer(raw_responses)
    gdf_utm = gdf_wgs.to_crs(epsg=32643)
    tree_roads = STRtree(gdf_utm.geometry) if not gdf_utm.empty else None

    return gdf_utm, tree_roads, tile_count, failed_tiles


if __name__ == "__main__":
    tiles = get_tiles(margin_km=25.0, tile_size=0.5)
    print(f"Karnataka + 25km margin tile count: {len(tiles)}")
    raw_resps, failed = download_all_road_tiles(tiles, max_workers=3)
    print(f"Tile count: {len(tiles)}, Failed tiles: {len(failed)}")
    gdf = build_road_layer(raw_resps)
    print(f"Built road layer with {len(gdf)} road features.")
