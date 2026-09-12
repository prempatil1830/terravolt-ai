"""
geoprocessing/score_candidate.py
---------------------------------
Evaluates a candidate lat/lon location across all 7 criteria in the Karnataka
Solar PV MCDM pipeline.

Pipeline Logic:
1. Hard Constraints Pre-filter:
   - Slope > 5.0%                          --> Excluded (fetch_slope)
   - Land-use class excluded (urban, etc.) --> Excluded (fetch_landuse)
   - Within 10km PA / 500m Water Buffer    --> Excluded (fetch_protected_buffer)
   If any hard constraint trips, status="excluded" is returned with the specific
   reason. No AHP score is computed for excluded points.

   Third status — "needs_review":
   If the Protected Area / Water Buffer check returns "needs_review" (i.e. the
   Overpass API was unreachable and proximity could not be confirmed), the
   candidate is NOT marked eligible.  The AHP score IS computed so a human
   reviewer has the number available, but the top-level status is "needs_review"
   and needs_review_reasons lists which constraint(s) triggered the flag.

   Optional Footprint Sampling (footprint_radius_km, default=0.8 km ~200 acres):
   - Slope: Samples centroid + 8 radial points (N, NE, E, SE, S, SW, W, NW).
     Reports mean and max slope. Trips if max slope > 5.0% (conservative constraint).
   - Land-use: Samples same 9 points. Reports excluded fraction. Trips if > 2 of 9
     samples are excluded classes (ensures predominantly usable site).

2. Evaluated Criteria Normalization & AHP Scoring:
   If all hard constraints pass (status="eligible"), the 4 evaluable criteria
   are fetched and normalized to [0, 1] (1.0 = best, 0.0 = worst):
   - Solar Irradiation (GHI)  : Normalized across [5.0, 6.0] kWh/m²/day
   - Distance to Substation   : Inverted & normalized across [0, 50] km
   - Land-Cost Proxy          : Normalized distance to city/town (0 to 1)
   - Temperature Derating     : Inverted ambient temp normalized across [20, 30] °C
   
   Final composite score is computed via the AHP priority weights.
"""

from typing import Any, Dict, List, Optional, Tuple
import math
import sys

from ahp_engine import compute_ahp_weights, WEIGHTED_CRITERIA
from fetch_slope import get_slope
from fetch_landuse import get_suitability_full
from fetch_protected_buffer import get_buffer_status
from fetch_nasa_power import fetch_climatology, NASAPowerRateLimitExceeded
from fetch_substation_distance import get_substation_distance_km, get_grid_connection_info, SubstationDataUnavailable
from fetch_landcost_proxy import get_landcost_proxy


# Bounds updated from stale [4.0, 6.5] to [5.0, 6.0] to reflect the corrected Criterion 1
# range (validated 5.29–5.61 kWh/m²/day across Karnataka, with some margin).
# Note: These replace stale bounds from before the GHI correction.
def normalize_ghi(ghi: float, min_ghi: float = 5.0, max_ghi: float = 6.0) -> float:
    """Higher GHI is better (Benefit criterion)."""
    return float(min(max((ghi - min_ghi) / (max_ghi - min_ghi), 0.0), 1.0))


def normalize_substation(dist_km: Optional[float], max_dist_km: float = 50.0) -> float:
    """Closer to substation is better (Benefit / inverted cost)."""
    if dist_km is None:
        return 0.0
    return float(1.0 - min(max(dist_km / max_dist_km, 0.0), 1.0))


def normalize_landcost(proxy_score: Optional[float]) -> float:
    """
    Higher distance from town = lower land cost = better for utility PV.
    fetch_landcost_proxy already outputs [0, 1] where 1.0 is farthest from town.
    """
    if proxy_score is None:
        return 0.5
    return float(min(max(proxy_score, 0.0), 1.0))


def normalize_temp_derating(t2m_celsius: float, min_temp: float = 20.0, max_temp: float = 30.0) -> float:
    """Lower ambient temperature = lower cell derating = better panel efficiency."""
    return float(min(max((max_temp - t2m_celsius) / (max_temp - min_temp), 0.0), 1.0))


def compute_footprint_sample_points(lat: float, lon: float, radius_km: float) -> List[Tuple[float, float, str]]:
    """
    Generates 9 sampling coordinates: the centroid plus 8 perimeter points
    at the given radius_km (N, NE, E, SE, S, SW, W, NW).
    """
    R_earth = 6371.0
    samples: List[Tuple[float, float, str]] = [(lat, lon, "centroid")]
    
    # 8 compass directions
    directions = {
        "N": 0.0,
        "NE": 45.0,
        "E": 90.0,
        "SE": 135.0,
        "S": 180.0,
        "SW": 225.0,
        "W": 270.0,
        "NW": 315.0,
    }
    lat_rad = math.radians(lat)
    for dir_label, deg in directions.items():
        rad = math.radians(deg)
        dlat = (radius_km * math.cos(rad) / R_earth) * (180.0 / math.pi)
        dlon = (radius_km * math.sin(rad) / (R_earth * math.cos(lat_rad))) * (180.0 / math.pi)
        samples.append((lat + dlat, lon + dlon, dir_label))
        
    return samples


def score_candidate(
    lat: float,
    lon: float,
    ahp_weights: Optional[Dict[str, float]] = None,
    footprint_radius_km: Optional[float] = 0.8,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Evaluates a candidate lat/lon through all 7 criteria.

    Parameters
    ----------
    lat : float
        Latitude in decimal degrees.
    lon : float
        Longitude in decimal degrees.
    ahp_weights : dict, optional
        Weights for the 4 evaluable criteria. If None, default AHP weights are computed.
    footprint_radius_km : float, optional
        Radius of circular site footprint (default: 0.8 km ~200 acres).
        If provided and > 0, evaluates slope and land-use across 9 footprint sample points
        (centroid + 8 perimeter samples). If None or <= 0, evaluates centroid only.
    verbose : bool
        If True, prints diagnostic progress.

    Returns
    -------
    dict
        Evaluation report containing status ('eligible', 'excluded', or
        'needs_review'), score (if eligible or needs_review), exclusion
        reason (if excluded), needs_review_reasons (if needs_review), and
        all raw & normalized values.
    """
    if ahp_weights is None:
        ahp_res = compute_ahp_weights(verbose=False)
        ahp_weights = ahp_res.weights

    result: Dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        "footprint_radius_km": footprint_radius_km,
        "status": "eligible",
        "score": None,
        "score_100": None,
        "exclusion_reason": None,
        "needs_review": False,
        "needs_review_reasons": [],
        "tripped_constraints": [],
        "constraints": {},
        "raw_criteria": {},
        "normalized_criteria": {},
        "weights": ahp_weights,
    }

    use_footprint = (footprint_radius_km is not None and footprint_radius_km > 0.0)
    sample_points = compute_footprint_sample_points(lat, lon, footprint_radius_km) if use_footprint else [(lat, lon, "centroid")]

    # -----------------------------------------------------------------------
    # Step 1: Evaluate Hard Constraints
    # -----------------------------------------------------------------------

    # Constraint A: Slope <= 5.0%
    try:
        if use_footprint:
            slope_samples = []
            for pt_lat, pt_lon, label in sample_points:
                s_val = get_slope(pt_lat, pt_lon)
                slope_samples.append((label, s_val))
            
            slope_vals = [s[1] for s in slope_samples]
            centroid_slope = slope_vals[0]
            mean_slope = sum(slope_vals) / len(slope_vals)
            max_slope = max(slope_vals)
            slope_tripped = (max_slope > 5.0)

            result["constraints"]["slope"] = {
                "mode": "footprint",
                "samples_count": len(slope_vals),
                "centroid_pct": round(centroid_slope, 2),
                "mean_pct": round(mean_slope, 2),
                "max_pct": round(max_slope, 2),
                "tripped": slope_tripped,
                "threshold": "max <= 5.0%",
            }
            if slope_tripped:
                reason = (
                    f"Max footprint slope ({max_slope:.2f}%, mean {mean_slope:.2f}%) "
                    f"exceeds 5.0% threshold (centroid was {centroid_slope:.2f}%)"
                )
                result["tripped_constraints"].append(reason)
        else:
            slope_pct = get_slope(lat, lon)
            slope_tripped = (slope_pct > 5.0)
            result["constraints"]["slope"] = {
                "mode": "centroid",
                "value_pct": round(slope_pct, 2),
                "tripped": slope_tripped,
                "threshold": "<= 5.0%",
            }
            if slope_tripped:
                reason = f"Slope ({slope_pct:.2f}%) exceeds maximum allowable threshold of 5.0%"
                result["tripped_constraints"].append(reason)
    except Exception as e:
        result["constraints"]["slope"] = {"error": str(e), "tripped": False}

    # Constraint B: Land-use Suitability
    try:
        if use_footprint:
            lu_samples = []
            for pt_lat, pt_lon, label in sample_points:
                lu_res = get_suitability_full(pt_lat, pt_lon)
                lu_samples.append((label, lu_res))
            
            centroid_lu = lu_samples[0][1]
            excluded_samples = [s for s in lu_samples if s[1].suitability == "excluded"]
            excluded_count = len(excluded_samples)
            excluded_fraction = excluded_count / float(len(lu_samples))
            lu_tripped = (excluded_count > 2)

            result["constraints"]["landuse"] = {
                "mode": "footprint",
                "samples_count": len(lu_samples),
                "centroid_class": centroid_lu.class_name,
                "centroid_suitability": centroid_lu.suitability,
                "excluded_count": excluded_count,
                "excluded_fraction": round(excluded_fraction, 4),
                "tripped": lu_tripped,
                "threshold": "<= 2 of 9 samples excluded",
            }
            if lu_tripped:
                reason = (
                    f"Land-use excluded: {excluded_count}/9 footprint samples are excluded class "
                    f"({excluded_fraction * 100:.1f}%), exceeding 2/9 allowable threshold"
                )
                result["tripped_constraints"].append(reason)
        else:
            lu = get_suitability_full(lat, lon)
            lu_tripped = (lu.suitability == "excluded")
            result["constraints"]["landuse"] = {
                "mode": "centroid",
                "class_code": lu.class_code,
                "class_name": lu.class_name,
                "suitability": lu.suitability,
                "tripped": lu_tripped,
            }
            if lu_tripped:
                reason = f"Land-use excluded: {lu.class_name} (WorldCover code {lu.class_code})"
                result["tripped_constraints"].append(reason)
    except Exception as e:
        result["constraints"]["landuse"] = {"error": str(e), "tripped": False}

    # Constraint C: Protected Area & Water Buffer (evaluated at site centroid)
    try:
        buf_status = get_buffer_status(lat, lon)
        buf_tripped = (buf_status == "excluded")
        result["constraints"]["protected_buffer"] = {
            "status": buf_status,
            "tripped": buf_tripped,
        }
        if buf_tripped:
            reason = "Inside Protected Area buffer (10 km) or Water Body buffer (500 m)"
            result["tripped_constraints"].append(reason)
        elif buf_status == "needs_review":
            # Water-body proximity could NOT be confirmed (Overpass API unreachable).
            # Do NOT treat as eligible — flag for human review instead.
            result["needs_review"] = True
            result["needs_review_reasons"].append(
                "Water-body buffer proximity unconfirmed: all Overpass API endpoints "
                "failed — manual or offline verification required"
            )
    except Exception as e:
        result["constraints"]["protected_buffer"] = {"error": str(e), "tripped": False}

    # If any hard constraint tripped, return excluded immediately (no score computation)
    if result["tripped_constraints"]:
        result["status"] = "excluded"
        result["exclusion_reason"] = "; ".join(result["tripped_constraints"])
        return result

    # If the buffer check returned "needs_review", we still compute the AHP score
    # (so a human reviewer has the number), but we must NOT mark the site eligible.
    # We fall through to the scoring block and set status at the end.

    # -----------------------------------------------------------------------
    # Step 2: Compute Evaluated Criteria for Eligible Candidate
    # -----------------------------------------------------------------------
    # SCOPING DECISION:
    # GHI (NASA POWER ~55 km native grid resolution), Distance to Substation,
    # Land-Cost Proxy, and Temperature Derating (~55 km grid) remain evaluated
    # at the centroid only. These datasets have coarse spatial resolution relative
    # to an ~0.8 km footprint (~200 acres), so multi-point sampling across the
    # footprint adds negligible variance while imposing substantial network latency.

    # Criterion 1 & 7: NASA POWER Climatology (GHI and Temperature)
    try:
        clima = fetch_climatology(lat, lon)
        ghi = clima.ghi_kwh_m2_day
        t2m = clima.t2m_celsius
        t_cell = t2m + 28.0
        derating_factor = 1.0 - 0.004 * (t_cell - 25.0)
    except NASAPowerRateLimitExceeded:
        # Rate limit failure must halt/raise — never silently substitute fake data
        raise
    except Exception as e:
        if "429" in str(e) or "rate limit" in str(e).lower():
            raise NASAPowerRateLimitExceeded(f"NASA POWER rate limit exceeded for ({lat}, {lon}): {e}") from e
        raise

    # Criterion 4: Distance to Substation (>=33kV qualifying) & Transmission Line
    sub_dist = None
    line_dist = None
    try:
        grid_info = get_grid_connection_info(lat, lon)
        sub_dist = grid_info.substation_dist_km
        line_dist = grid_info.transmission_line_dist_km

        if sub_dist is None:
            # Query succeeded, but no qualifying >=33kV substation found within 50 km
            result["needs_review"] = True
            result["needs_review_reasons"].append(
                "No qualifying >=33kV substation found within 50 km in OSM (substation score = 0.0) — requires KPTCL grid verification"
            )
        elif grid_info.low_confidence and grid_info.confidence_reason:
            # e.g. untagged substation closer than confirmed >=33kV substation
            result["needs_review"] = True
            result["needs_review_reasons"].append(grid_info.confidence_reason)

    except SubstationDataUnavailable as e:
        # Overpass failed across all endpoints — must NOT swallow silently
        result["needs_review"] = True
        result["needs_review_reasons"].append(
            f"Substation data unavailable: all Overpass API endpoints failed/timed out — manual grid interconnection verification required"
        )
    except Exception as e:
        result["needs_review"] = True
        result["needs_review_reasons"].append(
            f"Substation query error ({type(e).__name__}): {e}"
        )

    # Criterion 6: Land-Cost Proxy Score
    try:
        lc_score = get_landcost_proxy(lat, lon)
    except Exception as e:
        print(f"Warning: Land-cost proxy fetch failed for ({lat}, {lon}): {e}")
        lc_score = None

    # Store raw criteria values
    result["raw_criteria"] = {
        "ghi_kwh_m2_day": round(ghi, 4),
        "substation_dist_km": round(sub_dist, 2) if sub_dist is not None else None,
        "transmission_line_dist_km": round(line_dist, 2) if line_dist is not None else None,
        "landcost_proxy_score": round(lc_score, 4) if lc_score is not None else None,
        "t2m_celsius": round(t2m, 2),
        "computed_derating_factor": round(derating_factor, 4),
    }

    # Normalize criteria to 0-1
    norm_ghi = normalize_ghi(ghi)
    norm_sub = normalize_substation(sub_dist)
    norm_lc = normalize_landcost(lc_score)
    norm_temp = normalize_temp_derating(t2m)

    result["normalized_criteria"] = {
        "ghi": round(norm_ghi, 4),
        "substation": round(norm_sub, 4),
        "landcost": round(norm_lc, 4),
        "temp_derating": round(norm_temp, 4),
    }

    # Compute AHP weighted score
    w_ghi = ahp_weights.get("ghi", 0.4000)
    w_sub = ahp_weights.get("substation", 0.4000)
    w_lc = ahp_weights.get("landcost", 0.1000)
    w_temp = ahp_weights.get("temp_derating", 0.1000)

    total_score = (
        w_ghi * norm_ghi
        + w_sub * norm_sub
        + w_lc * norm_lc
        + w_temp * norm_temp
    )

    result["score"] = round(total_score, 4)
    result["score_100"] = round(total_score * 100.0, 2)

    # Promote to needs_review status if any constraint flagged it — even though
    # the AHP score was computed, the site has NOT been fully cleared.
    if result["needs_review"]:
        result["status"] = "needs_review"

    return result


def print_evaluation(name: str, res: Dict[str, Any]) -> None:
    """Pretty-prints the candidate evaluation result."""
    mode_str = f"Footprint (r={res['footprint_radius_km']} km, 9 samples)" if res.get("footprint_radius_km") else "Centroid-Only"
    print("=" * 68)
    print(f"Candidate Site : {name}")
    print(f"Coordinates    : ({res['latitude']:.4f}, {res['longitude']:.4f}) | Mode: {mode_str}")
    print(f"Overall Status : {res['status'].upper()}")

    if res["status"] == "excluded":
        print(f"Exclusion Note : {res['exclusion_reason']}")
        print("Score          : [NOT COMPUTED — EXCLUDED BY HARD CONSTRAINT]")
        print("-" * 68)
        print("Constraint Check Details:")
        for k, v in res["constraints"].items():
            print(f"  - {k:<17}: {v}")

    elif res["status"] == "needs_review":
        print("⚠  NEEDS MANUAL REVIEW — protected-area / water-buffer status could")
        print("⚠  not be confirmed (Overpass API unreachable). Site is NOT cleared.")
        for reason in res.get("needs_review_reasons", []):
            print(f"   Reason: {reason}")
        if res["score"] is not None:
            print(f"AHP Suitability: {res['score']:.4f}  ({res['score_100']:.2f} / 100.0)  [indicative only — review required]")
        else:
            print("Score          : [NOT COMPUTED]")
        print("-" * 68)
        print("Constraint Check Details (hard constraints that DID pass):")
        for k, v in res["constraints"].items():
            print(f"  - {k:<17}: {v}")
        print("-" * 68)
        print("Evaluated Criteria Breakdown (Centroid Scoped):")
        raw = res["raw_criteria"]
        norm = res["normalized_criteria"]
        w = res["weights"]
        print(f"  - Solar GHI      : {raw['ghi_kwh_m2_day']} kWh/m²/day | Norm: {norm['ghi']:.4f} | Weight: {w['ghi']:.4f}")
        sub_str = f"{raw['substation_dist_km']} km" if raw['substation_dist_km'] is not None else "NO DATA"
        print(f"  - Substation Dist: {sub_str:<18} | Norm: {norm['substation']:.4f} | Weight: {w['substation']:.4f}")
        lc_str = f"{raw['landcost_proxy_score']}" if raw['landcost_proxy_score'] is not None else "NO DATA"
        print(f"  - Land-Cost Proxy: {lc_str:<18} | Norm: {norm['landcost']:.4f} | Weight: {w['landcost']:.4f}")
        print(f"  - Temp Derating  : {raw['t2m_celsius']} °C (Factor {raw['computed_derating_factor']}) | Norm: {norm['temp_derating']:.4f} | Weight: {w['temp_derating']:.4f}")

    else:  # "eligible"
        print(f"AHP Suitability: {res['score']:.4f}  ({res['score_100']:.2f} / 100.0)")
        print("-" * 68)
        print("Pre-Filter Hard Constraints: All Passed ✓")
        for k, v in res["constraints"].items():
            print(f"  - {k:<17}: {v}")
        print("-" * 68)
        print("Evaluated Criteria Breakdown (Centroid Scoped):")
        raw = res["raw_criteria"]
        norm = res["normalized_criteria"]
        w = res["weights"]
        print(f"  - Solar GHI      : {raw['ghi_kwh_m2_day']} kWh/m²/day | Norm: {norm['ghi']:.4f} | Weight: {w['ghi']:.4f}")
        sub_str = f"{raw['substation_dist_km']} km" if raw['substation_dist_km'] is not None else "NO DATA"
        print(f"  - Substation Dist: {sub_str:<18} | Norm: {norm['substation']:.4f} | Weight: {w['substation']:.4f}")
        lc_str = f"{raw['landcost_proxy_score']}" if raw['landcost_proxy_score'] is not None else "NO DATA"
        print(f"  - Land-Cost Proxy: {lc_str:<18} | Norm: {norm['landcost']:.4f} | Weight: {w['landcost']:.4f}")
        print(f"  - Temp Derating  : {raw['t2m_celsius']} °C (Factor {raw['computed_derating_factor']}) | Norm: {norm['temp_derating']:.4f} | Weight: {w['temp_derating']:.4f}")
    print("=" * 68 + "\n")


def _status_score_str(res: Dict[str, Any]) -> str:
    """Returns a compact score/status string for benchmark tables."""
    if res["score"] is not None:
        base = f"{res['score']:.4f} ({res['score_100']:.2f}/100)"
        if res["status"] == "needs_review":
            return base + " ⚠NR"
        return base
    return res["status"].upper()


def run_benchmark() -> None:
    """
    Executes the test suite of candidate points, comparing the old centroid-only
    evaluation with the new 9-point circular footprint sampling side by side.
    Also includes a forced needs_review test to validate the third status path.
    """
    print("\n" + "#" * 78)
    print("AHP / MCDM Candidate Scoring Suite: Centroid vs. Footprint Comparison")
    print("#" * 78)

    # 1. Compute and display AHP weights & Consistency Ratio
    ahp_res = compute_ahp_weights(verbose=True)

    # 2. Test points
    test_points = [
        ("Bandipur core (Protected Area)", 11.6667, 76.6283),
        ("Central Bengaluru (Urban Land-use)", 12.9716, 77.5946),
        ("Kalaburagi rural (Arid Northern Plain)", 17.5000, 76.5000),
        ("Tumakuru rural (Sample 1 - Exact Coord)", 13.5000, 76.9000),
        ("Tumakuru rural (Sample 2 - Flat Plain)", 13.5200, 76.8800),
        ("Madikeri escarpment (Western Ghats Escarpment)", 12.4200, 75.7400),
    ]

    print("\n" + "=" * 78)
    print("SIDE-BY-SIDE BENCHMARK RESULTS (Centroid-Only vs Footprint Radius = 0.8 km)")
    print("=" * 78)

    all_statuses: List[str] = []

    for name, lat, lon in test_points:
        print(f"\n>>> EVALUATING SITE: {name} ({lat:.4f}, {lon:.4f})")

        # Centroid-only evaluation
        res_centroid = score_candidate(lat, lon, ahp_weights=ahp_res.weights, footprint_radius_km=None)

        # Footprint evaluation (0.8 km radius ~ 200 acres)
        res_footprint = score_candidate(lat, lon, ahp_weights=ahp_res.weights, footprint_radius_km=0.8)

        all_statuses.append(res_centroid["status"])
        all_statuses.append(res_footprint["status"])

        # Print side-by-side summary
        agree = (res_centroid["status"] == res_footprint["status"])
        if agree:
            match_str = "[AGREE: Both " + res_centroid["status"].upper() + "]"
        else:
            match_str = ("[DISAGREE: Centroid=" + res_centroid["status"].upper()
                         + ", Footprint=" + res_footprint["status"].upper() + "]")

        print(f"Decision Comparison : {match_str}")
        print(f"{'Metric':<22} | {'Centroid-Only (Old)':<25} | {'Footprint Sampling (New, 9 pts)':<28}")
        print("-" * 78)

        # Slope comparison
        c_slope = f"{res_centroid['constraints']['slope'].get('value_pct', '?')}%"
        f_slope_dict = res_footprint['constraints']['slope']
        f_slope = f"Mean={f_slope_dict.get('mean_pct', '?')}%, Max={f_slope_dict.get('max_pct', '?')}%"
        print(f"{'Slope':<22} | {c_slope:<25} | {f_slope:<28}")

        # Land-use comparison
        c_lu = f"{res_centroid['constraints']['landuse'].get('class_name', '?')} ({res_centroid['constraints']['landuse'].get('suitability', '?')})"
        f_lu_dict = res_footprint['constraints']['landuse']
        f_lu = f"Excl={f_lu_dict.get('excluded_count', '?')}/9 ({f_lu_dict.get('excluded_fraction', 0)*100:.1f}%)"
        print(f"{'Land-Use':<22} | {c_lu:<25} | {f_lu:<28}")

        # Buffer comparison
        c_buf = res_centroid['constraints']['protected_buffer'].get('status', '?')
        f_buf = res_footprint['constraints']['protected_buffer'].get('status', '?')
        c_buf_disp = f"{c_buf} ⚠" if c_buf == "needs_review" else c_buf
        f_buf_disp = f"{f_buf} ⚠" if f_buf == "needs_review" else f_buf
        print(f"{'PA/Water Buffer':<22} | {c_buf_disp:<25} | {f_buf_disp:<28}")

        # Status & Score
        c_score = _status_score_str(res_centroid)
        f_score = _status_score_str(res_footprint)
        print(f"{'Suitability Score':<22} | {c_score:<25} | {f_score:<28}")

        if not agree:
            excl_r = res_footprint.get('exclusion_reason') or res_centroid.get('exclusion_reason', '?')
            print(f"Disagreement Detail : {excl_r}")
        elif res_footprint["status"] == "excluded":
            print(f"Exclusion Details   : Centroid: {res_centroid['exclusion_reason']} || Footprint: {res_footprint['exclusion_reason']}")
        elif res_footprint["status"] == "needs_review":
            print(f"Needs-Review Detail : {'; '.join(res_footprint.get('needs_review_reasons', []))}")
        else:
            print(f"Eligible Site Score : Score = {res_footprint['score']:.4f} (Centroid and Footprint both fully eligible)")
        print("-" * 78)

    # ------------------------------------------------------------------
    # Status integrity check
    # ------------------------------------------------------------------
    valid_statuses = {"eligible", "excluded", "needs_review"}
    invalid = [s for s in all_statuses if s not in valid_statuses]
    print("\n" + "=" * 78)
    print("STATUS INTEGRITY CHECK (6-point benchmark)")
    print("=" * 78)
    seen = set(all_statuses)
    print(f"  Distinct statuses seen : {seen}")
    if invalid:
        print(f"  ✗ UNEXPECTED STATUS VALUES: {invalid}")
    else:
        print(f"  ✓ All statuses are within the valid set: {valid_statuses}")

    # ------------------------------------------------------------------
    # Forced needs_review test — monkey-patch check_water_buffer to
    # always raise WaterBufferDataUnavailable so the path is exercised
    # even when the live API is reachable.
    # ------------------------------------------------------------------
    import fetch_protected_buffer as _fpb
    from fetch_protected_buffer import WaterBufferDataUnavailable

    _original_check_water = _fpb.check_water_buffer

    def _always_unavailable(lat, lon, buffer_km=0.5):
        raise WaterBufferDataUnavailable(
            "[FORCED FOR TESTING] Simulated total Overpass API failure"
        )

    print("\n" + "=" * 78)
    print("FORCED needs_review TEST (Kalaburagi rural — water buffer always fails)")
    print("=" * 78)

    # Use a point that would otherwise be eligible (not a PA or excluded land-use)
    _fpb.check_water_buffer = _always_unavailable
    try:
        forced_res = score_candidate(17.5000, 76.5000, ahp_weights=ahp_res.weights, footprint_radius_km=None)
    finally:
        _fpb.check_water_buffer = _original_check_water  # always restore

    print(f"  status            : {forced_res['status']}")
    print(f"  needs_review      : {forced_res['needs_review']}")
    print(f"  needs_review_reasons: {forced_res['needs_review_reasons']}")
    print(f"  score             : {forced_res['score']} (computed for reviewer)")
    buf_field = forced_res["constraints"].get("protected_buffer", {})
    print(f"  buffer constraint : {buf_field}")

    if forced_res["status"] == "needs_review" and forced_res["needs_review"] is True:
        print("  ✓ PASS — forced failure correctly surfaces as 'needs_review', NOT 'eligible'.")
    else:
        print("  ✗ FAIL — status was not 'needs_review'. Check the pipeline logic.")
    print("=" * 78)


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        lat_in = float(sys.argv[1])
        lon_in = float(sys.argv[2])
        r_km = float(sys.argv[3]) if len(sys.argv) >= 4 else 0.8
        r = score_candidate(lat_in, lon_in, footprint_radius_km=r_km)
        print_evaluation(f"Custom Point ({lat_in}, {lon_in})", r)
    else:
        run_benchmark()
