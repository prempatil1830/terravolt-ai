"""
tests/test_weights_validation.py
--------------------------------
Tests for the three HITL weight validation rules at Gate 2 (weights):
  1. Weights must sum to 1.0.
  2. Land-cost proxy weight must be <= 10% (0.10).
  3. The AHP Consistency Ratio (CR) of the implied/submitted pairwise matrix must be <= 0.10.
     (or user submits a pairwise matrix from which consistent weights are derived).
"""

import os
import sys
import pytest
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.hitl import validate_weights_payload


def test_reject_weights_not_sum_to_one():
    """Weights summing to != 1.0 must be rejected with a clear error."""
    bad_payload = {
        "weights": {
            "ghi": 0.40,
            "substation": 0.40,
            "landcost": 0.10,
            "temp_derating": 0.05,  # Sum = 0.95
        }
    }
    with pytest.raises(ValueError) as excinfo:
        validate_weights_payload(bad_payload)
    assert "Weights must sum to 1.0" in str(excinfo.value)
    assert "0.9500" in str(excinfo.value)


def test_reject_weights_sum_exceeds_one():
    """Weights summing to 1.2 must be rejected."""
    bad_payload = {
        "weights": {
            "ghi": 0.50,
            "substation": 0.50,
            "landcost": 0.10,
            "temp_derating": 0.10,  # Sum = 1.20
        }
    }
    with pytest.raises(ValueError) as excinfo:
        validate_weights_payload(bad_payload)
    assert "Weights must sum to 1.0" in str(excinfo.value)
    assert "1.2000" in str(excinfo.value)


def test_reject_landcost_exceeds_ten_percent():
    """Land-cost proxy weight > 10% must be rejected."""
    bad_payload = {
        "weights": {
            "ghi": 0.35,
            "substation": 0.35,
            "landcost": 0.20,  # 20% > 10%
            "temp_derating": 0.10,
        }
    }
    with pytest.raises(ValueError) as excinfo:
        validate_weights_payload(bad_payload)
    assert "Land-cost proxy weight cannot exceed 10%" in str(excinfo.value)
    assert "20.0%" in str(excinfo.value)


def test_reject_inconsistent_pairwise_matrix():
    """Pairwise matrix with Consistency Ratio > 0.10 must be rejected."""
    # A strongly contradictory matrix where:
    # GHI > Sub (9), Sub > Land (9), but Land > GHI (9)
    inconsistent_matrix = [
        [1.0, 9.0, 1/9.0, 1.0],
        [1/9.0, 1.0, 9.0, 1.0],
        [9.0, 1/9.0, 1.0, 1.0],
        [1.0, 1.0, 1.0, 1.0],
    ]
    bad_payload = {
        "pairwise_matrix": inconsistent_matrix
    }
    with pytest.raises(ValueError) as excinfo:
        validate_weights_payload(bad_payload)
    assert "AHP Consistency Ratio" in str(excinfo.value)
    assert "exceeds the 0.10 threshold" in str(excinfo.value)


def test_accept_valid_direct_weights():
    """Valid weights summing to 1.0 with landcost <= 10% must be accepted."""
    good_payload = {
        "weights": {
            "ghi": 0.40,
            "substation": 0.40,
            "landcost": 0.10,
            "temp_derating": 0.10,
        }
    }
    weights, cr = validate_weights_payload(good_payload)
    assert round(sum(weights.values()), 4) == 1.0
    assert weights["landcost"] == 0.10
    assert cr <= 0.10


def test_accept_valid_pairwise_matrix():
    """Valid consistent pairwise matrix must compute weights and verify CR <= 0.10."""
    # Standard Saaty matrix with equal GHI and Substation, 4x over Land and Temp
    consistent_matrix = [
        [1.0, 1.0, 4.0, 4.0],
        [1.0, 1.0, 4.0, 4.0],
        [0.25, 0.25, 1.0, 1.0],
        [0.25, 0.25, 1.0, 1.0],
    ]
    payload = {
        "pairwise_matrix": consistent_matrix
    }
    weights, cr = validate_weights_payload(payload)
    assert cr <= 0.10
    assert round(sum(weights.values()), 4) == 1.0
    assert weights["landcost"] <= 0.10 + 1e-3
    assert weights["ghi"] > 0.35
