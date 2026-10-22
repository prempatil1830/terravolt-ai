"""
backend/hitl.py
----------------
Human-in-the-Loop (HITL) Controller with 4 checkpoints:
  (a) scope: Problem & Scope
  (b) weights: Criteria & Weights
  (c) matrix: Decision Matrix before scoring
  (d) ranking: Final Ranking & Plain-English Explanation

Each gate pauses, stores its payload in MongoDB with an incremented version,
and resumes only on approve or revise via POST /api/hitl/{run_id}/{gate}.
Logs every decision to audit_log (who, when, what changed, version).
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import uuid
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_GEO_DIR = os.path.join(_PROJECT_ROOT, "geoprocessing")
if _GEO_DIR not in sys.path:
    sys.path.insert(0, _GEO_DIR)

from backend.db import get_runs_col, get_audit_log_col
from geoprocessing.agents.planner import PlannerAgent, DEFAULT_WEIGHTS
from geoprocessing.agents.explainer import explain_ranking

VALID_GATES = ["scope", "weights", "matrix", "ranking"]
GATE_ORDER = ["scope", "weights", "matrix", "ranking"]


def _log_audit_event(
    run_id: str,
    gate: str,
    action: str,
    who: str,
    version: int,
    what_changed: str,
    payload_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Records an immutable audit entry in the audit_log collection."""
    col = get_audit_log_col()
    entry = {
        "run_id": run_id,
        "gate": gate,
        "action": action,
        "who": who,
        "when": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "what_changed": what_changed,
        "payload_snapshot": payload_snapshot,
    }
    col.insert_one(entry)
    # Remove MongoDB _id before returning
    entry.pop("_id", None)
    return entry


def create_hitl_run(
    latitude: float,
    longitude: float,
    site_id: Optional[Any] = None,
    who: str = "operator",
) -> Dict[str, Any]:
    """
    Initializes a new HITL screening run paused at Gate 1 (scope).
    Stores payload in MongoDB with version 1.
    """
    runs_col = get_runs_col()
    run_uuid = uuid.uuid4().hex[:8]
    run_id = f"hitl_run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{run_uuid}"
    effective_site_id = site_id if site_id is not None else f"{latitude:.4f}_{longitude:.4f}"
    now_iso = datetime.now(timezone.utc).isoformat()

    scope_payload = {
        "problem": "Utility-Scale Solar PV Site Feasibility Screening (≥50 MWp)",
        "geography": "State of Karnataka, India (GADM 4.1 Boundary / EPSG:4326)",
        "target_capacity_mwp": 50.0,
        "latitude": latitude,
        "longitude": longitude,
        "site_id": effective_site_id,
        "screening_policy": "4-Gate Human-in-the-Loop Deterministic AHP Protocol",
    }

    run_doc: Dict[str, Any] = {
        "run_id": run_id,
        "site_id": effective_site_id,
        "latitude": latitude,
        "longitude": longitude,
        "current_gate": "scope",
        "status": "paused_at_scope",
        "version": 1,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": who,
        "gates": {
            "scope": {
                "status": "pending",
                "version": 1,
                "payload": scope_payload,
            },
            "weights": {
                "status": "not_reached",
                "version": 0,
                "payload": None,
            },
            "matrix": {
                "status": "not_reached",
                "version": 0,
                "payload": None,
            },
            "ranking": {
                "status": "not_reached",
                "version": 0,
                "payload": None,
            },
        },
    }

    runs_col.insert_one(dict(run_doc))
    run_doc.pop("_id", None)

    _log_audit_event(
        run_id=run_id,
        gate="scope",
        action="create_run",
        who=who,
        version=1,
        what_changed=f"Initialized run for coordinate ({latitude}, {longitude}) at scope gate",
        payload_snapshot=scope_payload,
    )

    return run_doc


def get_hitl_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves full run state and audit trail history from MongoDB."""
    runs_col = get_runs_col()
    audit_col = get_audit_log_col()

    doc = runs_col.find_one({"run_id": run_id}, {"_id": 0})
    if not doc:
        return None

    audit_entries = list(audit_col.find({"run_id": run_id}, {"_id": 0}).sort("when", 1))
    doc["audit_trail"] = audit_entries
    return doc


def process_gate_action(
    run_id: str,
    gate: str,
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    who: str = "operator",
) -> Dict[str, Any]:
    """
    Processes approve or revise for the specified gate.
    Increments version, stores updated payload, logs to audit_log,
    and advances to next gate on approval.
    """
def validate_weights_payload(payload: Dict[str, Any]) -> tuple[Dict[str, float], float]:
    """
    Validates criteria weights per project rules:
    1. Weights must sum to 1.0 (within ±0.001 tolerance).
    2. Land-cost proxy weight must be <= 10% (0.10).
    3. The AHP Consistency Ratio (CR) of the implied or submitted pairwise comparison matrix must be <= 0.10.
       If user submits a pairwise_matrix instead, weights and CR are computed from it.
    """
    import numpy as np
    from geoprocessing.ahp_engine import compute_ahp_weights

    if not isinstance(payload, dict):
        raise ValueError("Weights payload must be a JSON object.")

    # Option A: User submitted a pairwise comparison matrix
    if "pairwise_matrix" in payload and payload["pairwise_matrix"] is not None:
        try:
            mat = np.array(payload["pairwise_matrix"], dtype=float)
        except Exception as e:
            raise ValueError(f"Invalid pairwise matrix format: {e}")

        try:
            ahp_res = compute_ahp_weights(mat, verbose=False)
        except Exception as e:
            raise ValueError(f"Failed to compute AHP weights from pairwise matrix: {e}")

        cr = ahp_res.consistency_ratio
        if cr > 0.10:
            raise ValueError(
                f"AHP Consistency Ratio (CR = {cr:.4f}) exceeds the 0.10 threshold. "
                "The submitted pairwise comparison matrix is inconsistent; please adjust judgments."
            )

        weights = ahp_res.weights
    elif "weights" in payload and payload["weights"] is not None:
        weights = payload["weights"]
        cr = float(payload.get("consistency_ratio", 0.0))
        if "pairwise_matrix" in payload and payload["pairwise_matrix"]:
            mat = np.array(payload["pairwise_matrix"], dtype=float)
            ahp_res = compute_ahp_weights(mat, verbose=False)
            cr = ahp_res.consistency_ratio
            if cr > 0.10:
                raise ValueError(
                    f"AHP Consistency Ratio (CR = {cr:.4f}) exceeds the 0.10 threshold. "
                    "The implied pairwise matrix is inconsistent."
                )
    else:
        raise ValueError("Payload at Gate 'weights' must contain 'weights' or 'pairwise_matrix'.")

    required_keys = ["ghi", "substation", "landcost", "temp_derating"]
    for k in required_keys:
        if k not in weights:
            raise ValueError(f"Missing required criterion weight: '{k}'. Required: {required_keys}")
        try:
            val = float(weights[k])
        except (ValueError, TypeError):
            raise ValueError(f"Weight for '{k}' must be numeric (got {weights[k]!r})")
        if val < 0.0:
            raise ValueError(f"Weight for '{k}' cannot be negative (got {val})")

    # Rule 1: Sum must equal 1.0
    total = sum(float(weights[k]) for k in required_keys)
    if abs(total - 1.0) > 0.001:
        raise ValueError(
            f"Weights must sum to 1.0 (current sum: {total:.4f}). "
            f"Values submitted: {weights}"
        )

    # Rule 2: Land-cost is <= 10%
    landcost = float(weights["landcost"])
    if landcost > 0.1001:
        raise ValueError(
            f"Land-cost proxy weight cannot exceed 10% (0.10) due to acknowledged proxy uncertainty "
            f"(submitted: {landcost * 100:.1f}%). Please reduce land-cost weight to <= 10%."
        )

    # Rule 3: Check CR <= 0.10
    if cr > 0.10:
        raise ValueError(
            f"AHP Consistency Ratio (CR = {cr:.4f}) exceeds the 0.10 threshold. "
            "Pairwise comparisons are inconsistent."
        )

    normalized_weights = {k: float(weights[k]) for k in required_keys}
    return normalized_weights, cr


def process_gate_action(
    run_id: str,
    gate: str,
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    who: str = "operator",
) -> Dict[str, Any]:
    """
    Processes a gate transition (approve or revise), logs audit event,
    enforces weight validation rules at the 'weights' gate,
    and advances to next gate on approval.
    """
    if gate not in VALID_GATES:
        raise ValueError(f"Invalid gate '{gate}'. Allowed gates: {VALID_GATES}")
    if action not in ("approve", "revise"):
        raise ValueError(f"Invalid action '{action}'. Allowed actions: 'approve', 'revise'")

    runs_col = get_runs_col()
    run = runs_col.find_one({"run_id": run_id})
    if not run:
        raise KeyError(f"Run '{run_id}' not found.")

    if run.get("status") == "completed":
        raise ValueError(f"Run '{run_id}' is already completed and locked.")

    current_gate = run.get("current_gate")
    if current_gate != gate:
        raise ValueError(
            f"Cannot act on gate '{gate}'. The run is currently paused at gate '{current_gate}'."
        )

    new_version = run.get("version", 1) + 1
    now_iso = datetime.now(timezone.utc).isoformat()
    gates = run["gates"]

    # 1. Handle REVISE Action
    if action == "revise":
        if not payload:
            raise ValueError(f"Action 'revise' on gate '{gate}' requires a non-empty payload.")

        if gate == "weights":
            norm_weights, cr = validate_weights_payload(payload)
            payload["weights"] = norm_weights
            payload["consistency_ratio"] = cr

        # Update current gate payload and status
        gates[gate]["payload"].update(payload)
        gates[gate]["version"] = new_version
        gates[gate]["status"] = "revised"
        gates[gate]["last_revised_at"] = now_iso
        gates[gate]["last_revised_by"] = who

        run["version"] = new_version
        run["updated_at"] = now_iso
        runs_col.update_one({"run_id": run_id}, {"$set": run})

        _log_audit_event(
            run_id=run_id,
            gate=gate,
            action="revise",
            who=who,
            version=new_version,
            what_changed=f"Revised payload for gate '{gate}'",
            payload_snapshot=gates[gate]["payload"],
        )

        run.pop("_id", None)
        return run

    # 2. Handle APPROVE Action
    if gate == "weights":
        target_payload = payload if (payload and ("weights" in payload or "pairwise_matrix" in payload)) else gates["weights"]["payload"]
        norm_weights, cr = validate_weights_payload(target_payload)
        gates["weights"]["payload"]["weights"] = norm_weights
        gates["weights"]["payload"]["consistency_ratio"] = cr

    gates[gate]["status"] = "approved"
    gates[gate]["approved_at"] = now_iso
    gates[gate]["approved_by"] = who

    what_changed_msg = f"Approved checkpoint gate '{gate}'"

    # Transition to Next Gate
    planner = PlannerAgent()
    lat = run["latitude"]
    lon = run["longitude"]
    site_id = run["site_id"]

    if gate == "scope":
        # Advance to Gate 2: Criteria & Weights
        next_gate = "weights"
        default_weights_payload = {
            "weights": dict(DEFAULT_WEIGHTS),
            "description": "Standard Analytic Hierarchy Process baseline weighting matrix",
            "criteria_definitions": {
                "ghi": "Solar insolation (Benefit)",
                "substation": "Distance to nearest ≥33 kV substation (Cost/Inverted)",
                "landcost": "Distance to district town land-cost proxy (Cost/Inverted)",
                "temp_derating": "Ambient air temperature panel derating (Cost/Inverted)",
            },
        }
        gates["weights"]["status"] = "pending"
        gates["weights"]["version"] = new_version
        gates["weights"]["payload"] = default_weights_payload
        run["current_gate"] = "weights"
        run["status"] = "paused_at_weights"
        what_changed_msg += " → Advanced to Gate 2 (weights)"

    elif gate == "weights":
        # Advance to Gate 3: Decision Matrix (runs 4 specialist agents)
        next_gate = "matrix"
        specialist_outputs = planner.dispatch_specialists(lat, lon, site_id=site_id)
        matrix_data = planner.assemble_decision_matrix(lat, lon, specialist_outputs)

        matrix_payload = {
            "specialist_outputs": specialist_outputs,
            "raw_criteria": matrix_data["raw_criteria"],
            "constraints": matrix_data["constraints"],
            "status": matrix_data["status"],
            "exclusion_reasons": matrix_data["exclusion_reasons"],
            "needs_review_reasons": matrix_data["needs_review_reasons"],
        }
        gates["matrix"]["status"] = "pending"
        gates["matrix"]["version"] = new_version
        gates["matrix"]["payload"] = matrix_payload
        run["current_gate"] = "matrix"
        run["status"] = "paused_at_matrix"
        what_changed_msg += " → Dispatched 4 specialist agents; paused at Gate 3 (matrix)"

    elif gate == "matrix":
        # Advance to Gate 4: Final Ranking (runs deterministic AHP scoring + explanation)
        next_gate = "ranking"
        approved_weights = gates["weights"]["payload"]["weights"]
        matrix_data = gates["matrix"]["payload"]

        # Run deterministic scoring
        scoring_result = planner.score_candidate(
            matrix_data=matrix_data,
            weights=approved_weights,
            site_id=site_id,
        )

        run_summary = {
            "latitude": lat,
            "longitude": lon,
            "site_id": site_id,
            "status": scoring_result["status"],
            "score": scoring_result["score"],
            "score_100": scoring_result["score_100"],
            "weights": scoring_result["weights"],
            "normalized_criteria": scoring_result["normalized_criteria"],
            "raw_criteria": matrix_data["raw_criteria"],
            "constraints": matrix_data["constraints"],
            "exclusion_reasons": matrix_data.get("exclusion_reasons", []),
            "needs_review_reasons": matrix_data.get("needs_review_reasons", []),
        }

        # Generate plain-English explanation (strictly verified)
        explanation_res = explain_ranking(run_summary)

        ranking_payload = {
            "scoring": scoring_result,
            "explanation": explanation_res["explanation"],
            "explanation_source": explanation_res["source"],
            "verified": explanation_res["verified"],
        }

        gates["ranking"]["status"] = "pending"
        gates["ranking"]["version"] = new_version
        gates["ranking"]["payload"] = ranking_payload
        run["current_gate"] = "ranking"
        run["status"] = "paused_at_ranking"
        what_changed_msg += " → Applied deterministic AHP scoring & generated explanation; paused at Gate 4 (ranking)"

    elif gate == "ranking":
        # Final gate approval completes the run
        run["status"] = "completed"
        gates["ranking"]["status"] = "approved"
        what_changed_msg += " → Completed and certified screening run"

    run["version"] = new_version
    run["updated_at"] = now_iso
    runs_col.update_one({"run_id": run_id}, {"$set": run})

    _log_audit_event(
        run_id=run_id,
        gate=gate,
        action="approve",
        who=who,
        version=new_version,
        what_changed=what_changed_msg,
        payload_snapshot=gates[gate]["payload"],
    )

    run.pop("_id", None)
    return run


def record_reviewer_decision(
    candidate_id: Any,
    decision: str,
    note: str,
    date: str,
    who: str = "reviewer",
) -> Dict[str, Any]:
    """
    Records a manual reviewer verification decision for a candidate site
    (e.g. resolving a needs_review flag).
    Decision must be 'cleared' or 'excluded'.
    Updates MongoDB candidates collection and geoprocessing/data/candidates.json,
    and logs an immutable entry to audit_log.
    """
    if decision not in ["cleared", "excluded"]:
        raise ValueError(f"Invalid decision '{decision}'. Must be 'cleared' or 'excluded'.")
    if not note or not note.strip():
        raise ValueError("A one-sentence explanation note is required for manual verification.")

    now_iso = datetime.now(timezone.utc).isoformat()
    reviewer_record = {
        "decision": decision,
        "note": note.strip(),
        "date": date,
        "reviewer": who,
        "timestamp": now_iso,
    }

    cand_rank = int(candidate_id) if str(candidate_id).isdigit() else None

    # Update candidates.json
    candidates_file = os.path.join(_PROJECT_ROOT, "geoprocessing", "data", "candidates.json")
    updated_cand_data = None
    if os.path.exists(candidates_file):
        with open(candidates_file, "r") as f:
            cdata = json.load(f)
        for c in cdata.get("candidates", []):
            if c.get("rank") == cand_rank or str(c.get("rank")) == str(candidate_id):
                c["reviewer_decision"] = reviewer_record
                c["needs_review"] = False
                c["status"] = "eligible" if decision == "cleared" else "excluded"
                updated_cand_data = c
                break
        with open(candidates_file, "w") as f:
            json.dump(cdata, f, indent=2)

    # Update MongoDB
    try:
        from backend.db import get_candidates_col
        cand_col = get_candidates_col()
        filter_q = {"$or": [{"rank": cand_rank}, {"rank": str(candidate_id)}, {"site_id": str(candidate_id)}]}
        cand_doc = cand_col.find_one(filter_q)
        if cand_doc:
            new_status = "eligible" if decision == "cleared" else "excluded"
            cand_col.update_one(
                filter_q,
                {
                    "$set": {
                        "reviewer_decision": reviewer_record,
                        "needs_review": False,
                        "status": new_status,
                        "updated_at": now_iso,
                    }
                }
            )
            cand_doc["reviewer_decision"] = reviewer_record
            cand_doc["needs_review"] = False
            cand_doc["status"] = new_status
            cand_doc.pop("_id", None)
            updated_cand_data = cand_doc
    except Exception as exc:
        print(f"MongoDB update warning: {exc}")

    # Log to audit_log
    _log_audit_event(
        run_id=f"site_{candidate_id}",
        gate="manual_verification",
        action=f"verify_{decision}",
        who=who,
        version=1,
        what_changed=f"Manual review for candidate #{candidate_id}: {decision.upper()} — {note.strip()}",
        payload_snapshot=reviewer_record,
    )

    return {
        "success": True,
        "candidate_id": candidate_id,
        "reviewer_decision": reviewer_record,
        "candidate": updated_cand_data,
    }

