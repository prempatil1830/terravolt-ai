"""
geoprocessing/agents/planner.py
-------------------------------
PlannerAgent: Orchestrator that takes a lat/lon coordinate, dispatches specialist agents,
assembles the candidate-by-criterion decision matrix, and applies deterministic AHP scoring.

Orchestration is plain Python (no LLM in the loop) guaranteeing 100% reproducible execution.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import os
import sys

_GEO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GEO_DIR not in sys.path:
    sys.path.insert(0, _GEO_DIR)

from agents.solar_climate import SolarClimateAgent
from agents.land_environment import LandEnvironmentAgent
from agents.infra_grid import InfraGridAgent
from agents.economic_risk import EconomicRiskAgent

from ahp_engine import compute_ahp_weights, WEIGHTED_CRITERIA
from score_candidate import (
    normalize_ghi,
    normalize_substation,
    normalize_landcost,
    normalize_temp_derating,
)
from provenance import record_value

DEFAULT_WEIGHTS: Dict[str, float] = {
    "ghi": 0.40,
    "substation": 0.40,
    "landcost": 0.10,
    "temp_derating": 0.10,
}


class PlannerAgent:
    """Deterministic orchestrator for Solar PV candidate evaluation."""

    def __init__(self, name: str = "PlannerAgent", footprint_radius_km: Optional[float] = 0.54):
        self.name = name
        self.footprint_radius_km = footprint_radius_km
        self.solar_agent = SolarClimateAgent()
        self.land_agent = LandEnvironmentAgent()
        self.grid_agent = InfraGridAgent()
        self.economic_agent = EconomicRiskAgent()

    def dispatch_specialists(
        self,
        lat: float,
        lon: float,
        site_id: Optional[Any] = None,
        footprint_radius_km: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Dispatches all four specialist agents deterministically in plain Python.
        Returns their raw structured outputs.
        """
        effective_site_id = site_id if site_id is not None else f"{lat:.4f}_{lon:.4f}"
        effective_fp = footprint_radius_km if footprint_radius_km is not None else self.footprint_radius_km

        # 1. Solar & Climate Agent
        solar_out = self.solar_agent.execute(lat, lon, site_id=effective_site_id)

        # 2. Land & Environmental Agent (calls evaluate_footprint_zonal with shared final rules)
        land_out = self.land_agent.execute(lat, lon, site_id=effective_site_id, footprint_radius_km=effective_fp)

        # 3. Infrastructure & Grid Agent
        grid_out = self.grid_agent.execute(lat, lon, site_id=effective_site_id)

        # 4. Economic & Risk Agent (passes ambient temp from Solar agent if available)
        raw_temp = solar_out.get("metrics", {}).get("raw_temperature", {}).get("value")
        econ_out = self.economic_agent.execute(
            lat, lon, t2m_celsius=raw_temp, site_id=effective_site_id
        )

        return {
            "solar_climate": solar_out,
            "land_environment": land_out,
            "infra_grid": grid_out,
            "economic_risk": econ_out,
        }

    def assemble_decision_matrix(
        self,
        lat: float,
        lon: float,
        specialist_outputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Combines specialist agent outputs into the structured candidate-by-criterion matrix
        and evaluates hard constraint feasibility.
        """
        solar = specialist_outputs["solar_climate"]
        land = specialist_outputs["land_environment"]
        grid = specialist_outputs["infra_grid"]
        econ = specialist_outputs["economic_risk"]

        solar_metrics = solar.get("metrics", {})
        land_metrics = land.get("metrics", {})
        grid_metrics = grid.get("metrics", {})
        econ_metrics = econ.get("metrics", {})

        raw_ghi = solar_metrics.get("raw_ghi", {}).get("value")
        raw_temp = solar_metrics.get("raw_temperature", {}).get("value")
        slope_metric = land_metrics.get("slope", {})
        landuse_metric = land_metrics.get("landuse", {})
        protected_metric = land_metrics.get("protected_buffer", {})
        substation_metric = grid_metrics.get("substation_dist_km", {})
        transmission_metric = grid_metrics.get("transmission_line_dist_km", {})
        gen_metric = grid_metrics.get("nearest_generation_substation_dist_km", {})
        landcost_metric = econ_metrics.get("landcost_proxy_score", {})
        derating_metric = econ_metrics.get("computed_derating_factor", {})

        # Collect needs_review reasons
        needs_review_reasons: List[str] = []
        needs_review_reasons.extend(solar.get("needs_review_reasons", []))
        needs_review_reasons.extend(land.get("needs_review_reasons", []))
        needs_review_reasons.extend(grid.get("needs_review_reasons", []))
        needs_review_reasons.extend(econ.get("needs_review_reasons", []))

        # Check hard constraints
        slope_tripped = slope_metric.get("tripped", False)
        landuse_tripped = landuse_metric.get("tripped", False)
        buffer_tripped = protected_metric.get("tripped", False)

        exclusion_reasons: List[str] = []
        if slope_tripped:
            exclusion_reasons.append(f"Smoothed slope usable fraction is below 80% threshold")
        if landuse_tripped:
            exclusion_reasons.append(f"Land-use usable fraction is below 80% threshold")
        if buffer_tripped:
            exclusion_reasons.append("Location falls within 10 km PA or 500 m water buffer")

        if exclusion_reasons:
            status = "excluded"
        elif needs_review_reasons:
            status = "needs_review"
        else:
            status = "eligible"

        sub_dist_val = round(substation_metric["value"], 2) if substation_metric.get("value") is not None else None
        tx_dist_val = round(transmission_metric["value"], 2) if transmission_metric.get("value") is not None else None
        gen_dist_val = round(gen_metric["value"], 2) if gen_metric.get("value") is not None else None

        sub_name = substation_metric.get("nearest_substation_name") or "unnamed"
        sub_op = substation_metric.get("nearest_substation_operator") or "unnamed"
        sub_vtag = substation_metric.get("nearest_substation_voltage_tag") or "no voltage tag"

        raw_criteria = {
            "ghi_kwh_m2_day": raw_ghi,
            "substation_dist_km": sub_dist_val,
            "_substation_dist_raw": substation_metric.get("value"),
            "substation_osm_id": substation_metric.get("nearest_substation_osm_id"),
            "substation_name": sub_name,
            "substation_operator": sub_op,
            "substation_voltage_tag": sub_vtag,
            "substation_voltage_v": substation_metric.get("nearest_substation_voltage"),
            "transmission_line_dist_km": tx_dist_val,
            "transmission_line_osm_id": transmission_metric.get("osm_id"),
            "nearest_generation_substation_dist_km": gen_dist_val,
            "nearest generation substation (context only)": gen_dist_val,
            "generation_substation_osm_id": gen_metric.get("osm_id"),
            "generation_substation_name": gen_metric.get("name"),
            "landcost_proxy_score": landcost_metric.get("value"),
            "_lc_raw": landcost_metric.get("unrounded_value"),
            "t2m_celsius": raw_temp,
            "computed_derating_factor": derating_metric.get("value"),
        }

        closer_untagged = grid.get("closer_untagged")
        if closer_untagged:
            raw_criteria["closer_untagged_osm_id"] = closer_untagged.get("osm_id")
            raw_criteria["closer_untagged_lat"] = closer_untagged.get("lat")
            raw_criteria["closer_untagged_lon"] = closer_untagged.get("lon")
            raw_criteria["closer_untagged_dist_km"] = closer_untagged.get("distance_km")
            raw_criteria["closer_untagged_tags"] = closer_untagged.get("tags")

        substation_details = {
            "osm_id": substation_metric.get("nearest_substation_osm_id"),
            "name": sub_name,
            "operator": sub_op,
            "voltage_tag": sub_vtag,
            "voltage_v": substation_metric.get("nearest_substation_voltage"),
            "distance_km": sub_dist_val,
            "closer_untagged": closer_untagged,
        }

        return {
            "status": status,
            "exclusion_reasons": exclusion_reasons,
            "needs_review_reasons": needs_review_reasons,
            "constraints": {
                "slope": slope_metric,
                "landuse": landuse_metric,
                "protected_buffer": protected_metric,
            },
            "raw_criteria": raw_criteria,
            "substation_details": substation_details,
        }

    def score_candidate(
        self,
        matrix_data: Dict[str, Any],
        weights: Optional[Dict[str, float]] = None,
        site_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Applies deterministic normalization and AHP synthesis to the decision matrix.
        Writes normalized criteria and composite score to provenance.
        """
        effective_site_id = site_id if site_id is not None else "evaluated_candidate"
        now_iso = datetime.now(timezone.utc).isoformat()
        w = dict(weights) if weights else dict(DEFAULT_WEIGHTS)

        # Normalize weights so they sum to 1.0
        total_w = sum(w.values())
        if total_w > 0:
            w = {k: v / total_w for k, v in w.items()}

        raw = matrix_data["raw_criteria"]
        status = matrix_data["status"]

        # Normalize criteria [0, 1]
        norm_ghi = normalize_ghi(raw["ghi_kwh_m2_day"]) if raw["ghi_kwh_m2_day"] is not None else 0.0
        sub_dist_for_norm = raw.get("_substation_dist_raw") if raw.get("_substation_dist_raw") is not None else raw.get("substation_dist_km")
        norm_sub = normalize_substation(sub_dist_for_norm)
        lc_for_norm = raw.get("_lc_raw") if raw.get("_lc_raw") is not None else raw.get("landcost_proxy_score")
        norm_lc = normalize_landcost(lc_for_norm)
        norm_temp = (
            normalize_temp_derating(raw["t2m_celsius"])
            if raw["t2m_celsius"] is not None
            else 0.5
        )

        normalized = {
            "ghi": round(norm_ghi, 4),
            "substation": round(norm_sub, 4),
            "landcost": round(norm_lc, 4),
            "temp_derating": round(norm_temp, 4),
        }

        # Write normalized provenance records
        record_value(
            site_id=effective_site_id,
            criterion="ghi",
            value=normalized["ghi"],
            source="Min-Max Linear Normalization [5.0, 6.0] (Benefit)",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated",
            origin="live_agent",
        )
        record_value(
            site_id=effective_site_id,
            criterion="substation",
            value=normalized["substation"],
            source="Linear Inverted Distance Normalization [0, 50 km] (Cost)",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated",
            origin="live_agent",
        )
        record_value(
            site_id=effective_site_id,
            criterion="landcost",
            value=normalized["landcost"],
            source="Land Cost Proxy Inversion Normalization",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated",
            origin="live_agent",
        )
        record_value(
            site_id=effective_site_id,
            criterion="temp_derating",
            value=normalized["temp_derating"],
            source="Thermal Efficiency Derating Inversion [20, 30°C] (Cost)",
            retrieved_at=now_iso,
            unit="dimensionless",
            spatial_ref="Centroid / EPSG:4326",
            status="calculated",
            origin="live_agent",
        )

        score: Optional[float] = None
        score_100: Optional[float] = None

        if status != "excluded":
            composite = (
                w.get("ghi", 0.4) * norm_ghi
                + w.get("substation", 0.4) * norm_sub
                + w.get("landcost", 0.1) * norm_lc
                + w.get("temp_derating", 0.1) * norm_temp
            )
            score = round(composite, 4)
            score_100 = round(composite * 100.0, 2)

            # Record final composite score
            record_value(
                site_id=effective_site_id,
                criterion="score",
                value=score,
                source="AHP Multi-Criteria Decision Analysis Weighted Synthesis",
                retrieved_at=now_iso,
                unit="dimensionless",
                spatial_ref="Composite",
                status="calculated",
                origin="live_agent",
            )

        return {
            "status": status,
            "score": score,
            "score_100": score_100,
            "weights": w,
            "normalized_criteria": normalized,
        }

    def run(
        self,
        lat: float,
        lon: float,
        site_id: Optional[Any] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Complete end-to-end execution:
        1. Dispatch specialist agents
        2. Assemble decision matrix
        3. Deterministic scoring
        """
        effective_site_id = site_id if site_id is not None else f"{lat:.4f}_{lon:.4f}"
        specialist_outputs = self.dispatch_specialists(lat, lon, site_id=effective_site_id)
        matrix_data = self.assemble_decision_matrix(lat, lon, specialist_outputs)
        scoring_data = self.score_candidate(matrix_data, weights=weights, site_id=effective_site_id)

        return {
            "site_id": effective_site_id,
            "latitude": lat,
            "longitude": lon,
            "status": scoring_data["status"],
            "score": scoring_data["score"],
            "score_100": scoring_data["score_100"],
            "weights": scoring_data["weights"],
            "normalized_criteria": scoring_data["normalized_criteria"],
            "raw_criteria": matrix_data["raw_criteria"],
            "constraints": matrix_data["constraints"],
            "exclusion_reasons": matrix_data["exclusion_reasons"],
            "needs_review_reasons": matrix_data["needs_review_reasons"],
            "specialist_outputs": specialist_outputs,
        }
