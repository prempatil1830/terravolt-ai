"""
geoprocessing/fetch_district.py
-------------------------------
Provides official Karnataka district lookup from OSM admin_level=5 boundary dataset.
"""

import os
from typing import Optional
import geopandas as gpd
from shapely.geometry import Point

_HERE = os.path.dirname(os.path.abspath(__file__))
_DISTRICTS_PATH = os.path.join(_HERE, "data", "karnataka_districts.geojson")

_districts_gdf = None


def get_district_name(lat: float, lon: float) -> str:
    """
    Returns the official district name for a given (lat, lon) in Karnataka.
    Falls back to 'Unknown' if outside the district boundaries.
    """
    global _districts_gdf
    if _districts_gdf is None:
        if os.path.exists(_DISTRICTS_PATH):
            _districts_gdf = gpd.read_file(_DISTRICTS_PATH)
        else:
            return "Unknown"

    pt = Point(lon, lat)
    matches = _districts_gdf[_districts_gdf.contains(pt)]
    if not matches.empty:
        return str(matches.iloc[0]["district"])
    
    # If point is right on boundary or slightly offset, find nearest within 5 km
    try:
        pt_geom = gpd.GeoSeries([pt], crs="EPSG:4326")
        dists = _districts_gdf.distance(pt)
        min_idx = dists.idxmin()
        if dists[min_idx] < 0.05:  # ~5 km
            return str(_districts_gdf.loc[min_idx, "district"])
    except Exception:
        pass

    return "Unknown"
