import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

GEO_DIR = os.path.join(PROJECT_ROOT, "geoprocessing")
if GEO_DIR not in sys.path:
    sys.path.insert(0, GEO_DIR)

from geoprocessing.score_candidate import score_candidate
from geoprocessing.agents.planner import PlannerAgent
from geoprocessing.raw_cache import is_replay_mode, get_cached, hash_request


def test_replay_mode_score_candidate(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    assert is_replay_mode() is True

    result = score_candidate(14.3, 77.4, site_id="test_replay_cand")
    assert result["status"] == "eligible"
    assert round(result["score_100"], 1) == 63.7
    assert round(result["raw_criteria"]["substation_dist_km"], 1) == 12.2
    assert round(result["raw_criteria"]["nearest_generation_substation_dist_km"], 1) == 0.9
    assert round(result["raw_criteria"]["ghi_kwh_m2_day"], 2) == 5.66


def test_replay_mode_planner_agent(monkeypatch):
    monkeypatch.setenv("REPLAY", "1")
    planner = PlannerAgent(footprint_radius_km=None)
    specs = planner.dispatch_specialists(14.3, 77.4, site_id="test_replay_planner")
    matrix = planner.assemble_decision_matrix(14.3, 77.4, specs)
    scored = planner.score_candidate(matrix, site_id="test_replay_planner")

    assert scored["status"] == "eligible"
    assert round(scored["score_100"], 1) == 63.7
    assert round(matrix["raw_criteria"]["substation_dist_km"], 1) == 12.2
