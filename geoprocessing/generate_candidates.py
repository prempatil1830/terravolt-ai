"""
geoprocessing/generate_candidates.py
--------------------------------------
Stage 1 of the Karnataka Solar PV site-selection pipeline.
Coarse grid sweep → preliminary screen → select diverse candidates → full score.

TWO-PHASE DESIGN
----------------
Phase 1 — Grid Sweep (all-local + NASA POWER only, no Overpass)
  Hard constraints: slope, land-use, WDPA buffer — all local GeoTIFF/shapefile.
  Preliminary scoring: GHI + temperature derating from NASA POWER (fast REST,
  no rate-limit issues observed). Substation distance, land-cost proxy, and
  water-body buffer are deliberately deferred — all three use Overpass, which
  cannot sustain 3000+ calls (429s, DNS failures, timeouts).
  Preliminary score: w_ghi * norm_ghi + w_temp * norm_temp (renormalized to [0,1]).

Phase 2 — Full Evaluation (12 selected sites only)
  Calls the canonical score_candidate() on each selected candidate, which:
  • fetches substation distance via Overpass
  • fetches land-cost proxy via Overpass (nearest city/town)
  • checks water-body buffer via Overpass
  • recomputes the full 4-criteria AHP score
  12 Overpass calls is well within API limits.

SELECTION RULE (FROZEN v2, 2026-10-15)
---------------------------------------
  a. Take only Phase-1 "eligible" grid points.
  b. Divide the bbox into REGION_ROWS × REGION_COLS spatial bins.
  c. Per bin, identify the top preliminary candidates.
  d. Rank candidates by a preliminary AHP score that includes substation
     distance (GHI 0.40, Substation 0.40, Temperature derating 0.10, renormalized to 1.0).
     Preliminary score = (w_ghi * norm_ghi + w_sub * norm_sub + w_temp * norm_temp) / (w_ghi + w_sub + w_temp).
  e. Sort regional winners by preliminary AHP score descending; take top TARGET.
  f. Tie-break: score desc → lat asc → lon asc (deterministic).
  Note: Land-cost proxy and 500m water buffer remain deferred to Phase 2.

USAGE
-----
  cd geoprocessing
  python generate_candidates.py [--step 0.1] [--workers 8] [--target 12] [--rescore-existing]
"""

import argparse
import concurrent.futures
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ahp_engine import compute_ahp_weights
from fetch_boundary import check_boundary_constraint, is_in_karnataka
from fetch_slope import get_slope
from fetch_landuse import get_suitability_full
from fetch_protected_buffer import check_wdpa_buffer, check_known_gaps_review
from fetch_district import get_district_name
from zonal_footprint import FOOTPRINT_RADIUS_KM, evaluate_footprint_zonal
from fetch_nasa_power import (
    fetch_climatology,
    NASAPowerRateLimitExceeded,
    reset_live_nasa_calls,
    get_live_nasa_calls,
)
from raw_cache import reset_cache_write_counter, get_cache_entries_written
from fetch_substation_distance import get_substation_distance_km
from score_candidate import (
    score_candidate,
    compute_footprint_sample_points,
    normalize_ghi,
    normalize_substation,
    normalize_temp_derating,
)

# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------
LAT_MIN, LAT_MAX = 11.5, 18.5
LON_MIN, LON_MAX = 74.0, 78.5

# ---------------------------------------------------------------------------
# Selection rule parameters (FROZEN v2)
# ---------------------------------------------------------------------------
REGION_ROWS    = 5
REGION_COLS    = 4
TARGET_MIN     = 8
TARGET_MAX     = 15
DEFAULT_TARGET = 12

SELECTION_RULE_V1_DESC = (
    "FROZEN v1 (2026-09-12): 5x4 region-grid diversity; "
    "top-Phase1-score-per-region (GHI + Temp only); top 12 by Phase-1 score. "
    "Final ranking uses full Phase-2 AHP score."
)

SELECTION_RULE_V2_DESC = (
    "FROZEN v2 (2026-10-15): 5x4 region-grid diversity; "
    "region winners ranked by preliminary AHP score including substation distance "
    "(GHI 0.40, Substation 0.40, Temperature 0.10, renormalized to 1.0). "
    "Final evaluation applies full 4-criteria AHP."
)

SELECTION_RULE_DESC = SELECTION_RULE_V2_DESC

OUTPUT_DIR      = os.path.join(_HERE, "data")
CANDIDATES_JSON = os.path.join(OUTPUT_DIR, "candidates.json")
CANDIDATES_MD   = os.path.join(OUTPUT_DIR, "candidates_summary.md")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_grid(step: float) -> List[Tuple[float, float]]:
    pts = []
    lat = LAT_MIN
    while lat <= LAT_MAX + 1e-9:
        lon = LON_MIN
        while lon <= LON_MAX + 1e-9:
            pts.append((round(lat, 6), round(lon, 6)))
            lon = round(lon + step, 6)
        lat = round(lat + step, 6)
    return pts


def get_region(lat: float, lon: float) -> Tuple[int, int]:
    row = int((lat - LAT_MIN) / (LAT_MAX - LAT_MIN) * REGION_ROWS)
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * REGION_COLS)
    return min(row, REGION_ROWS - 1), min(col, REGION_COLS - 1)


# ---------------------------------------------------------------------------
# Phase 1: fast screening (no Overpass)
# ---------------------------------------------------------------------------

def screen_point(
    lat: float,
    lon: float,
    w_ghi: float,
    w_temp: float,
) -> Dict[str, Any]:
    """
    Checks hard constraints (local only) and computes a preliminary GHI+T2m score.
    Returns status "phase1_eligible" | "excluded" | "error".
    Substation, land-cost, and water-body checks are deferred to Phase 2.
    """
    base: Dict[str, Any] = {
        "latitude": lat, "longitude": lon,
        "status": "phase1_eligible",
        "p1_score": None,
        "exclusion_reason": None,
        "tripped_constraints": [],
    }

    # Filter 0: Karnataka State Boundary (>= 90% disk inside boundary)
    try:
        passed_b, disk_frac, b_reason = check_boundary_constraint(
            lat, lon, radius_km=FOOTPRINT_RADIUS_KM, min_disk_fraction=0.90
        )
        if not passed_b:
            base["tripped_constraints"].append(b_reason)
            base["status"] = "excluded"
            base["exclusion_reason"] = b_reason
            return base
    except Exception as e:
        pass

    # Footprint screening as zonal fractions (slope >= 80%, landuse >= 80%, PA disk buffer >= 10 km, water buffer >= 500m)
    try:
        fp_res = evaluate_footprint_zonal(lat, lon, radius_km=FOOTPRINT_RADIUS_KM, min_usable_fraction=0.80)
        base["footprint_zonal"] = fp_res
        if not fp_res["is_feasible"]:
            base["status"] = "excluded"
            base["exclusion_reason"] = "; ".join(fp_res["tripped_constraints"])
            base["tripped_constraints"].extend(fp_res["tripped_constraints"])
            return base
    except Exception as e:
        base["status"] = "excluded"
        base["exclusion_reason"] = f"Footprint evaluation error: {e}"
        base["tripped_constraints"].append(str(e))
        return base

    # Preliminary score: GHI + temperature (NASA POWER only)
    try:
        clima = fetch_climatology(lat, lon)
        ghi = clima.ghi_kwh_m2_day
        t2m = clima.t2m_celsius
    except NASAPowerRateLimitExceeded:
        # Rate limit failure must halt/raise — never silently substitute fake data
        raise
    except Exception as e:
        if "429" in str(e) or "rate limit" in str(e).lower():
            raise NASAPowerRateLimitExceeded(f"NASA POWER rate limit at ({lat}, {lon}): {e}") from e
        raise

    norm_ghi  = normalize_ghi(ghi)
    norm_temp = normalize_temp_derating(t2m)

    # Renormalize weights to sum to 1 (substation + landcost excluded in Phase 1)
    total_w = w_ghi + w_temp
    p1_score = (w_ghi * norm_ghi + w_temp * norm_temp) / total_w if total_w > 0 else 0.0

    base["p1_score"] = round(p1_score, 4)
    base["p1_ghi"]   = round(ghi, 4)
    base["p1_t2m"]   = round(t2m, 2)
    return base


# ---------------------------------------------------------------------------
# Candidate selection (FROZEN RULE v2)
# ---------------------------------------------------------------------------

def select_candidates(
    eligible: List[Dict[str, Any]],
    target: int,
    w_sub: float = 0.40,
    w_ghi: float = 0.40,
    w_temp: float = 0.10,
    rule_version: str = "v2",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Candidate selection using 5x4 regional diversity.
    Under FROZEN v2: Region winners are evaluated with substation distance and
    ranked by preliminary AHP score (GHI 0.40, Substation 0.40, Temp 0.10).
    Returns (selected_v2, shortlist_v1_for_comparison).
    """
    # Load local grid layers for instant local evaluation
    from bulk_download_substations import query_grid_locally
    import geopandas as gpd
    from shapely.strtree import STRtree

    subs_path = os.path.join(OUTPUT_DIR, "substations_karnataka.geojson")
    lines_path = os.path.join(OUTPUT_DIR, "lines_karnataka.geojson")
    
    if os.path.exists(subs_path) and os.path.exists(lines_path):
        gdf_all = gpd.read_file(subs_path).to_crs(epsg=32643)
        gdf_q = gdf_all[(gdf_all["is_qualifying"] == True) & (gdf_all.get("is_generation", False) != True)].copy()
        gdf_gen = gdf_all[gdf_all.get("is_generation", False) == True].copy()
        gdf_lines = gpd.read_file(lines_path).to_crs(epsg=32643)
        index_bundle = {
            "gdf_qualifying": gdf_q,
            "gdf_generation": gdf_gen,
            "gdf_all_subs": gdf_all,
            "gdf_lines": gdf_lines,
            "tree_qualifying": STRtree(gdf_q.geometry) if not gdf_q.empty else None,
            "tree_generation": STRtree(gdf_gen.geometry) if not gdf_gen.empty else None,
            "tree_all_subs": STRtree(gdf_all.geometry) if not gdf_all.empty else None,
            "tree_lines": STRtree(gdf_lines.geometry) if not gdf_lines.empty else None,
        }
    else:
        index_bundle = None

    total_w = w_ghi + w_sub + w_temp
    scored_all: List[Dict[str, Any]] = []

    print("\n" + "=" * 70)
    print("EVALUATING SUBSTATION DISTANCES ACROSS ALL FEASIBLE CANDIDATES")
    print("=" * 70)

    for pt in eligible:
        cand = dict(pt)
        lat, lon = cand["latitude"], cand["longitude"]

        if index_bundle is not None:
            g_res = query_grid_locally(lat, lon, index_bundle, max_search_dist_km=60.0)
            sub_dist = g_res["substation_dist_km"]
            line_dist = g_res["transmission_line_dist_km"]
            cand["grid_info"] = g_res
        else:
            try:
                sub_dist = get_substation_distance_km(lat, lon)
            except Exception:
                sub_dist = None
            line_dist = None

        norm_ghi = normalize_ghi(cand.get("p1_ghi", 5.0))
        norm_sub = normalize_substation(sub_dist)
        norm_temp = normalize_temp_derating(cand.get("p1_t2m", 25.0))
        p1_v2 = (w_ghi * norm_ghi + w_sub * norm_sub + w_temp * norm_temp) / total_w

        cand["p1_score_v2"] = round(p1_v2, 4)
        cand["substation_dist_km"] = sub_dist
        cand["transmission_line_dist_km"] = line_dist
        gen_dist = g_res.get("nearest_generation_substation_dist_km") if index_bundle is not None else None
        cand["nearest_generation_substation_dist_km"] = gen_dist
        cand["nearest generation substation (context only)"] = gen_dist
        cand["region"] = get_region(lat, lon)
        scored_all.append(cand)

    # Group by region
    region_pools: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for cand in scored_all:
        region_pools.setdefault(cand["region"], []).append(cand)

    print("\n" + "=" * 70)
    print("PER-REGION SCORED CANDIDATE COUNTS (Full Feasible Pool):")
    print("=" * 70)
    for reg in sorted(region_pools.keys()):
        print(f"  Region {reg}: {len(region_pools[reg])} feasible points scored")
    print("=" * 70)

    # Pick regional winners for v1 (top by p1_score)
    v1_winners = {}
    for reg, pool in region_pools.items():
        v1_winners[reg] = max(pool, key=lambda r: (r.get("p1_score") or 0, -r["latitude"], -r["longitude"]))

    v1_ranked = sorted(
        v1_winners.values(),
        key=lambda r: (-(r.get("p1_score") or 0), r["latitude"], r["longitude"]),
    )

    if rule_version == "v1":
        return v1_ranked[:target], v1_ranked[:target]

    # Pick regional winners for v2 (top by preliminary AHP score with substation distance)
    v2_winners = {}
    for reg, pool in region_pools.items():
        v2_winners[reg] = max(pool, key=lambda r: (r.get("p1_score_v2") or 0, -r["latitude"], -r["longitude"]))

    v2_ranked = sorted(
        v2_winners.values(),
        key=lambda r: (-(r.get("p1_score_v2") or 0), r["latitude"], r["longitude"]),
    )

    chosen_v1 = v1_ranked[:target]
    chosen_v2 = v2_ranked[:target]

    # Compute v1-vs-v2 set difference
    v1_coords = set((round(c["latitude"], 4), round(c["longitude"], 4)) for c in chosen_v1)
    v2_coords = set((round(c["latitude"], 4), round(c["longitude"], 4)) for c in chosen_v2)
    sym_diff = v1_coords.symmetric_difference(v2_coords)

    # Old 12 (pre-electrode-exclusion / pre-final-rules shortlist)
    old_12_coords = {
        (14.2, 76.5), (13.3, 77.9), (14.5, 76.5), (15.0, 76.1),
        (16.0, 76.9), (16.3, 76.2), (16.0, 77.4), (14.2, 75.6),
        (16.0, 75.1), (17.1, 76.8), (17.2, 75.9), (15.7, 75.1)
    }
    common_coords = old_12_coords.intersection(v2_coords)
    dropped_coords = old_12_coords - v2_coords
    added_coords = v2_coords - old_12_coords

    print("\n" + "=" * 70)
    print("OLD 12 vs NEW 12 SHORTLIST SET DIFFERENCE (Final Rules & Electrode Exclusion)")
    print("=" * 70)
    print(f"  Old 12 Shortlist ({len(old_12_coords)}): {sorted(list(old_12_coords))}")
    print(f"  New 12 Shortlist ({len(v2_coords)}): {sorted(list(v2_coords))}")
    print(f"  Common Sites ({len(common_coords)}): {sorted(list(common_coords))}")
    print(f"  Dropped from Old ({len(dropped_coords)}): {sorted(list(dropped_coords))}")
    print(f"  Added in New ({len(added_coords)}): {sorted(list(added_coords))}")
    print(f"  Total Symmetric Set Difference: {len(old_12_coords ^ v2_coords)} coordinates")
    print("=" * 70)

    print("\n" + "=" * 70)
    print("V1 vs V2 SHORTLIST SET DIFFERENCE")
    print("=" * 70)
    print(f"  Frozen v1 Shortlist Coordinates ({len(v1_coords)}): {sorted(list(v1_coords))}")
    print(f"  Frozen v2 Shortlist Coordinates ({len(v2_coords)}): {sorted(list(v2_coords))}")
    print(f"  Total Symmetric Set Difference: {len(sym_diff)} coordinates")
    if sym_diff:
        print(f"  Coordinates in v1 only: {sorted(list(v1_coords - v2_coords))}")
        print(f"  Coordinates in v2 only: {sorted(list(v2_coords - v1_coords))}")
    else:
        print("  0 coordinates changed set membership across the shortlist (identical candidate pool; internal rankings shifted).")
    print("=" * 70 + "\n")

    return chosen_v2, chosen_v1


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def generate_candidate_reasons(s: Dict[str, Any]) -> List[str]:
    """
    Generates candidate justification reasons strictly from computed fields.
    Removes marketing adjectives and claims not present in measured data.
    """
    reasons = []
    raw = s.get("raw_criteria", {})
    ghi = raw.get("ghi_kwh_m2_day")
    sub_dist = raw.get("substation_dist_km")
    tx_dist = raw.get("transmission_line_dist_km")
    temp = raw.get("t2m_celsius")

    constraints = s.get("constraints", {})
    slope_info = constraints.get("slope", {})
    slope_mean = slope_info.get("mean_pct")
    slope_max = slope_info.get("max_pct")

    lu_info = constraints.get("landuse", {})
    centroid_class = lu_info.get("centroid_class")

    if ghi is not None:
        reasons.append(f"GHI: {ghi:.3f} kWh/m²/day")
    if sub_dist is not None:
        reasons.append(f"Primary substation distance: {sub_dist:.2f} km")
    if tx_dist is not None:
        reasons.append(f"Transmission corridor distance: {tx_dist:.2f} km")
    if slope_mean is not None:
        reasons.append(f"Footprint mean slope: {slope_mean:.2f}% (max: {slope_max:.2f}%)")
    if centroid_class:
        reasons.append(f"Land cover: {centroid_class}")
    if temp is not None:
        reasons.append(f"Ambient temperature (T2m): {temp:.2f}°C")
    return reasons


def write_json(selected: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sweep_metadata": meta,
        "selection_rule": SELECTION_RULE_DESC,
        "candidates": [],
    }
    for rank, s in enumerate(selected, 1):
        out["candidates"].append({
            "rank": rank,
            "latitude": s["latitude"],
            "longitude": s["longitude"],
            "district": get_district_name(s["latitude"], s["longitude"]),
            "status": s["status"],
            "score": s.get("score"),
            "score_100": s.get("score_100"),
            "p1_score": s.get("p1_score"),
            "p1_score_v2": s.get("p1_score_v2"),
            "exclusion_reason": s.get("exclusion_reason") or (
                "; ".join(s.get("tripped_constraints", [])) if s.get("tripped_constraints") else (
                    "Inside Water Body buffer (500 m) or Protected Area buffer (10 km)" if s.get("constraints", {}).get("protected_buffer", {}).get("tripped") else None
                )
            ),
            "tripped_constraints": s.get("tripped_constraints", []),
            "needs_review": s.get("needs_review", False),
            "needs_review_reasons": s.get("needs_review_reasons", []),
            "raw_criteria": s.get("raw_criteria", {}),
            "substation_details": s.get("substation_details", {}),
            "normalized_criteria": s.get("normalized_criteria", {}),
            "weights": s.get("weights", {}),
            "constraints": s.get("constraints", {}),
            "region": list(get_region(s["latitude"], s["longitude"])),
            "exact_reasons": generate_candidate_reasons(s),
        })
    with open(CANDIDATES_JSON, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {CANDIDATES_JSON}")


def write_md(selected: List[Dict[str, Any]], meta: Dict[str, Any]) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    nr = meta.get("final_needs_review", 0)
    n  = len(selected)
    lines = [
        "# Karnataka Solar PV — Selected Candidate Sites",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Grid step:** {meta['step_deg']}deg (~11 km)  ",
        f"**Total grid points:** {meta['total_points']}  ",
        f"**Phase-1 eligible / excluded:** {meta['p1_eligible']} / {meta['p1_excluded']}  ",
        f"**Phase-2 (final) eligible / excluded / needs_review:** "
        f"{meta['final_eligible']} / {meta['final_excluded']} / {meta['final_needs_review']}  ",
        "",
        f"> **Selection rule:** {SELECTION_RULE_DESC}",
        "",
        "## Selected Candidates (sorted by full AHP score)",
        "",
        "| Rank | District | Lat | Lon | AHP Score | /100 | GHI (kWh/m2/d) | Sub (km) | Tx line (km) | LC proxy | T2m (C) | Status |",
        "|------|----------|-----|-----|-----------|------|----------------|----------|--------------|----------|---------|--------|",
    ]
    for rank, s in enumerate(selected, 1):
        raw = s.get("raw_criteria", {})
        dist = get_district_name(s["latitude"], s["longitude"])
        ghi = raw.get("ghi_kwh_m2_day", s.get("p1_ghi", "-"))
        sub = raw.get("substation_dist_km", "-")
        line = raw.get("transmission_line_dist_km", "-")
        lc  = raw.get("landcost_proxy_score", "-")
        t2m = raw.get("t2m_celsius", s.get("p1_t2m", "-"))
        sc  = s.get("score") or s.get("p1_score", "-")
        sc100 = s.get("score_100") or (round(float(sc)*100,2) if sc != "-" else "-")
        st  = "NEEDS_REVIEW" if s["status"] == "needs_review" else s["status"]
        lines.append(
            f"| {rank} | {dist} | {s['latitude']:.4f} | {s['longitude']:.4f} "
            f"| {round(float(sc),4) if sc != '-' else '-'} "
            f"| {round(float(sc100),2) if sc100 != '-' else '-'} "
            f"| {ghi} | {sub} | {line} "
            f"| {round(lc,4) if isinstance(lc,float) else '-'} "
            f"| {t2m} | {st} |"
        )
    lines += [
        "",
        "## needs_review count",
        "",
        f"Of the **{n}** selected candidates, **{nr}** returned `needs_review` "
        "(Overpass unreachable during water-body check). "
        "These require offline water-body verification before final eligibility.",
        "",
        "## Notes",
        "- Phase-1 screening uses slope + land-use + WDPA (all local).",
        "- Phase-1 preliminary score uses GHI + T2m from NASA POWER only.",
        "- Phase-2 full AHP score adds substation distance, land-cost proxy, and water-body buffer (Overpass, 12 calls only).",
        "- Centroid-only evaluation at grid scale; shortlisted sites should be re-evaluated with 9-point footprint sampling.",
    ]
    with open(CANDIDATES_MD, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {CANDIDATES_MD}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_sweep(step: float, max_workers: int, target: int) -> None:
    t0 = time.time()
    start_time_iso = datetime.now(timezone.utc).isoformat()
    reset_live_nasa_calls()
    reset_cache_write_counter()

    print("=" * 70)
    print("LIVE GRID SWEEP START")
    print(f"  Start Time (UTC): {start_time_iso}")
    print("  Configuration   : Network ON, Raw Cache Active, Throttle 50 req/min")
    print("=" * 70 + "\n")

    print("Computing AHP weights ...")
    ahp_res = compute_ahp_weights(verbose=False)
    w = ahp_res.weights
    w_ghi  = w.get("ghi", 0.40)
    w_temp = w.get("temp_derating", 0.10)
    print(f"  Full weights : {w}")
    print(f"  Phase-1 uses : ghi={w_ghi:.3f}  temp={w_temp:.3f}  (renorm to 1.0)\n")

    grid  = build_grid(step)
    total = len(grid)
    target_n = max(TARGET_MIN, min(target, TARGET_MAX))
    print(f"Grid : {LAT_MIN}-{LAT_MAX}N x {LON_MIN}-{LON_MAX}E @ {step}deg -> {total} points")
    print(f"Workers : {max_workers}  |  Target : {target_n}  |  "
          f"Regions : {REGION_ROWS}x{REGION_COLS}={REGION_ROWS*REGION_COLS}\n")

    # Runtime estimation based on empirical ~58% passing local constraint checks to NASA POWER
    est_eligible_pts = int(total * 0.58)
    est_p1_seconds = est_eligible_pts * 1.2
    est_p2_seconds = target_n * 12.0  # Overpass queries
    total_est_seconds = est_p1_seconds + est_p2_seconds

    print("=" * 70)
    print("RUNTIME ESTIMATION (NASA POWER Rate Limit: 50 requests/min = 1.2s spacing)")
    print("=" * 70)
    print(f"  Total grid points to evaluate : {total}")
    print(f"  Estimated locally excluded    : ~{total - est_eligible_pts} points (~42%) [slope/land-use/WDPA]")
    print(f"  Estimated reaching NASA POWER : ~{est_eligible_pts} points (~58%)")
    print(f"  Estimated Phase 1 duration    : {est_eligible_pts} x 1.2s = {est_p1_seconds:.0f}s (~{est_p1_seconds/60:.1f} min)")
    print(f"  Estimated Phase 2 duration    : {target_n} Overpass calls x ~12s = {est_p2_seconds:.0f}s (~{est_p2_seconds/60:.1f} min)")
    print(f"  Total Estimated Runtime       : {total_est_seconds:.0f}s (~{total_est_seconds/60:.1f} min)")
    print("=" * 70 + "\n")

    # ── Phase 1 ─────────────────────────────────────────────────────────────
    print("=== PHASE 1: Grid screen (slope + land-use + WDPA + NASA POWER) ===")
    results: List[Dict[str, Any]] = []
    done = 0

    def _cb(res: Dict[str, Any]) -> None:
        nonlocal done
        done += 1
        if done % 200 == 0 or done == total:
            el  = time.time() - t0
            eta = (el / done) * (total - done) if done else 0
            print(f"  [{done:>4}/{total}] {done/total*100:5.1f}%  "
                  f"elapsed {el:4.0f}s  ETA {eta:4.0f}s  "
                  f"({res['latitude']:.1f},{res['longitude']:.1f}) "
                  f"{res['status']}", flush=True)

    import gc
    for lat, lon in grid:
        res = screen_point(lat, lon, w_ghi, w_temp)
        results.append(res)
        _cb(res)
        if done % 200 == 0:
            gc.collect()

    t1 = time.time()
    p1_eligible = [r for r in results if r["status"] == "phase1_eligible"]
    p1_excluded = [r for r in results if r["status"] == "excluded"]

    print(f"\nPhase 1 done in {t1-t0:.0f}s")
    print(f"  phase1_eligible : {len(p1_eligible)} ({len(p1_eligible)/total*100:.1f}%)")
    print(f"  excluded        : {len(p1_excluded)} ({len(p1_excluded)/total*100:.1f}%)")

    if not p1_eligible:
        print("No eligible points — cannot select candidates.")
        return

    # ── Candidate selection ─────────────────────────────────────────────────
    p1_sorted = sorted(p1_eligible,
                       key=lambda r: (-(r["p1_score"] or 0), r["latitude"], r["longitude"]))
    w_sub = w.get("substation", 0.40)
    selected_p1, shortlist_v1 = select_candidates(
        p1_sorted, target_n, w_sub=w_sub, w_ghi=w_ghi, w_temp=w_temp, rule_version="v2"
    )

    regions_covered = len(set(get_region(r["latitude"], r["longitude"]) for r in p1_eligible))
    print(f"\n=== CANDIDATE SELECTION (FROZEN RULE v2: PRELIMINARY AHP WITH SUBSTATION) ===")
    print(f"  Regions with eligible hits : {regions_covered}/{REGION_ROWS*REGION_COLS}")
    print(f"  Selected for Phase 2       : {len(selected_p1)}")

    print("\n" + "=" * 96)
    print("SHORTLIST COMPARISON: FROZEN v1 (GHI+Temp) vs FROZEN v2 (GHI+Substation+Temp)")
    print("=" * 96)
    print(f"{'Rk':<3} | {'OLD SHORTLIST (FROZEN v1 - GHI+Temp)':<42} | {'NEW SHORTLIST (FROZEN v2 - GHI+Sub+Temp)':<45}")
    print(f"{'':<3} | {'Coord':<16} {'P1 v1':<8} {'Region':<8} {'Sub km':<7} | {'Coord':<16} {'P1 v2':<8} {'Region':<8} {'Sub km':<7}")
    print("-" * 96)
    for i in range(max(len(shortlist_v1), len(selected_p1))):
        s1 = shortlist_v1[i] if i < len(shortlist_v1) else {}
        s2 = selected_p1[i] if i < len(selected_p1) else {}
        c1 = f"({s1.get('latitude',0):.2f}, {s1.get('longitude',0):.2f})"
        c2 = f"({s2.get('latitude',0):.2f}, {s2.get('longitude',0):.2f})"
        p1 = f"{s1.get('p1_score',0):.4f}"
        p2 = f"{s2.get('p1_score_v2',0):.4f}"
        r1 = str(get_region(s1.get('latitude',0), s1.get('longitude',0)))
        r2 = str(get_region(s2.get('latitude',0), s2.get('longitude',0)))
        sub1 = str(s1.get('substation_dist_km', '-'))
        sub2 = str(s2.get('substation_dist_km', '-'))
        print(f"{i+1:<3} | {c1:<16} {p1:<8} {r1:<8} {sub1:<7} | {c2:<16} {p2:<8} {r2:<8} {sub2:<7}")
    print("=" * 96 + "\n")

    # ── Phase 2: full score_candidate on selected sites ─────────────────────
    print(f"\n=== PHASE 2: Full AHP score + water-buffer ({len(selected_p1)} sites) ===")
    final: List[Dict[str, Any]] = []
    for i, s in enumerate(selected_p1, 1):
        lat, lon = s["latitude"], s["longitude"]
        print(f"  [{i}/{len(selected_p1)}] ({lat:.4f}, {lon:.4f}) ...", end=" ", flush=True)
        try:
            full = score_candidate(lat, lon, ahp_weights=w, footprint_radius_km=FOOTPRINT_RADIUS_KM)
            full["p1_score"] = s["p1_score"]
            full["p1_score_v2"] = s.get("p1_score_v2")
            full["p1_ghi"]   = s.get("p1_ghi")
            full["p1_t2m"]   = s.get("p1_t2m")
            full["footprint_zonal"] = s.get("footprint_zonal")
            
            # Substation confidence from grid query
            grid_info = s.get("grid_info")
            if grid_info and grid_info.get("low_confidence"):
                full["needs_review"] = True
                conf_reason = grid_info.get("confidence_reason")
                if conf_reason and conf_reason not in full["needs_review_reasons"]:
                    full["needs_review_reasons"].append(conf_reason)

            # Assign single authoritative final status
            if full["needs_review"]:
                full["status"] = "needs_review"
            elif full["tripped_constraints"]:
                full["status"] = "excluded"
            else:
                full["status"] = "eligible"

            print(f"{full['status']}  score={full['score']}")
            final.append(full)
        except Exception as exc:
            print(f"ERROR: {exc}")
            s["status"] = "error"; s["score"] = None; s["score_100"] = None
            final.append(s)

    t2 = time.time()

    # Sort final by full AHP score (needs_review and excluded retain their score)
    final_sorted = sorted(
        final,
        key=lambda s: (-(s.get("score") or 0), s["latitude"], s["longitude"]),
    )

    f_eligible   = [s for s in final if s["status"] == "eligible"]
    f_excluded   = [s for s in final if s["status"] == "excluded"]
    f_nr         = [s for s in final if s["status"] == "needs_review"]
    nr_rate      = len(f_nr) / len(final) * 100 if final else 0.0

    # ── Print summary ────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("FINAL RESULTS")
    print(f"{'='*70}")
    print(f"  Phase 1  : {t1-t0:.0f}s  ({total} grid points, {len(p1_eligible)} eligible)")
    print(f"  Phase 2  : {t2-t1:.0f}s  ({len(selected_p1)} Overpass + AHP evaluations)")
    print(f"  Total    : {t2-t0:.0f}s")
    print(f"\n  Grid summary : {total} pts  |  "
          f"eligible={len(p1_eligible)} ({len(p1_eligible)/total*100:.1f}%)  |  "
          f"excluded={len(p1_excluded)} ({len(p1_excluded)/total*100:.1f}%)")
    print(f"\n  Final candidates ({len(final_sorted)}):")
    print(f"    eligible    : {len(f_eligible)}")
    print(f"    excluded    : {len(f_excluded)}  (failed water-buffer check)")
    print(f"    needs_review: {len(f_nr)} ({nr_rate:.1f}%)  (Overpass unreachable)")
    print()
    print(f"  {'Rk':<3}  {'Lat':>8}  {'Lon':>8}  {'AHP':>7}  {'P1':>6}  "
          f"{'GHI':>5}  {'Sub km':>7}  {'LC':>5}  Region  Status")
    print("  " + "-"*78)
    for rank, s in enumerate(final_sorted, 1):
        raw = s.get("raw_criteria", {})
        ghi = raw.get("ghi_kwh_m2_day", s.get("p1_ghi", "?"))
        sub = raw.get("substation_dist_km", "?")
        lc  = raw.get("landcost_proxy_score", "?")
        region = get_region(s["latitude"], s["longitude"])
        ahp_sc = f"{s['score']:.4f}" if s.get("score") is not None else "  -   "
        p1_sc  = f"{s['p1_score']:.4f}" if s.get("p1_score") is not None else "  -   "
        st = "NR" if s["status"] == "needs_review" else s["status"][:8]
        print(f"  {rank:<3}  {s['latitude']:>8.4f}  {s['longitude']:>8.4f}  "
              f"{ahp_sc:>7}  {p1_sc:>6}  {str(ghi):>5}  {str(sub):>7}  "
              f"{str(round(lc,3)) if isinstance(lc,float) else '?':>5}  "
              f"{str(region):>8}  {st}")

    # ── Write outputs ────────────────────────────────────────────────────────
    end_time_iso = datetime.now(timezone.utc).isoformat()
    live_nasa_calls = get_live_nasa_calls()
    cache_entries_written = get_cache_entries_written()

    print("\n" + "=" * 70)
    print("LIVE SWEEP EXECUTION STATS")
    print("=" * 70)
    print(f"  Sweep Start Time (UTC)         : {start_time_iso}")
    print(f"  Sweep End Time (UTC)           : {end_time_iso}")
    print(f"  Total Duration                 : {t2 - t0:.2f}s")
    print(f"  Number of Live NASA POWER Calls: {live_nasa_calls}")
    print(f"  Number of Raw Cache Writes     : {cache_entries_written}")
    print("=" * 70 + "\n")

    meta = {
        "step_deg": step,
        "lat_min": LAT_MIN, "lat_max": LAT_MAX,
        "lon_min": LON_MIN, "lon_max": LON_MAX,
        "total_points": total,
        "p1_eligible": len(p1_eligible),
        "p1_excluded": len(p1_excluded),
        "target": target_n,
        "region_rows": REGION_ROWS, "region_cols": REGION_COLS,
        "final_eligible": len(f_eligible),
        "final_excluded": len(f_excluded),
        "final_needs_review": len(f_nr),
        "needs_review_rate_pct": round(nr_rate, 2),
        "phase1_wall_time_s": round(t1 - t0, 1),
        "phase2_wall_time_s": round(t2 - t1, 1),
        "total_wall_time_s": round(t2 - t0, 1),
        "selection_rule_version": "v2",
        "selection_rule_frozen_date": "2026-10-15",
        "sweep_start_time": start_time_iso,
        "sweep_end_time": end_time_iso,
        "live_nasa_power_calls": live_nasa_calls,
        "cache_entries_written": cache_entries_written,
    }
    print()
    write_json(final_sorted, meta)
    write_md(final_sorted, meta)
    print("\nDone.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Karnataka Solar PV — full candidate selection"
    )
    parser.add_argument("--step",    type=float, default=0.1)
    parser.add_argument("--workers", type=int,   default=4)
    parser.add_argument("--target",  type=int,   default=DEFAULT_TARGET)
    args = parser.parse_args()
    if not (TARGET_MIN <= args.target <= TARGET_MAX):
        print(f"ERROR: --target must be {TARGET_MIN}-{TARGET_MAX}")
        sys.exit(1)
    run_sweep(step=args.step, max_workers=args.workers, target=args.target)

