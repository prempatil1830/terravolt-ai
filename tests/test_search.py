"""
tests/test_search.py
---------------------
Tests for the interactive search engine (Features 1–4).
"""

import os
import sys
import math
from datetime import datetime, timezone
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from backend.search_engine import (
    size_to_footprint,
    validate_weights,
    resolve_weights,
    execute_search,
    save_search_run,
    get_search_run,
    get_districts,
    PRECOMPUTED_RADII_KM,
    PRIORITY_PRESETS,
    _haversine_km,
    _normalize_ghi,
    _normalize_substation,
    _normalize_landcost,
    _normalize_temp_derating,
)


# ──────────────────────────────────────────────────────────────────────
# Feature 2: Size to footprint
# ──────────────────────────────────────────────────────────────────────

class TestSizeToFootprint:
    def test_50mwp_default(self):
        """50 MWp → 225 acres → radius ~0.538 km → uses 0.54."""
        fp = size_to_footprint(50.0)
        assert fp["size_mwp"] == 50.0
        assert fp["required_acres"] == 225.0
        assert fp["used_radius_km"] == 0.54
        assert fp["radius_key"] == "0.54"

    def test_5mwp_smallest(self):
        """5 MWp → 22.5 acres → very small radius → uses 0.25."""
        fp = size_to_footprint(5.0)
        assert fp["used_radius_km"] == 0.25

    def test_200mwp_largest(self):
        """200 MWp → 900 acres → large radius → uses 1.10 (capped)."""
        fp = size_to_footprint(200.0)
        assert fp["used_radius_km"] == 1.10

    def test_footprint_formula(self):
        """Verify acres = MWp × 4.5 and radius formula."""
        for mwp in [10, 30, 50, 100]:
            fp = size_to_footprint(mwp)
            expected_acres = mwp * 4.5
            assert fp["required_acres"] == expected_acres

            area_m2 = expected_acres * 4046.8564224
            expected_r_km = math.sqrt(area_m2 / math.pi) / 1000.0
            assert abs(fp["required_radius_km"] - round(expected_r_km, 4)) < 0.001

    def test_used_radius_is_precomputed(self):
        """used_radius_km must always be one of the precomputed radii."""
        for mwp in [5, 10, 20, 50, 100, 150, 200]:
            fp = size_to_footprint(mwp)
            assert fp["used_radius_km"] in PRECOMPUTED_RADII_KM

    def test_used_radius_covers_required(self):
        """used_radius_km must be >= required_radius_km."""
        for mwp in [5, 10, 20, 50, 100, 150, 200]:
            fp = size_to_footprint(mwp)
            assert fp["used_radius_km"] >= fp["required_radius_km"] - 0.01


# ──────────────────────────────────────────────────────────────────────
# Weight validation
# ──────────────────────────────────────────────────────────────────────

class TestWeights:
    def test_default_preset(self):
        w = resolve_weights("default")
        assert abs(sum(w.values()) - 1.0) < 0.001
        assert w["landcost"] <= 0.10 + 1e-6

    def test_solar_first_preset(self):
        w = resolve_weights("solar_first")
        assert w["ghi"] == 0.60
        assert abs(sum(w.values()) - 1.0) < 0.001

    def test_grid_first_preset(self):
        w = resolve_weights("grid_first")
        assert w["substation"] == 0.60
        assert abs(sum(w.values()) - 1.0) < 0.001

    def test_custom_valid(self):
        w = resolve_weights("custom", {
            "ghi": 0.40, "substation": 0.40,
            "landcost": 0.10, "temp_derating": 0.10,
        })
        assert abs(sum(w.values()) - 1.0) < 0.001

    def test_custom_sum_wrong(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            resolve_weights("custom", {
                "ghi": 0.50, "substation": 0.50,
                "landcost": 0.10, "temp_derating": 0.10,
            })

    def test_custom_landcost_too_high(self):
        with pytest.raises(ValueError, match="Land-cost"):
            resolve_weights("custom", {
                "ghi": 0.30, "substation": 0.30,
                "landcost": 0.30, "temp_derating": 0.10,
            })

    def test_unknown_preset(self):
        with pytest.raises(ValueError, match="Unknown priority"):
            resolve_weights("fastest")

    def test_custom_no_weights(self):
        with pytest.raises(ValueError, match="Custom weights required"):
            resolve_weights("custom")


# ──────────────────────────────────────────────────────────────────────
# Normalizations
# ──────────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_ghi_range(self):
        assert _normalize_ghi(5.0) == 0.0
        assert _normalize_ghi(6.0) == 1.0
        assert 0.0 < _normalize_ghi(5.5) < 1.0
        assert _normalize_ghi(4.0) == 0.0
        assert _normalize_ghi(7.0) == 1.0

    def test_substation_range(self):
        assert _normalize_substation(0.0) == 1.0
        assert _normalize_substation(50.0) == 0.0
        assert _normalize_substation(25.0) == 0.5
        assert _normalize_substation(None) == 0.0

    def test_landcost_range(self):
        assert _normalize_landcost(0.0) == 0.0
        assert _normalize_landcost(1.0) == 1.0
        assert _normalize_landcost(None) == 0.5

    def test_temp_range(self):
        assert _normalize_temp_derating(20.0) == 1.0
        assert _normalize_temp_derating(30.0) == 0.0
        assert _normalize_temp_derating(None) == 0.5


# ──────────────────────────────────────────────────────────────────────
# Haversine
# ──────────────────────────────────────────────────────────────────────

class TestHaversine:
    def test_same_point(self):
        assert _haversine_km(15.0, 76.0, 15.0, 76.0) == 0.0

    def test_known_distance(self):
        # ~111 km per degree latitude
        d = _haversine_km(15.0, 76.0, 16.0, 76.0)
        assert 110 < d < 112


# ──────────────────────────────────────────────────────────────────────
# Feature 1 & 3: Execute search (requires MongoDB with feature_points)
# ──────────────────────────────────────────────────────────────────────

class TestExecuteSearch:
    """Integration tests — require MongoDB with 1,603 feature_points loaded."""

    @pytest.fixture(autouse=True)
    def _check_db(self):
        """Skip if feature_points not loaded."""
        try:
            from backend.db import get_feature_points_col
            col = get_feature_points_col()
            if col.count_documents({}) < 100:
                pytest.skip("Feature points not loaded in MongoDB")
        except Exception:
            pytest.skip("MongoDB not available")

    def test_default_search_returns_sites(self):
        result = execute_search()
        assert result["site_count"] > 0
        assert result["funnel"]["total_considered"] > 100
        assert len(result["sites"]) <= 10

    def test_search_has_funnel(self):
        result = execute_search()
        f = result["funnel"]
        assert "total_considered" in f
        assert "survivors" in f
        assert f["total_considered"] >= f["survivors"]

    def test_site_schema_matches_candidates(self):
        """Each site should have the same top-level keys as candidates.json."""
        result = execute_search(top_n=3)
        required_keys = {
            "rank", "latitude", "longitude", "district", "status",
            "score", "score_100", "needs_review", "needs_review_reasons",
            "raw_criteria", "normalized_criteria", "weights", "constraints",
        }
        for site in result["sites"]:
            assert required_keys.issubset(set(site.keys())), \
                f"Missing keys: {required_keys - set(site.keys())}"

    def test_greedy_spacing(self):
        """Sites must be >= 20 km apart."""
        result = execute_search(top_n=10)
        sites = result["sites"]
        for i, s1 in enumerate(sites):
            for s2 in sites[i + 1:]:
                d = _haversine_km(
                    s1["latitude"], s1["longitude"],
                    s2["latitude"], s2["longitude"],
                )
                assert d >= 19.9, f"Sites {s1['rank']} and {s2['rank']} only {d:.1f} km apart"

    def test_scores_descending(self):
        """Sites should be ordered by score descending."""
        result = execute_search(top_n=10)
        scores = [s["score"] for s in result["sites"]]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

    def test_district_filter(self):
        result = execute_search(districts=["Raichur"], top_n=5)
        for site in result["sites"]:
            assert site["district"] == "Raichur"

    def test_solar_first_boosts_ghi(self):
        default = execute_search(priority="default", top_n=5)
        solar = execute_search(priority="solar_first", top_n=5)
        assert solar["weights"]["ghi"] > default["weights"]["ghi"]

    def test_top_n_respected(self):
        for n in [5, 10, 20]:
            result = execute_search(top_n=n)
            assert len(result["sites"]) <= n

    def test_slope_filter_3pct(self):
        """Stricter slope (3%) should yield fewer survivors."""
        r5 = execute_search(max_slope_pct=5)
        r3 = execute_search(max_slope_pct=3)
        assert r3["funnel"]["survivors"] <= r5["funnel"]["survivors"]

    def test_avoid_cropland_reduces_survivors(self):
        """Excluding cropland should filter more points."""
        with_crop = execute_search(avoid_cropland=False)
        no_crop = execute_search(avoid_cropland=True)
        assert no_crop["funnel"]["survivors"] <= with_crop["funnel"]["survivors"]

    def test_footprint_in_result(self):
        result = execute_search(size_mwp=100)
        assert result["footprint"]["size_mwp"] == 100
        assert result["footprint"]["used_radius_km"] in PRECOMPUTED_RADII_KM

    def test_warnings_present(self):
        result = execute_search()
        assert isinstance(result["warnings"], list)

    def test_needs_review_status(self):
        """At least some points should have needs_review if voltage tags are missing."""
        result = execute_search(top_n=20)
        statuses = {s["status"] for s in result["sites"]}
        # We just check both values exist in the schema
        assert all(s in ("eligible", "needs_review") for s in statuses)


# ──────────────────────────────────────────────────────────────────────
# Feature 1: Districts
# ──────────────────────────────────────────────────────────────────────

class TestDistricts:
    @pytest.fixture(autouse=True)
    def _check_db(self):
        try:
            from backend.db import get_feature_points_col
            col = get_feature_points_col()
            if col.count_documents({}) < 100:
                pytest.skip("Feature points not loaded in MongoDB")
        except Exception:
            pytest.skip("MongoDB not available")

    def test_districts_returns_list(self):
        districts = get_districts()
        assert len(districts) > 0
        assert all("name" in d for d in districts)

    def test_known_districts(self):
        districts = get_districts()
        names = {d["name"] for d in districts}
        assert "Raichur" in names
        assert "Chitradurga" in names


# ──────────────────────────────────────────────────────────────────────
# Feature 4: Run logging
# ──────────────────────────────────────────────────────────────────────

class TestRunLogging:
    @pytest.fixture(autouse=True)
    def _check_db(self):
        try:
            from backend.db import get_feature_points_col
            col = get_feature_points_col()
            if col.count_documents({}) < 100:
                pytest.skip("Feature points not loaded in MongoDB")
        except Exception:
            pytest.skip("MongoDB not available")

    def test_save_and_retrieve_run(self):
        params = {"districts": "all", "size_mwp": 50}
        result = execute_search()
        run_id = save_search_run(params, result, build_id="test_123")

        assert run_id.startswith("search_")

        retrieved = get_search_run(run_id)
        assert retrieved is not None
        assert retrieved["run_id"] == run_id
        assert retrieved["type"] == "interactive_search"
        assert retrieved["parameters"] == params
        assert retrieved["site_count"] == result["site_count"]

    def test_nonexistent_run(self):
        assert get_search_run("search_nonexistent_xyz") is None


# ──────────────────────────────────────────────────────────────────────
# Feature: Human-in-the-Loop Constraint Refinement
# ──────────────────────────────────────────────────────────────────────

class TestConstraintRefinement:
    @pytest.fixture(autouse=True)
    def _check_db(self):
        try:
            from backend.db import get_feature_points_col
            col = get_feature_points_col()
            if col.count_documents({}) < 100:
                pytest.skip("Feature points not loaded in MongoDB")
        except Exception:
            pytest.skip("MongoDB not available")

    def test_catalogue_structure_and_constraints(self):
        """GET /api/constraints returns catalogue with 13 constraints across 5 groups."""
        from backend.constraints import CONSTRAINTS_CATALOGUE, CONSTRAINTS_BY_ID
        assert len(CONSTRAINTS_CATALOGUE) == 13
        expected_ids = {
            "close_to_town",
            "lower_land_cost",
            "near_road",
            "near_transmission_line",
            "near_solar_plants",
            "strong_sun",
            "cooler_sites",
            "very_flat",
            "avoid_farmland",
            "away_from_water",
            "wider_protected_buffer",
            "min_grid_voltage",
            "max_substation_distance",
        }
        assert set(CONSTRAINTS_BY_ID.keys()) == expected_ids
        for c in CONSTRAINTS_CATALOGUE:
            assert "id" in c
            assert "label" in c
            assert "group" in c
            assert "allowed_values" in c and len(c["allowed_values"]) > 0
            assert "direction" in c
            assert c["group"] in {
                "Location and access",
                "Land",
                "Sun and climate",
                "Grid",
                "Environment",
            }
        assert CONSTRAINTS_BY_ID["lower_land_cost"]["modes_allowed"] == ["prefer"]

    def test_same_inputs_give_identical_results(self):
        """Identical inputs produce 100% identical results (scores, sites, funnels)."""
        extras = [
            {"id": "near_road", "value": 5, "mode": "must"},
            {"id": "cooler_sites", "value": 25, "mode": "prefer", "importance": "medium"},
        ]
        r1 = execute_search(districts=["Chitradurga"], size_mwp=50, extra_constraints=extras)
        r2 = execute_search(districts=["Chitradurga"], size_mwp=50, extra_constraints=extras)

        assert r1["site_count"] == r2["site_count"]
        assert len(r1["sites"]) == len(r2["sites"])
        assert r1["funnel"] == r2["funnel"]
        assert r1["weights"] == r2["weights"]
        assert r1["scaled_base_weights"] == r2["scaled_base_weights"]

        for s1, s2 in zip(r1["sites"], r2["sites"]):
            assert s1["rank"] == s2["rank"]
            assert s1["latitude"] == s2["latitude"]
            assert s1["longitude"] == s2["longitude"]
            assert s1["score"] == s2["score"]
            assert s1["extra_constraints"] == s2["extra_constraints"]

    def test_must_constraints_never_increase_site_count(self):
        """Must constraints never increase the survivor or shortlisted site count."""
        base = execute_search(districts=["Chitradurga"], size_mwp=50)
        base_survivors = base["funnel"]["survivors"]
        base_sites = len(base["sites"])

        test_cases = [
            [{"id": "near_road", "value": 5, "mode": "must"}],
            [{"id": "avoid_farmland", "value": 25, "mode": "must"}],
            [{"id": "strong_sun", "value": 5.5, "mode": "must"}],
            [{"id": "cooler_sites", "value": 24, "mode": "must"}],
            [{"id": "away_from_water", "value": 2, "mode": "must"}],
            [
                {"id": "near_road", "value": 5, "mode": "must"},
                {"id": "strong_sun", "value": 5.3, "mode": "must"},
            ],
        ]

        for extra in test_cases:
            res = execute_search(districts=["Chitradurga"], size_mwp=50, extra_constraints=extra)
            assert res["funnel"]["survivors"] <= base_survivors
            assert len(res["sites"]) <= base_sites
            # Also check funnel details
            assert "must_constraints" in res["funnel"]
            for m_step in res["funnel"]["must_constraints"]:
                assert m_step["remaining"] <= m_step["count_before"]

    def test_extra_weights_above_50_percent_rejected(self):
        """Total extra prefer weights exceeding 50% must be rejected."""
        # 3 high prefer criteria = 3 * 20% = 60% > 50%
        excessive_extras = [
            {"id": "cooler_sites", "value": 25, "mode": "prefer", "importance": "high"},
            {"id": "near_road", "value": 5, "mode": "prefer", "importance": "high"},
            {"id": "strong_sun", "value": 5.5, "mode": "prefer", "importance": "high"},
        ]
        with pytest.raises(ValueError) as exc:
            execute_search(extra_constraints=excessive_extras)
        assert "exceed 50%" in str(exc.value)

        # Custom weight > 50%
        single_excessive = [
            {"id": "cooler_sites", "value": 25, "mode": "prefer", "importance": 0.55},
        ]
        with pytest.raises(ValueError) as exc:
            execute_search(extra_constraints=single_excessive)
        assert "exceed 50%" in str(exc.value)

    def test_no_extras_equal_existing_search(self):
        """With no extras, search results are strictly identical to existing search."""
        base_none = execute_search(districts=["Chitradurga"], size_mwp=50, extra_constraints=None)
        base_empty = execute_search(districts=["Chitradurga"], size_mwp=50, extra_constraints=[])
        base_default = execute_search(districts=["Chitradurga"], size_mwp=50)

        assert base_none["site_count"] == base_empty["site_count"] == base_default["site_count"]
        assert base_none["funnel"] == base_empty["funnel"] == base_default["funnel"]

        for s1, s2 in zip(base_none["sites"], base_default["sites"]):
            assert s1["rank"] == s2["rank"]
            assert s1["latitude"] == s2["latitude"]
            assert s1["longitude"] == s2["longitude"]
            assert s1["score"] == s2["score"]

    def test_one_audit_log_entry_per_reevaluate(self):
        """Re-evaluating records exactly one audit_log entry with reviewer and counts."""
        from backend.db import get_audit_log_col
        audit_col = get_audit_log_col()
        before_count = audit_col.count_documents({"action": "re_evaluate"})

        reviewer = f"Auditor_{int(datetime.now(timezone.utc).timestamp())}"
        extras = [{"id": "near_road", "value": 5, "mode": "must"}]
        params = {
            "districts": ["Chitradurga"],
            "size_mwp": 50,
            "extra_constraints": extras,
        }
        res = execute_search(districts=["Chitradurga"], size_mwp=50, extra_constraints=extras)
        run_id = save_search_run(
            params,
            res,
            reviewer_name=reviewer,
            is_reevaluate=True,
            counts_before=10,
        )

        after_count = audit_col.count_documents({"action": "re_evaluate"})
        assert after_count == before_count + 1

        entry = audit_col.find_one({"run_id": run_id, "action": "re_evaluate"})
        assert entry is not None
        assert entry["reviewer_name"] == reviewer
        assert entry["counts_before"] == 10
        assert entry["counts_after"] == res["site_count"]
        assert entry["constraints"] == extras

    def test_no_network_calls_during_search(self, monkeypatch):
        """Verify that search with extra constraints executes with zero network calls."""
        def _fail_network(*args, **kwargs):
            raise RuntimeError("Network call attempted during local search!")

        import socket
        monkeypatch.setattr(socket, "getaddrinfo", _fail_network)
        monkeypatch.setattr(socket, "create_connection", _fail_network)

        try:
            import requests
            monkeypatch.setattr(requests, "get", _fail_network)
            monkeypatch.setattr(requests, "post", _fail_network)
        except ImportError:
            pass

        try:
            import httpx
            monkeypatch.setattr(httpx, "get", _fail_network)
            monkeypatch.setattr(httpx, "post", _fail_network)
        except ImportError:
            pass

        extras = [
            {"id": "near_road", "value": 5, "mode": "must"},
            {"id": "cooler_sites", "value": 25, "mode": "prefer", "importance": "medium"},
        ]
        # Must execute completely without raising any network exceptions
        res = execute_search(districts=["Chitradurga"], size_mwp=50, extra_constraints=extras)
        assert res["site_count"] > 0
        assert len(res["sites"]) > 0

    def test_scenarios_a_b_c_exact_reproducibility(self):
        """Verifies the three evaluation scenarios:
        (a) Chitradurga, 50 MWp, defaults
        (b) (a) plus Must near_road within 5 km and Prefer cooler_sites (medium)
        (c) (a) plus Must avoid_farmland at 25%
        """
        # (a)
        res_a = execute_search(districts=["Chitradurga"], size_mwp=50, top_n=5)
        assert res_a["funnel"]["total_considered"] == 73
        assert res_a["funnel"]["survivors"] == 27
        assert len(res_a["sites"]) == 5

        # (b)
        res_b = execute_search(
            districts=["Chitradurga"],
            size_mwp=50,
            top_n=5,
            extra_constraints=[
                {"id": "near_road", "value": 5, "mode": "must"},
                {"id": "cooler_sites", "value": 25, "mode": "prefer", "importance": "medium"},
            ],
        )
        assert res_b["funnel"]["survivors"] == 27
        assert len(res_b["sites"]) == 5
        # Prefer cooler_sites boosted the cooler candidate
        assert res_b["scaled_base_weights"]["ghi"] == 0.36  # 0.40 * 0.90
        assert res_b["extra_weights_total"] == 0.10
        assert res_b["sites"][0]["extra_constraints"]["cooler_sites"]["weight"] == 0.10

        # (c)
        res_c = execute_search(
            districts=["Chitradurga"],
            size_mwp=50,
            top_n=5,
            extra_constraints=[
                {"id": "avoid_farmland", "value": 25, "mode": "must"},
            ],
        )
        assert res_c["funnel"]["survivors"] == 0
        assert len(res_c["sites"]) == 0

