import os
import geopandas as gpd
from shapely.geometry import Point
import pandas as pd

import glob

WDPA_DIRS = [
    "data/wdpa/part1",
    "data/wdpa/part2",
    "data/wdpa/part3",
]
base_dir = "/Users/prempatil/Documents/solar-pv-karnataka/geoprocessing"

print("--- WDPA Diagnostic ---")

gdfs = []
for d in WDPA_DIRS:
    for shp_path in glob.glob(os.path.join(base_dir, d, "*polygons.shp")):
        if os.path.exists(shp_path):
            gdfs.append(gpd.read_file(shp_path))

gdf = pd.concat(gdfs, ignore_index=True)
gdf = gpd.GeoDataFrame(gdf, geometry='geometry')

print(f"(a) Total polygon features loaded: {len(gdf)}")

if 'NAME' in gdf.columns:
    for target in ["Bandipur", "Nagarhole", "Bhadra"]:
        m = gdf[gdf['NAME'].astype(str).str.contains(target, case=False, na=False)]
        print(f"(b) Polygon with '{target}' exists: {not m.empty}")
        if not m.empty:
            print(f"    Matches for {target}:", m['NAME'].tolist()[:3])
else:
    print(f"(b) No 'NAME' column found. Columns are: {gdf.columns.tolist()}")

original_crs = gdf.crs
print(f"(c) CRS of the WDPA data before reprojection: {original_crs}")

pt = Point(76.6283, 11.6667)
pt_gdf = gpd.GeoDataFrame(geometry=[pt], crs="EPSG:4326")

if original_crs is None:
    gdf.set_crs(epsg=4326, inplace=True)

gdf_proj = gdf.to_crs(epsg=32643)
pt_proj = pt_gdf.to_crs(epsg=32643)
pt_geom = pt_proj.geometry.iloc[0]

distances = gdf_proj.geometry.distance(pt_geom)
min_dist_idx = distances.idxmin()
min_dist_m = distances[min_dist_idx]
nearest_name = gdf_proj.loc[min_dist_idx, 'NAME'] if 'NAME' in gdf_proj.columns else 'Unknown'

print(f"(d) Raw computed distance to nearest polygon: {min_dist_m:.2f} meters")
print(f"    Nearest polygon name: {nearest_name}")
