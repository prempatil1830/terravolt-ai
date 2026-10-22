"""
geoprocessing/agents/land_environment.py
-----------------------------------------
LandEnvironmentAgent: Specialist agent responsible for terrain slope, land-use/cover
suitability, and protected area / water body setbacks.

Fixed Tool Allow-List:
  - slope (wraps get_slope)
  - landuse (wraps get_suitability_full)
  - protected_buffer (wraps get_buffer_status / check_wdpa_buffer / check_water_buffer)

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

from fetch_slope import get_slope
from fetch_landuse import get_suitability_full, _tile_id
from fetch_protected_buffer import get_buffer_status, WaterBufferDataUnavailable, check_known_gaps_review
from zonal_footprint import evaluate_footprint_zonal, FOOTPRINT_RADIUS_KM
from provenance import record_value


class LandEnvironmentAgent:
    """Specialist agent for terrain slope, land-use classification, and environmental setbacks."""

    ALLOWED_TOOLS = ["slope", "landuse", "protected_buffer"]

    def __init__(self, name: str = "LandEnvironmentAgent"):
        self.name = name

    def execute(
        self,
        lat: float,
        lon: float,
        site_id: Optional[Any] = None,
        footprint_radius_km: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Executes slope, landuse, and protected_buffer tools for the candidate point using
        the shared final rules (zonal footprint when specified, smoothed slope, local water layer).
        Returns structured JSON with value, source, status, and writes provenance.
        """
        effective_site_id = site_id if site_id is not None else f"{lat:.4f}_{lon:.4f}"
        now_iso = datetime.now(timezone.utc).isoformat()
        needs_review_reasons: List[str] = []
        overall_status = "eligible"

        use_footprint = (footprint_radius_km is not None and footprint_radius_km > 0.0)
        effective_radius_km = footprint_radius_km if use_footprint else None

        # 0. Zonal footprint evaluation (smoothed DEM slope and WorldCover land-use over disk)
        fp_res = None
        if use_footprint:
            try:
                fp_res = evaluate_footprint_zonal(lat, lon, radius_km=effective_radius_km, min_usable_fraction=0.80)
            except Exception:
                pass

        # 1. Slope Check (smoothed slope over 0.54 km disk)
        slope_val: Optional[float] = None
        slope_prov_status = "sourced"
        slope_tripped = False
        try:
            if fp_res is not None:
                slope_val = fp_res["mean_slope_pct"]
                slope_tripped = not fp_res["slope_passed"]
            else:
                slope_val = round(float(get_slope(lat, lon)), 2)
                slope_tripped = (slope_val > 5.0)

            if slope_tripped:
                overall_status = "excluded"
        except Exception as exc:
            slope_prov_status = "unavailable"
            needs_review_reasons.append(f"Slope computation failed at ({lat}, {lon}): {exc}")
            if overall_status != "excluded":
                overall_status = "needs_review"

        record_value(
            site_id=effective_site_id,
            criterion="slope",
            value=slope_val,
            source="Copernicus DEM GLO-30 (AWS Open Data; 3x3 mean smoothed) / slope_karnataka.tif",
            source_key="geoprocessing/data/slope_karnataka.tif",
            retrieved_at="2024-01-01T00:00:00+00:00",
            unit="%",
            spatial_ref="30m smoothed / 0.54 km footprint",
            status=slope_prov_status,
            origin="local_layer",
        )

        # 2. Land-use Check (zonal non-forest/non-urban fraction over 0.54 km disk)
        lu_class_name: Optional[str] = None
        lu_class_code: Optional[int] = None
        lu_suitability: Optional[str] = None
        lu_prov_status = "sourced"
        lu_tripped = False
        try:
            lu_res = get_suitability_full(lat, lon)
            lu_class_name = lu_res.class_name
            lu_class_code = lu_res.class_code

            if fp_res is not None:
                lu_tripped = not fp_res["lu_passed"]
                lu_suitability = "excluded" if lu_tripped else lu_res.suitability
            else:
                lu_suitability = lu_res.suitability
                lu_tripped = (lu_suitability == "excluded")

            if lu_tripped:
                overall_status = "excluded"
        except Exception as exc:
            lu_prov_status = "unavailable"
            needs_review_reasons.append(f"Land-use check failed at ({lat}, {lon}): {exc}")
            if overall_status != "excluded":
                overall_status = "needs_review"

        tile_name = _tile_id(lat, lon)
        record_value(
            site_id=effective_site_id,
            criterion="landuse",
            value=lu_class_name,
            source=f"ESA WorldCover 2021 (v200 10m Cloud-Optimized GeoTIFF) / {tile_name}",
            source_key=f"geoprocessing/data/worldcover_tiles/ESA_WorldCover_10m_2021_v200_{tile_name}_Map.tif",
            retrieved_at="2021-10-28T00:00:00+00:00",
            unit="class_name",
            spatial_ref="10m raster / 0.54 km footprint",
            status=lu_prov_status,
            origin="local_layer",
        )

        # 3. Protected Buffer Check (WDPA 10 km & Water Body 500 m on 0.54 km disk edge)
        pb_status: Optional[str] = None
        pb_prov_status = "sourced"
        try:
            pb_status = get_buffer_status(lat, lon, footprint_radius_km=FOOTPRINT_RADIUS_KM)
            if pb_status == "excluded":
                overall_status = "excluded"
            elif pb_status == "needs_review":
                pb_prov_status = "unavailable"
                needs_review_reasons.append(
                    f"Water-body buffer proximity unconfirmed at ({lat}, {lon}): Overpass API timed out"
                )
                if overall_status != "excluded":
                    overall_status = "needs_review"
        except WaterBufferDataUnavailable as exc:
            pb_status = None
            pb_prov_status = "unavailable"
            needs_review_reasons.append(
                f"Water-body buffer unconfirmed: Overpass timed out at ({lat}, {lon}): {exc}"
            )
            if overall_status != "excluded":
                overall_status = "needs_review"
        except Exception as exc:
            pb_status = None
            pb_prov_status = "unavailable"
            needs_review_reasons.append(
                f"Protected buffer check encountered error at ({lat}, {lon}): {exc}"
            )
            if overall_status != "excluded":
                overall_status = "needs_review"

        # 4. Known unmapped sanctuary review rule
        try:
            gap_warnings = check_known_gaps_review(lat, lon, review_dist_km=25.0)
            if gap_warnings:
                needs_review_reasons.extend(gap_warnings)
                if overall_status != "excluded":
                    overall_status = "needs_review"
        except Exception:
            pass

        record_value(
            site_id=effective_site_id,
            criterion="protected_buffer",
            value=pb_status,
            source="UNEP-WCMC WDPA Protected Areas & OpenStreetMap Waterbodies",
            source_key="geoprocessing/data/water_karnataka.geojson",
            retrieved_at="2026-09-01T00:00:00+00:00",
            unit="buffer_status",
            spatial_ref="10 km PA & 500 m waterbody buffer / EPSG:4326",
            status=pb_prov_status,
            origin="local_layer",
        )

        return {
            "agent": self.name,
            "status": overall_status,
            "allowed_tools": self.ALLOWED_TOOLS,
            "metrics": {
                "slope": {
                    "value": slope_val,
                    "unit": "%",
                    "source": "Copernicus DEM GLO-30 (AWS Open Data)",
                    "threshold": "<= 5.0% (>= 80% footprint disk)",
                    "tripped": slope_tripped,
                    "status": slope_prov_status,
                },
                "landuse": {
                    "value": lu_class_name,
                    "code": lu_class_code,
                    "unit": "class_name",
                    "suitability": lu_suitability,
                    "source": "ESA WorldCover 2021 (v200 10m Cloud-Optimized GeoTIFF)",
                    "tripped": lu_tripped,
                    "status": lu_prov_status,
                },
                "protected_buffer": {
                    "value": pb_status,
                    "unit": "buffer_status",
                    "source": "UNEP-WCMC WDPA Protected Areas & OpenStreetMap Waterbodies",
                    "tripped": (pb_status == "excluded"),
                    "status": pb_prov_status,
                },
            },
            "footprint_zonal": fp_res,
            "needs_review_reasons": needs_review_reasons,
        }
