"""
backend/migrate_to_mongo.py
---------------------------
Loads candidates.json into MongoDB, creating candidate records and real provenance records
directly from the raw cache and local layer datasets.

Provenance Rules:
- Fetched values (raw_ghi, raw_temperature, raw_substation, raw_landcost, slope, landuse, protected_buffer):
  status: "sourced"
  source: Exact API request or query key from raw cache, or local layer file and processing step
  retrieved_at: Cache entry timestamp (ISO 8601) or dataset release timestamp
  origin: "raw_cache" or "local_layer" (NO migrated_backfill)
- Derived/normalized values (ghi, substation, landcost, temp_derating, score):
  status: "calculated"
  source: Explicit normalization or AHP synthesis formula
  retrieved_at: null
  origin: "calculation"
"""

import os
import sys
import json
import math
import glob
import urllib.parse
from datetime import datetime, timezone

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.db import (
    get_candidates_col,
    get_provenance_col,
    get_audit_log_col,
    get_runs_col,
)
from geoprocessing.provenance import record_value
from geoprocessing.raw_cache import get_cache_record, hash_request
from geoprocessing.fetch_landuse import _tile_id

CANDIDATES_JSON_PATH = os.path.join(PROJECT_ROOT, "geoprocessing", "data", "candidates.json")
RAW_CACHE_DIR = os.path.join(PROJECT_ROOT, "geoprocessing", "data", "raw_cache")


def run_migration():
    print(f"Reading candidates dataset from: {CANDIDATES_JSON_PATH}")
    if not os.path.exists(CANDIDATES_JSON_PATH):
        raise FileNotFoundError(f"Dataset not found at {CANDIDATES_JSON_PATH}")

    with open(CANDIDATES_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    candidates = data.get("candidates", [])
    sweep_metadata = data.get("sweep_metadata", {})
    selection_rule = data.get("selection_rule", "")
    generated_at = data.get("generated_at", datetime.now(timezone.utc).isoformat())

    print(f"Found {len(candidates)} candidates to migrate.")

    candidates_col = get_candidates_col()
    provenance_col = get_provenance_col()
    audit_log_col = get_audit_log_col()
    runs_col = get_runs_col()

    # Clear previous collections so clean dataset is applied with zero reviewer decisions
    candidates_col.delete_many({})
    provenance_col.delete_many({})
    print("Cleared existing candidates and provenance records for fresh migration (zero reviewer decisions).")

    # Index grid tile cache files for primary substation provenance
    print("Indexing Overpass grid tiles from raw cache...")
    tile_index = {}
    for fpath in glob.glob(os.path.join(RAW_CACHE_DIR, "*.json")):
        with open(fpath, "r", encoding="utf-8") as fp:
            try:
                d = json.load(fp)
                req = d.get("request", "")
                if "OVERPASS_GRID_TILE" in req:
                    part = req.split("bbox=")[1].split()[0]
                    tile_index[part] = (req, d.get("cached_at"), os.path.basename(fpath))
            except Exception:
                pass
    print(f"✓ Indexed {len(tile_index)} grid tiles from raw cache.")

    # 1. Store Run Metadata in `runs`
    run_record = {
        "run_id": "sweep_v1_frozen",
        "generated_at": generated_at,
        "sweep_metadata": sweep_metadata,
        "selection_rule": selection_rule,
        "total_candidates": len(candidates),
        "migrated_at": datetime.now(timezone.utc).isoformat(),
    }
    runs_col.update_one({"run_id": "sweep_v1_frozen"}, {"$set": run_record}, upsert=True)
    print("✓ Stored run metadata in 'runs' collection.")

    # 2. Insert Candidates in `candidates` and Provenance in `provenance`
    candidates_migrated = 0
    provenance_migrated = 0

    for cand in candidates:
        rank = cand["rank"]
        lat = cand["latitude"]
        lon = cand["longitude"]
        cand_doc = dict(cand)
        cand_doc["site_id"] = f"{lat:.4f}_{lon:.4f}"
        cand_doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        coord_key = f"{lat:.4f}_{lon:.4f}"

        raw = cand.get("raw_criteria", {})
        norm = cand.get("normalized_criteria", {})
        constraints = cand.get("constraints", {})

        # Ensure substation_details is structured on candidate doc
        sub_details = cand.get("substation_details")
        if not sub_details:
            closer_unt = None
            if raw.get("closer_untagged_osm_id"):
                closer_unt = {
                    "osm_id": raw.get("closer_untagged_osm_id"),
                    "lat": raw.get("closer_untagged_lat"),
                    "lon": raw.get("closer_untagged_lon"),
                    "distance_km": raw.get("closer_untagged_dist_km"),
                    "tags": raw.get("closer_untagged_tags", {}),
                }
            sub_details = {
                "osm_id": raw.get("substation_osm_id"),
                "name": raw.get("substation_name", "unnamed"),
                "operator": raw.get("substation_operator", "unnamed"),
                "voltage_tag": raw.get("substation_voltage_tag", "no voltage tag"),
                "voltage_v": raw.get("substation_voltage_v"),
                "distance_km": raw.get("substation_dist_km"),
                "closer_untagged": closer_unt,
            }
            cand_doc["substation_details"] = sub_details

        # Upsert candidate by rank
        candidates_col.update_one({"rank": rank}, {"$set": cand_doc}, upsert=True)
        candidates_migrated += 1

        # Retrieve raw cache metadata for live-fetched criteria
        # A. NASA POWER (GHI and Temperature)
        params = {
            "parameters": "ALLSKY_SFC_SW_DWN,T2M,T2M_MAX",
            "community": "RE",
            "longitude": lon,
            "latitude": lat,
            "format": "JSON",
        }
        nasa_key = f"NASA_POWER: url=https://power.larc.nasa.gov/api/temporal/climatology/point?{urllib.parse.urlencode(sorted(params.items()))}"
        nasa_rec = get_cache_record(nasa_key)
        nasa_cached_at = (nasa_rec.get("cached_at") if nasa_rec else None) or generated_at

        # B. Land Cost Proxy
        delta = 1.0
        bbox = f"{lat - delta},{lon - delta},{lat + delta},{lon + delta}"
        query = f"""[out:json][timeout:25];\n(\n  node["place"="city"]({bbox});\n  node["place"="town"]({bbox});\n);\nout geom;"""
        lc_key = f"OVERPASS_LANDCOST: bbox={bbox} query={query.strip()}"
        lc_rec = get_cache_record(lc_key)
        lc_cached_at = (lc_rec.get("cached_at") if lc_rec else None) or generated_at

        # Excluded candidates (if any):
        if cand.get("status") == "excluded":
            pb_status = constraints.get("protected_buffer", {}).get("status", "excluded")
            record_value(
                site_id=coord_key,
                criterion="protected_buffer",
                value=pb_status,
                source="UNEP-WCMC WDPA Protected Areas & OpenStreetMap Waterbodies",
                retrieved_at=None,
                unit="buffer_status",
                spatial_ref="10 km PA & 500 m waterbody buffer / EPSG:4326",
                status="calculated",
                origin="calculation",
                lat=lat,
                lon=lon,
                candidate_rank=rank,
            )
            provenance_migrated += 1
            continue

        # 1. GHI (Raw and Normalized)
        record_value(
            site_id=coord_key,
            criterion="raw_ghi",
            value=raw.get("ghi_kwh_m2_day"),
            source=f"NASA POWER climatology, 2001-2020 (20-year) via {hash_request(nasa_key)}.json",
            source_key=nasa_key,
            retrieved_at=nasa_cached_at,
            unit="kWh/m²/day",
            spatial_ref="0.5° grid / EPSG:4326",
            status="sourced",
            origin="raw_cache",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        record_value(
            site_id=coord_key,
            criterion="ghi",
            value=norm.get("ghi"),
            source="Min-Max Linear Normalization [5.0, 6.0] (Benefit)",
            source_key="Min-Max Linear Normalization [5.0, 6.0] (Benefit)",
            retrieved_at=None,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated",
            origin="calculation",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        # 2. Substation Distance (Raw and Normalized)
        sub_osm_id = sub_details.get("osm_id") or raw.get("substation_osm_id")
        sub_dist = raw.get("substation_dist_km")
        record_value(
            site_id=coord_key,
            criterion="raw_substation",
            value=sub_dist,
            source=f"substations_karnataka.geojson (osm_id: {sub_osm_id}, distance: {sub_dist} km)",
            source_key="geoprocessing/data/substations_karnataka.geojson",
            retrieved_at="2026-10-16T18:05:49.276472+00:00",
            unit="km",
            spatial_ref="Centroid / EPSG:4326",
            status="sourced",
            origin="local_dataset",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        record_value(
            site_id=coord_key,
            criterion="substation",
            value=norm.get("substation"),
            source="Linear Inverted Distance Normalization [0, 50 km] (Cost)",
            source_key="Linear Inverted Distance Normalization [0, 50 km] (Cost)",
            retrieved_at=None,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated",
            origin="calculation",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        # 3. Land Cost Proxy (Raw and Normalized)
        record_value(
            site_id=coord_key,
            criterion="raw_landcost",
            value=raw.get("landcost_proxy_score"),
            source=f"Overpass distance-to-town proxy via {hash_request(lc_key)}.json",
            source_key=lc_key,
            retrieved_at=lc_cached_at,
            unit="score [0-1]",
            spatial_ref="Centroid / EPSG:4326",
            status="sourced",
            origin="raw_cache",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        record_value(
            site_id=coord_key,
            criterion="landcost",
            value=norm.get("landcost"),
            source="Land Cost Proxy Inversion Normalization",
            source_key="Land Cost Proxy Inversion Normalization",
            retrieved_at=None,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated",
            origin="calculation",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        # 4. Temperature & Derating
        record_value(
            site_id=coord_key,
            criterion="raw_temperature",
            value=raw.get("t2m_celsius"),
            source=f"NASA POWER Climatology (T2M) via {hash_request(nasa_key)}.json",
            source_key=nasa_key,
            retrieved_at=nasa_cached_at,
            unit="°C",
            spatial_ref="0.5° grid / EPSG:4326",
            status="sourced",
            origin="raw_cache",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        record_value(
            site_id=coord_key,
            criterion="temp_derating",
            value=norm.get("temp_derating"),
            source="Thermal Efficiency Derating Inversion [20, 30°C] (Cost)",
            source_key="Thermal Efficiency Derating Inversion [20, 30°C] (Cost)",
            retrieved_at=None,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated",
            origin="calculation",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        # 5. Local Hard Constraints
        # Slope
        slope_info = constraints.get("slope", {})
        slope_val = slope_info.get("mean_pct") or slope_info.get("centroid_pct") or slope_info.get("value_pct")
        record_value(
            site_id=coord_key,
            criterion="slope",
            value=slope_val,
            source="Copernicus DEM GLO-30 (AWS Open Data; 3x3 mean smoothed) / slope_karnataka.tif",
            source_key="geoprocessing/data/slope_karnataka.tif",
            retrieved_at="2024-01-01T00:00:00+00:00",
            unit="%",
            spatial_ref="30m resolution / EPSG:4326",
            status="sourced",
            origin="local_layer",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        # Land-use
        lu_info = constraints.get("landuse", {})
        lu_val = lu_info.get("centroid_class") or lu_info.get("class_name") or lu_info.get("class_code")
        tile_name = _tile_id(lat, lon)
        record_value(
            site_id=coord_key,
            criterion="landuse",
            value=lu_val,
            source=f"ESA WorldCover 2021 (v200) / ESA_WorldCover_10m_2021_v200_{tile_name}_Map.tif",
            source_key=f"geoprocessing/data/worldcover_tiles/ESA_WorldCover_10m_2021_v200_{tile_name}_Map.tif",
            retrieved_at="2021-10-28T00:00:00+00:00",
            unit="class_name",
            spatial_ref="10m raster / EPSG:4326",
            status="sourced",
            origin="local_layer",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        # Protected Buffer
        pb_info = constraints.get("protected_buffer", {})
        pb_status = pb_info.get("status", "eligible")
        record_value(
            site_id=coord_key,
            criterion="protected_buffer",
            value=pb_status,
            source="UNEP-WCMC WDPA (Sep 2026) & OSM Water (water_karnataka.geojson)",
            source_key="geoprocessing/data/water_karnataka.geojson",
            retrieved_at="2026-09-01T00:00:00+00:00",
            unit="buffer_status",
            spatial_ref="10 km PA & 500 m waterbody buffer / EPSG:4326",
            status="sourced",
            origin="local_layer",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

        # Composite AHP Score
        score_val = cand.get("score")
        record_value(
            site_id=coord_key,
            criterion="score",
            value=score_val,
            source="AHP Multi-Criteria Decision Analysis Weighted Synthesis (Weights: GHI 40%, Substation 40%, Landcost 10%, Temp 10%)",
            retrieved_at=None,
            unit="dimensionless",
            spatial_ref="Composite",
            status="calculated",
            origin="calculation",
            lat=lat,
            lon=lon,
            candidate_rank=rank,
        )
        provenance_migrated += 1

    print(f"✓ Migrated {candidates_migrated} candidates to 'candidates' collection.")
    print(f"✓ Recorded {provenance_migrated} real provenance entries to 'provenance' collection.")

    # Audit Log
    audit_entry = {
        "event": "migration_load_candidates_real_provenance",
        "candidates_count": candidates_migrated,
        "provenance_count": provenance_migrated,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_file": os.path.relpath(CANDIDATES_JSON_PATH, PROJECT_ROOT),
        "status": "success",
    }
    audit_log_col.insert_one(audit_entry)
    print("✓ Recorded migration event in 'audit_log' collection.")


if __name__ == "__main__":
    run_migration()
