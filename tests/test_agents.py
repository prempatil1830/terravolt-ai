"""
tests/test_agents.py
--------------------
Validates the specialist agents, planner orchestration, HITL controller, and explanation validation.

Mandatory Invariants:
  1. An agent cannot import scoring code (ahp_engine or score_candidate).
  2. The same input twice gives an identical ranking (deterministic plain Python orchestration).
  3. A forced tool failure produces needs_review (never a fallback number).
  4. HITL controller enforces 4-checkpoint progression and immutable audit logging.
  5. explain_ranking rejects any text with unverified/hallucinated numbers.
"""

import ast
import os
import sys
import pytest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

GEO_DIR = os.path.join(PROJECT_ROOT, "geoprocessing")
if GEO_DIR not in sys.path:
    sys.path.insert(0, GEO_DIR)

from geoprocessing.agents.solar_climate import SolarClimateAgent
from geoprocessing.agents.land_environment import LandEnvironmentAgent
from geoprocessing.agents.infra_grid import InfraGridAgent
from geoprocessing.agents.economic_risk import EconomicRiskAgent
from geoprocessing.agents.planner import PlannerAgent
from geoprocessing.agents.explainer import (
    explain_ranking,
    validate_explanation_numbers,
    generate_template_explanation,
)
from backend.hitl import create_hitl_run, get_hitl_run, process_gate_action
from backend.db import get_audit_log_col, get_runs_col
from fetch_nasa_power import NASAPowerRateLimitExceeded
from fetch_protected_buffer import WaterBufferDataUnavailable
from fetch_substation_distance import SubstationDataUnavailable


# ---------------------------------------------------------------------------
# Test 1: An Agent Cannot Import Scoring Code
# ---------------------------------------------------------------------------

def test_agents_cannot_import_scoring_code():
    """
    CRITICAL ARCHITECTURAL TEST:
    Verifies that NONE of the specialist agents import ahp_engine, score_candidate,
    or any scoring code. Their responsibility is strictly data fetching & provenance.
    """
    agent_files = [
        "solar_climate.py",
        "land_environment.py",
        "infra_grid.py",
        "economic_risk.py",
    ]
    agents_dir = os.path.join(GEO_DIR, "agents")

    forbidden_modules = ["ahp_engine", "score_candidate"]

    for fname in agent_files:
        fpath = os.path.join(agents_dir, fname)
        assert os.path.exists(fpath), f"File {fpath} does not exist"

        with open(fpath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fname)

        # Inspect all import statements in the AST
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_modules:
                        assert forbidden not in alias.name, (
                            f"Violation: {fname} directly imports forbidden scoring module '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for forbidden in forbidden_modules:
                        assert forbidden not in node.module, (
                            f"Violation: {fname} imports from forbidden scoring module '{node.module}'"
                        )


# ---------------------------------------------------------------------------
# Test 2: The Same Input Twice Gives an Identical Ranking
# ---------------------------------------------------------------------------

MOCK_SPECIALIST_OUTPUTS = {
    "solar_climate": {
        "agent": "SolarClimateAgent",
        "status": "eligible",
        "metrics": {
            "raw_ghi": {"value": 5.6566, "unit": "kWh/m²/day", "source": "NASA POWER", "status": "sourced"},
            "raw_temperature": {"value": 26.67, "unit": "°C", "source": "NASA POWER", "status": "sourced"},
        },
        "needs_review_reasons": [],
    },
    "land_environment": {
        "agent": "LandEnvironmentAgent",
        "status": "eligible",
        "metrics": {
            "slope": {"value": 0.97, "unit": "%", "tripped": False, "status": "sourced"},
            "landuse": {"value": "Shrubland", "code": 20, "tripped": False, "status": "sourced"},
            "protected_buffer": {"value": "eligible", "tripped": False, "status": "sourced"},
        },
        "needs_review_reasons": [],
    },
    "infra_grid": {
        "agent": "InfraGridAgent",
        "status": "eligible",
        "metrics": {
            "substation_dist_km": {"value": 0.90, "unit": "km", "status": "sourced"},
            "transmission_line_dist_km": {"value": 0.21, "unit": "km", "status": "sourced"},
        },
        "needs_review_reasons": [],
    },
    "economic_risk": {
        "agent": "EconomicRiskAgent",
        "status": "eligible",
        "metrics": {
            "landcost_proxy_score": {"value": 0.3826, "unit": "score [0-1]", "status": "calculated"},
            "computed_derating_factor": {"value": 0.8813, "unit": "factor", "status": "calculated"},
        },
        "needs_review_reasons": [],
    },
}


def test_deterministic_ranking_identical_runs():
    """
    REPRODUCIBILITY INVARIANT:
    Running PlannerAgent with the same coordinate twice must yield byte-for-byte
    and float-for-float identical rankings and scores (plain Python orchestration).
    """
    planner = PlannerAgent()
    lat, lon = 14.3000, 77.4000  # Site #1 Pavagada

    with patch.object(planner, "dispatch_specialists", return_value=MOCK_SPECIALIST_OUTPUTS):
        run_1 = planner.run(lat, lon, site_id="test_repro_1")
        run_2 = planner.run(lat, lon, site_id="test_repro_2")

    # Identical Status
    assert run_1["status"] == run_2["status"] == "eligible"

    # Identical Score
    assert run_1["score"] == run_2["score"]
    assert run_1["score_100"] == run_2["score_100"]

    # Identical Normalized Matrix
    assert run_1["normalized_criteria"] == run_2["normalized_criteria"]

    # Identical Raw Criteria
    assert run_1["raw_criteria"]["ghi_kwh_m2_day"] == run_2["raw_criteria"]["ghi_kwh_m2_day"]
    assert run_1["raw_criteria"]["substation_dist_km"] == run_2["raw_criteria"]["substation_dist_km"]
    assert run_1["raw_criteria"]["landcost_proxy_score"] == run_2["raw_criteria"]["landcost_proxy_score"]
    assert run_1["raw_criteria"]["t2m_celsius"] == run_2["raw_criteria"]["t2m_celsius"]


# ---------------------------------------------------------------------------
# Test 3: Forced Tool Failure Produces needs_review (Never a Fallback Number)
# ---------------------------------------------------------------------------

def test_forced_water_buffer_failure_produces_needs_review():
    """Verify that forced WaterBufferDataUnavailable produces needs_review, never a fallback."""
    agent = LandEnvironmentAgent()

    with patch("geoprocessing.agents.land_environment.get_buffer_status", side_effect=WaterBufferDataUnavailable("Overpass 504 Gateway Timeout")):
        out = agent.execute(14.3, 77.4, site_id="test_fail_water")

        assert out["status"] == "needs_review"
        assert out["metrics"]["protected_buffer"]["value"] is None
        assert out["metrics"]["protected_buffer"]["status"] == "unavailable"
        assert any("Overpass" in r for r in out["needs_review_reasons"])


def test_forced_substation_failure_produces_needs_review():
    """Verify that forced SubstationDataUnavailable produces needs_review, never a fallback number."""
    agent = InfraGridAgent()

    with patch("geoprocessing.agents.infra_grid.get_grid_connection_info", side_effect=SubstationDataUnavailable("Overpass endpoints unreachable")):
        out = agent.execute(14.3, 77.4, site_id="test_fail_sub")

        assert out["status"] == "needs_review"
        assert out["metrics"]["substation_dist_km"]["value"] is None
        assert out["metrics"]["substation_dist_km"]["status"] == "unavailable"
        assert any("unavailable" in r.lower() for r in out["needs_review_reasons"])


def test_forced_nasa_power_failure_produces_needs_review():
    """Verify that forced NASAPowerRateLimitExceeded produces needs_review, never a fallback number."""
    agent = SolarClimateAgent()

    with patch("geoprocessing.agents.solar_climate.fetch_climatology", side_effect=NASAPowerRateLimitExceeded("429 Too Many Requests")):
        out = agent.execute(14.3, 77.4, site_id="test_fail_nasa")

        assert out["status"] == "needs_review"
        assert out["metrics"]["raw_ghi"]["value"] is None
        assert out["metrics"]["raw_ghi"]["status"] == "unavailable"
        assert any("rate limit" in r.lower() for r in out["needs_review_reasons"])


def test_planner_integrates_forced_failure_as_needs_review():
    """Verify PlannerAgent elevates candidate to needs_review when an agent reports a failure."""
    planner = PlannerAgent()

    failure_outputs = dict(MOCK_SPECIALIST_OUTPUTS)
    failure_outputs['infra_grid'] = {
        'agent': 'InfraGridAgent',
        'status': 'needs_review',
        'metrics': {
            'substation_dist_km': {'value': None, 'unit': 'km', 'status': 'unavailable'},
            'transmission_line_dist_km': {'value': None, 'unit': 'km', 'status': 'unavailable'},
        },
        'needs_review_reasons': ['Substation data unavailable: Overpass connection timed out'],
    }

    with patch.object(planner, 'dispatch_specialists', return_value=failure_outputs):
        result = planner.run(14.3, 77.4, site_id='test_planner_needs_review')

    assert result['status'] == 'needs_review'
    assert result['raw_criteria']['substation_dist_km'] is None
    assert result['normalized_criteria']['substation'] == 0.0
    assert len(result['needs_review_reasons']) > 0
# ---------------------------------------------------------------------------
# Test 4: HITL Controller 4-Checkpoint Lifecycle & Audit Logging
# ---------------------------------------------------------------------------

def test_hitl_controller_lifecycle_and_audit_trail():
    """
    Tests end-to-end 4-gate progression:
      (a) scope -> approve -> (b) weights -> approve -> (c) matrix -> approve -> (d) ranking -> approve -> completed
    Verifies that every gate pauses, stores payload in MongoDB, increments version,
    and logs immutable entries to audit_log.
    """
    # 1. Initialize Run
    run = create_hitl_run(latitude=14.3, longitude=77.4, site_id="test_hitl_lifecycle", who="test_lead")
    run_id = run["run_id"]
    assert run["version"] == 1
    assert run["current_gate"] == "scope"
    assert run["status"] == "paused_at_scope"

    # 2. Approve Gate 1 (scope) -> Advances to Gate 2 (weights)
    run = process_gate_action(run_id, "scope", "approve", who="test_lead")
    assert run["version"] == 2
    assert run["current_gate"] == "weights"
    assert run["status"] == "paused_at_weights"
    assert run["gates"]["scope"]["status"] == "approved"
    assert run["gates"]["weights"]["status"] == "pending"

    # Revise Gate 2 (weights)
    custom_weights = {"ghi": 0.50, "substation": 0.30, "landcost": 0.10, "temp_derating": 0.10}
    run = process_gate_action(run_id, "weights", "revise", payload={"weights": custom_weights}, who="test_lead")
    assert run["version"] == 3
    assert run["gates"]["weights"]["payload"]["weights"]["ghi"] == 0.50

    # Approve Gate 2 (weights) -> Advances to Gate 3 (matrix)
    with patch.object(PlannerAgent, "dispatch_specialists", return_value=MOCK_SPECIALIST_OUTPUTS):
        run = process_gate_action(run_id, "weights", "approve", who="test_lead")
    assert run["version"] == 4
    assert run["current_gate"] == "matrix"
    assert run["status"] == "paused_at_matrix"
    assert run["gates"]["matrix"]["status"] == "pending"
    assert "specialist_outputs" in run["gates"]["matrix"]["payload"]

    # Approve Gate 3 (matrix) -> Advances to Gate 4 (ranking)
    run = process_gate_action(run_id, "matrix", "approve", who="test_lead")
    assert run["version"] == 5
    assert run["current_gate"] == "ranking"
    assert run["status"] == "paused_at_ranking"
    assert run["gates"]["ranking"]["status"] == "pending"
    assert run["gates"]["ranking"]["payload"]["scoring"]["score"] is not None
    assert run["gates"]["ranking"]["payload"]["explanation"] is not None

    # Approve Gate 4 (ranking) -> Certified & Completed
    run = process_gate_action(run_id, "ranking", "approve", who="test_lead")
    assert run["version"] == 6
    assert run["status"] == "completed"
    assert run["gates"]["ranking"]["status"] == "approved"

    # Verify Audit Trail in MongoDB
    full_state = get_hitl_run(run_id)
    assert full_state is not None
    trail = full_state.get("audit_trail", [])
    assert len(trail) >= 5, f"Expected at least 5 audit entries, found {len(trail)}"

    gates_logged = [e["gate"] for e in trail]
    assert "scope" in gates_logged
    assert "weights" in gates_logged
    assert "matrix" in gates_logged
    assert "ranking" in gates_logged


# ---------------------------------------------------------------------------
# Test 5: Numerical Consistency Validator in explain_ranking
# ---------------------------------------------------------------------------

def test_explain_ranking_numeric_verification():
    """
    Verifies that explain_ranking rejects any text containing hallucinated numbers
    and falls back to a clean deterministic template.
    """
    dummy_run = {
        "latitude": 14.3,
        "longitude": 77.4,
        "status": "eligible",
        "score": 0.7270,
        "score_100": 72.70,
        "raw_criteria": {
            "ghi_kwh_m2_day": 5.6566,
            "substation_dist_km": 0.90,
            "transmission_line_dist_km": 0.21,
            "landcost_proxy_score": 0.3826,
            "t2m_celsius": 26.67,
        },
        "constraints": {
            "slope": {"value_pct": 0.97},
            "landuse": {"class_name": "Shrubland"},
            "protected_buffer": {"status": "eligible"},
        },
    }

    # 1. Valid explanation matching real data numbers
    valid_text = (
        "Site at 14.3°N, 77.4°E scores 72.70 out of 100. GHI is 5.6566 kWh/m2/day. "
        "Substation distance is 0.90 km, slope is 0.97%."
    )
    is_valid, invalids = validate_explanation_numbers(valid_text, dummy_run)
    assert is_valid, f"Expected valid explanation, but found invalid numbers: {invalids}"

    # 2. Invalid explanation containing hallucinated/fake numbers (e.g. 99.9, 123.45)
    hallucinated_text = (
        "Site at 14.3°N, 77.4°E scores 99.9 out of 100. GHI is 123.45 kWh/m2/day."
    )
    is_valid_bad, invalids_bad = validate_explanation_numbers(hallucinated_text, dummy_run)
    assert not is_valid_bad, "Validator should have rejected hallucinated numbers"
    assert 99.9 in invalids_bad or 123.45 in invalids_bad

    # 3. explain_ranking fallback produces verified template
    res = explain_ranking(dummy_run)
    assert res["verified"] is True
    assert "72.70" in res["explanation"] or "0.7270" in res["explanation"]
    assert "5.6566" in res["explanation"]
