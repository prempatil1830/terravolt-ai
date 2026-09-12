import geopandas as gpd
from shapely.geometry import Point
import pandas as pd
import os

WDPA_DIRS = [
    "data/wdpa/part1",
    "data/wdpa/part2",
    "data/wdpa/part3",
]
SHP_FILENAME = "WDPA_WDOECM_Sep2026_Public_IND_shp-polygons.shp"
base_dir = "."
gdfs = []
for d in WDPA_DIRS:
    shp_path = os.path.join(base_dir, d, SHP_FILENAME)
    if os.path.exists(shp_path):
        gdfs.append(gpd.read_file(shp_path))
gdf = pd.concat(gdfs, ignore_index=True)
gdf = gpd.GeoDataFrame(gdf, geometry='geometry', crs="EPSG:4326")
pt = Point(76.6283, 11.6667)
pt_gdf = gpd.GeoDataFrame(geometry=[pt], crs="EPSG:4326")
print("Distance to nearest in degrees:", gdf.geometry.distance(pt).min())
gdf = gdf.to_crs(epsg=32643)
pt_gdf = pt_gdf.to_crs(epsg=32643)
pt_geom = pt_gdf.geometry.iloc[0]
print("Distance in meters:", gdf.geometry.distance(pt_geom).min())
