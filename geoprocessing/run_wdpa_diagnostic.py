"""
geoprocessing/run_wdpa_diagnostic.py
-----------------------------------
Comprehensive diagnostic script for WDPA filtering:
1. Search raw 193,460 polygons for the 16 target names and track pipeline survival.
2. Print distinct DESIG_ENG, DESIG, IUCN_CAT, and STATUS in Karnataka's bounding box.
3. Apply updated filter rule and generate new Karnataka dataset.
4. Verify required key parks, feature count, designation breakdown, and buffer checks.
"""

import os
import sys
import glob
import re
import json
import geopandas as gpd
import pandas as pd
from shapely.geometry import box, Point

_HERE = os.path.dirname(os.path.abspath(__file__))
WDPA_PARTS = [
    os.path.join(_HERE, "data/wdpa/part1/*polygons.shp"),
    os.path.join(_HERE, "data/wdpa/part2/*polygons.shp"),
    os.path.join(_HERE, "data/wdpa/part3/*polygons.shp"),
]
KARNATAKA_BOUNDARY = os.path.join(_HERE, "data/karnataka_boundary.geojson")
FILTERED_OUTPUT = os.path.join(_HERE, "data/wdpa/wdpa_karnataka_filtered.geojson")

TARGET_NAMES = [
    "Kudremukh", "Bannerghatta", "Anshi", "Dandeli", "Biligiri", "BRT", "Cauvery",
    "Mookambika", "Sharavathi", "Someshwara", "Bhimgad", "Talakaveri", "Brahmagiri",
    "Pushpagiri", "Rangayyanadurga", "Shettihalli"
]

BBOX_KARNATAKA = box(74.0, 11.5, 78.5, 18.5)


def run_diagnostic():
    print("=" * 80)
    print("STEP 1: RAW 193,460-POLYGON WDPA SEARCH & PIPELINE SURVIVAL")
    print("=" * 80)

    kar_gdf = gpd.read_file(KARNATAKA_BOUNDARY)
    kar_geom = kar_gdf.geometry.union_all()

    # Load all raw polygons
    gdfs = []
    for pattern in WDPA_PARTS:
        for f in sorted(glob.glob(pattern)):
            print(f"Loading {f} ...")
            gdfs.append(gpd.read_file(f))

    raw_gdf = pd.concat(gdfs, ignore_index=True)
    raw_gdf = gpd.GeoDataFrame(raw_gdf, geometry="geometry", crs="EPSG:4326")
    total_raw = len(raw_gdf)
    print(f"\nTotal raw polygons loaded: {total_raw}")

    # Search for target names
    matches = []
    for t in TARGET_NAMES:
        # Match case-insensitively on NAME and NAME_ENG
        m_name = raw_gdf["NAME"].astype(str).str.contains(t, case=False, na=False)
        m_eng = raw_gdf["NAME_ENG"].astype(str).str.contains(t, case=False, na=False) if "NAME_ENG" in raw_gdf.columns else False
        matched = raw_gdf[m_name | m_eng].copy()
        matched["search_target"] = t
        matches.append(matched)

    df_matches = pd.concat(matches, ignore_index=True).drop_duplicates(subset=["SITE_ID", "NAME", "DESIG", "search_target"])
    print(f"\nFound {len(df_matches)} total matching records for the 16 targets across raw dataset:\n")

    # Evaluate pipeline survival for each match
    target_desig_terms = [
        "national park", "wildlife sanctuary", "sanctuary", "tiger",
        "conservation reserve", "community reserve", "ramsar",
        "world heritage", "biosphere"
    ]
    valid_iucn = ["ia", "ib", "ii", "iii", "iv"]

    results_table = []
    for idx, r in df_matches.iterrows():
        name = str(r["NAME"])
        desig = str(r["DESIG"])
        desig_eng = str(r["DESIG_ENG"])
        desig_type = str(r.get("DESIG_TYPE", ""))
        iucn = str(r.get("IUCN_CAT", ""))
        status = str(r.get("STATUS", ""))
        site_id = str(r.get("SITE_ID", ""))
        b = r.geometry.bounds
        bounds_str = f"({b[0]:.2f}, {b[1]:.2f}, {b[2]:.2f}, {b[3]:.2f})"

        # Check Step A: Designation filter
        desig_text = f"{desig_eng} {desig}".lower()
        has_desig = any(term in desig_text for term in target_desig_terms)
        has_iucn = (iucn.lower() in valid_iucn and status.lower() == "designated")
        
        # Exclude forests and OECMs unless designated above
        is_forest_or_oecm = any(f in desig_text for f in ["reserve forest", "reserved forest", "protected forest", "state forest", "village forest"])
        site_type = str(r.get("SITE_TYPE", "")).lower()
        is_oecm = (site_type == "oecm" and "ramsar" not in desig_text and "biosphere" not in desig_text)

        survives_filter = (has_desig or has_iucn) and not (is_forest_or_oecm and not has_desig) and not is_oecm

        # Also check if name-based statutory preservation applies
        has_name_desig = any(term in name.lower() for term in target_desig_terms)

        # Check Step B: Dedupe by WDPAID (SITE_ID)
        survives_dedupe = bool(site_id and site_id != "nan" and site_id != "None") or True

        # Check Step C: Clip / Intersects Karnataka
        survives_intersect = r.geometry.intersects(kar_geom)

        results_table.append({
            "target": r["search_target"],
            "name": name,
            "site_id": site_id,
            "desig": desig,
            "desig_eng": desig_eng,
            "desig_type": desig_type,
            "iucn": iucn,
            "status": status,
            "bounds": bounds_str,
            "survives_filter": survives_filter,
            "has_name_desig": has_name_desig,
            "survives_intersect": survives_intersect,
        })

    for t in TARGET_NAMES:
        sub = [m for m in results_table if m["target"] == t]
        print(f"--- Target: {t} ({len(sub)} matches) ---")
        if not sub:
            print(f"  NO MATCHES found in the entire 193,460 polygons for '{t}'")
        for m in sub:
            print(f"  NAME: {m['name']:<42} | DESIG: {m['desig']:<18} | DESIG_ENG: {m['desig_eng']:<18} | IUCN: {m['iucn']:<12} | STATUS: {m['status']:<10} | Bounds: {m['bounds']:<26} | Filter: {str(m['survives_filter']):<5} | Intersects: {str(m['survives_intersect']):<5}")
        print()

    print("=" * 80)
    print("STEP 2: DISTINCT DESIG_ENG, DESIG, IUCN_CAT IN KARNATAKA BBOX")
    print("=" * 80)

    # Polygons intersecting bbox
    in_bbox = raw_gdf[raw_gdf.geometry.intersects(BBOX_KARNATAKA)].copy()
    print(f"Polygons intersecting Karnataka bounding box (74.0-78.5E, 11.5-18.5N): {len(in_bbox)}")

    print("\nDistinct DESIG_ENG:")
    for v in sorted(in_bbox["DESIG_ENG"].dropna().unique()):
        cnt = (in_bbox["DESIG_ENG"] == v).sum()
        print(f"  {v:<50} : {cnt:>6} polygons")

    print("\nDistinct DESIG:")
    for v in sorted(in_bbox["DESIG"].dropna().unique()):
        cnt = (in_bbox["DESIG"] == v).sum()
        print(f"  {v:<50} : {cnt:>6} polygons")

    print("\nDistinct IUCN_CAT:")
    for v in sorted(in_bbox["IUCN_CAT"].dropna().unique()):
        cnt = (in_bbox["IUCN_CAT"] == v).sum()
        print(f"  {v:<50} : {cnt:>6} polygons")

    print("\nDistinct STATUS:")
    for v in sorted(in_bbox["STATUS"].dropna().unique()):
        cnt = (in_bbox["STATUS"] == v).sum()
        print(f"  {v:<50} : {cnt:>6} polygons")

    print("\n" + "=" * 80)
    print("STEP 3: FIX FILTER RULE AND GENERATE KARNATAKA WDPA DATASET")
    print("=" * 80)

    # Implementation of rule 3:
    # 1. keep a polygon if its designation text (or name) contains:
    #    National Park, Wildlife Sanctuary or Sanctuary, Tiger, Conservation Reserve, Community Reserve, Ramsar, World Heritage, Biosphere
    #    OR its IUCN_CAT is Ia, Ib, II, III or IV with STATUS Designated.
    # 2. Exclude only state/reserved/protected forests and OECMs (unless designated by statutory terms above).
    # 3. Dedupe by WDPAID only, never by NAME; keep multi-part polygons as separate geometries.
    # 4. Use an intersects test against Karnataka boundary and keep the full geometry (do not clip it).

    def is_selected(row):
        d_eng = str(row.get("DESIG_ENG", "")).lower()
        d_orig = str(row.get("DESIG", "")).lower()
        name = str(row.get("NAME", "")).lower()
        iucn = str(row.get("IUCN_CAT", "")).lower()
        status = str(row.get("STATUS", "")).lower()
        desig_text = f"{d_eng} {d_orig}"

        # Match statutory conservation designation
        has_desig = any(t in desig_text for t in target_desig_terms)
        has_name_desig = any(t in name for t in target_desig_terms)
        has_iucn = (iucn in valid_iucn and status == "designated")

        # Exclusions: state/reserved/protected forests and non-statutory OECMs
        is_pure_forest = (
            any(f in desig_text for f in ["reserve forest", "reserved forest", "protected forest", "state forest", "village forest"])
            and not has_desig
            and not has_name_desig
        )
        site_type = str(row.get("SITE_TYPE", "")).lower()
        is_pure_oecm = (site_type == "oecm" and not ("ramsar" in desig_text or "biosphere" in desig_text))

        if is_pure_forest or is_pure_oecm:
            return False

        return has_desig or has_name_desig or has_iucn

    filtered_candidates = in_bbox[in_bbox.apply(is_selected, axis=1)].copy()
    print(f"Candidates passing filter rule in Karnataka bbox: {len(filtered_candidates)}")

    # Intersects test against Karnataka boundary (keep full geometry, do NOT clip)
    intersecting_kar = filtered_candidates[filtered_candidates.geometry.intersects(kar_geom)].copy()
    print(f"Candidates intersecting Karnataka boundary polygon: {len(intersecting_kar)}")

    # Dedupe by WDPAID (SITE_ID) only, never by NAME
    # Note: Keep records where SITE_ID is nan/missing as individual valid entries
    has_id = intersecting_kar[intersecting_kar["SITE_ID"].notnull() & (intersecting_kar["SITE_ID"] != "nan")].drop_duplicates(subset=["SITE_ID"])
    no_id = intersecting_kar[intersecting_kar["SITE_ID"].isnull() | (intersecting_kar["SITE_ID"] == "nan")]
    deduped = pd.concat([has_id, no_id], ignore_index=True)
    deduped = gpd.GeoDataFrame(deduped, geometry="geometry", crs="EPSG:4326")
    print(f"Features after deduplication by WDPAID: {len(deduped)}")

    # Explode multi-part polygons into separate geometries
    exploded = deduped.explode(index_parts=False).reset_index(drop=True)
    print(f"Features after exploding multi-parts into separate geometries: {len(exploded)}")

    # Standardize clean designation property
    def assign_category(row):
        d_eng = str(row.get("DESIG_ENG", "")).lower()
        name = str(row.get("NAME", "")).lower()
        if "national park" in d_eng or "national park" in name:
            return "National Park"
        if "tiger" in d_eng or "tiger" in name:
            return "Tiger Reserve"
        if "conservation reserve" in d_eng or "conservation reserve" in name:
            return "Conservation Reserve"
        if "community reserve" in d_eng or "community reserve" in name:
            return "Community Reserve"
        if "ramsar" in d_eng or "wetland of international importance" in d_eng or "ramsar" in name:
            return "Ramsar Site"
        if "world heritage" in d_eng or "world heritage" in name:
            return "World Heritage Site"
        if "biosphere" in d_eng or "biosphere" in name:
            return "Biosphere Reserve"
        if "sanctuary" in d_eng or "sanctuary" in name:
            return "Wildlife Sanctuary"
        return "Protected Area"

    exploded["designation"] = exploded.apply(assign_category, axis=1)

    # Save to GeoJSON
    os.makedirs(os.path.dirname(FILTERED_OUTPUT), exist_ok=True)
    exploded.to_file(FILTERED_OUTPUT, driver="GeoJSON")
    print(f"\nSaved updated dataset to {FILTERED_OUTPUT}")

    print("\n" + "=" * 80)
    print("STEP 4: NEW KARNATAKA FEATURE COUNT & DESIGNATION BREAKDOWN")
    print("=" * 80)
    print(f"Total Features: {len(exploded)}")
    print("\nDesignation Breakdown:")
    breakdown = exploded["designation"].value_counts()
    for des, cnt in breakdown.items():
        print(f"  {des:<25} : {cnt:>3}")

    print("\nList of all features in final Karnataka set:")
    for idx, r in exploded.iterrows():
        b = r.geometry.bounds
        print(f"  [{r['designation']:<20}] {str(r['NAME']):<45} | SITE_ID: {str(r.get('SITE_ID', '-')):<12} | bounds: ({b[0]:.2f}, {b[1]:.2f}, {b[2]:.2f}, {b[3]:.2f})")

    # Key parks check: Bandipur, Nagarhole, Anshi, Bannerghatta, Kudremukh, Bhadra, BRT
    print("\n" + "=" * 80)
    print("KEY PARKS AUDIT (Bandipur, Nagarhole, Anshi, Bannerghatta, Kudremukh, Bhadra, BRT)")
    print("=" * 80)
    required_7 = ["Bandipur", "Nagarhole", "Anshi", "Bannerghatta", "Kudremukh", "Bhadra", "BRT"]
    final_names = " ".join(exploded["NAME"].astype(str).tolist())
    for p in required_7:
        found = p.lower() in final_names.lower()
        print(f"  {p:<15}: {'FOUND' if found else 'NOT FOUND'}")

    print("\n" + "=" * 80)
    print("BUFFER VALIDATION RE-RUN")
    print("=" * 80)
    from fetch_protected_buffer import check_wdpa_buffer

    # Force re-load in fetch_protected_buffer
    import fetch_protected_buffer
    fetch_protected_buffer._wdpa_gdf = None
    fetch_protected_buffer._wdpa_tree = None

    bandipur = check_wdpa_buffer(11.6667, 76.6283, buffer_km=10)
    print(f"  Bandipur Core (11.6667N, 76.6283E) -> {'EXCLUDED (Correct)' if bandipur else 'ELIGIBLE (Error)'}")

    bidar1 = check_wdpa_buffer(17.91, 77.51, buffer_km=10)
    print(f"  Rural Bidar   (17.9100N, 77.5100E) -> {'EXCLUDED (Error)' if bidar1 else 'ELIGIBLE (Correct)'}")

    bidar2 = check_wdpa_buffer(17.90, 77.50, buffer_km=10)
    print(f"  Rural Bidar   (17.9000N, 77.5000E) -> {'EXCLUDED (Error)' if bidar2 else 'ELIGIBLE (Correct)'}")


if __name__ == "__main__":
    run_diagnostic()
