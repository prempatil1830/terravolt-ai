"""
backend/db.py — MongoDB connection and collection management.

URI is loaded from environment (.env), never hardcoded.
Collections:
  - candidates: Candidate solar PV sites across Karnataka
  - provenance: Source lineage and audit records for every criteria value
  - audit_log: System modification and event logs
  - runs: Metadata and configuration for MCDM sweep runs
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv
import pymongo
from pymongo.database import Database as SyncDatabase
from pymongo.collection import Collection as SyncCollection
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection

logger = logging.getLogger(__name__)

# Load .env from current directory or project root
load_dotenv()
if not os.getenv("MONGODB_URI"):
    root_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if os.path.exists(root_env):
        load_dotenv(root_env)

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError(
        "MONGODB_URI not found in environment or .env file. "
        "Please specify MONGODB_URI in your .env configuration."
    )

MONGODB_DB_NAME = os.getenv("MONGODB_DB", "solar_pv_karnataka")

# Collection Names
COLL_CANDIDATES = "candidates"
COLL_PROVENANCE = "provenance"
COLL_AUDIT_LOG = "audit_log"
COLL_RUNS = "runs"
COLL_FEATURE_POINTS = "feature_points"

# ---------------------------------------------------------------------------
# Synchronous (PyMongo) Client & Collections (Migrations, Geoprocessing, Tests)
# ---------------------------------------------------------------------------
_sync_client: Optional[pymongo.MongoClient] = None
_sync_db: Optional[SyncDatabase] = None


def get_sync_client() -> pymongo.MongoClient:
    global _sync_client
    if _sync_client is None:
        _sync_client = pymongo.MongoClient(MONGODB_URI)
    return _sync_client


def get_sync_db() -> SyncDatabase:
    global _sync_db
    if _sync_db is None:
        client = get_sync_client()
        _sync_db = client[MONGODB_DB_NAME]
    return _sync_db


def get_candidates_col() -> SyncCollection:
    return get_sync_db()[COLL_CANDIDATES]


def get_provenance_col() -> SyncCollection:
    return get_sync_db()[COLL_PROVENANCE]


def get_audit_log_col() -> SyncCollection:
    return get_sync_db()[COLL_AUDIT_LOG]


def get_runs_col() -> SyncCollection:
    return get_sync_db()[COLL_RUNS]


def get_feature_points_col() -> SyncCollection:
    return get_sync_db()[COLL_FEATURE_POINTS]


# ---------------------------------------------------------------------------
# Asynchronous (Motor) Client & Collections (FastAPI routes)
# ---------------------------------------------------------------------------
_async_client: Optional[AsyncIOMotorClient] = None
_async_db: Optional[AsyncIOMotorDatabase] = None


async def connect_db() -> None:
    """Initialize the asynchronous Motor client."""
    global _async_client, _async_db
    logger.info("Connecting async Motor client to MongoDB at %s...", MONGODB_URI)
    _async_client = AsyncIOMotorClient(MONGODB_URI)
    _async_db = _async_client[MONGODB_DB_NAME]
    await _ensure_indexes()
    logger.info("MongoDB async connection established. DB: %s", MONGODB_DB_NAME)


async def close_db() -> None:
    """Close the asynchronous Motor client."""
    global _async_client
    if _async_client is not None:
        _async_client.close()
        logger.info("MongoDB async connection closed.")


def get_db() -> AsyncIOMotorDatabase:
    """Return active async database for FastAPI handlers."""
    if _async_db is None:
        global _async_client
        _async_client = AsyncIOMotorClient(MONGODB_URI)
        return _async_client[MONGODB_DB_NAME]
    return _async_db


def get_async_candidates_col() -> AsyncIOMotorCollection:
    return get_db()[COLL_CANDIDATES]


def get_async_provenance_col() -> AsyncIOMotorCollection:
    return get_db()[COLL_PROVENANCE]


def get_async_audit_log_col() -> AsyncIOMotorCollection:
    return get_db()[COLL_AUDIT_LOG]


def get_async_runs_col() -> AsyncIOMotorCollection:
    return get_db()[COLL_RUNS]


def get_async_feature_points_col() -> AsyncIOMotorCollection:
    return get_db()[COLL_FEATURE_POINTS]


async def _ensure_indexes() -> None:
    """Ensure indexes exist on collections."""
    db = get_db()
    
    # Candidates index on rank and geometry
    try:
        await db[COLL_CANDIDATES].create_index("rank", unique=True, background=True)
    except Exception as exc:
        logger.debug("Candidates rank index check: %s", exc)
        
    # Provenance index on site_id and criterion
    try:
        await db[COLL_PROVENANCE].create_index([("site_id", 1), ("criterion", 1)], background=True)
        await db[COLL_PROVENANCE].create_index("site_id", background=True)
    except Exception as exc:
        logger.debug("Provenance index check: %s", exc)
