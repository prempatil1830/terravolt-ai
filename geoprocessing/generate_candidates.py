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

SELECTION RULE (FROZEN v1, 2026-09-12)
---------------------------------------
  a. Take only Phase-1 "eligible" grid points.
  b. Divide the bbox into REGION_ROWS × REGION_COLS spatial bins.
  c. Per bin, keep the highest-scoring (Phase-1 preliminary score) point.
  d. Sort bin winners by preliminary score descending; take top TARGET.
  e. Tie-break: score desc → lat asc → lon asc (deterministic).
  Rule must not change before the final experiment without versioning.

USAGE
-----
  cd geoprocessing
  python generate_candidates.py [--step 0.1] [--workers 8] [--target 12]
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
from fetch_slope import get_slope
from fetch_landuse import get_suitability_full
from fetch_protected_buffer import check_wdpa_buffer
from fetch_nasa_power import fetch_climatology, NASAPowerRateLimitExceeded
from score_candidate import score_candidate, normalize_ghi, normalize_temp_derating

# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------
LAT_MIN, LAT_MAX = 11.5, 18.5
LON_MIN, LON_MAX = 74.0, 78.5

# ---------------------------------------------------------------------------
# Selection rule parameters (FROZEN v1)
# ---------------------------------------------------------------------------
REGION_ROWS    = 5
REGION_COLS    = 4
TARGET_MIN     = 8
TARGET_MAX     = 15
DEFAULT_TARGET = 12

SELECTION_RULE_DESC = (
    "FROZEN v1 (2026-09-12): 5x4 region-grid diversity; "
    "top-Phase1-score-per-region; top 12 by Phase-1 score. "
    "Final ranking uses full Phase-2 AHP score. "
    "See generate_candidates.py docstring."
)

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

    # Constraint A: Slope (local GeoTIFF)
    try:
        slope_pct = get_slope(lat, lon)
        if slope_pct > 5.0:
            base["tripped_constraints"].append(
                f"Slope ({slope_pct:.2f}%) > 5.0%"
            )
    except Exception as e:
        pass  # data gap — don't exclude, let Phase 2 decide

    if base["tripped_constraints"]:
        base["status"] = "excluded"
        base["exclusion_reason"] = "; ".join(base["tripped_constraints"])
        return base

    # Constraint B: Land-use (local GeoTIFF)
    try:
        lu = get_suitability_full(lat, lon)
        if lu.suitability == "excluded":
            base["tripped_constraints"].append(
                f"Land-use: {lu.class_name} (code {lu.class_code})"
            )
    except Exception:
        pass

    if base["tripped_constraints"]:
        base["status"] = "excluded"
        base["exclusion_reason"] = "; ".join(base["tripped_constraints"])
        return base

    # Constraint C: WDPA buffer (local shapefile, no Overpass)
    try:
        if check_wdpa_buffer(lat, lon, buffer_km=10):
            base["tripped_constraints"].append("Inside Protected Area buffer (10 km)")
    except Exception:
        pass

    if base["tripped_constraints"]:
        base["status"] = "excluded"
        base["exclusion_reason"] = "; ".join(base["tripped_constraints"])
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
# Candidate selection (FROZEN RULE v1)
# ---------------------------------------------------------------------------

def select_candidates(
    eligible: List[Dict[str, Any]],
    target: int,
) -> List[Dict[str, Any]]:
    """FROZEN SELECTION RULE v1 — see module docstring."""
    best: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for pt in eligible:
        region = get_region(pt["latitude"], pt["longitude"])
        existing = best.get(region)
        if existing is None or (pt["p1_score"] or 0) > (existing["p1_score"] or 0):
            best[region] = pt
    ranked = sorted(
        best.values(),
        key=lambda r: (-(r["p1_score"] or 0), r["latitude"], r["longitude"]),
    )
    return ranked[:target]


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

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
            "status": s["status"],
            "score": s.get("score"),
            "score_100": s.get("score_100"),
            "p1_score": s.get("p1_score"),
            "needs_review": s.get("needs_review", False),
            "needs_review_reasons": s.get("needs_review_reasons", []),
            "raw_criteria": s.get("raw_criteria", {}),
            "normalized_criteria": s.get("normalized_criteria", {}),
            "weights": s.get("weights", {}),
            "constraints": s.get("constraints", {}),
            "region": list(get_region(s["latitude"], s["longitude"])),
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
        "| Rank | Lat | Lon | AHP Score | /100 | GHI (kWh/m2/d) | Sub (km) | Tx line (km) | LC proxy | T2m (C) | Status |",
        "|------|-----|-----|-----------|------|----------------|----------|--------------|----------|---------|--------|",
    ]
    for rank, s in enumerate(selected, 1):
        raw = s.get("raw_criteria", {})
        ghi = raw.get("ghi_kwh_m2_day", s.get("p1_ghi", "-"))
        sub = raw.get("substation_dist_km", "-")
        line = raw.get("transmission_line_dist_km", "-")
        lc  = raw.get("landcost_proxy_score", "-")
        t2m = raw.get("t2m_celsius", s.get("p1_t2m", "-"))
        sc  = s.get("score") or s.get("p1_score", "-")
        sc100 = s.get("score_100") or (round(float(sc)*100,2) if sc != "-" else "-")
        st  = "NEEDS_REVIEW" if s["status"] == "needs_review" else s["status"]
        lines.append(
            f"| {rank} | {s['latitude']:.4f} | {s['longitude']:.4f} "
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(screen_point, lat, lon, w_ghi, w_temp): (lat, lon)
                for lat, lon in grid}
        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            results.append(res)
            _cb(res)

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
    selected_p1 = select_candidates(p1_sorted, target_n)

    regions_covered = len(set(get_region(r["latitude"], r["longitude"]) for r in p1_eligible))
    print(f"\n=== CANDIDATE SELECTION (FROZEN RULE v1) ===")
    print(f"  Regions with eligible hits : {regions_covered}/{REGION_ROWS*REGION_COLS}")
    print(f"  Selected for Phase 2       : {len(selected_p1)}")
    for i, s in enumerate(selected_p1, 1):
        print(f"    {i:>2}. ({s['latitude']:.4f}, {s['longitude']:.4f})  "
              f"p1_score={s['p1_score']:.4f}  "
              f"GHI={s.get('p1_ghi','?')}  T2m={s.get('p1_t2m','?')}  "
              f"region={get_region(s['latitude'],s['longitude'])}")

    # ── Phase 2: full score_candidate on selected sites ─────────────────────
    print(f"\n=== PHASE 2: Full AHP score + water-buffer ({len(selected_p1)} sites) ===")
    final: List[Dict[str, Any]] = []
    for i, s in enumerate(selected_p1, 1):
        lat, lon = s["latitude"], s["longitude"]
        print(f"  [{i}/{len(selected_p1)}] ({lat:.4f}, {lon:.4f}) ...", end=" ", flush=True)
        try:
            full = score_candidate(lat, lon, ahp_weights=w, footprint_radius_km=None)
            full["p1_score"] = s["p1_score"]
            full["p1_ghi"]   = s.get("p1_ghi")
            full["p1_t2m"]   = s.get("p1_t2m")
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
        "selection_rule_version": "v1",
        "selection_rule_frozen_date": "2026-09-12",
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
        description="Karnataka Solar PV — two-phase candidate generation"
    )
    parser.add_argument("--step",    type=float, default=0.1)
    parser.add_argument("--workers", type=int,   default=8)
    parser.add_argument("--target",  type=int,   default=DEFAULT_TARGET)
    args = parser.parse_args()
    if not (TARGET_MIN <= args.target <= TARGET_MAX):
        print(f"ERROR: --target must be {TARGET_MIN}-{TARGET_MAX}")
        sys.exit(1)
    run_sweep(step=args.step, max_workers=args.workers, target=args.target)
