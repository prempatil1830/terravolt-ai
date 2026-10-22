"""
backend/constraints.py
----------------------
Constraint catalogue and evaluation engine for human-in-the-loop refinement.

Supports 13 multi-criteria constraints across 5 groups:
1. Location and access: close_to_town, near_road
2. Land: lower_land_cost, very_flat, avoid_farmland
3. Sun and climate: strong_sun, cooler_sites
4. Grid: near_transmission_line, min_grid_voltage, max_substation_distance, near_solar_plants
5. Environment: away_from_water, wider_protected_buffer

Modes:
- "must": Hard filter applied after base screening.
- "prefer": Scored criterion with importance low (5%), medium (10%), high (20%).
  Extras may not exceed 50% in total; scaled base weights by (1 - sum(extra_weights)).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

CONSTRAINTS_CATALOGUE = [
    {
        "id": "close_to_town",
        "label": "Close to city or town",
        "group": "Location and access",
        "allowed_values": [10, 25, 50],
        "direction": "<=",
        "unit": "km",
        "modes_allowed": ["must", "prefer"],
        "description": "Within 10, 25, or 50 km of an OSM city or town.",
    },
    {
        "id": "near_road",
        "label": "Near major road",
        "group": "Location and access",
        "allowed_values": [2, 5, 10],
        "direction": "<=",
        "unit": "km",
        "modes_allowed": ["must", "prefer"],
        "description": "Within 2, 5, or 10 km of a motorway, trunk, primary, or secondary highway.",
    },
    {
        "id": "lower_land_cost",
        "label": "Lower land cost",
        "group": "Land",
        "allowed_values": [50],
        "direction": ">=",
        "unit": "proxy",
        "modes_allowed": ["prefer"],
        "note": "Distance-from-towns proxy, not real market prices.",
        "description": "Prefer sites farther from towns as an economic proxy for lower land acquisition cost.",
    },
    {
        "id": "very_flat",
        "label": "Very flat terrain (≤3% slope)",
        "group": "Land",
        "allowed_values": [80],
        "direction": ">=",
        "unit": "% share",
        "modes_allowed": ["must", "prefer"],
        "description": "At least 80% of the footprint area has slope of 3% or less.",
    },
    {
        "id": "avoid_farmland",
        "label": "Avoid farmland / cropland",
        "group": "Land",
        "allowed_values": [50, 25, 0],
        "direction": "<=",
        "unit": "% cropland",
        "modes_allowed": ["must", "prefer"],
        "description": "Cap maximum cropland share within plant footprint at 50%, 25%, or 0%.",
    },
    {
        "id": "strong_sun",
        "label": "Strong solar irradiance",
        "group": "Sun and climate",
        "allowed_values": [5.3, 5.5, 5.6],
        "direction": ">=",
        "unit": "kWh/m²/d",
        "modes_allowed": ["must", "prefer"],
        "description": "Annual GHI solar irradiance of at least 5.3, 5.5, or 5.6 kWh/m²/day.",
    },
    {
        "id": "cooler_sites",
        "label": "Cooler ambient temperature",
        "group": "Sun and climate",
        "allowed_values": [24, 25, 26],
        "direction": "<=",
        "unit": "°C",
        "modes_allowed": ["must", "prefer"],
        "description": "Annual 2m ambient temperature at most 24, 25, or 26°C to minimize PV thermal derating.",
    },
    {
        "id": "near_transmission_line",
        "label": "Near transmission line (≥33 kV)",
        "group": "Grid",
        "allowed_values": [2, 5, 10],
        "direction": "<=",
        "unit": "km",
        "modes_allowed": ["must", "prefer"],
        "description": "Within 2, 5, or 10 km of a high-voltage transmission line.",
    },
    {
        "id": "min_grid_voltage",
        "label": "Minimum substation voltage",
        "group": "Grid",
        "allowed_values": [33, 66, 110],
        "direction": ">=",
        "unit": "kV",
        "modes_allowed": ["must", "prefer"],
        "description": "Interconnection substation must meet or exceed 33, 66, or 110 kV.",
    },
    {
        "id": "max_substation_distance",
        "label": "Max substation distance",
        "group": "Grid",
        "allowed_values": [5, 10, 25],
        "direction": "<=",
        "unit": "km",
        "modes_allowed": ["must", "prefer"],
        "description": "Distance to nearest qualifying substation at most 5, 10, or 25 km.",
    },
    {
        "id": "near_solar_plants",
        "label": "Near existing solar plants",
        "group": "Grid",
        "allowed_values": [10, 25, 50],
        "direction": "<=",
        "unit": "km",
        "modes_allowed": ["must", "prefer"],
        "description": "Within 10, 25, or 50 km of an operational solar power plant.",
    },
    {
        "id": "away_from_water",
        "label": "Away from water bodies",
        "group": "Environment",
        "allowed_values": [1, 2, 5],
        "direction": ">=",
        "unit": "km",
        "modes_allowed": ["must", "prefer"],
        "description": "Buffer of at least 1, 2, or 5 km from rivers, reservoirs, and water bodies.",
    },
    {
        "id": "wider_protected_buffer",
        "label": "Wider protected area buffer",
        "group": "Environment",
        "allowed_values": [1, 5, 10, 25],
        "direction": ">=",
        "unit": "km",
        "modes_allowed": ["must", "prefer"],
        "description": "Buffer of at least 1, 5, 10, or 25 km from national parks and sanctuaries.",
    },
]

CONSTRAINTS_BY_ID = {c["id"]: c for c in CONSTRAINTS_CATALOGUE}

IMPORTANCE_MAP = {
    "low": 0.05,
    "medium": 0.10,
    "high": 0.20,
}


def parse_importance_weight(importance: Any) -> Tuple[str, float]:
    """Parse importance string ('low', 'medium', 'high') or numeric weight into (name, weight)."""
    if isinstance(importance, str):
        imp_lower = importance.strip().lower()
        if imp_lower in IMPORTANCE_MAP:
            return imp_lower, IMPORTANCE_MAP[imp_lower]
        try:
            val = float(imp_lower)
            if val > 1.0:
                val /= 100.0
            return ("custom", val)
        except ValueError:
            return ("medium", 0.10)
    elif isinstance(importance, (int, float)):
        val = float(importance)
        if val > 1.0:
            val /= 100.0
        # Check closest standard
        for name, std_w in IMPORTANCE_MAP.items():
            if abs(val - std_w) < 1e-4:
                return (name, std_w)
        return ("custom", val)
    return ("medium", 0.10)


def extract_raw_constraint_value(
    pt: Dict[str, Any],
    cid: str,
    radius_key: str,
    radius_km: float,
    min_kv: int = 33,
) -> Any:
    """Extract the relevant raw feature value from a feature point."""
    radii_data = pt.get("radii", {}).get(radius_key, {})

    if cid == "close_to_town":
        return pt.get("dist_town_km")
    elif cid == "near_road":
        return pt.get("dist_road_km")
    elif cid == "lower_land_cost":
        return pt.get("dist_town_km")
    elif cid == "very_flat":
        return radii_data.get("slope_le_3pct", radii_data.get("share_slope_le_3", 0.0))
    elif cid == "avoid_farmland":
        return radii_data.get("cropland_share", pt.get("cropland_share", 0.0))
    elif cid == "strong_sun":
        return pt.get("ghi")
    elif cid == "cooler_sites":
        return pt.get("t2m")
    elif cid == "near_transmission_line":
        return pt.get("dist_line_km")
    elif cid == "min_grid_voltage":
        # Returns distance for the active voltage tier
        sub_v = pt.get("substations", {}).get(str(min_kv), pt.get(f"substation_{min_kv}kv", {}))
        return sub_v.get("distance_km") if sub_v else None
    elif cid == "max_substation_distance":
        sub_v = pt.get("substations", {}).get(str(min_kv), pt.get(f"substation_{min_kv}kv", {}))
        return sub_v.get("distance_km") if sub_v else None
    elif cid == "near_solar_plants":
        return pt.get("dist_solar_plant_km")
    elif cid == "away_from_water":
        return pt.get("dist_water_body_km")
    elif cid == "wider_protected_buffer":
        dist_pa = pt.get("dist_protected_area_km")
        if dist_pa is None:
            return 999.0  # infinite distance from protected area
        return round(dist_pa - radius_km, 3)
    return None


def satisfies_must_constraint(
    raw_val: Any,
    cid: str,
    limit: float,
    pt: Dict[str, Any],
    radius_km: float,
) -> bool:
    """Check if raw_val satisfies the Must filter."""
    if cid in ("close_to_town", "near_road", "near_transmission_line", "near_solar_plants"):
        return raw_val is not None and float(raw_val) <= limit + 1e-6
    elif cid == "lower_land_cost":
        # Prefer only per specification
        return True
    elif cid == "very_flat":
        thresh = limit / 100.0 if limit > 1.0 else limit
        return raw_val is not None and float(raw_val) >= thresh - 1e-6
    elif cid == "avoid_farmland":
        thresh = limit / 100.0 if limit > 1.0 else limit
        return raw_val is not None and float(raw_val) <= thresh + 1e-6
    elif cid == "strong_sun":
        return raw_val is not None and float(raw_val) >= limit - 1e-6
    elif cid == "cooler_sites":
        return raw_val is not None and float(raw_val) <= limit + 1e-6
    elif cid == "min_grid_voltage":
        v_target = int(limit)
        sub_v = pt.get("substations", {}).get(str(v_target), pt.get(f"substation_{v_target}kv", {}))
        dist_v = sub_v.get("distance_km") if sub_v else None
        return dist_v is not None and dist_v <= 60.0
    elif cid == "max_substation_distance":
        return raw_val is not None and float(raw_val) <= limit + 1e-6
    elif cid == "away_from_water":
        return raw_val is not None and float(raw_val) >= limit - 1e-6
    elif cid == "wider_protected_buffer":
        dist_pa = pt.get("dist_protected_area_km")
        if dist_pa is None:
            return True
        buffer_val = dist_pa - radius_km
        return buffer_val >= limit - 1e-6
    return True


def normalize_prefer_criterion(
    raw_val: Any,
    cid: str,
    limit: float,
    pt: Dict[str, Any],
    radius_km: float,
) -> float:
    """
    Normalize criterion to 0 to 1 according to specification:
    - closeness = 1 minus distance over the chosen limit, clamped: clamp(1 - dist/limit, 0, 1)
    - sunshine = (GHI minus 5.0) over 1.0: clamp((ghi - 5.0)/1.0, 0, 1)
    - temperature = (30 minus T) over 10: clamp((30 - T)/10, 0, 1)
    - farmland = 1 minus cropland share: clamp(1 - cropland_share, 0, 1)
    - water = distance over 10 km, capped at 1: clamp(dist/10.0, 0, 1)
    - lower_land_cost = proxy farther from towns: clamp(dist / 50.0, 0, 1)
    - very_flat = slope share <= 3%: clamp(share, 0, 1)
    - wider_protected_buffer = buffer / 25.0: clamp(buffer / 25.0, 0, 1)
    - min_grid_voltage = 1.0 if qualified else 0.0
    """
    if raw_val is None:
        if cid == "wider_protected_buffer":
            return 1.0
        return 0.0

    val = float(raw_val)

    if cid in ("close_to_town", "near_road", "near_transmission_line", "near_solar_plants", "max_substation_distance"):
        lim = float(limit) if limit > 0 else 1.0
        closeness = 1.0 - (val / lim)
        return max(0.0, min(1.0, closeness))

    elif cid == "lower_land_cost":
        # proxy = farther from towns (raw dist up to 50 km)
        return max(0.0, min(1.0, val / 50.0))

    elif cid == "very_flat":
        return max(0.0, min(1.0, val))

    elif cid == "avoid_farmland":
        # farmland = 1 minus cropland share
        return max(0.0, min(1.0, 1.0 - val))

    elif cid == "strong_sun":
        # sunshine = (GHI minus 5.0) over 1.0
        return max(0.0, min(1.0, (val - 5.0) / 1.0))

    elif cid == "cooler_sites":
        # temperature = (30 minus T) over 10
        return max(0.0, min(1.0, (30.0 - val) / 10.0))

    elif cid == "away_from_water":
        # water = distance over 10 km, capped at 1
        return max(0.0, min(1.0, val / 10.0))

    elif cid == "wider_protected_buffer":
        dist_pa = pt.get("dist_protected_area_km")
        if dist_pa is None:
            return 1.0
        buf = dist_pa - radius_km
        return max(0.0, min(1.0, buf / 25.0))

    elif cid == "min_grid_voltage":
        v_target = int(limit)
        sub_v = pt.get("substations", {}).get(str(v_target), pt.get(f"substation_{v_target}kv", {}))
        dist_v = sub_v.get("distance_km") if sub_v else None
        return 1.0 if (dist_v is not None and dist_v <= 60.0) else 0.0

    return 0.0
