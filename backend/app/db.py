"""
db.py — MongoDB connection and collection/index setup.

This module owns the single Motor (async) client for the application.
It creates the `candidate_sites` collection and its 2dsphere index on
startup so the API is ready to serve geo queries immediately.

The 2dsphere index enables:
  - $near / $geoNear (nearest candidate sites to a point)
  - $geoWithin (sites within a polygon / bounding box)
  - $geoIntersects (sites intersecting a geometry)

NOTE: Raw geospatial computation does NOT happen here.
      See app/geoprocessing/processor.py for that.
"""

import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import GEOSPHERE
from app.config import settings

logger = logging.getLogger(__name__)

# Module-level client — initialised once at startup, closed at shutdown.
_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None

CANDIDATE_SITES_COLLECTION = "candidate_sites"


async def connect_db() -> None:
    """Open the Motor client and ensure indexes exist."""
    global _client, _db

    logger.info("Connecting to MongoDB at %s …", settings.mongodb_uri)
    _client = AsyncIOMotorClient(settings.mongodb_uri)
    _db = _client[settings.mongodb_db]

    await _ensure_indexes()
    logger.info("MongoDB connection established. DB: %s", settings.mongodb_db)


async def close_db() -> None:
    """Close the Motor client gracefully."""
    global _client
    if _client is not None:
        _client.close()
        logger.info("MongoDB connection closed.")


def get_db() -> AsyncIOMotorDatabase:
    """Return the active database handle (for use in FastAPI dependencies)."""
    if _db is None:
        raise RuntimeError("Database not initialised. Call connect_db() first.")
    return _db


async def _ensure_indexes() -> None:
    """
    Create the 2dsphere index on candidate_sites.geometry if it does not
    already exist.

    Document schema (enforced by application code, not by MongoDB):
    {
      "_id": ObjectId,
      "name": str,
      "geometry": {                  ← GeoJSON geometry (Point or Polygon)
        "type": "Point" | "Polygon",
        "coordinates": [...]
      },
      "area_ha": float,
      "ahp_score": float,            ← composite AHP suitability score 0–1
      "criterion_scores": { ... },   ← per-criterion scores
      "nearest_grid_kv": int,        ← kV of nearest transmission line
      "data_vintage": str,           ← ISO date string of source data
      "created_at": datetime
    }
    """
    collection = _db[CANDIDATE_SITES_COLLECTION]

    existing = await collection.index_information()
    index_name = "geometry_2dsphere"

    if index_name not in existing:
        await collection.create_index(
            [("geometry", GEOSPHERE)],
            name=index_name,
            background=True,
        )
        logger.info(
            "Created 2dsphere index '%s' on collection '%s'.",
            index_name,
            CANDIDATE_SITES_COLLECTION,
        )
    else:
        logger.info(
            "2dsphere index '%s' already exists on '%s'. Skipping.",
            index_name,
            CANDIDATE_SITES_COLLECTION,
        )
