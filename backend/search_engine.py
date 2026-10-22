"""
backend/search_engine.py
-------------------------
Interactive search engine over the precomputed feature_points table.
No network calls at query time — all data comes from MongoDB feature_points.

Features:
  1. GET /api/districts  — names and simplified GeoJSON
  2. POST /api/search    — full filtering + scoring + greedy spacing
  3. Size-to-footprint   — acres = MWp x 4.5, pick smallest precomputed radius
  4. Run logging + audit — every search saved to 'runs' + 'audit_log'
"""

from __future__ import annotations

import os
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.db import (
    get_feature_points_col,
    get_runs_col,
    get_audit_log_col,
)
from backend.constraints import (
    CONSTRAINTS_CATALOGUE,
    CONSTRAINTS_BY_ID,
    parse_importance_weight,
    extract_raw_constraint_value,
    satisfies_must_constraint,
    normalize_prefer_criterion,
)

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────
PRECOMPUTED_RADII_KM = [0.25, 0.40, 0.54, 0.70, 0.85, 1.00, 1.10]

PRIORITY_PRESETS = {
    "default":      {"ghi": 0.40, "substation": 0.40, "landcost": 0.10, "temp_derating": 0.10},
    "balanced":     {"ghi": 0.40, "substation": 0.40, "landcost": 0.10, "temp_derating": 0.10},
    "solar_first":  {"ghi": 0.60, "substation": 0.20, "landcost": 0.10, "temp_derating": 0.10},
    "solar":        {"ghi": 0.60, "substation": 0.20, "landcost": 0.10, "temp_derating": 0.10},
    "grid_first":   {"ghi": 0.20, "substation": 0.60, "landcost": 0.10, "temp_derating": 0.10},
    "grid":         {"ghi": 0.20, "substation": 0.60, "landcost": 0.10, "temp_derating": 0.10},
}

WEIGHTED_CRITERIA = ["ghi", "substation", "landcost", "temp_derating"]

MIN_SPACING_KM = 20.0


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km."""
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def size_to_footprint(size_mwp: float) -> Dict[str, Any]:
    """
    Convert MWp capacity to footprint radius and pick the smallest
    precomputed radius that covers it.

    acres = MWp × 4.5
    area_m2 = acres × 4046.8564224
    radius_km = sqrt(area_m2 / π) / 1000
    """
    acres = size_mwp * 4.5
    area_m2 = acres * 4046.8564224
    required_radius_km = math.sqrt(area_m2 / math.pi) / 1000.0

    # Pick smallest precomputed radius >= required
    used_radius_km = None
    for r in PRECOMPUTED_RADII_KM:
        if r >= required_radius_km - 1e-6:
            used_radius_km = r
            break
    if used_radius_km is None:
        used_radius_km = PRECOMPUTED_RADII_KM[-1]  # cap at largest

    used_acres = math.pi * (used_radius_km * 1000.0) ** 2 / 4046.8564224

    return {
        "size_mwp": size_mwp,
        "required_acres": round(acres, 2),
        "required_radius_km": round(required_radius_km, 4),
        "used_radius_km": used_radius_km,
        "used_acres": round(used_acres, 2),
        "radius_key": f"{used_radius_km:.2f}",
    }


def validate_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Validate custom weights: must sum to 1.0, landcost <= 10%."""
    total = sum(weights.get(k, 0.0) for k in WEIGHTED_CRITERIA)
    if abs(total - 1.0) > 0.005:
        raise ValueError(
            f"Weights must sum to 1.0 (got {total:.4f}). "
            f"Provided: {weights}"
        )
    if weights.get("landcost", 0.0) > 0.10 + 1e-6:
        raise ValueError(
            f"Land-cost proxy weight cannot exceed 10% "
            f"(got {weights['landcost'] * 100:.1f}%)"
        )
    return {k: weights.get(k, 0.0) for k in WEIGHTED_CRITERIA}


def resolve_weights(priority: str, custom_weights: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Resolve weight preset or validate custom weights."""
    if priority == "custom":
        if custom_weights is None:
            raise ValueError("Custom weights required when priority='custom'")
        return validate_weights(custom_weights)
    if priority in PRIORITY_PRESETS:
        return PRIORITY_PRESETS[priority]
    raise ValueError(f"Unknown priority preset '{priority}'. Use: {list(PRIORITY_PRESETS.keys())} or 'custom'")


# ──────────────────────────────────────────────────────────────────────
# Normalization (mirrors score_candidate.py)
# ──────────────────────────────────────────────────────────────────────

def _normalize_ghi(ghi: Optional[float], min_ghi: float = 5.0, max_ghi: float = 6.0) -> float:
    if ghi is None:
        return 0.0
    return float(min(max((ghi - min_ghi) / (max_ghi - min_ghi), 0.0), 1.0))


def _normalize_substation(dist_km: Optional[float], max_dist_km: float = 50.0) -> float:
    if dist_km is None:
        return 0.0
    return float(1.0 - min(max(dist_km / max_dist_km, 0.0), 1.0))


def _normalize_landcost(proxy: Optional[float]) -> float:
    if proxy is None:
        return 0.5
    return float(min(max(proxy, 0.0), 1.0))


def _normalize_temp_derating(t2m: Optional[float], min_temp: float = 20.0, max_temp: float = 30.0) -> float:
    if t2m is None:
        return 0.5
    return float(min(max((max_temp - t2m) / (max_temp - min_temp), 0.0), 1.0))


# ──────────────────────────────────────────────────────────────────────
# Main search
# ──────────────────────────────────────────────────────────────────────

def execute_search(
    districts: Any = "all",
    size_mwp: float = 50.0,
    priority: str = "default",
    custom_weights: Optional[Dict[str, float]] = None,
    top_n: int = 10,
    min_kv: int = 33,
    max_substation_km: Optional[float] = None,  # None = "any"
    max_slope_pct: int = 5,
    avoid_cropland: bool = False,
    pa_buffer_km: float = 10.0,
    extra_constraints: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Execute a search against the precomputed feature_points table.
    Supports extra Must and Prefer constraints for human-in-the-loop refinement.

    Returns: {footprint, weights, scaled_base_weights, extra_constraints, funnel, sites, warnings}
    """
    # 1. Resolve footprint and weights
    footprint = size_to_footprint(size_mwp)
    radius_key = footprint["radius_key"]
    radius_km = footprint["used_radius_km"]

    weights = resolve_weights(priority, custom_weights)

    # 1b. Extra constraints parsing & validation
    extras = extra_constraints or []
    must_constraints = [c for c in extras if str(c.get("mode", "must")).lower() == "must"]
    prefer_constraints = [c for c in extras if str(c.get("mode", "must")).lower() == "prefer"]

    total_extra_weight = 0.0
    prefer_items = []
    for pc in prefer_constraints:
        cid = pc.get("id")
        val = float(pc.get("value", 0))
        imp_name, weight = parse_importance_weight(pc.get("importance", "medium"))
        total_extra_weight += weight
        prefer_items.append({
            "id": cid,
            "value": val,
            "importance": imp_name,
            "weight": weight,
        })

    if total_extra_weight > 0.50 + 1e-6:
        raise ValueError(
            f"Extras may not exceed 50% in total (got {round(total_extra_weight * 100, 1)}%)."
        )

    # Scale base weights by (1 - sum(extra_weights))
    scale_factor = 1.0 - total_extra_weight
    scaled_base_weights = {k: round(v * scale_factor, 4) for k, v in weights.items()}

    # 2. Load feature points from MongoDB
    col = get_feature_points_col()

    query: Dict[str, Any] = {}
    if districts != "all" and isinstance(districts, list) and len(districts) > 0:
        query["district"] = {"$in": districts}

    all_points = list(col.find(query, {"_id": 0}))

    funnel = {
        "total_considered": len(all_points),
        "filtered_by_district": 0,
        "filtered_by_boundary": 0,
        "filtered_by_slope": 0,
        "filtered_by_landuse": 0,
        "filtered_by_protected_area": 0,
        "filtered_by_water": 0,
        "filtered_by_substation_distance": 0,
        "filtered_by_substation_voltage": 0,
        "base_survivors": 0,
        "must_constraints": [],
        "filtered_by_must": 0,
        "survivors": 0,
    }

    slope_key = f"slope_le_{max_slope_pct}pct"
    if max_slope_pct == 3:
        slope_field = "share_slope_le_3"
    elif max_slope_pct == 5:
        slope_field = "share_slope_le_5"
    elif max_slope_pct == 8:
        slope_field = "share_slope_le_8"
    else:
        slope_field = "share_slope_le_5"

    lu_field = "share_cropland_excluded" if avoid_cropland else "share_cropland_allowed"

    # 3. Base screening
    base_survivors = []
    for pt in all_points:
        radii_data = pt.get("radii", {}).get(radius_key, {})
        if not radii_data:
            continue

        # 3a. Boundary: disk >= 90% inside Karnataka
        ka_frac = radii_data.get("disk_karnataka_fraction", radii_data.get("karnataka_fraction", 0.0))
        if ka_frac < 0.90:
            funnel["filtered_by_boundary"] += 1
            continue

        # 3b. Slope: share >= 80%
        slope_share = radii_data.get(slope_field, radii_data.get(slope_key, 0.0))
        if slope_share < 0.80:
            funnel["filtered_by_slope"] += 1
            continue

        # 3c. Land-use: share >= 80%
        if lu_field == "share_cropland_excluded":
            lu_share = radii_data.get("share_cropland_excluded", radii_data.get("lu_cropland_excluded", 0.0))
        else:
            lu_share = radii_data.get("share_cropland_allowed", radii_data.get("lu_cropland_allowed", 0.0))
        if lu_share < 0.80:
            funnel["filtered_by_landuse"] += 1
            continue

        # 3d. Protected area: distance - radius >= buffer
        dist_pa_km = pt.get("dist_protected_area_km")
        if dist_pa_km is not None:
            if (dist_pa_km - radius_km) < pa_buffer_km:
                funnel["filtered_by_protected_area"] += 1
                continue

        # 3e. Water: distance - radius >= 0.5 km
        dist_water_km = pt.get("dist_water_body_km")
        if dist_water_km is not None:
            if (dist_water_km - radius_km) < 0.5:
                funnel["filtered_by_water"] += 1
                continue

        # 3e2. Canals/streams: only flagged if within radius
        dist_canal_km = pt.get("dist_canal_stream_km")
        if dist_canal_km is not None:
            if dist_canal_km <= radius_km:
                funnel["filtered_by_water"] += 1
                continue

        # 3f. Substation: pick the right voltage tier
        sub_key = str(min_kv)
        sub_data = pt.get("substations", {}).get(sub_key, pt.get(f"substation_{min_kv}kv", {}))
        sub_dist_km = sub_data.get("distance_km") if sub_data else None

        if max_substation_km is not None and max_substation_km > 0:
            if sub_dist_km is None or sub_dist_km > max_substation_km:
                funnel["filtered_by_substation_distance"] += 1
                continue

        # Survivor — compute normalized base criteria and status
        status = "eligible"
        needs_review_reasons = []

        if sub_data:
            if sub_data.get("untagged_closer_than_confirmed"):
                status = "needs_review"
                needs_review_reasons.append(
                    "Untagged substation closer than confirmed >=33kV primary"
                )
            if sub_data.get("voltage_tag_missing"):
                status = "needs_review"
                needs_review_reasons.append(
                    "Primary substation voltage not tagged in OSM; >=33 kV unconfirmed"
                )
        if sub_dist_km is None:
            status = "needs_review"
            needs_review_reasons.append(
                "No substation data available for this voltage tier"
            )

        if pt.get("known_gap_review"):
            status = "needs_review"
            needs_review_reasons.extend(pt.get("known_gap_reasons", []))

        ghi = pt.get("ghi")
        t2m = pt.get("t2m")
        landcost_proxy = pt.get("landcost_proxy")

        norm_ghi = _normalize_ghi(ghi)
        norm_sub = _normalize_substation(sub_dist_km)
        norm_lc = _normalize_landcost(landcost_proxy)
        norm_temp = _normalize_temp_derating(t2m)

        base_survivors.append({
            "_pt": pt,
            "norm_ghi": round(norm_ghi, 4),
            "norm_sub": round(norm_sub, 4),
            "norm_lc": round(norm_lc, 4),
            "norm_temp": round(norm_temp, 4),
            "sub_dist_km": sub_dist_km,
            "sub_data": sub_data,
            "status": status,
            "needs_review_reasons": needs_review_reasons,
            "slope_share": slope_share,
            "lu_share": lu_share,
            "ka_frac": ka_frac,
        })

    funnel["base_survivors"] = len(base_survivors)

    # 4. Must constraints filtering
    current_survivors = base_survivors
    must_funnel = []
    for mc in must_constraints:
        cid = mc["id"]
        val = float(mc["value"])
        c_label = CONSTRAINTS_BY_ID.get(cid, {}).get("label", cid)
        count_before = len(current_survivors)
        next_survivors = []
        for s in current_survivors:
            pt = s["_pt"]
            raw_v = extract_raw_constraint_value(pt, cid, radius_key, radius_km, min_kv=min_kv)
            if satisfies_must_constraint(raw_v, cid, val, pt, radius_km):
                next_survivors.append(s)
        current_survivors = next_survivors
        removed = count_before - len(current_survivors)
        must_funnel.append({
            "id": cid,
            "label": c_label,
            "value": val,
            "count_before": count_before,
            "removed": removed,
            "remaining": len(current_survivors),
        })

    funnel["must_constraints"] = must_funnel
    funnel["filtered_by_must"] = sum(item["removed"] for item in must_funnel)
    funnel["survivors"] = len(current_survivors)

    # 5. Score survivors with base weights scaled + prefer criteria
    for s in current_survivors:
        pt = s["_pt"]
        base_score = (
            scaled_base_weights["ghi"] * s["norm_ghi"]
            + scaled_base_weights["substation"] * s["norm_sub"]
            + scaled_base_weights["landcost"] * s["norm_lc"]
            + scaled_base_weights["temp_derating"] * s["norm_temp"]
        )

        extra_score = 0.0
        extra_details = {}

        for pc in prefer_items:
            cid = pc["id"]
            val = pc["value"]
            w = pc["weight"]
            raw_v = extract_raw_constraint_value(pt, cid, radius_key, radius_km, min_kv=min_kv)
            norm_v = normalize_prefer_criterion(raw_v, cid, val, pt, radius_km)
            contrib = round(w * norm_v, 4)
            extra_score += contrib
            extra_details[cid] = {
                "id": cid,
                "label": CONSTRAINTS_BY_ID.get(cid, {}).get("label", cid),
                "mode": "prefer",
                "limit": val,
                "raw_value": raw_v,
                "normalized": round(norm_v, 4),
                "importance": pc["importance"],
                "weight": w,
                "contribution": contrib,
                "satisfied": True,
            }

        for mc in must_constraints:
            cid = mc["id"]
            val = float(mc["value"])
            raw_v = extract_raw_constraint_value(pt, cid, radius_key, radius_km, min_kv=min_kv)
            extra_details[cid] = {
                "id": cid,
                "label": CONSTRAINTS_BY_ID.get(cid, {}).get("label", cid),
                "mode": "must",
                "limit": val,
                "raw_value": raw_v,
                "satisfied": True,
                "contribution": 0.0,
            }

        final_score = round(base_score + extra_score, 4)
        s["score"] = final_score
        s["score_100"] = round(final_score * 100, 2)
        s["extra_constraints"] = extra_details

    # 6. Sort by score descending
    current_survivors.sort(key=lambda s: (-s["score"], s["_pt"]["lat"], s["_pt"]["lon"]))

    # 7. Greedy spacing: pick top_n with >= 20 km between sites
    selected = []
    for s in current_survivors:
        lat = s["_pt"]["lat"]
        lon = s["_pt"]["lon"]
        too_close = False
        for sel in selected:
            if _haversine_km(lat, lon, sel["_pt"]["lat"], sel["_pt"]["lon"]) < MIN_SPACING_KM:
                too_close = True
                break
        if not too_close:
            selected.append(s)
            if len(selected) >= top_n:
                break

    # 8. Build result sites
    sites = []
    for rank, s in enumerate(selected, 1):
        pt = s["_pt"]
        sub_data = s.get("sub_data") or {}
        radii_data = pt.get("radii", {}).get(radius_key, {})

        exact_reasons = []
        if pt.get("ghi") is not None:
            exact_reasons.append(f"GHI: {pt['ghi']} kWh/m²/day")
        if s["sub_dist_km"] is not None:
            exact_reasons.append(f"Primary substation distance: {s['sub_dist_km']} km")
        mean_slope = radii_data.get("slope_le_5pct", radii_data.get("share_slope_le_5", None))
        if mean_slope is not None:
            exact_reasons.append(f"Slope usable (<=5%): {mean_slope*100:.1f}%")
        if pt.get("t2m") is not None:
            exact_reasons.append(f"Ambient temperature (T2m): {pt['t2m']}°C")

        site = {
            "rank": rank,
            "latitude": pt["lat"],
            "longitude": pt["lon"],
            "district": pt.get("district"),
            "status": s["status"],
            "score": s["score"],
            "score_100": s["score_100"],
            "exclusion_reason": None,
            "needs_review": s["status"] == "needs_review",
            "needs_review_reasons": s["needs_review_reasons"],
            "tripped_constraints": [],
            "raw_criteria": {
                "ghi_kwh_m2_day": pt.get("ghi"),
                "substation_dist_km": s["sub_dist_km"],
                "substation_osm_id": sub_data.get("osm_id"),
                "substation_name": sub_data.get("name"),
                "substation_operator": sub_data.get("operator"),
                "substation_voltage_tag": sub_data.get("voltage_tag"),
                "transmission_line_dist_km": pt.get("dist_line_km"),
                "landcost_proxy_score": pt.get("landcost_proxy"),
                "t2m_celsius": pt.get("t2m"),
            },
            "substation_details": {
                "osm_id": sub_data.get("osm_id"),
                "name": sub_data.get("name"),
                "operator": sub_data.get("operator"),
                "voltage_tag": sub_data.get("voltage_tag"),
                "distance_km": s["sub_dist_km"],
            },
            "normalized_criteria": {
                "ghi": s["norm_ghi"],
                "substation": s["norm_sub"],
                "landcost": s["norm_lc"],
                "temp_derating": s["norm_temp"],
            },
            "weights": weights,
            "scaled_base_weights": scaled_base_weights,
            "extra_constraints": s["extra_constraints"],
            "constraints": {
                "boundary": {
                    "passed": True,
                    "disk_fraction": s["ka_frac"],
                    "threshold": ">= 90% disk inside boundary",
                    "tripped": False,
                },
                "slope": {
                    "mode": "feature_table",
                    "f_slope_usable": s["slope_share"],
                    "tripped": False,
                    "threshold": f">= 80% disk slope <= {max_slope_pct}%",
                },
                "landuse": {
                    "mode": "feature_table",
                    "f_lu_usable": s["lu_share"],
                    "tripped": False,
                    "threshold": ">= 80% disk eligible land-use",
                },
                "protected_buffer": {
                    "status": "eligible",
                    "tripped": False,
                },
            },
            "exact_reasons": exact_reasons,
            "footprint": {
                "used_radius_km": footprint["used_radius_km"],
                "used_acres": footprint["used_acres"],
            },
        }

        # Flat helper keys for UI tables
        for cid, info in s["extra_constraints"].items():
            site[f"extra_{cid}_val"] = info.get("raw_value")
            site[f"extra_{cid}_contrib"] = info.get("contribution", 0.0)
            site[f"extra_{cid}_norm"] = info.get("normalized")

        sites.append(site)

    # 9. Warnings
    warnings = []
    if len(selected) < top_n:
        warnings.append(
            f"Only {len(selected)} sites found (requested {top_n}). "
            f"Try relaxing filters or reducing top_n."
        )

    filter_counts = {
        "boundary": funnel["filtered_by_boundary"],
        "slope": funnel["filtered_by_slope"],
        "landuse": funnel["filtered_by_landuse"],
        "protected_area": funnel["filtered_by_protected_area"],
        "water": funnel["filtered_by_water"],
        "substation_distance": funnel["filtered_by_substation_distance"],
        "must_constraints": funnel["filtered_by_must"],
    }
    if filter_counts:
        max_filter = max(filter_counts, key=filter_counts.get)
        max_count = filter_counts[max_filter]
        if max_count > 0:
            warnings.append(
                f"Most restrictive filter: '{max_filter}' removed {max_count} points"
            )

    return {
        "footprint": footprint,
        "weights": weights,
        "scaled_base_weights": scaled_base_weights,
        "extra_weights_total": round(total_extra_weight, 4),
        "extra_constraints": extras,
        "funnel": funnel,
        "sites": sites,
        "warnings": warnings,
        "site_count": len(sites),
    }


# ──────────────────────────────────────────────────────────────────────
# Run logging
# ──────────────────────────────────────────────────────────────────────

def save_search_run(
    params: Dict[str, Any],
    result: Dict[str, Any],
    build_id: Optional[str] = None,
    reviewer_name: Optional[str] = None,
    is_reevaluate: bool = False,
    counts_before: Optional[int] = None,
) -> str:
    """Save search run to MongoDB runs + audit_log. Returns run_id."""
    run_id = f"search_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    reviewer = reviewer_name or params.get("reviewer_name") or "Reviewer"
    action = "re_evaluate" if (is_reevaluate or params.get("is_reevaluate")) else "interactive_search"
    site_count = result.get("site_count", len(result.get("sites", [])))
    cnt_before = counts_before if counts_before is not None else params.get("counts_before", site_count)

    run_doc = {
        "run_id": run_id,
        "type": "interactive_search",
        "action": action,
        "created_at": now,
        "reviewer_name": reviewer,
        "parameters": params,
        "feature_table_build_id": build_id,
        "footprint": result.get("footprint"),
        "weights": result.get("weights"),
        "scaled_base_weights": result.get("scaled_base_weights"),
        "extra_constraints": result.get("extra_constraints"),
        "funnel": result.get("funnel"),
        "warnings": result.get("warnings"),
        "site_count": site_count,
        "sites": result.get("sites"),
    }

    runs_col = get_runs_col()
    runs_col.replace_one({"run_id": run_id}, run_doc, upsert=True)

    audit_col = get_audit_log_col()
    audit_col.insert_one({
        "timestamp": now,
        "action": action,
        "run_id": run_id,
        "reviewer_name": reviewer,
        "reviewer": reviewer,
        "constraints": params.get("extra_constraints", []),
        "counts_before": cnt_before,
        "counts_after": site_count,
        "survivors_before": result.get("funnel", {}).get("base_survivors", result.get("funnel", {}).get("survivors", 0)),
        "survivors_after": result.get("funnel", {}).get("survivors", 0),
        "site_count": site_count,
        "funnel_survivors": result.get("funnel", {}).get("survivors", 0),
        "parameters": params,
    })

    return run_id


def get_search_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a search run by run_id."""
    runs_col = get_runs_col()
    doc = runs_col.find_one({"run_id": run_id}, {"_id": 0})
    return doc


# ──────────────────────────────────────────────────────────────────────
# Districts
# ──────────────────────────────────────────────────────────────────────

_DISTRICTS_CACHE: Optional[List[Dict[str, Any]]] = None

def get_districts() -> List[Dict[str, Any]]:
    """Get distinct district names and simplified GeoJSON boundaries."""
    global _DISTRICTS_CACHE
    if _DISTRICTS_CACHE is not None:
        return _DISTRICTS_CACHE

    geojson_path = os.path.join(
        os.path.dirname(__file__), "..", "geoprocessing", "data", "karnataka_districts.geojson"
    )

    geom_by_district: Dict[str, Any] = {}
    if os.path.exists(geojson_path):
        try:
            import shapely.geometry
            with open(geojson_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for feat in data.get("features", []):
                name = feat.get("properties", {}).get("district")
                geom = feat.get("geometry")
                if name and geom:
                    poly = shapely.geometry.shape(geom)
                    simplified = poly.simplify(0.01)
                    geom_by_district[name] = shapely.geometry.mapping(simplified)
        except Exception:
            pass

    col = get_feature_points_col()
    names = sorted(col.distinct("district"))
    result = []
    for n in names:
        if n:
            item: Dict[str, Any] = {"name": n}
            if n in geom_by_district:
                item["geojson"] = geom_by_district[n]
            result.append(item)

    _DISTRICTS_CACHE = result
    return result

