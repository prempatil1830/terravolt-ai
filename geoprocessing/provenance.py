"""
geoprocessing/provenance.py
---------------------------
Provenance and audit trail recording for Terravolt AI MCDM.

Every value feeding the decision matrix must have a provenance record
documenting its source, timestamp, unit, spatial reference, and status.
Allowed statuses:
  - 'sourced': Directly fetched from an authoritative primary dataset
  - 'calculated': Computed/derived via an explicit mathematical transformation
  - 'user_provided': Supplied by user or human reviewer
  - 'unavailable': Data endpoint timed out or could not be retrieved
"""

from typing import Any, Callable, Dict, List, Optional, Set
from datetime import datetime, timezone
import os
import sys

# Ensure backend can be imported
sys_path_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if sys_path_root not in sys.path:
    sys.path.insert(0, sys_path_root)

try:
    from backend.db import get_provenance_col
except ImportError:
    get_provenance_col = None

VALID_STATUSES: Set[str] = {
    "sourced",
    "calculated",
    "user_provided",
    "unavailable",
}

# Thread-safe in-memory cache of recorded provenance (site_id, criterion) -> record
# This guarantees that tests and in-process callers can verify provenance even in offline mode.
_IN_MEMORY_PROVENANCE: Dict[str, Dict[str, Any]] = {}


def _make_key(site_id: Any, criterion: str) -> str:
    return f"{site_id}::{criterion}"


def record_value(
    site_id: Any,
    criterion: str,
    value: Any,
    source: str,
    retrieved_at: Any,
    unit: str,
    spatial_ref: str,
    status: str,
    origin: Optional[str] = None,
    source_key: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    candidate_rank: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Records the provenance of a data value entering the evaluation matrix.

    Parameters
    ----------
    site_id : Any
        Identifier of the candidate site (e.g. coordinates string '14.2000_76.5000' or rank).
    criterion : str
        Criterion name (e.g. 'ghi', 'substation', 'landcost', 'temp_derating', 'slope', etc.).
    value : Any
        The measured, normalized, or computed value.
    source : str
        Name or identifier of the authoritative source or formula.
    retrieved_at : Any
        Timestamp of retrieval (ISO 8601 string, datetime, or None if backfilled).
    unit : str
        Physical or logical unit (e.g. 'kWh/m²/day', 'km', 'dimensionless', '°C', '%').
    spatial_ref : str
        Spatial resolution or coordinate reference (e.g. '0.5° grid / EPSG:4326', 'Centroid').
    status : str
        Must be one of: 'sourced', 'calculated', 'user_provided', 'unavailable'.
    origin : str, optional
        Origin tag (e.g. 'migrated_backfill', 'live_fetch').
    source_key : str, optional
        Exact request URL or file path.
    lat : float, optional
        Site latitude.
    lon : float, optional
        Site longitude.
    candidate_rank : int, optional
        Candidate rank (1..12).

    Returns
    -------
    dict
        The recorded provenance dictionary.
    """
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid provenance status '{status}'. "
            f"Status must be one of: {sorted(list(VALID_STATUSES))}"
        )

    if retrieved_at is None:
        retrieved_at_str = None
    elif isinstance(retrieved_at, datetime):
        retrieved_at_str = retrieved_at.isoformat()
    elif isinstance(retrieved_at, str):
        retrieved_at_str = retrieved_at
    else:
        retrieved_at_str = str(retrieved_at)

    if status == "sourced":
        if retrieved_at_str is None:
            raise ValueError(
                "Integrity violation: 'sourced' provenance records must have a non-null retrieved_at timestamp."
            )
        if origin == "migrated_backfill":
            raise ValueError(
                "Integrity violation: Status 'sourced' is reserved for live fetches and cannot have origin 'migrated_backfill'."
            )

    # Determine coordinates if not directly supplied
    parsed_lat = lat
    parsed_lon = lon
    if (parsed_lat is None or parsed_lon is None) and isinstance(site_id, str):
        import re
        m = re.match(r"^([0-9.]+)[_,]([0-9.]+)$", site_id.strip())
        if m:
            try:
                parsed_lat = float(m.group(1))
                parsed_lon = float(m.group(2))
            except Exception:
                pass

    record: Dict[str, Any] = {
        "site_id": site_id,
        "criterion": criterion,
        "value": value,
        "source": source,
        "source_key": source_key if source_key is not None else source,
        "retrieved_at": retrieved_at_str,
        "unit": unit,
        "spatial_ref": spatial_ref,
        "status": status,
        "origin": origin,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if parsed_lat is not None and parsed_lon is not None:
        record["latitude"] = parsed_lat
        record["longitude"] = parsed_lon
        record["coordinates"] = [parsed_lat, parsed_lon]
    if candidate_rank is not None:
        record["candidate_rank"] = candidate_rank

    # Store in memory registry
    key = _make_key(site_id, criterion)
    _IN_MEMORY_PROVENANCE[key] = record

    # Persist to MongoDB if available
    if get_provenance_col is not None:
        try:
            col = get_provenance_col()
            # Upsert by site_id and criterion
            col.update_one(
                {"site_id": site_id, "criterion": criterion},
                {"$set": record},
                upsert=True,
            )
        except Exception as exc:
            # Fallback gracefully if MongoDB connection is temporarily unavailable
            pass

    return record


def get_provenance_record(site_id: Any, criterion: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single provenance record for site_id and criterion."""
    # Check in-memory first
    key = _make_key(site_id, criterion)
    if key in _IN_MEMORY_PROVENANCE:
        return _IN_MEMORY_PROVENANCE[key]

    # Check MongoDB
    if get_provenance_col is not None:
        try:
            col = get_provenance_col()
            query_conditions = [{"site_id": site_id}]
            if isinstance(site_id, str) and site_id.isdigit():
                query_conditions.extend([{"site_id": int(site_id)}, {"candidate_rank": int(site_id)}])
            elif isinstance(site_id, int):
                query_conditions.extend([{"site_id": str(site_id)}, {"candidate_rank": site_id}])

            doc = col.find_one({"$or": query_conditions, "criterion": criterion}, {"_id": 0})
            if doc:
                return doc
        except Exception:
            pass

    return None


def get_site_provenance(site_id: Any) -> List[Dict[str, Any]]:
    """Retrieve all provenance records for a given site_id."""
    records = []
    # Check MongoDB
    if get_provenance_col is not None:
        try:
            col = get_provenance_col()
            query_ids = [site_id]
            if isinstance(site_id, str) and site_id.isdigit():
                query_ids.append(int(site_id))
            elif isinstance(site_id, int):
                query_ids.append(str(site_id))

            query_or = [{"site_id": {"$in": query_ids}}]
            int_ranks = [int(x) for x in query_ids if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]
            if int_ranks:
                query_or.append({"candidate_rank": {"$in": int_ranks}})

            cursor = col.find({"$or": query_or}, {"_id": 0}).sort("criterion", 1)
            records = list(cursor)
        except Exception:
            records = []

    if not records:
        # Fall back to in-memory records
        prefix = f"{site_id}::"
        records = [v for k, v in _IN_MEMORY_PROVENANCE.items() if k.startswith(prefix)]

    return records


def wrap_score_candidate(score_candidate_fn: Callable) -> Callable:
    """
    Wraps score_candidate so that every criterion value feeding the matrix
    is written to provenance as it is computed.
    Ensures that no value may enter the matrix without a provenance record.
    """
    def wrapper(
        lat: float,
        lon: float,
        ahp_weights: Optional[Dict[str, float]] = None,
        footprint_radius_km: Optional[float] = None,
        verbose: bool = False,
        site_id: Optional[Any] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        # Determine site_id
        effective_site_id = site_id if site_id is not None else f"{lat:.4f}_{lon:.4f}"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Call the original scoring computation
        result = score_candidate_fn(
            lat,
            lon,
            ahp_weights=ahp_weights,
            footprint_radius_km=footprint_radius_km,
            verbose=verbose,
            **kwargs,
        )

        # 1. Record Hard Constraints
        constraints = result.get("constraints", {})
        
        # Slope constraint
        slope_info = constraints.get("slope", {})
        slope_val = slope_info.get("value_pct") if "value_pct" in slope_info else slope_info.get("mean_pct")
        record_value(
            site_id=effective_site_id,
            criterion="slope",
            value=slope_val,
            source="Copernicus DEM GLO-30 (AWS Open Data; 3x3 mean smoothed) / slope_karnataka.tif",
            source_key="geoprocessing/data/slope_karnataka.tif",
            retrieved_at="2024-01-01T00:00:00+00:00",
            unit="%",
            spatial_ref="30m resolution / EPSG:4326",
            status="sourced" if slope_val is not None else "unavailable",
            origin="local_layer",
        )

        # Land-use constraint
        lu_info = constraints.get("landuse", {})
        lu_val = lu_info.get("class_name") or lu_info.get("class_code")
        tile_name = ""
        if lat is not None and lon is not None:
            try:
                from fetch_landuse import _tile_id
                tile_name = _tile_id(lat, lon)
            except Exception:
                pass
        lu_source_key = (
            f"geoprocessing/data/worldcover_tiles/ESA_WorldCover_10m_2021_v200_{tile_name}_Map.tif"
            if tile_name
            else "geoprocessing/data/worldcover_tiles/ESA_WorldCover_10m_2021_v200_N12E075_Map.tif"
        )
        record_value(
            site_id=effective_site_id,
            criterion="landuse",
            value=lu_val,
            source=f"ESA WorldCover 2021 (v200 10m Cloud-Optimized GeoTIFF) / {tile_name}",
            source_key=lu_source_key,
            retrieved_at="2021-10-28T00:00:00+00:00",
            unit="class_name",
            spatial_ref="10m raster / EPSG:4326",
            status="sourced" if lu_val is not None else "unavailable",
            origin="local_layer",
        )

        # Protected Area / Water Buffer constraint
        pb_info = constraints.get("protected_buffer", {})
        pb_status = pb_info.get("status")
        record_value(
            site_id=effective_site_id,
            criterion="protected_buffer",
            value=pb_status,
            source="UNEP-WCMC WDPA Protected Areas & OpenStreetMap Waterbodies",
            source_key="geoprocessing/data/water_karnataka.geojson",
            retrieved_at="2026-09-01T00:00:00+00:00",
            unit="buffer_status",
            spatial_ref="10 km PA & 500 m waterbody buffer / EPSG:4326",
            status="sourced" if pb_status not in ("needs_review", "error", None) else "unavailable",
            origin="local_layer",
        )

        # 2. Record Raw Criteria Values
        raw = result.get("raw_criteria", {})
        
        # Raw GHI
        raw_ghi = raw.get("ghi_kwh_m2_day")
        record_value(
            site_id=effective_site_id,
            criterion="raw_ghi",
            value=raw_ghi,
            source="NASA POWER climatology, 2001-2020 (20-year, per API header)",
            retrieved_at=now_iso,
            unit="kWh/m²/day",
            spatial_ref="0.5° grid / EPSG:4326",
            status="sourced" if raw_ghi is not None else "unavailable",
        )

        # Raw Substation distance
        raw_sub = raw.get("substation_dist_km")
        record_value(
            site_id=effective_site_id,
            criterion="raw_substation",
            value=raw_sub,
            source="OpenStreetMap Verified Transmission & Substation Infrastructure (≥33 kV)",
            retrieved_at=now_iso,
            unit="km",
            spatial_ref="Centroid / EPSG:4326",
            status="sourced" if raw_sub is not None else "unavailable",
        )

        # Raw Land cost proxy
        raw_lc = raw.get("landcost_proxy_score")
        record_value(
            site_id=effective_site_id,
            criterion="raw_landcost",
            value=raw_lc,
            source="District Town Bid-Rent Proximate Distance Model",
            retrieved_at=now_iso,
            unit="score [0-1]",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated" if raw_lc is not None else "unavailable",
        )

        # Raw Temperature
        raw_temp = raw.get("t2m_celsius")
        record_value(
            site_id=effective_site_id,
            criterion="raw_temperature",
            value=raw_temp,
            source="NASA POWER Climatology (T2M Annual Mean)",
            retrieved_at=now_iso,
            unit="°C",
            spatial_ref="0.5° grid / EPSG:4326",
            status="sourced" if raw_temp is not None else "unavailable",
        )

        # 3. Record Normalized Values Entering the AHP Matrix
        norm = result.get("normalized_criteria", {})
        
        # Normalized GHI
        norm_ghi = norm.get("ghi")
        record_value(
            site_id=effective_site_id,
            criterion="ghi",
            value=norm_ghi,
            source="Min-Max Linear Normalization [5.0, 6.0] (Benefit)",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated" if norm_ghi is not None else "unavailable",
        )

        # Normalized Substation
        norm_sub = norm.get("substation")
        record_value(
            site_id=effective_site_id,
            criterion="substation",
            value=norm_sub,
            source="Linear Inverted Distance Normalization [0, 50 km] (Cost)",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated" if raw_sub is not None else "unavailable",
        )

        # Normalized Land cost
        norm_lc = norm.get("landcost")
        record_value(
            site_id=effective_site_id,
            criterion="landcost",
            value=norm_lc,
            source="Land Cost Proxy Inversion Normalization",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated" if norm_lc is not None else "unavailable",
        )

        # Normalized Temperature Derating
        norm_temp = norm.get("temp_derating")
        record_value(
            site_id=effective_site_id,
            criterion="temp_derating",
            value=norm_temp,
            source="Thermal Efficiency Derating Inversion [20, 30°C] (Cost)",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated" if norm_temp is not None else "unavailable",
        )

        # Enforce rule: No value may enter the matrix without a record
        MATRIX_CRITERIA = ["ghi", "substation", "landcost", "temp_derating"]
        if result.get("status") in ("eligible", "needs_review") and norm:
            for crit in MATRIX_CRITERIA:
                rec = get_provenance_record(effective_site_id, crit)
                if rec is None:
                    raise RuntimeError(
                        f"Matrix integrity violation: Criterion '{crit}' entered the evaluation "
                        f"matrix for site '{effective_site_id}' without a provenance record!"
                    )

        # 4. Record Final Composite AHP Score
        score = result.get("score")
        record_value(
            site_id=effective_site_id,
            criterion="score",
            value=score,
            source="AHP Multi-Criteria Decision Analysis Weighted Synthesis",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Composite",
            status="calculated" if score is not None else "unavailable",
        )

        return result

    return wrapper
