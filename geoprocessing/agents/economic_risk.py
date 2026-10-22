"""
geoprocessing/agents/economic_risk.py
-------------------------------------
EconomicRiskAgent: Specialist agent responsible for municipal proximity land-cost
proxies and ambient temperature panel derating metrics.

Fixed Tool Allow-List:
  - landcost_proxy (wraps get_landcost_proxy)
  - temperature_derating (computes NOCT cell derating factor from ambient temperature)

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

from fetch_landcost_proxy import get_landcost_proxy
from fetch_nasa_power import fetch_climatology, NASAPowerRateLimitExceeded
from provenance import record_value


class EconomicRiskAgent:
    """Specialist agent for economic proxy modeling and panel thermal risk."""

    ALLOWED_TOOLS = ["landcost_proxy", "temperature_derating"]

    def __init__(self, name: str = "EconomicRiskAgent"):
        self.name = name

    def execute(
        self,
        lat: float,
        lon: float,
        t2m_celsius: Optional[float] = None,
        site_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Executes landcost proxy query and temperature derating calculation.
        Returns structured JSON with value, source, status, and writes provenance.
        """
        effective_site_id = site_id if site_id is not None else f"{lat:.4f}_{lon:.4f}"
        now_iso = datetime.now(timezone.utc).isoformat()
        needs_review_reasons: List[str] = []
        overall_status = "eligible"

        # 1. Land-cost proxy via municipal proximity
        raw_lc: Optional[float] = None
        lc_prov_status = "sourced"
        delta = 1.0
        bbox = f"{lat - delta},{lon - delta},{lat + delta},{lon + delta}"
        query = f"""[out:json][timeout:25];\n    (\n      node["place"="city"]({bbox});\n      node["place"="town"]({bbox});\n    );\n    out geom;"""
        lc_key = f"OVERPASS_LANDCOST: bbox={bbox} query={query.strip()}"
        lc_cached_at = now_iso
        try:
            from raw_cache import get_cache_record, hash_request
            lc_rec = get_cache_record(lc_key)
            if lc_rec and lc_rec.get("cached_at"):
                lc_cached_at = lc_rec["cached_at"]
        except Exception:
            pass

        try:
            lc_val = get_landcost_proxy(lat, lon)
            if lc_val is not None:
                raw_lc = round(float(lc_val), 4)
            else:
                lc_prov_status = "unavailable"
                needs_review_reasons.append(
                    f"No municipal town/city found within search radius for land cost at ({lat}, {lon})"
                )
        except Exception as exc:
            lc_prov_status = "unavailable"
            needs_review_reasons.append(f"Land cost query failed at ({lat}, {lon}): {exc}")
            overall_status = "needs_review"

        try:
            from raw_cache import hash_request
            lc_hash_str = f" via {hash_request(lc_key)}.json"
        except Exception:
            lc_hash_str = ""

        record_value(
            site_id=effective_site_id,
            criterion="raw_landcost",
            value=raw_lc,
            source=f"Overpass distance-to-town proxy{lc_hash_str}",
            source_key=lc_key,
            retrieved_at=lc_cached_at,
            unit="score [0-1]",
            spatial_ref="Centroid / EPSG:4326",
            status=lc_prov_status,
            origin="live_agent",
        )

        # 2. Temperature Derating
        # If t2m_celsius is provided, use it; otherwise fetch from climatology
        t2m = t2m_celsius
        temp_prov_status = "calculated"
        derating_factor: Optional[float] = None

        if t2m is None:
            try:
                clim = fetch_climatology(lat, lon)
                t2m = clim.t2m_celsius
            except NASAPowerRateLimitExceeded as exc:
                temp_prov_status = "unavailable"
                needs_review_reasons.append(f"Temperature derating NASA POWER rate limited at ({lat}, {lon}): {exc}")
                overall_status = "needs_review"
            except Exception as exc:
                temp_prov_status = "unavailable"
                needs_review_reasons.append(f"Temperature derating fetch failed at ({lat}, {lon}): {exc}")
                overall_status = "needs_review"

        if t2m is not None:
            # Physical NOCT formula: T_cell = T2M + 28.0; derating_factor = 1.0 - 0.004 * (T_cell - 25.0)
            t_cell = t2m + 28.0
            derating_factor = round(1.0 - 0.004 * (t_cell - 25.0), 4)

        return {
            "agent": self.name,
            "status": overall_status,
            "allowed_tools": self.ALLOWED_TOOLS,
            "metrics": {
                "landcost_proxy_score": {
                    "value": raw_lc,
                    "unrounded_value": float(lc_val) if lc_val is not None else None,
                    "unit": "score [0-1]",
                    "source": "District Town Bid-Rent Proximate Distance Model",
                    "status": lc_prov_status,
                },
                "computed_derating_factor": {
                    "value": derating_factor,
                    "t2m_celsius": round(t2m, 2) if t2m is not None else None,
                    "unit": "dimensionless_factor",
                    "source": "Thermal Efficiency Derating Model (NOCT +28°C, -0.4%/°C)",
                    "status": temp_prov_status,
                },
            },
            "needs_review_reasons": needs_review_reasons,
        }
