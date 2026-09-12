import os
import math
import time
import requests
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point


class WaterBufferDataUnavailable(Exception):
    """Raised when all Overpass API endpoints fail and water-body
    proximity cannot be determined.  Callers must NOT treat this as
    'no water found'; instead they should flag the result as needing
    manual review."""

# TEMPORARY MANUAL FALLBACK — replace with correct WDPA National-designation data before production use. Not exhaustive; covers only Karnataka's largest protected areas.
FALLBACK_PROTECTED_AREAS = [
    {"name": "Bandipur NP", "lat": 11.6667, "lon": 76.6283, "radius_km": 17.0},
    {"name": "Nagarhole NP", "lat": 12.0300, "lon": 76.1500, "radius_km": 15.0},
    {"name": "Bhadra WLS", "lat": 13.6900, "lon": 75.6400, "radius_km": 13.0},
    {"name": "BRT WLS", "lat": 11.9900, "lon": 77.1400, "radius_km": 13.0},
    {"name": "Dandeli-Anshi (Kali) NP", "lat": 15.0200, "lon": 74.3600, "radius_km": 16.0},
    {"name": "Bannerghatta NP", "lat": 12.7800, "lon": 77.5800, "radius_km": 9.0},
    {"name": "Kudremukh NP", "lat": 13.2200, "lon": 75.2500, "radius_km": 14.0},
]

WDPA_DIRS = [
    "data/wdpa/part1",
    "data/wdpa/part2",
    "data/wdpa/part3",
]
SHP_FILENAME = "WDPA_WDOECM_Sep2026_Public_IND_shp-polygons.shp"

import threading

_wdpa_gdf = None
_wdpa_lock = threading.Lock()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def check_fallback_protected_areas(lat, lon, buffer_km=10, check_buffer=True):
    for pa in FALLBACK_PROTECTED_AREAS:
        dist = haversine(lat, lon, pa["lat"], pa["lon"])
        threshold = (pa["radius_km"] + buffer_km) if check_buffer else pa["radius_km"]
        if dist <= threshold:
            return True, pa["name"]
    return False, None

def _load_wdpa_data():
    global _wdpa_gdf
    if _wdpa_gdf is not None:
        return _wdpa_gdf
        
    with _wdpa_lock:
        if _wdpa_gdf is not None:
            return _wdpa_gdf
            
        gdfs = []
        base_dir = os.path.dirname(__file__)
        for d in WDPA_DIRS:
            shp_path = os.path.join(base_dir, d, SHP_FILENAME)
        if os.path.exists(shp_path):
            print(f"Loading WDPA data from {shp_path}...")
            gdfs.append(gpd.read_file(shp_path))
    
    if not gdfs:
        print("Warning: No WDPA shapefiles found in any of the parts.")
        _wdpa_gdf = gpd.GeoDataFrame()
        return _wdpa_gdf

    print("Concatenating WDPA data...")
    _wdpa_gdf = pd.concat(gdfs, ignore_index=True)
    # Ensure it's a GeoDataFrame after concat
    _wdpa_gdf = gpd.GeoDataFrame(_wdpa_gdf, geometry='geometry')
    
    # The shapefiles are likely in EPSG:4326. We need a projected CRS to do metric buffers accurately.
    # EPSG:32643 is UTM zone 43N, good for Karnataka/India.
    print("Projecting WDPA data to EPSG:32643...")
    if _wdpa_gdf.crs is None:
        _wdpa_gdf.set_crs(epsg=4326, inplace=True)
    _wdpa_gdf = _wdpa_gdf.to_crs(epsg=32643)
    return _wdpa_gdf

def check_wdpa_buffer(lat, lon, buffer_km=10):
    gdf = _load_wdpa_data()
    
    # Used ONLY if loaded WDPA dataset has fewer than 100 features (< 100 indicates broken/filtered dataset)
    # or if a lookup point falls within the fallback radius.
    is_broken_dataset = len(gdf) < 100
    
    if is_broken_dataset:
        hit, _ = check_fallback_protected_areas(lat, lon, buffer_km=buffer_km, check_buffer=True)
        if hit:
            return True
    else:
        hit, _ = check_fallback_protected_areas(lat, lon, buffer_km=0, check_buffer=False)
        if hit:
            return True

    if gdf.empty:
        return False
    
    # Create point
    pt = Point(lon, lat)
    pt_gdf = gpd.GeoDataFrame(geometry=[pt], crs="EPSG:4326").to_crs(epsg=32643)
    pt_geom = pt_gdf.geometry.iloc[0]
    
    buffer_m = buffer_km * 1000
    
    # Create a buffer around the point and check for intersection
    point_buffer = pt_geom.buffer(buffer_m)
    
    intersects = gdf.geometry.intersects(point_buffer)
    return bool(intersects.any())

def check_water_buffer(lat, lon, buffer_km=0.5):
    # Queries Overpass API for natural=water, waterway=river/canal within a tight bbox
    # 0.5km is roughly 0.005 degrees
    delta = 0.005
    bbox = f"{lat - delta},{lon - delta},{lat + delta},{lon + delta}"
    query = f"""
    [out:json][timeout:25];
    (
      way["natural"="water"]({bbox});
      relation["natural"="water"]({bbox});
      way["waterway"~"river|canal"]({bbox});
      relation["waterway"~"river|canal"]({bbox});
    );
    out geom;
    """
    
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
    ]
    
    last_error = None
    for attempt in range(3):
        url = endpoints[attempt % len(endpoints)]
        try:
            headers = {'User-Agent': 'SolarPVKarnataka/1.0'}
            response = requests.post(url, data={'data': query}, headers=headers, timeout=25)
            response.raise_for_status()
            data = response.json()

            if data.get('elements', []):
                return True  # Excluded due to water
            return False
        except Exception as e:
            last_error = e
            print(f"Overpass API attempt {attempt + 1}/3 failed ({url}): {e}")
            if attempt < 2:
                time.sleep(2)

    raise WaterBufferDataUnavailable(
        f"All 3 Overpass API attempts exhausted — water-body proximity unknown. "
        f"Last error: {last_error}"
    )

def get_buffer_status(lat, lon):
    """
    Returns one of three string statuses:

      "excluded"      — inside 10 km PA buffer OR 500 m water-body buffer.
      "eligible"      — confirmed outside both buffers (all data available).
      "needs_review"  — WDPA check passed but water-body proximity could not
                        be determined (all Overpass API endpoints failed).
                        The candidate must NOT be treated as eligible on the
                        basis of an unchecked assumption; human or offline
                        verification is required.
    """
    if check_wdpa_buffer(lat, lon, buffer_km=10):
        return "excluded"

    try:
        if check_water_buffer(lat, lon, buffer_km=0.5):
            return "excluded"
    except WaterBufferDataUnavailable as exc:
        # Water-body data is genuinely unavailable — do NOT assume "no water".
        # Return a distinct status so callers know this is unresolved.
        return "needs_review"

    return "eligible"

if __name__ == "__main__":
    import sys

    # Default: run 10 consecutive checks on Central Bengaluru to confirm
    # there is no flakiness (result must always be the same real answer,
    # or always "needs_review" when the API is unreachable — never a
    # silent False that masks an API failure as "no water found").
    lat, lon = 12.9716, 77.5946  # Central Bengaluru
    if len(sys.argv) == 3:
        lat, lon = float(sys.argv[1]), float(sys.argv[2])

    print(f"Running 10 consecutive get_buffer_status checks for ({lat}, {lon}) …\n")
    results = []
    for i in range(1, 11):
        status = get_buffer_status(lat, lon)
        results.append(status)
        print(f"  Run {i:>2}/10  →  {status}")

    unique = set(results)
    print(f"\nDistinct results seen: {unique}")
    if len(unique) == 1:
        print("✓ Consistent — no flakiness detected.")
    else:
        print("✗ INCONSISTENT — flakiness present; investigate further.")
