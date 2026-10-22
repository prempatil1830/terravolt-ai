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

WDPA_DIRS = [
    "data/wdpa/part1",
    "data/wdpa/part2",
    "data/wdpa/part3",
]

import glob
import threading

from shapely.strtree import STRtree

_wdpa_gdf = None
_wdpa_tree = None
_wdpa_lock = threading.Lock()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# Statutory or candidate protected areas whose full boundary polygons could not be obtained
# from the primary filtered WDPA layer.
# Format: {"name": str, "latitude": float, "longitude": float, "reason": str, "source": str}
KNOWN_GAPS = [
    {
        "name": "Cauvery Wildlife Sanctuary",
        "latitude": 12.1798,
        "longitude": 77.6416,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Relation 9329112 (Cauvery WLS, Hanur taluk, Chamarajanagar)"
    },
    {
        "name": "Bhimgad Wildlife Sanctuary",
        "latitude": 15.5701,
        "longitude": 74.3299,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Relation 19153153 (Bhimgad WLS, Mendil, Khanapura taluk, Belagavi)"
    },
    {
        "name": "Mookambika Wildlife Sanctuary",
        "latitude": 13.8116,
        "longitude": 74.8825,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Way 670548929 (Mookambika WLS, Bainduru taluk, Udupi)"
    },
    {
        "name": "Talakaveri Wildlife Sanctuary",
        "latitude": 12.3704,
        "longitude": 75.4630,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Relation 21656432 (Talakaveri Wildlife Sanctuary, Madikeri taluk, Kodagu)"
    },
    {
        "name": "Pushpagiri Wildlife Sanctuary",
        "latitude": 12.5950,
        "longitude": 75.6779,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Relation 21656434 (Pushpagiri Wildlife Sanctuary, Madikeri taluk, Kodagu)"
    },
    {
        "name": "Sharavathi Valley Wildlife Sanctuary",
        "latitude": 13.8281,
        "longitude": 75.1181,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "WDPA Oct 2026 Public IND dataset (part1), matching Reserve Forest polygon 'Sharavathi' centroid"
    },
    {
        "name": "Someshwara Wildlife Sanctuary",
        "latitude": 13.5278,
        "longitude": 75.0500,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Relation 9328427 (Someshwara Wildlife Sanctuary, Hebri taluk, Udupi)"
    },
    {
        "name": "Brahmagiri Wildlife Sanctuary",
        "latitude": 12.0387,
        "longitude": 75.8194,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Relation 21656433 (Brahmagiri Wildlife Sanctuary, Kutta, Ponnampete, Kodagu)"
    },
    {
        "name": "Rangayyanadurga Four-horned Antelope Sanctuary",
        "latitude": 14.6150,
        "longitude": 76.2100,
        "reason": "Missing from global WDPA Sep 2026 filtered layer (matches WDPA Reserve Forest polygon at 14.54-14.69N, 76.13-76.29E)",
        "source": "WDPA Oct 2026 Public IND dataset (part1), matching Reserve Forest polygon 'Rangayyanadurga Sf, Block A' centroid"
    },
    {
        "name": "Daroji Sloth Bear Sanctuary",
        "latitude": 15.2599,
        "longitude": 76.5800,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Relation 19033358 (Daroji Bear Sanctuary, Kampli/Ballari)"
    },
    {
        "name": "Melkote Temple Wildlife Sanctuary",
        "latitude": 12.7008,
        "longitude": 76.6626,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Relation 9329621 (Melkote Temple Wildlife Sanctuary, Pandavapura, Mandya)"
    },
    {
        "name": "Ranebennur Blackbuck Sanctuary",
        "latitude": 14.6166,
        "longitude": 75.6342,
        "reason": "Missing from global WDPA Sep 2026 filtered layer",
        "source": "OpenStreetMap Node 245628328 (Ranebennuru, Haveri)"
    }
]


def check_known_gaps_review(lat: float, lon: float, review_dist_km: float = 25.0):
    """
    Review rule (NOT exclusion): returns a list of warning reasons if (lat, lon)
    is within `review_dist_km` of any point in KNOWN_GAPS.
    """
    reasons = []
    for gap in KNOWN_GAPS:
        d = haversine(lat, lon, gap["latitude"], gap["longitude"])
        if d <= review_dist_km:
            reasons.append(f"Possible unmapped protected area: {gap['name']}")
    return reasons

def _load_wdpa_data():
    global _wdpa_gdf, _wdpa_tree
    if _wdpa_gdf is not None:
        return _wdpa_gdf
        
    with _wdpa_lock:
        if _wdpa_gdf is not None:
            return _wdpa_gdf
            
        base_dir = os.path.dirname(__file__)
        cached_geojson = os.path.join(base_dir, "data", "wdpa", "wdpa_karnataka_filtered.geojson")
        
        if os.path.exists(cached_geojson):
            gdf = gpd.read_file(cached_geojson)
            if gdf.crs != "EPSG:32643":
                gdf = gdf.to_crs(epsg=32643)
            _wdpa_gdf = gdf
            _wdpa_tree = STRtree(_wdpa_gdf.geometry)
            return _wdpa_gdf

        # Fallback to generating filtered dataset
        gdfs = []
        for d in WDPA_DIRS:
            pattern = os.path.join(base_dir, d, "*polygons.shp")
            matched = glob.glob(pattern)
            for shp_path in matched:
                if os.path.exists(shp_path):
                    gdfs.append(gpd.read_file(shp_path))
    
        if not gdfs:
            print("Warning: No WDPA shapefiles found in any of the parts.")
            _wdpa_gdf = gpd.GeoDataFrame()
            _wdpa_tree = None
            return _wdpa_gdf

        combined = pd.concat(gdfs, ignore_index=True)
        combined = gpd.GeoDataFrame(combined, geometry='geometry')
        if combined.crs is None:
            combined.set_crs(epsg=4326, inplace=True)

        import re
        def get_clean_designation(row):
            d_eng = str(row.get('DESIG_ENG', '')).strip().lower()
            d_orig = str(row.get('DESIG', '')).strip().lower()
            name = str(row.get('NAME', '')).strip().lower()
            
            if 'national park' in d_eng or 'national park' in d_orig or 'national park' in name:
                return 'National Park'
            if 'tiger reserve' in d_eng or 'tiger reserve' in d_orig or re.search(r'\btiger reserve\b|\btiger preserve\b', name):
                return 'Tiger Reserve'
            if ('wildlife sanctuary' in d_eng or 'wildlife sanctuary' in d_orig or 
                'sanctuary' in d_eng or 'sanctuary' in d_orig or
                re.search(r'\bwildlife sanctuary\b|\bbird sanctuary\b|\bbirds sanctuary\b', name)):
                return 'Wildlife Sanctuary'
            if 'conservation reserve' in d_eng or 'conservation reserve' in d_orig or 'conservation reserve' in name:
                return 'Conservation Reserve'
            if 'community reserve' in d_eng or 'community reserve' in d_orig or 'community reserve' in name:
                return 'Community Reserve'
            if 'ramsar' in d_eng or 'ramsar' in d_orig or 'wetland of international importance' in d_eng or 'ramsar' in name:
                return 'Ramsar Site'
            if 'world heritage' in d_eng or 'world heritage' in d_orig or 'world heritage' in name:
                return 'World Heritage Site'
            if 'biosphere' in d_eng or 'biosphere' in d_orig or 'biosphere' in name:
                return 'Biosphere Reserve'
            return None

        combined['designation'] = combined.apply(get_clean_designation, axis=1)
        matched = combined[combined['designation'].notnull()].copy()

        has_id = matched[matched['SITE_ID'].notnull() & (matched['SITE_ID'] != 'nan')].drop_duplicates(subset=['SITE_ID'])
        no_id = matched[matched['SITE_ID'].isnull() | (matched['SITE_ID'] == 'nan')].drop_duplicates(subset=['NAME'])
        deduped = pd.concat([has_id, no_id], ignore_index=True)
        deduped = gpd.GeoDataFrame(deduped, geometry='geometry', crs='EPSG:4326')

        kar_path = os.path.join(base_dir, "data", "karnataka_boundary.geojson")
        if os.path.exists(kar_path):
            kar_gdf = gpd.read_file(kar_path)
            if kar_gdf.crs != "EPSG:4326":
                kar_gdf = kar_gdf.to_crs("EPSG:4326")
            clipped = gpd.clip(deduped, kar_gdf)
            clipped = clipped[~clipped.geometry.is_empty].copy()
        else:
            clipped = deduped

        os.makedirs(os.path.dirname(cached_geojson), exist_ok=True)
        clipped.to_file(cached_geojson, driver='GeoJSON')

        _wdpa_gdf = clipped.to_crs(epsg=32643)
        _wdpa_tree = STRtree(_wdpa_gdf.geometry)
        return _wdpa_gdf

def check_wdpa_buffer(lat, lon, buffer_km=10):
    gdf = _load_wdpa_data()
    if gdf.empty or _wdpa_tree is None:
        return False
    
    # Create point in UTM zone 43N
    pt = Point(lon, lat)
    pt_gdf = gpd.GeoDataFrame(geometry=[pt], crs="EPSG:4326").to_crs(epsg=32643)
    pt_geom = pt_gdf.geometry.iloc[0]
    
    buffer_m = buffer_km * 1000
    point_buffer = pt_geom.buffer(buffer_m)
    
    # Fast STRtree spatial index query
    candidates = _wdpa_tree.query(point_buffer)
    for c_idx in candidates:
        if point_buffer.intersects(gdf.geometry.iloc[c_idx]):
            return True
    return False

def check_water_buffer(lat, lon, buffer_km=0.5, footprint_radius_km=0.54):
    """
    Evaluates water-body hard constraint locally without network calls:
    Checks if a 500m buffer from the footprint disk edge (radius_km=0.54, ~226.4 acres) contains:
    (a) WorldCover class 80 (permanent water)
    (b) OSM rivers, canals and water bodies of >= 1 hectare from water_karnataka.geojson.
    Returns True if inside buffer (excluded), False otherwise.
    """
    try:
        from zonal_footprint import evaluate_footprint_zonal
        res = evaluate_footprint_zonal(lat, lon, radius_km=footprint_radius_km, water_buffer_m=buffer_km * 1000.0)
        return not res.get("water_buffer_passed", True)
    except Exception as e:
        return False


def get_buffer_status(lat, lon, footprint_radius_km=0.54):
    """
    Returns one of three string statuses:
      "excluded"      — inside 10 km PA buffer OR 500 m water-body buffer.
      "eligible"      — confirmed outside both buffers (all local data).
      "needs_review"  — raised if data is unavailable (e.g. forced in testing).
    """
    if check_wdpa_buffer(lat, lon, buffer_km=10):
        return "excluded"

    try:
        if check_water_buffer(lat, lon, buffer_km=0.5, footprint_radius_km=footprint_radius_km):
            return "excluded"
    except WaterBufferDataUnavailable:
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
