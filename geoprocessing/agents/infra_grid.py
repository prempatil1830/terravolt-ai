"""
geoprocessing/agents/infra_grid.py
----------------------------------
InfraGridAgent: Specialist agent responsible for electrical interconnection distances
to qualifying substations (>=33 kV) and transmission line corridors.

Fixed Tool Allow-List:
  - substation_distance (wraps get_grid_connection_info)
  - transmission_distance (wraps get_grid_connection_info)

Strict Constraint:
  NO access to ahp_engine, score_candidate, or any scoring code.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import os
import sys

_GEO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GEO_DIR not in sys.path:
    sys.path.insert(0, _GEO_DIR)

from fetch_substation_distance import get_grid_connection_info, SubstationDataUnavailable
from provenance import record_value


class InfraGridAgent:
    """Specialist agent for grid substation and transmission infrastructure proximity."""

    ALLOWED_TOOLS = ["substation_distance", "transmission_distance"]

    def __init__(self, name: str = "InfraGridAgent"):
        self.name = name

    def execute(self, lat: float, lon: float, site_id: Optional[Any] = None) -> Dict[str, Any]:
        """
        Executes grid infrastructure checks for the candidate point.
        Returns structured JSON with value, source, status, and writes provenance.
        Reuses SubstationDataUnavailable as needs_review, never a fallback number.
        """
        effective_site_id = site_id if site_id is not None else f"{lat:.4f}_{lon:.4f}"
        now_iso = datetime.now(timezone.utc).isoformat()
        needs_review_reasons: List[str] = []
        overall_status = "eligible"

        sub_dist_km: Optional[float] = None
        tx_dist_km: Optional[float] = None
        nearest_sub_name: Optional[str] = None
        nearest_sub_voltage: Optional[str] = None
        nearest_gen_dist_km: Optional[float] = None
        low_confidence: bool = False
        sub_prov_status = "sourced"

        nearest_sub_osm_id: Optional[str] = None
        nearest_sub_operator: Optional[str] = None
        nearest_sub_voltage_tag: Optional[str] = None
        tx_osm_id: Optional[str] = None
        closer_untagged: Optional[Dict[str, Any]] = None

        try:
            grid_info = get_grid_connection_info(lat, lon)
            sub_dist_km = grid_info.substation_dist_km
            tx_dist_km = grid_info.transmission_line_dist_km
            nearest_sub_osm_id = getattr(grid_info, "substation_osm_id", None)
            nearest_sub_name = getattr(grid_info, "substation_name", None)
            nearest_sub_operator = getattr(grid_info, "substation_operator", None)
            nearest_sub_voltage_tag = getattr(grid_info, "substation_voltage_tag", None)
            nearest_sub_voltage = getattr(grid_info, "substation_voltage_v", None)
            tx_osm_id = getattr(grid_info, "transmission_line_osm_id", None)
            nearest_gen_dist_km = getattr(grid_info, "nearest_generation_substation_dist_km", None)
            low_confidence = getattr(grid_info, "low_confidence", False)

            if getattr(grid_info, "closer_untagged_osm_id", None):
                closer_untagged = {
                    "osm_id": grid_info.closer_untagged_osm_id,
                    "lat": grid_info.closer_untagged_lat,
                    "lon": grid_info.closer_untagged_lon,
                    "distance_km": grid_info.closer_untagged_dist_km,
                    "tags": grid_info.closer_untagged_tags,
                }

            if sub_dist_km is None:
                # No qualifying >=33kV substation found within 50 km
                overall_status = "needs_review"
                sub_prov_status = "unavailable"
                needs_review_reasons.append(
                    f"No qualifying >=33kV substation data retrieved within 50 km at ({lat}, {lon}) — grid interconnection requires verification"
                )
            elif low_confidence:
                reason = getattr(grid_info, "confidence_reason", None)
                if reason:
                    needs_review_reasons.append(reason)
                overall_status = "needs_review"

            # Voltage-unknown primary check
            sub_v_tag = getattr(grid_info, "substation_voltage_tag", None)
            if sub_dist_km is not None and (sub_v_tag is None or str(sub_v_tag).strip().lower() in ["none", "nan", "", "no voltage tag"]):
                overall_status = "needs_review"
                needs_review_reasons.append(
                    "Primary substation voltage not tagged in OSM; >=33 kV unconfirmed"
                )

        except SubstationDataUnavailable as exc:
            overall_status = "needs_review"
            sub_prov_status = "unavailable"
            sub_dist_km = None
            needs_review_reasons.append(
                f"Substation data unavailable: Overpass API timed out or unreachable at ({lat}, {lon}): {exc}"
            )
        except Exception as exc:
            overall_status = "needs_review"
            sub_prov_status = "unavailable"
            sub_dist_km = None
            needs_review_reasons.append(
                f"Grid query encountered unexpected error at ({lat}, {lon}): {exc}"
            )

        # Look up tile request and timestamp from raw_cache
        grid_req = None
        grid_cached_at = now_iso
        tile_fname = "bulk_tile.json"
        try:
            import math, glob, json
            from raw_cache import CACHE_DIR
            s = math.floor(lat * 2) / 2
            w = math.floor(lon * 2) / 2
            n = s + 0.5
            e = w + 0.5
            grid_bbox = f"{s:.1f},{w:.1f},{n:.1f},{e:.1f}"
            for fpath in glob.glob(os.path.join(CACHE_DIR, "*.json")):
                with open(fpath, "r", encoding="utf-8") as fp:
                    d = json.load(fp)
                    req = d.get("request", "")
                    if "OVERPASS_GRID_TILE" in req and f"bbox={grid_bbox}" in req:
                        grid_req = req
                        grid_cached_at = d.get("cached_at", now_iso)
                        tile_fname = os.path.basename(fpath)
                        break
        except Exception:
            pass

        # Write provenance for Substation distance
        stored_sub_dist = round(sub_dist_km, 2) if sub_dist_km is not None else None
        sub_cite = f"substations_karnataka.geojson (osm_id: {nearest_sub_osm_id}, distance: {stored_sub_dist} km)"
        record_value(
            site_id=effective_site_id,
            criterion="raw_substation",
            value=stored_sub_dist,
            source=sub_cite,
            source_key="geoprocessing/data/substations_karnataka.geojson",
            retrieved_at=grid_cached_at,
            unit="km",
            spatial_ref="Centroid / EPSG:4326",
            status=sub_prov_status,
            origin="local_dataset",
            lat=lat,
            lon=lon,
        )

        return {
            "agent": self.name,
            "status": overall_status,
            "allowed_tools": self.ALLOWED_TOOLS,
            "metrics": {
                "substation_dist_km": {
                    "value": sub_dist_km,
                    "unit": "km",
                    "source": "OpenStreetMap Verified Transmission & Substation Infrastructure (≥33 kV)",
                    "nearest_substation_osm_id": nearest_sub_osm_id,
                    "nearest_substation_name": nearest_sub_name,
                    "nearest_substation_operator": nearest_sub_operator,
                    "nearest_substation_voltage_tag": nearest_sub_voltage_tag,
                    "nearest_substation_voltage": nearest_sub_voltage,
                    "status": sub_prov_status,
                },
                "transmission_line_dist_km": {
                    "value": tx_dist_km,
                    "unit": "km",
                    "source": "OpenStreetMap Power Lines (≥66 kV corridors)",
                    "osm_id": tx_osm_id,
                    "status": "sourced" if tx_dist_km is not None else "unavailable",
                },
                "nearest_generation_substation_dist_km": {
                    "value": nearest_gen_dist_km,
                    "unit": "km",
                    "source": "OpenStreetMap Generation/Captive Substation (Context Only)",
                    "status": "sourced" if nearest_gen_dist_km is not None else "unavailable",
                },
            },
            "closer_untagged": closer_untagged,
            "low_confidence": low_confidence,
            "needs_review_reasons": needs_review_reasons,
        }
