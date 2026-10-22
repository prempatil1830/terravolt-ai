"""
tests/test_feature_points.py
----------------------------
Tests for the per-point feature table:
1. Row count equals the number of grid points inside Karnataka boundary.
2. For every row and radius, share at 3% <= share at 5% <= share at 8%,
   and share with cropland excluded <= share with cropland allowed.
3. Applying default rules (0.54 km, 5% slope, 80% usable, cropland allowed,
   10 km protected buffer, 500 m water buffer where canals and streams only count
   if they cross the disk, 33 kV) reproduces the existing 377 feasible points.
4. Schema validation: GHI, T2M, land-cost proxy, distances, known-gap review flag,
   substations at 33, 66, 110 kV with flags, build_id, and build_date.
"""

import os
import sys
import json
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

GEO_DIR = os.path.join(PROJECT_ROOT, "geoprocessing")
if GEO_DIR not in sys.path:
    sys.path.insert(0, GEO_DIR)

from backend.db import get_feature_points_col
from geoprocessing.generate_candidates import build_grid, FOOTPRINT_RADIUS_KM
from geoprocessing.fetch_boundary import check_boundary_constraint, is_in_karnataka
from geoprocessing.zonal_footprint import evaluate_footprint_zonal

GEO_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "geoprocessing", "data")
FEATURE_POINTS_JSON = os.path.join(GEO_DATA_DIR, "feature_points.json")
FEATURE_POINTS_NO_EXT = os.path.join(GEO_DATA_DIR, "feature_points")
RADII = [0.25, 0.40, 0.54, 0.70, 0.85, 1.00, 1.10]


@pytest.fixture(scope="module")
def feature_data():
    """Load feature table documents from file or MongoDB."""
    if os.path.exists(FEATURE_POINTS_JSON):
        with open(FEATURE_POINTS_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
            return raw.get("feature_points", []), raw.get("metadata", {})
    col = get_feature_points_col()
    docs = list(col.find())
    return docs, {}


def test_row_count_equals_grid_points_inside_karnataka(feature_data):
    """
    1. Points: every 0.1 degree grid point inside the Karnataka boundary (~1,600).
    Verify row count in saved file and MongoDB equals grid points inside Karnataka (1,603).
    """
    rows, meta = feature_data
    grid = build_grid(0.1)
    inside_pts = [pt for pt in grid if is_in_karnataka(pt[0], pt[1])]

    assert len(inside_pts) == 1603, f"Expected 1603 points inside Karnataka, got {len(inside_pts)}"
    assert len(rows) == len(inside_pts), f"Expected {len(inside_pts)} feature rows, got {len(rows)}"

    # Also verify MongoDB collection count
    col = get_feature_points_col()
    assert col.count_documents({}) == len(inside_pts)

    # Verify both file paths exist
    assert os.path.exists(FEATURE_POINTS_JSON), f"Missing {FEATURE_POINTS_JSON}"
    assert os.path.exists(FEATURE_POINTS_NO_EXT), f"Missing {FEATURE_POINTS_NO_EXT}"


def test_monotonic_slope_and_cropland_shares(feature_data):
    """
    2. For every row and radius:
       - share at 3% <= share at 5% <= share at 8%
       - share with cropland excluded <= share with cropland allowed
    """
    rows, _ = feature_data
    assert len(rows) > 0, "Feature points table is empty"

    for r_idx, row in enumerate(rows):
        radii_dict = row["radii"]
        for r in RADII:
            r_str = f"{r:.2f}"
            assert r_str in radii_dict, f"Missing radius {r_str} in row {r_idx}"
            r_data = radii_dict[r_str]

            s3 = r_data.get("slope_le_3pct", r_data.get("share_slope_le_3"))
            s5 = r_data.get("slope_le_5pct", r_data.get("share_slope_le_5"))
            s8 = r_data.get("slope_le_8pct", r_data.get("share_slope_le_8"))

            assert s3 is not None and s5 is not None and s8 is not None
            # Floating point monotonic assertion with tolerance
            assert s3 <= s5 + 1e-6, (
                f"Row {r_idx} ({row['lat']},{row['lon']}) at radius {r}: slope 3% ({s3}) > 5% ({s5})"
            )
            assert s5 <= s8 + 1e-6, (
                f"Row {r_idx} ({row['lat']},{row['lon']}) at radius {r}: slope 5% ({s5}) > 8% ({s8})"
            )

            crop_allowed = r_data.get("lu_cropland_allowed", r_data.get("share_cropland_allowed"))
            crop_excl = r_data.get("lu_cropland_excluded", r_data.get("share_cropland_excluded"))

            assert crop_allowed is not None and crop_excl is not None
            assert crop_excl <= crop_allowed + 1e-6, (
                f"Row {r_idx} ({row['lat']},{row['lon']}) at radius {r}: "
                f"crop_excl ({crop_excl}) > crop_allowed ({crop_allowed})"
            )


def test_default_rules_reproduce_377_feasible_points(feature_data):
    """
    5. Applying the default rules:
       - 0.54 km radius
       - 5% slope (at least 80% usable)
       - 80% usable land cover (cropland allowed)
       - 10 km protected buffer
       - 500 m water buffer where canals and streams only count if they cross the disk
       - 33 kV substation interconnection available (<= 60 km)
       - disk inside Karnataka boundary >= 90%
       reproduces the existing 377 feasible points exactly.
    """
    rows, _ = feature_data

    # Filter feature points under default rules
    feasible_from_features = []
    for row in rows:
        lat, lon = round(row["lat"], 4), round(row["lon"], 4)
        r54 = row["radii"]["0.54"]

        # 1. Boundary fraction >= 0.90
        if r54["disk_karnataka_fraction"] < 0.90:
            continue

        # 2. Slope: at least 80% at or under 5% slope
        if r54["slope_le_5pct"] < 0.80:
            continue

        # 3. Land cover: at least 80% in eligible classes with cropland allowed
        if r54["lu_cropland_allowed"] < 0.80:
            continue

        # 4. Protected area buffer: 10 km from disk edge (point distance >= 10.54 km)
        if row["dist_protected_area_m"] is not None and row["dist_protected_area_m"] < 10540.0:
            continue

        # 5. Water buffer: 500m from disk edge (point dist >= 1040m), canals only count if crossing disk (dist > 540m)
        if row["dist_water_body_m"] is not None and row["dist_water_body_m"] < 1040.0:
            continue
        if row["dist_canal_stream_m"] is not None and row["dist_canal_stream_m"] <= 540.0:
            continue

        # 6. Minimum 33 kV substation available
        sub33 = row["substations"]["33"]
        if sub33["distance_km"] is None or sub33["distance_km"] > 60.0:
            continue

        feasible_from_features.append((lat, lon))

    feasible_set = set(feasible_from_features)

    # Ground truth from existing zonal footprint pipeline
    grid = build_grid(0.1)
    orig_377 = set()
    for lat, lon in grid:
        passed_b, _, _ = check_boundary_constraint(lat, lon, radius_km=FOOTPRINT_RADIUS_KM, min_disk_fraction=0.90)
        if not passed_b:
            continue
        fp = evaluate_footprint_zonal(lat, lon, radius_km=FOOTPRINT_RADIUS_KM, min_usable_fraction=0.80)
        if fp["is_feasible"]:
            orig_377.add((round(lat, 4), round(lon, 4)))

    assert len(orig_377) == 377, f"Expected 377 original feasible points, got {len(orig_377)}"
    assert len(feasible_set) == 377, f"Expected 377 feasible points from feature table, got {len(feasible_set)}"
    assert feasible_set == orig_377, (
        f"Mismatch between feature table feasible points and original 377 points! "
        f"Difference: {orig_377 ^ feasible_set}"
    )


def test_feature_table_attributes_and_substations(feature_data):
    """
    Verify presence of all requested attributes across the table:
    - GHI, T2M from raw cache
    - land-cost proxy
    - distances to protected area, water body, canal/stream
    - known-gap review flag and reasons
    - substations for min voltages 33, 66, and 110 kV with required keys and flags
    - build_id and build_date
    """
    rows, meta = feature_data
    assert len(rows) == 1603

    sample = rows[0]
    # Core attributes
    assert "lat" in sample and "lon" in sample and "district" in sample
    assert sample["district"] != "Unknown"
    assert "ghi" in sample and sample["ghi"] > 0
    assert "t2m" in sample and sample["t2m"] > 0
    assert "landcost_proxy" in sample and sample["landcost_proxy"] is not None
    assert 0.0 <= sample["landcost_proxy"] <= 1.0

    assert "dist_protected_area_km" in sample
    assert "dist_water_body_km" in sample
    assert "dist_canal_stream_km" in sample
    assert "known_gap_review" in sample
    assert isinstance(sample["known_gap_review"], bool)

    # Substations for 33, 66, 110 kV
    substations = sample["substations"]
    for v_str in ["33", "66", "110"]:
        assert v_str in substations
        s = substations[v_str]
        assert "osm_id" in s
        assert "name" in s
        assert "operator" in s
        assert "voltage_tag" in s
        assert "distance_km" in s
        assert "untagged_closer_than_confirmed" in s
        assert isinstance(s["untagged_closer_than_confirmed"], bool)
        assert "voltage_tag_missing" in s
        assert isinstance(s["voltage_tag_missing"], bool)

    # Metadata
    assert "build_id" in sample and len(sample["build_id"]) > 0
    assert "build_date" in sample and len(sample["build_date"]) > 0


def test_new_columns_exist_and_no_nulls(feature_data):
    """
    Verify that new columns exist on every row with zero nulls, and all distances are >= 0:
    - dist_town_km >= 0
    - dist_road_km >= 0
    - dist_line_km >= 0
    - dist_solar_plant_km >= 0
    - cropland_share
    """
    rows, _ = feature_data
    assert len(rows) == 1603

    new_cols = ["dist_town_km", "dist_road_km", "dist_line_km", "dist_solar_plant_km", "cropland_share"]

    for idx, row in enumerate(rows):
        for col in new_cols:
            assert col in row, f"Row {idx} ({row['lat']}, {row['lon']}) missing column '{col}'"
            val = row[col]
            assert val is not None, f"Row {idx} ({row['lat']}, {row['lon']}) has null for '{col}'"

        # Check distances >= 0
        assert row["dist_town_km"] >= 0.0, f"Negative dist_town_km at row {idx}"
        assert row["dist_road_km"] >= 0.0, f"Negative dist_road_km at row {idx}"
        assert row["dist_line_km"] >= 0.0, f"Negative dist_line_km at row {idx}"
        assert row["dist_solar_plant_km"] >= 0.0, f"Negative dist_solar_plant_km at row {idx}"

    # Also check MongoDB documents directly
    col = get_feature_points_col()
    for col_name in new_cols:
        null_count = col.count_documents({col_name: {"$in": [None, float("nan")]}})
        assert null_count == 0, f"MongoDB collection has {null_count} nulls for column '{col_name}'"


def test_dist_road_km_at_bengaluru_below_state_median(feature_data):
    """
    Verify that dist_road_km at Bengaluru-area points is below the statewide median.
    """
    import numpy as np

    rows, _ = feature_data
    all_road_dists = [r["dist_road_km"] for r in rows]
    state_median = float(np.median(all_road_dists))

    # Central Bengaluru-area points (around 12.97°N, 77.59°E)
    bengaluru_points = [
        r for r in rows
        if (12.9 <= r["lat"] <= 13.1 and 77.5 <= r["lon"] <= 77.7)
    ]
    assert len(bengaluru_points) > 0, "No Bengaluru-area points found"

    # Verify that dist_road_km for every Bengaluru-area point is below the statewide median
    for p in bengaluru_points:
        assert p["dist_road_km"] < state_median, (
            f"Bengaluru point ({p['lat']}, {p['lon']}) dist_road_km={p['dist_road_km']} "
            f"not below state median={state_median}"
        )

    # And the median across all Bengaluru district points is below state median
    b_all = [
        r["dist_road_km"] for r in rows
        if "bengaluru" in r.get("district", "").lower() or "bangalore" in r.get("district", "").lower()
    ]
    assert np.median(b_all) < state_median


def test_cropland_share_within_zero_to_one(feature_data):
    """
    Verify that cropland_share is within 0 to 1 for all points,
    both at top level and for each radius, and equals lu_cropland_allowed - lu_cropland_excluded.
    """
    rows, _ = feature_data
    for idx, row in enumerate(rows):
        # Top-level cropland_share (0.54 km default)
        cs = row["cropland_share"]
        assert 0.0 <= cs <= 1.0, f"Row {idx} top-level cropland_share={cs} outside [0, 1]"

        # Per radius cropland_share
        radii_dict = row["radii"]
        for r_str, r_data in radii_dict.items():
            assert "cropland_share" in r_data, f"Row {idx} missing cropland_share for radius {r_str}"
            r_cs = r_data["cropland_share"]
            assert 0.0 <= r_cs <= 1.0, f"Row {idx} radius {r_str} cropland_share={r_cs} outside [0, 1]"

            expected = round(r_data["lu_cropland_allowed"] - r_data["lu_cropland_excluded"], 4)
            assert abs(r_cs - expected) < 1e-4, (
                f"Row {idx} radius {r_str}: cropland_share={r_cs} != expected {expected}"
            )
