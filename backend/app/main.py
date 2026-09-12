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

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

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
    title="Solar PV Karnataka — API",
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
