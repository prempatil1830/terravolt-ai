"""
geoprocessing/agents/solar_climate.py
-------------------------------------
SolarClimateAgent: Specialist agent responsible for solar irradiation and ambient
temperature data extraction.

Fixed Tool Allow-List:
  - fetch_nasa_power (wraps fetch_climatology)

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

from fetch_nasa_power import fetch_climatology, NASAPowerRateLimitExceeded
from provenance import record_value
from raw_cache import hash_request, get_cache_record
import urllib.parse


class SolarClimateAgent:
    """Specialist agent for solar insolation and thermal climatology data."""

    ALLOWED_TOOLS = ["fetch_nasa_power"]

    def __init__(self, name: str = "SolarClimateAgent"):
        self.name = name

    def execute(self, lat: float, lon: float, site_id: Optional[Any] = None) -> Dict[str, Any]:
        """
        Executes fetch_nasa_power for the specified lat/lon point.
        Returns structured JSON with value, source, status, and writes provenance.
        Reuses NASAPowerRateLimitExceeded as needs_review, never a fallback number.
        """
        effective_site_id = site_id if site_id is not None else f"{lat:.4f}_{lon:.4f}"
        now_iso = datetime.now(timezone.utc).isoformat()
        needs_review_reasons: List[str] = []

        raw_ghi: Optional[float] = None
        raw_temp: Optional[float] = None
        raw_temp_max: Optional[float] = None
        tool_status = "eligible"

        try:
            result = fetch_climatology(lat, lon)
            raw_ghi = round(result.ghi_kwh_m2_day, 4)
            raw_temp = round(result.t2m_celsius, 2)
            raw_temp_max = round(result.t2m_max_celsius, 2)
            prov_status = "sourced"
        except NASAPowerRateLimitExceeded as exc:
            tool_status = "needs_review"
            prov_status = "unavailable"
            needs_review_reasons.append(
                f"NASA POWER rate limit exceeded (HTTP 429) at ({lat}, {lon}): {exc}"
            )
        except Exception as exc:
            tool_status = "needs_review"
            prov_status = "unavailable"
            needs_review_reasons.append(
                f"NASA POWER API error at ({lat}, {lon}): {exc}"
            )

        # Look up raw cache metadata
        params = {
            "parameters": "ALLSKY_SFC_SW_DWN,T2M,T2M_MAX",
            "community": "RE",
            "longitude": lon,
            "latitude": lat,
            "format": "JSON",
        }
        url = "https://power.larc.nasa.gov/api/temporal/climatology/point"
        req_key = f"NASA_POWER: url={url}?{urllib.parse.urlencode(sorted(params.items()))}"
        cached_rec = get_cache_record(req_key)
        nasa_cached_at = (cached_rec.get("cached_at") if cached_rec else None) or now_iso

        # Write provenance for GHI
        record_value(
            site_id=effective_site_id,
            criterion="raw_ghi",
            value=raw_ghi,
            source=f"NASA POWER climatology, 2001-2020 (20-year) via {hash_request(req_key)}.json",
            source_key=req_key,
            retrieved_at=nasa_cached_at,
            unit="kWh/m²/day",
            spatial_ref="0.5° grid / EPSG:4326",
            status=prov_status,
            origin="live_agent",
        )

        # Write provenance for Ambient Temperature
        record_value(
            site_id=effective_site_id,
            criterion="raw_temperature",
            value=raw_temp,
            source=f"NASA POWER Climatology (T2M) via {hash_request(req_key)}.json",
            source_key=req_key,
            retrieved_at=nasa_cached_at,
            unit="°C",
            spatial_ref="0.5° grid / EPSG:4326",
            status=prov_status,
            origin="live_agent",
        )

        return {
            "agent": self.name,
            "status": tool_status,
            "allowed_tools": self.ALLOWED_TOOLS,
            "metrics": {
                "raw_ghi": {
                    "value": raw_ghi,
                    "unit": "kWh/m²/day",
                    "source": "NASA POWER climatology, 2001-2020 (20-year, per API header)",
                    "spatial_ref": "0.5° grid / EPSG:4326",
                    "status": prov_status,
                },
                "raw_temperature": {
                    "value": raw_temp,
                    "unit": "°C",
                    "source": "NASA POWER Climatology (T2M Annual Mean)",
                    "spatial_ref": "0.5° grid / EPSG:4326",
                    "status": prov_status,
                },
                "raw_temperature_max": {
                    "value": raw_temp_max,
                    "unit": "°C",
                    "source": "NASA POWER Climatology (T2M_MAX Annual Mean)",
                    "spatial_ref": "0.5° grid / EPSG:4326",
                    "status": prov_status,
                },
            },
            "needs_review_reasons": needs_review_reasons,
        }
