"""
main.py — FastAPI application entry point for solar-pv-karnataka.

Endpoints
---------
GET /health   → liveness/readiness check (MongoDB ping included)

Architecture note
-----------------
This API layer is intentionally thin. It handles HTTP routing and
delegates persistence to db.py. All heavy geospatial computation lives
in the standalone geoprocessing module (app/geoprocessing/processor.py)
and is invoked as a separate process/task — never inside a request handler.
"""

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
for _p in [_PROJECT_ROOT, _BACKEND_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.db import connect_db, close_db, get_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan: connect / disconnect MongoDB around the app lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    yield
    await close_db()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Terravolt AI — API",
    description=(
        "Pre-feasibility site suitability screening for utility-scale "
        "solar PV development in Karnataka, India. "
        "Scope: Karnataka only. Phase 0 — scaffold."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Allow the React dev server (localhost:5173) to call this API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"], summary="Liveness and readiness check")
async def health():
    """
    Returns 200 OK when the API is running and MongoDB is reachable.
    Returns 503 Service Unavailable if the MongoDB ping fails.
    """
    try:
        db = get_db()
        await db.command("ping")
        mongo_status = "ok"
    except Exception as exc:
        logger.error("MongoDB ping failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"MongoDB unreachable: {exc}",
        )

    return {
        "status": "ok",
        "service": "solar-pv-karnataka-api",
        "version": "0.1.0",
        "scope": "Karnataka, India",
        "mongodb": mongo_status,
    }


import json
import os

CANDIDATES_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "geoprocessing", "data", "candidates.json")
)


COLL_CANDIDATES = "candidates"
COLL_PROVENANCE = "provenance"
COLL_RUNS = "runs"


@app.get("/api/candidates", tags=["candidates"], summary="Get candidate sites from MongoDB")
async def get_candidates():
    """
    Returns candidate sites and sweep metadata from the MongoDB `candidates` and `runs` collections.
    """
    try:
        db = get_db()
        cursor = db[COLL_CANDIDATES].find({}, {"_id": 0}).sort("rank", 1)
        candidates_list = await cursor.to_list(length=100)

        # Retrieve sweep metadata from `runs` collection
        run_doc = await db[COLL_RUNS].find_one({"run_id": "sweep_v1_frozen"}, {"_id": 0})
        sweep_metadata = run_doc.get("sweep_metadata") if run_doc else {}
        selection_rule = run_doc.get("selection_rule") if run_doc else ""
        generated_at = run_doc.get("generated_at") if run_doc else None

        if candidates_list:
            return {
                "source": "mongodb",
                "generated_at": generated_at,
                "sweep_metadata": sweep_metadata,
                "selection_rule": selection_rule,
                "candidates": candidates_list,
            }

        # Fallback to candidates.json if MongoDB collection is empty
        logger.warning("MongoDB candidates collection empty; falling back to candidates.json")
        if os.path.exists(CANDIDATES_PATH):
            with open(CANDIDATES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)

        raise HTTPException(
            status_code=404,
            detail="No candidates found in MongoDB or disk",
        )
    except Exception as exc:
        logger.error("Failed to retrieve candidates from MongoDB: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Error reading candidates from MongoDB: {exc}",
        )


@app.get("/api/candidates/{id}/provenance", tags=["candidates"], summary="Get data provenance for a candidate site")
async def get_candidate_provenance(id: str):
    """
    Returns provenance records for the specified candidate site from MongoDB `provenance` collection.
    """
    try:
        db = get_db()
        query_ids = [id]
        if id.isdigit():
            query_ids.append(int(id))

        # Check candidate exists
        cand = await db[COLL_CANDIDATES].find_one(
            {"$or": [{"rank": {"$in": query_ids}}, {"site_id": {"$in": query_ids}}]},
            {"_id": 0}
        )
        if not cand:
            raise HTTPException(
                status_code=404,
                detail=f"Candidate site '{id}' not found.",
            )

        c_lat = cand.get("latitude")
        c_lon = cand.get("longitude")
        c_rank = cand.get("rank")
        coord_key = f"{c_lat:.4f}_{c_lon:.4f}" if (c_lat is not None and c_lon is not None) else None

        query_conditions = [{"site_id": {"$in": query_ids}}]
        if coord_key:
            query_conditions.append({"site_id": coord_key})
        if c_rank:
            query_conditions.append({"candidate_rank": c_rank})

        cursor = db[COLL_PROVENANCE].find(
            {"$or": query_conditions},
            {"_id": 0}
        ).sort("criterion", 1)
        records = await cursor.to_list(length=100)

        return {
            "site_id": int(id) if id.isdigit() else id,
            "candidate_rank": cand.get("rank"),
            "count": len(records),
            "records": records,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to retrieve provenance for site %s: %s", id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving provenance: {exc}",
        )


@app.get("/api/candidates/{id}/explain", tags=["candidates"], summary="Get LLM explanation for candidate ranking and review status")
async def get_candidate_explanation(id: str):
    """
    Returns an LLM explanation of why the candidate is ranked at its position,
    and if flagged as 'needs_review', why it requires manual review.
    """
    try:
        cand = None
        try:
            db = get_db()
            query_ids = [id]
            if id.isdigit():
                query_ids.append(int(id))

            cand = await db[COLL_CANDIDATES].find_one(
                {"$or": [{"rank": {"$in": query_ids}}, {"site_id": {"$in": query_ids}}]},
                {"_id": 0}
            )
        except Exception as db_err:
            logger.debug("MongoDB candidate query skipped/failed, checking local JSON: %s", db_err)

        if not cand:
            if os.path.exists(CANDIDATES_PATH):
                with open(CANDIDATES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for c in data.get("candidates", []):
                        if str(c.get("rank")) == str(id):
                            cand = c
                            break

        if not cand:
            raise HTTPException(status_code=404, detail=f"Candidate site '{id}' not found.")

        from geoprocessing.agents.explainer import explain_ranking
        res = await asyncio.to_thread(explain_ranking, cand)
        return res
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to generate candidate explanation: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/candidates/explain", tags=["candidates"], summary="Get LLM explanation for dynamic candidate data")
async def post_candidate_explanation(cand: Dict[str, Any]):
    """
    Accepts candidate data from interactive search and returns an LLM explanation.
    """
    try:
        from geoprocessing.agents.explainer import explain_ranking
        res = await asyncio.to_thread(explain_ranking, cand)
        return res
    except Exception as exc:
        logger.error("Failed to generate candidate explanation: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


from backend.hitl import create_hitl_run, get_hitl_run, process_gate_action


class StartRunRequest(BaseModel):
    latitude: float = Field(..., ge=11.0, le=19.0, description="Latitude in Karnataka")
    longitude: float = Field(..., ge=73.5, le=79.0, description="Longitude in Karnataka")
    site_id: Optional[str] = None
    who: Optional[str] = "operator"

StartRunRequest.model_rebuild()


class GateActionRequest(BaseModel):
    action: str = Field(..., pattern="^(approve|revise)$", description="Must be 'approve' or 'revise'")
    payload: Optional[dict] = None
    who: Optional[str] = "operator"

GateActionRequest.model_rebuild()


@app.post("/api/hitl/start", tags=["hitl"], summary="Initialize a new 4-gate HITL screening run")
async def api_start_hitl_run(req: StartRunRequest):
    """Initializes a new HITL run at Gate 1 (scope), paused for human review."""
    try:
        run_doc = create_hitl_run(
            latitude=req.latitude,
            longitude=req.longitude,
            site_id=req.site_id,
            who=req.who or "operator",
        )
        return run_doc
    except Exception as exc:
        logger.error("Failed to start HITL run: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/hitl/{run_id}", tags=["hitl"], summary="Get HITL run status, checkpoints, and audit trail")
async def api_get_hitl_run(run_id: str):
    """Retrieves current checkpoint state and immutable audit trail for a run."""
    run_doc = get_hitl_run(run_id)
    if not run_doc:
        raise HTTPException(status_code=404, detail=f"HITL run '{run_id}' not found.")
    return run_doc


@app.post("/api/hitl/{run_id}/{gate}", tags=["hitl"], summary="Act on a checkpoint gate (approve or revise)")
async def api_hitl_gate_action(run_id: str, gate: str, req: GateActionRequest):
    """
    Approves or revises a gate checkpoint.
    Gates: scope, weights, matrix, ranking, review/verify.
    On approve, advances to next checkpoint and updates version.
    """
    try:
        if gate in ["review", "verify", "manual_verification"]:
            from backend.hitl import record_reviewer_decision
            payload = req.payload or {}
            res = record_reviewer_decision(
                candidate_id=run_id,
                decision=payload.get("decision", "cleared"),
                note=payload.get("note", "Manual reviewer verified"),
                date=payload.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                who=req.who or "reviewer",
            )
            return res

        updated_run = process_gate_action(
            run_id=run_id,
            gate=gate,
            action=req.action,
            payload=req.payload,
            who=req.who or "operator",
        )
        return updated_run
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Error processing gate '%s' on run '%s': %s", gate, run_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


class ReviewerDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(cleared|excluded)$", description="Must be 'cleared' or 'excluded'")
    note: str = Field(..., min_length=3, description="One-sentence rationale for the decision")
    date: Optional[str] = None
    who: Optional[str] = "reviewer"

ReviewerDecisionRequest.model_rebuild()


@app.post("/api/hitl/candidates/{id}/review", tags=["hitl"], summary="Submit reviewer manual verification decision")
@app.post("/api/hitl/{id}/review", tags=["hitl"], summary="Submit reviewer manual verification decision")
async def api_submit_reviewer_decision(id: str, req: Dict[str, Any]):
    """
    Submits manual reviewer verification for candidate sites (e.g. Site #8, Site #11).
    Sets decision (cleared or excluded), rationale note, date, and updates audit_log and MongoDB.
    """
    try:
        model = ReviewerDecisionRequest(**req)
        from backend.hitl import record_reviewer_decision
        result = record_reviewer_decision(
            candidate_id=id,
            decision=model.decision,
            note=model.note,
            date=model.date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            who=model.who or "reviewer",
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Error submitting reviewer decision for candidate %s: %s", id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/hitl", tags=["hitl"], summary="List recent HITL runs")
async def api_list_hitl_runs(limit: int = 10):
    """Returns recent HITL screening runs."""
    try:
        db = get_db()
        cursor = db[COLL_RUNS].find({"run_id": {"$regex": "^hitl_run_"}}, {"_id": 0}).sort("created_at", -1)
        runs = await cursor.to_list(length=limit)
        return {"count": len(runs), "runs": runs}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Interactive Search (Features 1–4)
# ---------------------------------------------------------------------------
import asyncio
from backend.search_engine import (
    execute_search,
    save_search_run,
    get_search_run,
    get_districts as _get_districts,
    size_to_footprint,
)
from backend.constraints import CONSTRAINTS_CATALOGUE


class SearchRequest(BaseModel):
    districts: Any = "all"
    size_mwp: float = Field(50.0, ge=5.0, le=200.0)
    priority: str = "default"
    custom_weights: Optional[Dict[str, float]] = None
    top_n: int = Field(10, ge=1, le=20)
    min_kv: int = Field(33, ge=33, le=110)
    max_substation_km: Optional[float] = None
    max_slope_pct: int = Field(5, ge=3, le=8)
    avoid_cropland: bool = False
    pa_buffer_km: float = Field(10.0, ge=1.0, le=10.0)
    extra_constraints: Optional[List[Dict[str, Any]]] = None
    reviewer_name: Optional[str] = "Reviewer"
    is_reevaluate: bool = False
    counts_before: Optional[int] = None

SearchRequest.model_rebuild()


@app.get("/api/constraints", tags=["search"], summary="Get constraint catalogue")
async def api_get_constraints():
    """Returns the multi-criteria constraint catalogue for interactive refinement."""
    return {"count": len(CONSTRAINTS_CATALOGUE), "constraints": CONSTRAINTS_CATALOGUE}


@app.get("/api/districts", tags=["search"], summary="Get Karnataka district names")
async def api_get_districts():
    """Returns distinct district names and simplified GeoJSON from the feature_points table."""
    try:
        districts = await asyncio.to_thread(_get_districts)
        return {"count": len(districts), "districts": districts}
    except Exception as exc:
        logger.error("Failed to get districts: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/search", tags=["search"], summary="Interactive site search")
async def api_search(req: SearchRequest):
    """
    Run an interactive search over the precomputed feature_points table.
    Supports extra Must and Prefer constraints for human-in-the-loop refinement.
    No network calls — all filtering, scoring and spacing done locally.
    """
    try:
        params = req.model_dump()
        result = await asyncio.to_thread(
            execute_search,
            districts=req.districts,
            size_mwp=req.size_mwp,
            priority=req.priority,
            custom_weights=req.custom_weights,
            top_n=req.top_n,
            min_kv=req.min_kv,
            max_substation_km=req.max_substation_km,
            max_slope_pct=req.max_slope_pct,
            avoid_cropland=req.avoid_cropland,
            pa_buffer_km=req.pa_buffer_km,
            extra_constraints=req.extra_constraints,
        )

        # Save run + audit
        build_id = None
        try:
            from backend.db import get_feature_points_col as _fp_col
            sample = _fp_col().find_one({}, {"build_id": 1, "_id": 0})
            if sample:
                build_id = sample.get("build_id")
        except Exception:
            pass

        run_id = await asyncio.to_thread(
            save_search_run,
            params,
            result,
            build_id=build_id,
            reviewer_name=req.reviewer_name,
            is_reevaluate=req.is_reevaluate,
            counts_before=req.counts_before,
        )
        result["run_id"] = run_id

        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Search failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/decision-log", tags=["search"], summary="Get constraint refinement decision log")
async def api_get_decision_log(limit: int = 50):
    """Retrieve decision log history from audit_log."""
    try:
        from backend.db import get_audit_log_col
        col = get_audit_log_col()
        cursor = col.find(
            {"action": {"$in": ["re_evaluate", "interactive_search", "constraint_refinement"]}},
            {"_id": 0}
        ).sort("timestamp", -1).limit(limit)
        entries = list(cursor)
        return {"count": len(entries), "entries": entries}
    except Exception as exc:
        logger.error("Failed to fetch decision log: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/runs/{run_id}", tags=["search"], summary="Get a search run by ID")
async def api_get_run(run_id: str):
    """Retrieve a search run by its run_id."""
    try:
        doc = await asyncio.to_thread(get_search_run, run_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
        return doc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get run %s: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


_BOUNDARY_SIMPLIFIED_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "geoprocessing", "data", "karnataka_boundary_simplified.geojson")
)
_BOUNDARY_RAW_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "geoprocessing", "data", "karnataka_boundary.geojson")
)
_cached_boundary = None


@app.get("/api/boundary", tags=["boundary"], summary="Get Karnataka state boundary GeoJSON")
async def get_karnataka_boundary():
    """
    Returns the simplified Karnataka boundary GeoJSON (tolerance ~0.001 degrees).
    """
    global _cached_boundary
    if _cached_boundary is not None:
        return _cached_boundary

    if os.path.exists(_BOUNDARY_SIMPLIFIED_PATH):
        with open(_BOUNDARY_SIMPLIFIED_PATH, "r", encoding="utf-8") as f:
            _cached_boundary = json.load(f)
            return _cached_boundary

    if os.path.exists(_BOUNDARY_RAW_PATH):
        try:
            from shapely.geometry import shape, mapping
            with open(_BOUNDARY_RAW_PATH, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            feat = raw_data["features"][0]
            sim = shape(feat["geometry"]).simplify(0.001, preserve_topology=True)
            _cached_boundary = {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": feat.get("properties", {}),
                    "geometry": mapping(sim),
                }],
            }
            return _cached_boundary
        except Exception as exc:
            logger.error("Boundary simplify failed: %s", exc)
            with open(_BOUNDARY_RAW_PATH, "r", encoding="utf-8") as f:
                _cached_boundary = json.load(f)
                return _cached_boundary

    raise HTTPException(status_code=404, detail="Karnataka boundary GeoJSON not found")




