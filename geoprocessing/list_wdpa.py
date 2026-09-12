import os
import geopandas as gpd
import pandas as pd

WDPA_DIRS = [
    "data/wdpa/part1",
    "data/wdpa/part2",
    "data/wdpa/part3",
]
SHP_FILENAME = "WDPA_WDOECM_Sep2026_Public_IND_shp-polygons.shp"
base_dir = "/Users/prempatil/Documents/solar-pv-karnataka/geoprocessing"

gdfs = []
for d in WDPA_DIRS:
    shp_path = os.path.join(base_dir, d, SHP_FILENAME)
    if os.path.exists(shp_path):
        gdfs.append(gpd.read_file(shp_path))

gdf = pd.concat(gdfs, ignore_index=True)

# Find the name and designation columns
name_col = next((c for c in gdf.columns if c.upper() in ['NAME', 'ORIG_NAME']), 'UNKNOWN_NAME')
desig_col = next((c for c in gdf.columns if c.upper() in ['DESIG_ENG', 'DESIG', 'TYPE']), 'UNKNOWN_DESIG')

print(f"Using columns: Name='{name_col}', Designation='{desig_col}'")
print("-" * 50)

for i, row in gdf.iterrows():
    name = row[name_col] if name_col in gdf.columns else 'N/A'
    desig = row[desig_col] if desig_col in gdf.columns else 'N/A'
    print(f"{i+1}. {name} ({desig})")
