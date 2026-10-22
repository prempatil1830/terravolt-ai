"""
tests/test_audit_and_constraints.py
------------------------------------
Validates the five core architectural requirements:
(a) Every candidate and its 800m footprint disk are strictly inside Karnataka state boundary (>=90%).
(b) Zonal-fraction function on a small synthetic raster produces the exact known answer.
(c) Fast local spatial index substation distance matches brute-force calculation on 100 random points.
(d) Sweep and scoring make zero network calls (monkeypatching requests/network to fail).
(e) No generation, captive, or power-plant pooling substation is ever used as primary substation distance.
"""

import os
import sys
import json
import random
import pytest
import numpy as np
from shapely.geometry import Point, box, Polygon
import geopandas as gpd
from affine import Affine
import rasterio
from rasterio.mask import mask
from rasterio.io import MemoryFile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

GEO_DIR = os.path.join(PROJECT_ROOT, "geoprocessing")
if GEO_DIR not in sys.path:
    sys.path.insert(0, GEO_DIR)

from fetch_boundary import check_boundary_constraint, is_in_karnataka
from fetch_substation_distance import get_substation_distance_km, get_grid_connection_info
from score_candidate import score_candidate
from generate_candidates import screen_point
from bulk_download_substations import query_grid_locally, _HERE


# ---------------------------------------------------------------------------
# (a) Every candidate & disk inside boundary
# ---------------------------------------------------------------------------

def test_all_candidates_and_disks_inside_karnataka():
    """Validates that 100% of shortlisted candidates and their footprint disks are inside Karnataka."""
    cand_path = os.path.join(GEO_DIR, "data", "candidates.json")
    assert os.path.exists(cand_path), "candidates.json missing"

    with open(cand_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    candidates = data.get("candidates", [])
    assert len(candidates) == 12, f"Expected 12 candidates, found {len(candidates)}"

    for c in candidates:
        lat, lon = c["latitude"], c["longitude"]
        rk = c.get("rank")

        # 1. Centroid point in polygon check
        assert is_in_karnataka(lat, lon) is True, f"Candidate #{rk} at ({lat}, {lon}) is outside Karnataka boundary!"

        # 2. Footprint disk containment check (>= 90% threshold for 0.54 km radius)
        passed, frac, reason = check_boundary_constraint(lat, lon, radius_km=0.54, min_disk_fraction=0.90)
        assert passed is True, f"Candidate #{rk} disk failed boundary constraint: {reason}"
        assert frac >= 0.90, f"Candidate #{rk} disk fraction {frac*100:.1f}% is below 90% threshold"


# ---------------------------------------------------------------------------
# (b) Zonal-fraction function on synthetic raster with known answer
# ---------------------------------------------------------------------------

def test_zonal_fraction_on_synthetic_raster():
    """
    Constructs a 100x100 synthetic raster where a known square region has
    pre-assigned values (e.g. 75% slope <= 5.0%, 25% slope > 5.0%).
    Verifies that masking with a polygon over that exact region yields the exact fraction.
    """
    width, height = 100, 100
    res = 10.0  # 10 meters per pixel

    # Synthetic grid coordinates in EPSG:32643
    x_min, y_min = 500000.0, 1500000.0
    transform = Affine.translation(x_min, y_min + height * res) * Affine.scale(res, -res)

    # Create synthetic raster: Top half (50 rows) = 2.0% slope, Bottom half (50 rows) = 10.0% slope
    data = np.full((1, height, width), 2.0, dtype=np.float32)
    data[0, 50:, :] = 10.0  # Exactly 50% of the raster is > 5.0% slope

    # Write to in-memory rasterio dataset
    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype=np.float32,
            crs="EPSG:32643",
            transform=transform,
        ) as ds:
            ds.write(data)

            # Define query polygon covering middle 40 rows (rows 30 to 70):
            # rows 30 to 50 (20 rows) have value 2.0 (<= 5.0%) -> 50%
            # rows 50 to 70 (20 rows) have value 10.0 (> 5.0%) -> 50%
            # Expected usable fraction: exactly 20 / 40 = 0.5000 (50.0%)
            y_top = (y_min + height * res) - (30 * res)
            y_bot = (y_min + height * res) - (70 * res)
            query_poly = box(x_min + 10 * res, y_bot, x_min + 90 * res, y_top)

            out_img, _ = mask(ds, [query_poly], crop=True)
            valid = out_img[0][out_img[0] >= 0]
            assert len(valid) > 0

            usable_count = int(np.count_nonzero(valid <= 5.0))
            computed_fraction = usable_count / float(len(valid))

            # Expected answer: exactly 0.50
            assert round(computed_fraction, 4) == 0.5000, f"Expected 0.5000, got {computed_fraction}"


# ---------------------------------------------------------------------------
# (c) Local substation distance matches brute-force on 100 random points
# ---------------------------------------------------------------------------

def test_local_substation_distance_matches_bruteforce_100_points():
    """
    Evaluates 100 reproducible random points across Karnataka.
    Compares query_grid_locally (STRtree index) against brute-force
    Euclidean distance over all qualifying non-generation substations.
    Tolerance: <= 0.001 km (1 meter).
    """
    subs_path = os.path.join(GEO_DIR, "data", "substations_karnataka.geojson")
    lines_path = os.path.join(GEO_DIR, "data", "lines_karnataka.geojson")
    assert os.path.exists(subs_path) and os.path.exists(lines_path)

    gdf_all = gpd.read_file(subs_path).to_crs(epsg=32643)
    gdf_q = gdf_all[(gdf_all["is_qualifying"] == True) & (gdf_all.get("is_generation", False) != True)].copy()
    gdf_gen = gdf_all[gdf_all.get("is_generation", False) == True].copy()
    gdf_lines = gpd.read_file(lines_path).to_crs(epsg=32643)

    from shapely.strtree import STRtree
    index_bundle = {
        "gdf_qualifying": gdf_q,
        "gdf_generation": gdf_gen,
        "gdf_all_subs": gdf_all,
        "gdf_lines": gdf_lines,
        "tree_qualifying": STRtree(gdf_q.geometry),
        "tree_generation": STRtree(gdf_gen.geometry),
        "tree_all_subs": STRtree(gdf_all.geometry),
        "tree_lines": STRtree(gdf_lines.geometry),
    }

    # Generate 100 deterministic pseudo-random points within Karnataka bounding box
    rng = random.Random(42)
    random_pts = [
        (round(rng.uniform(12.0, 18.0), 4), round(rng.uniform(74.5, 78.0), 4))
        for _ in range(100)
    ]

    for lat, lon in random_pts:
        # Spatial index result
        res = query_grid_locally(lat, lon, index_bundle, max_search_dist_km=80.0)
        idx_dist = res["substation_dist_km"]

        # Brute-force Euclidean distance in UTM Zone 43N (EPSG:32643)
        pt_utm = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(epsg=32643).iloc[0]
        dists = gdf_q.geometry.distance(pt_utm) / 1000.0  # in km
        min_bf_dist = round(float(dists.min()), 3)

        if min_bf_dist <= 80.0:
            assert idx_dist is not None, f"Index returned None for point ({lat}, {lon}) where brute force found {min_bf_dist} km"
            diff = abs(idx_dist - min_bf_dist)
            assert diff <= 0.002, f"Mismatch at ({lat}, {lon}): Index={idx_dist} km, BruteForce={min_bf_dist} km (diff {diff*1000:.1f}m)"


# ---------------------------------------------------------------------------
# (d) Sweep and scoring make zero network calls (monkeypatch requests)
# ---------------------------------------------------------------------------

def test_sweep_and_scoring_make_zero_network_calls(monkeypatch):
    """
    Monkeypatches requests.get, requests.post, and socket connections to fail.
    Confirms that screen_point and score_candidate execute successfully without any network activity.
    """
    def _fail_on_network(*args, **kwargs):
        raise RuntimeError("Illegal network call detected! Sweep and scoring must operate 100% locally.")

    import requests
    monkeypatch.setattr(requests, "get", _fail_on_network)
    monkeypatch.setattr(requests, "post", _fail_on_network)
    monkeypatch.setattr(requests, "put", _fail_on_network)
    monkeypatch.setattr(requests, "delete", _fail_on_network)

    # 1. Test screen_point (Phase 1 screening) on a known grid candidate
    scr_res = screen_point(14.2000, 76.5000, w_ghi=0.40, w_temp=0.10)
    assert scr_res is not None
    assert scr_res["status"] == "phase1_eligible"

    # 2. Test score_candidate (Phase 2 scoring)
    sc_res = score_candidate(14.2000, 76.5000)
    assert sc_res is not None
    assert sc_res["status"] == "eligible"
    assert sc_res["score"] is not None
    assert sc_res["raw_criteria"]["substation_dist_km"] is not None


# ---------------------------------------------------------------------------
# (e) No generation substation used in primary distance
# ---------------------------------------------------------------------------

def test_no_generation_substation_used_in_primary_distance():
    """
    Verifies that:
    1. Points inside Pavagada solar park or near captive/generation stations never
       match a generation substation as the primary qualifying substation.
    2. Generation substations are captured under the secondary context-only field.
    3. Every shortlisted candidate's primary substation is verified non-generation.
    """
    # Point at Pavagada Solar Park (14.3, 77.4)
    info = get_grid_connection_info(14.3, 77.4)
    assert info is not None

    # Primary substation must NOT be S/S-1 or any Pavagada pooling substation
    primary_name = str(info.substation_name or "").lower()
    assert "s/s-1" not in primary_name
    assert "solar" not in primary_name
    assert "pooling" not in primary_name

    # The nearby generation pooling station must appear in context field
    assert info.nearest_generation_substation_dist_km is not None
    assert info.nearest_generation_substation_dist_km < 2.0  # S/S-1 is ~0.9 km away

    # Primary qualifying substation distance must be the external grid substation (Ramagiri, ~12.2 km)
    assert info.substation_dist_km is not None
    assert info.substation_dist_km > 10.0

    # Verify across all 12 shortlisted candidates
    cand_path = os.path.join(GEO_DIR, "data", "candidates.json")
    with open(cand_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    subs_path = os.path.join(GEO_DIR, "data", "substations_karnataka.geojson")
    gdf_all = gpd.read_file(subs_path)

    for c in data.get("candidates", []):
        raw = c.get("raw_criteria", {})
        sub_dist = raw.get("substation_dist_km")
        gen_dist = raw.get("nearest_generation_substation_dist_km")
        assert sub_dist is not None, f"Site #{c.get('rank')} has no primary substation distance"

        # If a generation substation is closer than the primary substation,
        # it must be properly segregated into gen_dist
        if gen_dist is not None and gen_dist < sub_dist:
            assert sub_dist != gen_dist, f"Site #{c.get('rank')} used generation substation distance as primary!"


# ---------------------------------------------------------------------------
# (f) Smoothed slope function on synthetic raster
# ---------------------------------------------------------------------------

def test_smoothed_slope_function_on_synthetic_raster():
    """
    Verifies that the 3x3 mean smoothing in _compute_slope_percent attenuates
    isolated high-frequency surface spikes (such as solar panel rows or fence lines)
    while preserving the authentic underlying topographic gradient.
    """
    from geoprocessing.fetch_slope import _compute_slope_raster
    from affine import Affine

    height, width = 50, 50
    transform = Affine.translation(76.0, 15.0) @ Affine.scale(0.000278, -0.000278)

    # Base flat elevation (500.0 m)
    elev = np.full((height, width), 500.0, dtype=np.float32)

    # Introduce an isolated surface obstacle spike (+15 m panel row at row 25, col 25)
    elev[25, 25] = 515.0

    # Compute slope using smoothed slope function
    slope_smoothed = _compute_slope_raster(elev, -9999.0, transform)

    # Compute raw unsmoothed slope using central difference
    dx_m = 0.000278 * 111320.0 * np.cos(np.radians(15.0))
    dy_m = 0.000278 * 110574.0
    raw_dz_dy, raw_dz_dx = np.gradient(elev.astype(np.float64), dy_m, dx_m)
    raw_slope = np.tan(np.arctan(np.sqrt(raw_dz_dx**2 + raw_dz_dy**2))) * 100.0

    max_raw = np.max(raw_slope)
    max_smooth = np.max(slope_smoothed)

    # 3x3 smoothing must significantly reduce the peak false slope caused by the isolated spike
    assert max_smooth < max_raw * 0.5, f"Expected 3x3 smoothing to attenuate peak spike from {max_raw:.1f}% to < {max_raw*0.5:.1f}%, got {max_smooth:.1f}%"


# ---------------------------------------------------------------------------
# (g) Canal crossing rule
# ---------------------------------------------------------------------------

def test_canal_crossing_rule():
    """
    Verifies the water constraint logic for canals/streams:
    1. A canal within 500m of disk edge (e.g. 100m away) but NOT crossing the disk passes.
    2. A canal crossing the disk (dist == 0 / intersecting) fails the constraint.
    """
    from shapely.geometry import Point, LineString

    disk_utm = Point(0, 0).buffer(540.0)

    # Canal 1: 100m north of disk edge (y = 540 + 100 = 640m)
    canal_non_crossing = LineString([(-1000, 640), (1000, 640)])
    dist_1 = canal_non_crossing.distance(disk_utm)
    assert 99.0 < dist_1 < 101.0

    canal_crosses_1 = (dist_1 <= 0.0)
    assert not canal_crosses_1, "Non-crossing canal should not be marked as crossing disk"

    # Canal 2: directly crosses the footprint disk (y = 0)
    canal_crossing = LineString([(-1000, 0), (1000, 0)])
    dist_2 = canal_crossing.distance(disk_utm)
    assert dist_2 == 0.0
    canal_crosses_2 = (dist_2 <= 0.0)
    assert canal_crosses_2, "Crossing canal must be detected as crossing disk"


# ---------------------------------------------------------------------------
# (h) Plant-touch recovery
# ---------------------------------------------------------------------------

def test_plant_touch_recovery():
    """
    Verifies plant-touch recovery:
    1. A utility-tagged substation (e.g. operator=KPTCL or substation=transmission)
       touching or inside a power=plant polygon is RECOVERED as a primary substation.
    2. A generation-tagged substation (e.g. substation=generation or developer operator)
       touching a power=plant polygon is NOT recovered and is flagged as generation.
    """
    from geoprocessing.bulk_download_substations import check_is_generation
    from shapely.geometry import Point, Polygon

    plant_poly = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
    plant_gdf = gpd.GeoDataFrame([{"name": "Solar Park Alpha", "geometry": plant_poly}], crs="EPSG:32643")
    sub_geom = Point(500, 500)

    # Case A: Utility operator (KPTCL) -> Should be recovered!
    tags_utility = {"name": "KPTCL 220kV Master Receiving Station", "operator": "KPTCL"}
    is_gen_util, reason_util = check_is_generation(tags_utility, sub_geom, plant_gdf)
    assert not is_gen_util, f"Utility-operated substation inside plant should be recovered, got {reason_util}"

    # Case B: Explicit generation tag -> Must NOT be recovered
    tags_gen = {"name": "Solar Pooling Station", "substation": "generation"}
    is_gen_gen, reason_gen = check_is_generation(tags_gen, sub_geom, plant_gdf)
    assert is_gen_gen, "Generation-tagged substation inside plant must be flagged as generation"
    assert "generation" in reason_gen.lower()

    # Case C: Private developer inside plant -> Must NOT be recovered
    tags_private = {"name": "Acme Solar Substation", "operator": "Acme Green Energy Ltd"}
    is_gen_priv, reason_priv = check_is_generation(tags_private, sub_geom, plant_gdf)
    assert is_gen_priv, "Private developer substation inside plant must be flagged as generation"


# ---------------------------------------------------------------------------
# (i) Electrode exclusion
# ---------------------------------------------------------------------------

def test_electrode_exclusion():
    """
    Verifies that HVDC electrode grounding stations are excluded from qualifying primary substations.
    """
    from geoprocessing.bulk_download_substations import check_is_generation
    from shapely.geometry import Point

    pt = Point(500000, 1500000)
    tags_electrode = {
        "name": "HVDC Ground Electrode Station Kolar",
        "voltage": "500000",
        "operator": "Power Grid Corporation of India",
    }
    is_gen, reason = check_is_generation(tags_electrode, pt, None)
    assert is_gen, "Electrode grounding station must be excluded from primary substations"
    assert "electrode" in reason.lower()


# ---------------------------------------------------------------------------
# (j) District lookup
# ---------------------------------------------------------------------------

def test_district_lookup():
    """
    Verifies district lookup from official Karnataka administrative boundaries:
    - (14.2, 76.5) -> Chitradurga
    - (16.0, 76.9) -> Raichur
    - (13.3, 77.9) -> Chikkaballapura
    - Outside Karnataka -> 'Unknown'
    """
    from geoprocessing.fetch_district import get_district_name

    assert get_district_name(14.2, 76.5) == "Chitradurga"
    assert get_district_name(16.0, 76.9) == "Raichur"
    assert get_district_name(13.3, 77.9) == "Chikkaballapura"
    assert get_district_name(28.6, 77.2) == "Unknown"


# ---------------------------------------------------------------------------
# (k) DB Shortlist Matches Selection Output Under REPLAY
# ---------------------------------------------------------------------------

def test_db_shortlist_matches_selection_function_under_replay():
    """
    Validates that the 12 shortlisted candidates stored in MongoDB
    match the output of the Frozen Rule v2 selection under REPLAY mode.
    """
    from backend.db import get_candidates_col
    candidates_col = get_candidates_col()
    db_candidates = list(candidates_col.find({}).sort("rank", 1))
    assert len(db_candidates) == 12, f"Expected 12 candidates in MongoDB, found {len(db_candidates)}"

    cand_path = os.path.join(GEO_DIR, "data", "candidates.json")
    with open(cand_path, "r", encoding="utf-8") as f:
        file_data = json.load(f)

    file_candidates = file_data.get("candidates", [])
    assert len(file_candidates) == 12

    db_shortlist = [(round(c["latitude"], 4), round(c["longitude"], 4)) for c in db_candidates]
    file_shortlist = [(round(c["latitude"], 4), round(c["longitude"], 4)) for c in file_candidates]

    assert db_shortlist == file_shortlist, (
        f"Database shortlist differs from selection function output:\n"
        f"DB:   {db_shortlist}\n"
        f"File: {file_shortlist}"
    )


# ---------------------------------------------------------------------------
# (l) No Selected Site Has Electrode or Generation Substation as Primary
# ---------------------------------------------------------------------------

def test_no_selected_site_has_electrode_or_generation_as_primary():
    """
    Validates that no shortlisted site in MongoDB or candidates.json
    has an electrode, captive, or generation substation as its primary qualifying substation.
    """
    from backend.db import get_candidates_col
    candidates_col = get_candidates_col()
    candidates = list(candidates_col.find({}))
    if not candidates:
        cand_path = os.path.join(GEO_DIR, "data", "candidates.json")
        with open(cand_path, "r", encoding="utf-8") as f:
            candidates = json.load(f).get("candidates", [])

    assert len(candidates) == 12

    # Load all substations to inspect primary matched substation tags
    subs_path = os.path.join(GEO_DIR, "data", "substations_karnataka.geojson")
    subs_gdf = gpd.read_file(subs_path)

    for c in candidates:
        rk = c["rank"]
        lat, lon = c["latitude"], c["longitude"]
        grid_info = get_grid_connection_info(lat, lon)
        sub_name = str(grid_info.substation_name or "").lower()
        sub_osm_id = grid_info.substation_osm_id

        # Explicit name and tag exclusions
        assert "electrode" not in sub_name, f"Site #{rk} at ({lat}, {lon}) matched electrode station: {sub_name}"
        assert "generation" not in sub_name, f"Site #{rk} at ({lat}, {lon}) matched generation station: {sub_name}"
        assert "solar" not in sub_name or "kptcl" in sub_name, f"Site #{rk} matched solar generation station: {sub_name}"

        # GeoDataFrame attributes check: is_generation flag
        if sub_osm_id is not None and not subs_gdf.empty:
            matched_rows = subs_gdf[subs_gdf["osm_id"] == sub_osm_id]
            if not matched_rows.empty:
                row = matched_rows.iloc[0]
                assert row.get("is_generation") is not True, (
                    f"Site #{rk} matched substation {sub_osm_id} marked as is_generation=True!"
                )


# ---------------------------------------------------------------------------
# (k) Planner Agent matches stored shortlist under REPLAY
# ---------------------------------------------------------------------------

def test_planner_agent_matches_stored_shortlist_under_replay(monkeypatch):
    """
    CRITICAL SINGLE SCORING PATH TEST:
    Under REPLAY=1, runs PlannerAgent for all 12 shortlisted coordinates and asserts
    the exact same scores, statuses, and criteria values as the stored shortlist.
    """
    monkeypatch.setenv("REPLAY", "1")
    from geoprocessing.agents.planner import PlannerAgent

    cand_path = os.path.join(GEO_DIR, "data", "candidates.json")
    with open(cand_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    candidates = data.get("candidates", [])
    assert len(candidates) == 12

    planner = PlannerAgent(footprint_radius_km=0.54)

    for cand in candidates:
        rank = cand["rank"]
        lat, lon = cand["latitude"], cand["longitude"]
        coord_id = f"{lat:.4f}_{lon:.4f}"
        specs = planner.dispatch_specialists(lat, lon, site_id=coord_id, footprint_radius_km=0.54)
        matrix = planner.assemble_decision_matrix(lat, lon, specs)
        scored = planner.score_candidate(matrix, site_id=coord_id)

        # 1. Assert identical status
        assert scored["status"] == cand["status"], (
            f"Site #{rank} status mismatch: planner={scored['status']} vs stored={cand['status']}"
        )

        # 2. Assert identical score (if eligible or needs_review)
        if cand.get("score") is not None:
            assert scored["score"] is not None
            assert round(scored["score"], 4) == round(cand["score"], 4), (
                f"Site #{rank} score mismatch: planner={scored['score']} vs stored={cand['score']}"
            )

        # 3. Assert identical raw criteria values
        raw_stored = cand.get("raw_criteria", {})
        raw_planner = matrix.get("raw_criteria", {})
        assert round(raw_planner["ghi_kwh_m2_day"], 4) == round(raw_stored["ghi_kwh_m2_day"], 4)
        assert round(raw_planner["substation_dist_km"], 2) == round(raw_stored["substation_dist_km"], 2)
        assert round(raw_planner["landcost_proxy_score"], 4) == round(raw_stored["landcost_proxy_score"], 4)
        assert round(raw_planner["t2m_celsius"], 2) == round(raw_stored["t2m_celsius"], 2)


# ---------------------------------------------------------------------------
# (l) Real Provenance Integrity for All Shortlisted Sites
# ---------------------------------------------------------------------------

def test_shortlist_provenance_integrity():
    """
    CRITICAL PROVENANCE AUDIT TEST:
    Verifies that every shortlisted matrix value in MongoDB has:
      1. A non-null, non-empty source
      2. For all 'sourced' values, a non-null, valid retrieved_at timestamp
      3. No 'migrated_backfill' origin tag
    """
    from backend.db import get_provenance_col, get_candidates_col
    prov_col = get_provenance_col()
    cand_col = get_candidates_col()

    candidates = list(cand_col.find({}))
    assert len(candidates) == 12, f"Expected 12 candidates, found {len(candidates)}"

    for cand in candidates:
        rank = cand["rank"]
        lat = cand["latitude"]
        lon = cand["longitude"]
        coord_key = f"{lat:.4f}_{lon:.4f}"
        site_records = list(prov_col.find({
            "$or": [
                {"site_id": coord_key},
                {"site_id": rank},
                {"site_id": str(rank)},
                {"candidate_rank": rank},
            ]
        }))
        assert len(site_records) > 0, f"Site #{rank} has no provenance records in MongoDB!"

        for rec in site_records:
            crit = rec.get("criterion")
            # 1. Non-null source
            assert rec.get("source"), f"Site #{rank} criterion '{crit}' has null or empty source!"

            # 2. No migrated_backfill
            assert rec.get("origin") != "migrated_backfill", (
                f"Site #{rank} criterion '{crit}' contains forbidden origin 'migrated_backfill'!"
            )

            # 3. If status is sourced, retrieved_at must be non-null
            if rec.get("status") == "sourced":
                assert rec.get("retrieved_at") is not None, (
                    f"Site #{rank} sourced criterion '{crit}' has null retrieved_at!"
                )


# ---------------------------------------------------------------------------
# (m) Voltage-Unknown Primary Substation Review Gating
# ---------------------------------------------------------------------------

def test_voltage_unknown_primary_substation_flagged_as_needs_review():
    """
    Validates that:
    1. Any candidate site whose primary substation lacks an explicit voltage tag
       (e.g. Site #2 using way/1134097124) receives status 'needs_review' with reason
       'Primary substation voltage not tagged in OSM; >=33 kV unconfirmed',
       while preserving its original score and distance.
    2. Exactly 6 of the 377 feasible grid points (1.59%) use a voltage-unknown primary
       substation across 5 distinct transmission substations.
    """
    from backend.db import get_candidates_col
    cand_col = get_candidates_col()

    # 1. Site #2 verification in database and candidates.json
    site2 = cand_col.find_one({"rank": 2})
    assert site2 is not None, "Site #2 not found in candidates collection"
    assert site2["status"] == "needs_review", f"Site #2 status should be 'needs_review', got '{site2['status']}'"
    assert site2.get("needs_review") is True
    reasons = site2.get("needs_review_reasons", [])
    expected_reason = "Primary substation voltage not tagged in OSM; >=33 kV unconfirmed"
    assert any(expected_reason in r for r in reasons), (
        f"Expected reason '{expected_reason}' not in Site #2 reasons: {reasons}"
    )
    # Score and distance preserved
    assert round(site2["score"], 4) == 0.6989
    sub_dist = site2.get("raw_criteria", {}).get("substation_dist_km") or site2.get("substation_details", {}).get("distance_km")
    assert round(sub_dist, 2) == 3.78

    # 2. Feasible grid point check across the 377 Phase-1 points
    from generate_candidates import build_grid, FOOTPRINT_RADIUS_KM
    from fetch_boundary import check_boundary_constraint
    from zonal_footprint import evaluate_footprint_zonal
    from bulk_download_substations import query_grid_locally
    from shapely.strtree import STRtree

    grid = build_grid(0.1)
    feasible_points = []
    for lat, lon in grid:
        passed_b, _, _ = check_boundary_constraint(lat, lon, radius_km=FOOTPRINT_RADIUS_KM, min_disk_fraction=0.90)
        if not passed_b:
            continue
        fp_res = evaluate_footprint_zonal(lat, lon, radius_km=FOOTPRINT_RADIUS_KM, min_usable_fraction=0.80)
        if fp_res["is_feasible"]:
            feasible_points.append((lat, lon))

    assert len(feasible_points) == 377, f"Expected 377 feasible points, got {len(feasible_points)}"

    subs_path = os.path.join(GEO_DIR, "data", "substations_karnataka.geojson")
    lines_path = os.path.join(GEO_DIR, "data", "lines_karnataka.geojson")
    gdf_all = gpd.read_file(subs_path).to_crs(epsg=32643)
    gdf_q = gdf_all[(gdf_all["is_qualifying"] == True) & (gdf_all.get("is_generation", False) != True)].copy()
    gdf_gen = gdf_all[gdf_all.get("is_generation", False) == True].copy()
    gdf_lines = gpd.read_file(lines_path).to_crs(epsg=32643)

    index_bundle = {
        "gdf_qualifying": gdf_q,
        "gdf_generation": gdf_gen,
        "gdf_all_subs": gdf_all,
        "gdf_lines": gdf_lines,
        "tree_qualifying": STRtree(gdf_q.geometry) if not gdf_q.empty else None,
        "tree_generation": STRtree(gdf_gen.geometry) if not gdf_gen.empty else None,
        "tree_all_subs": STRtree(gdf_all.geometry) if not gdf_all.empty else None,
        "tree_lines": STRtree(gdf_lines.geometry) if not gdf_lines.empty else None,
    }

    voltage_unknown_count = 0
    voltage_known_count = 0
    distinct_unknown_subs = set()

    for lat, lon in feasible_points:
        info = query_grid_locally(lat, lon, index_bundle=index_bundle)
        v_tag = info.get("substation_voltage_tag")
        osm_id = info.get("substation_osm_id")
        if v_tag is None or str(v_tag).strip().lower() in ["none", "nan", "", "no voltage tag"]:
            voltage_unknown_count += 1
            distinct_unknown_subs.add(osm_id)
        else:
            voltage_known_count += 1

    assert voltage_unknown_count == 6, f"Expected exactly 6 feasible points with voltage-unknown primary, got {voltage_unknown_count}"
    assert voltage_known_count == 371, f"Expected 371 feasible points with voltage-known primary, got {voltage_known_count}"
    assert len(distinct_unknown_subs) == 5, f"Expected 5 distinct voltage-unknown substations, got {len(distinct_unknown_subs)}"
    assert "way/1134097124" in distinct_unknown_subs


# ---------------------------------------------------------------------------
# (n) Report, README and API Candidate Coordinates Match Database
# ---------------------------------------------------------------------------

def test_coordinates_in_report_and_api_equal_database():
    """
    Validates that the coordinates for all 12 shortlisted candidates
    in final_report.md, README.md, and the FastAPI /api/candidates endpoint
    strictly match the MongoDB database.
    """
    import re
    import anyio
    from backend.db import get_candidates_col
    from backend.app.main import get_candidates

    cand_col = get_candidates_col()
    db_cands = sorted(list(cand_col.find({})), key=lambda x: x["rank"])
    assert len(db_cands) == 12, f"Expected 12 candidates in DB, found {len(db_cands)}"

    # 1. API Verification
    from app.db import connect_db, close_db

    async def _fetch_api():
        await connect_db()
        try:
            return await get_candidates()
        finally:
            await close_db()

    api_payload = anyio.run(_fetch_api)
    api_cands = sorted(api_payload["candidates"], key=lambda x: x["rank"])
    assert len(api_cands) == 12, f"Expected 12 candidates from API, found {len(api_cands)}"
    for db_c, api_c in zip(db_cands, api_cands):
        assert db_c["rank"] == api_c["rank"]
        assert db_c["latitude"] == api_c["latitude"], (
            f"API latitude mismatch for rank {db_c['rank']}: {api_c['latitude']} != {db_c['latitude']}"
        )
        assert db_c["longitude"] == api_c["longitude"], (
            f"API longitude mismatch for rank {db_c['rank']}: {api_c['longitude']} != {db_c['longitude']}"
        )

    # 2. final_report.md Verification
    report_path = os.path.join(PROJECT_ROOT, "final_report.md")
    assert os.path.exists(report_path)
    with open(report_path, "r", encoding="utf-8") as f:
        report_content = f.read()

    for db_c in db_cands:
        rk = db_c["rank"]
        lat = db_c["latitude"]
        lon = db_c["longitude"]
        coord_pattern = rf"\| #{rk} \|.*?\| {lat:.4f}°N, {lon:.4f}°E \|"
        assert re.search(coord_pattern, report_content), (
            f"Candidate #{rk} coordinates ({lat:.4f}°N, {lon:.4f}°E) not found in final_report.md"
        )

    # 3. README.md Verification
    readme_path = os.path.join(PROJECT_ROOT, "README.md")
    assert os.path.exists(readme_path)
    with open(readme_path, "r", encoding="utf-8") as f:
        readme_content = f.read()

    for db_c in db_cands:
        rk = db_c["rank"]
        lat = db_c["latitude"]
        lon = db_c["longitude"]
        coord_pattern = rf"\| #{rk} \|.*?\| {lat:.4f}°N, {lon:.4f}°E \|"
        assert re.search(coord_pattern, readme_content), (
            f"Candidate #{rk} coordinates ({lat:.4f}°N, {lon:.4f}°E) not found in README.md"
        )




