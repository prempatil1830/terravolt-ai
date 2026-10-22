import os
import json
import requests
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon, LineString
from shapely.ops import polygonize, unary_union

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(_HERE, "data", "karnataka_districts.geojson")

def download_districts():
    if os.path.exists(OUT_PATH):
        print(f"Districts already exist at {OUT_PATH}")
        gdf = gpd.read_file(OUT_PATH)
        return gdf

    query = """[out:json][timeout:60];
area["ISO3166-2"="IN-KA"]->.a;
(
  relation["admin_level"="5"](area.a);
);
out body;
>;
out skel qt;
"""
    endpoints = [
        "https://overpass.private.coffee/api/interpreter",
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    data = None
    for ep in endpoints:
        try:
            print(f"Fetching districts from {ep}...")
            r = requests.post(ep, data={"data": query}, headers={"User-Agent": "SolarPVKarnataka/1.3"}, timeout=45)
            if r.status_code == 200:
                data = r.json()
                break
        except Exception as e:
            print(f"Error from {ep}: {e}")

    if not data:
        raise RuntimeError("Could not download district boundaries.")

    # Parse OSM nodes, ways, relations into polygons
    nodes = {n["id"]: (n["lon"], n["lat"]) for n in data.get("elements", []) if n["type"] == "node"}
    ways = {}
    for w in data.get("elements", []):
        if w["type"] == "way":
            coords = [nodes[nid] for nid in w.get("nodes", []) if nid in nodes]
            if len(coords) >= 2:
                ways[w["id"]] = LineString(coords)

    districts = []
    for rel in data.get("elements", []):
        if rel["type"] == "relation":
            tags = rel.get("tags", {})
            name = tags.get("name") or tags.get("name:en")
            if not name:
                continue
            
            # Clean name (e.g. "Mysuru District" -> "Mysuru")
            clean_name = name.replace(" District", "").strip()

            outer_lines = []
            for m in rel.get("members", []):
                if m.get("role") in ["outer", ""] and m.get("type") == "way":
                    wid = m.get("ref")
                    if wid in ways:
                        outer_lines.append(ways[wid])

            if outer_lines:
                try:
                    polys = list(polygonize(outer_lines))
                    if polys:
                        geom = unary_union(polys)
                        districts.append({
                            "osm_id": f"relation/{rel['id']}",
                            "district": clean_name,
                            "geometry": geom
                        })
                except Exception as e:
                    print(f"Failed polygonize for {name}: {e}")

    gdf = gpd.GeoDataFrame(districts, crs="EPSG:4326")
    gdf.to_file(OUT_PATH, driver="GeoJSON")
    print(f"Successfully saved {len(gdf)} districts to {OUT_PATH}")
    return gdf

if __name__ == "__main__":
    download_districts()
