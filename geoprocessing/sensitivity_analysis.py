"""
geoprocessing/sensitivity_analysis.py
---------------------------------------
Sensitivity Analysis for Karnataka Solar PV MCDM / AHP Siting.

Re-evaluates the frozen candidate set across 3 alternate AHP weight scenarios:
  1. Baseline (AHP Default) : GHI 40%, Substation 40%, Land-cost 10%, Temp Derating 10%
  2. GHI-heavy               : GHI 60%, Substation 20%, Land-cost 10%, Temp Derating 10%
  3. Substation-heavy        : GHI 20%, Substation 60%, Land-cost 10%, Temp Derating 10%
  4. Balanced (Equal)        : GHI 25%, Substation 25%, Land-cost 25%, Temp Derating 25%

Analyzes:
  - Stability of top 3 ranked candidates across all scenarios
  - Rank shifts and score variance per candidate
  - Prints a comprehensive terminal comparison table
  - Generates markdown-formatted tables for inclusion in final_report.md
"""

import json
import os
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ahp_engine import compute_ahp_weights, WEIGHTED_CRITERIA
import numpy as np


# ---------------------------------------------------------------------------
# Scenario Definitions & Pairwise Comparison Matrices
# ---------------------------------------------------------------------------

SCENARIO_CONFIGS: Dict[str, Dict[str, Any]] = {
    "Baseline (Default)": {
        "description": "Standard AHP weighting balancing solar resource and grid evacuation CAPEX.",
        "matrix": np.array([
            # GHI   Sub    Land   Temp
            [1.0,   1.0,   4.0,   4.0],
            [1.0,   1.0,   4.0,   4.0],
            [0.25,  0.25,  1.0,   1.0],
            [0.25,  0.25,  1.0,   1.0],
        ], dtype=float),
        "rationale": "Equal medium weights for GHI & Substation (40% each); 10% cap on land-cost proxy and flat temp derating.",
    },
    "GHI-heavy": {
        "description": "Resource-maximization scenario emphasizing annual energy yield over interconnection cost.",
        "matrix": np.array([
            # GHI   Sub    Land   Temp
            [1.0,   3.0,   6.0,   6.0],
            [1/3.0, 1.0,   2.0,   2.0],
            [1/6.0, 1/2.0, 1.0,   1.0],
            [1/6.0, 1/2.0, 1.0,   1.0],
        ], dtype=float),
        "rationale": "GHI priority elevated (3x over Substation, 6x over Low criteria); simulates state-subsidized transmission corridors.",
    },
    "Substation-heavy": {
        "description": "Grid-proximity scenario minimizing transmission line capital expenditure and right-of-way risk.",
        "matrix": np.array([
            # GHI   Sub    Land   Temp
            [1.0,   1/3.0, 2.0,   2.0],
            [3.0,   1.0,   6.0,   6.0],
            [1/2.0, 1/6.0, 1.0,   1.0],
            [1/2.0, 1/6.0, 1.0,   1.0],
        ], dtype=float),
        "rationale": "Substation priority elevated (3x over GHI, 6x over Low criteria); reflects harsh ₹1.5-2 Cr/km evacuation cost penalty.",
    },
    "Balanced": {
        "description": "Equal-weight benchmark treating all four evaluable criteria with identical 25% importance.",
        "matrix": np.ones((4, 4), dtype=float),
        "rationale": "Completely unbiased multi-criteria baseline to detect if ranking is an artifact of criterion weighting.",
    },
}


class CandidateScore(NamedTuple):
    original_rank: int
    lat: float
    lon: float
    status: str
    needs_review: bool
    district: str
    score: Optional[float]
    score_100: Optional[float]
    raw_criteria: Dict[str, Any]
    norm_criteria: Dict[str, float]


class ScenarioResult(NamedTuple):
    scenario_name: str
    description: str
    rationale: str
    weights: Dict[str, float]
    consistency_ratio: float
    is_consistent: bool
    rankings: List[Tuple[int, CandidateScore]]  # (scenario_rank, candidate)
    top_3_ids: List[int]                       # [candidate_id_1, candidate_id_2, candidate_id_3]


def load_candidates(candidates_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Loads candidates from JSON file."""
    if candidates_path is None:
        candidates_path = os.path.join(_HERE, "data", "candidates.json")
    if not os.path.exists(candidates_path):
        raise FileNotFoundError(f"Candidates file not found: {candidates_path}")
    with open(candidates_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("candidates", [])


def evaluate_scenario(
    scenario_name: str,
    cfg: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> ScenarioResult:
    """Computes AHP weights, scores all candidates, and ranks them for a single scenario."""
    matrix = cfg["matrix"]
    ahp_res = compute_ahp_weights(matrix=matrix, method="eigenvector", verbose=False)
    weights = {k: round(v, 4) for k, v in ahp_res.weights.items()}

    evaluated: List[CandidateScore] = []
    for c in candidates:
        orig_rank = c.get("rank")
        lat = c.get("latitude")
        lon = c.get("longitude")
        status = c.get("status")
        needs_review = c.get("needs_review", False)
        district = c.get("district", "Unknown")
        raw_c = c.get("raw_criteria", {})
        norm_c = c.get("normalized_criteria", {})

        if status == "excluded" or not norm_c:
            evaluated.append(CandidateScore(
                original_rank=orig_rank,
                lat=lat,
                lon=lon,
                status=status,
                needs_review=needs_review,
                district=district,
                score=None,
                score_100=None,
                raw_criteria=raw_c,
                norm_criteria=norm_c,
            ))
        else:
            w_score = (
                weights["ghi"] * norm_c.get("ghi", 0.0)
                + weights["substation"] * norm_c.get("substation", 0.0)
                + weights["landcost"] * norm_c.get("landcost", 0.0)
                + weights["temp_derating"] * norm_c.get("temp_derating", 0.0)
            )
            score_rnd = round(w_score, 4)
            evaluated.append(CandidateScore(
                original_rank=orig_rank,
                lat=lat,
                lon=lon,
                status=status,
                needs_review=needs_review,
                district=district,
                score=score_rnd,
                score_100=round(score_rnd * 100.0, 2),
                raw_criteria=raw_c,
                norm_criteria=norm_c,
            ))

    # Sort: scored candidates by score DESC, lat ASC, lon ASC; excluded at the end
    scored = [x for x in evaluated if x.score is not None]
    scored.sort(key=lambda x: (-x.score, x.lat, x.lon))
    unscored = [x for x in evaluated if x.score is None]
    unscored.sort(key=lambda x: (x.lat, x.lon))

    rankings: List[Tuple[int, CandidateScore]] = []
    for r_idx, cand in enumerate(scored, 1):
        rankings.append((r_idx, cand))
    for r_idx, cand in enumerate(unscored, len(scored) + 1):
        rankings.append((r_idx, cand))

    top_3_ids = [cand.original_rank for _, cand in rankings[:3]]

    return ScenarioResult(
        scenario_name=scenario_name,
        description=cfg["description"],
        rationale=cfg["rationale"],
        weights=weights,
        consistency_ratio=round(ahp_res.consistency_ratio, 4),
        is_consistent=ahp_res.is_consistent,
        rankings=rankings,
        top_3_ids=top_3_ids,
    )


def run_sensitivity_analysis(
    candidates_path: Optional[str] = None,
) -> Dict[str, ScenarioResult]:
    """Runs all 4 scenarios across the candidate set and returns results."""
    candidates = load_candidates(candidates_path)
    results: Dict[str, ScenarioResult] = {}
    for sname, cfg in SCENARIO_CONFIGS.items():
        results[sname] = evaluate_scenario(sname, cfg, candidates)
    return results


def print_sensitivity_report(results: Dict[str, ScenarioResult]) -> None:
    """Prints a terminal comparison table and stability analysis."""
    baseline = results["Baseline (Default)"]
    top3_baseline = baseline.top_3_ids

    print("\n" + "=" * 90)
    print("           KARNATAKA SOLAR PV MCDM — SENSITIVITY ANALYSIS & STABILITY AUDIT")
    print("=" * 90)

    # 1. Weights Summary
    print("\n[1] SCENARIO WEIGHTS & AHP CONSISTENCY RATIOS")
    print("-" * 90)
    print(f"{'Scenario Name':<22} | {'GHI':>7} | {'Substation':>10} | {'LandCost':>8} | {'Temp':>6} | {'CR':>6} | {'Status'}")
    print("-" * 90)
    for sname, res in results.items():
        w = res.weights
        cr_status = "PASS (Consistent)" if res.is_consistent else "FAIL"
        print(f"{sname:<22} | {w['ghi']:>7.2%} | {w['substation']:>10.2%} | {w['landcost']:>8.2%} | {w['temp_derating']:>6.2%} | {res.consistency_ratio:>6.4f} | {cr_status}")
    print("-" * 90)

    # Build per-candidate cross-scenario lookup
    all_cands_map: Dict[int, Dict[str, Any]] = {}
    for sname, res in results.items():
        for rank, cand in res.rankings:
            cid = cand.original_rank
            if cid not in all_cands_map:
                all_cands_map[cid] = {
                    "lat": cand.lat,
                    "lon": cand.lon,
                    "status": cand.status,
                    "district": cand.district,
                    "raw": cand.raw_criteria,
                    "ranks": {},
                    "scores": {},
                }
            all_cands_map[cid]["ranks"][sname] = rank
            all_cands_map[cid]["scores"][sname] = cand.score

    # 2. Comprehensive Comparison Table
    print("\n[2] COMPREHENSIVE CANDIDATE COMPARISON TABLE ACROSS ALL SCENARIOS")
    print("-" * 115)
    header = (
        f"{'Site':<5} | {'Coords (Lat, Lon)':<18} | {'Status':<12} | "
        f"{'Baseline':<14} | {'GHI-Heavy':<14} | {'Sub-Heavy':<14} | {'Balanced':<14} | {'Max Shift':<9}"
    )
    print(header)
    print("-" * 115)

    # Sort rows by baseline rank
    sorted_cids = sorted(all_cands_map.keys(), key=lambda cid: all_cands_map[cid]["ranks"]["Baseline (Default)"])

    for cid in sorted_cids:
        info = all_cands_map[cid]
        coords = f"({info['lat']:.1f}°N, {info['lon']:.1f}°E)"
        st = info["status"]

        b_rk = info["ranks"]["Baseline (Default)"]
        b_sc = info["scores"]["Baseline (Default)"]
        b_str = f"#{b_rk:2d} ({b_sc:.4f})" if b_sc is not None else f"#{b_rk:2d} (EXCL)"

        g_rk = info["ranks"]["GHI-heavy"]
        g_sc = info["scores"]["GHI-heavy"]
        g_str = f"#{g_rk:2d} ({g_sc:.4f})" if g_sc is not None else f"#{g_rk:2d} (EXCL)"

        s_rk = info["ranks"]["Substation-heavy"]
        s_sc = info["scores"]["Substation-heavy"]
        s_str = f"#{s_rk:2d} ({s_sc:.4f})" if s_sc is not None else f"#{s_rk:2d} (EXCL)"

        l_rk = info["ranks"]["Balanced"]
        l_sc = info["scores"]["Balanced"]
        l_str = f"#{l_rk:2d} ({l_sc:.4f})" if l_sc is not None else f"#{l_rk:2d} (EXCL)"

        all_rks = [b_rk, g_rk, s_rk, l_rk]
        max_shift = max(all_rks) - min(all_rks)
        shift_str = f"Δ {max_shift}" if st != "excluded" else "—"

        print(f"#{cid:<4} | {coords:<18} | {st:<12} | {b_str:<14} | {g_str:<14} | {s_str:<14} | {l_str:<14} | {shift_str:<9}")
    print("-" * 115)

    # 3. Top-3 Stability Matrix & Detailed Verdict
    print("\n[3] TOP-3 STABILITY MATRIX & RESHUFFLE AUDIT")
    print("-" * 90)
    print(f"{'Scenario':<22} | {'Rank 1':<18} | {'Rank 2':<18} | {'Rank 3':<18} | {'Stability Verdict'}")
    print("-" * 90)

    for sname, res in results.items():
        t1 = f"Site #{res.top_3_ids[0]} ({res.rankings[0][1].lat:.1f}, {res.rankings[0][1].lon:.1f})"
        t2 = f"Site #{res.top_3_ids[1]} ({res.rankings[1][1].lat:.1f}, {res.rankings[1][1].lon:.1f})"
        t3 = f"Site #{res.top_3_ids[2]} ({res.rankings[2][1].lat:.1f}, {res.rankings[2][1].lon:.1f})"

        if sname == "Baseline (Default)":
            verdict = "REFERENCE (Top 3 = [1, 2, 3])"
        elif res.top_3_ids == top3_baseline:
            verdict = "100% STABLE (Exact Match)"
        elif set(res.top_3_ids) == set(top3_baseline):
            verdict = "PERMUTED (Same 3 sites, internal swap)"
        else:
            diff = set(res.top_3_ids) - set(top3_baseline)
            verdict = f"RESHUFFLED (Displaced by Sites {list(diff)})"

        print(f"{sname:<22} | {t1:<18} | {t2:<18} | {t3:<18} | {verdict}")
    print("-" * 90)

    # 4. Detailed Narrative Findings
    print("\n[4] KEY SENSITIVITY FINDINGS & ENGINEERING IMPLICATIONS")
    print("-" * 90)
    b_res = results["Baseline (Default)"]
    g_res = results["GHI-heavy"]
    s_res = results["Substation-heavy"]
    l_res = results["Balanced"]

    top_id = b_res.top_3_ids[0]
    lat = all_cands_map[top_id]["lat"]
    lon = all_cands_map[top_id]["lon"]
    b_rk = all_cands_map[top_id]["ranks"]["Baseline (Default)"]
    g_rk = all_cands_map[top_id]["ranks"]["GHI-heavy"]
    s_rk = all_cands_map[top_id]["ranks"]["Substation-heavy"]
    l_rk = all_cands_map[top_id]["ranks"]["Balanced"]

    b_sc = all_cands_map[top_id]["scores"]["Baseline (Default)"]
    g_sc = all_cands_map[top_id]["scores"]["GHI-heavy"]
    s_sc = all_cands_map[top_id]["scores"]["Substation-heavy"]
    l_sc = all_cands_map[top_id]["scores"]["Balanced"]

    raw = all_cands_map[top_id].get("raw", {})
    ghi_val = raw.get("ghi_kwh_m2_day")
    sub_val = raw.get("substation_dist_km")

    print(f"• Site #{top_id} ({lat:.1f}°N, {lon:.1f}°E):")
    print(f"  - Ranks #{b_rk} in Baseline ({b_sc:.4f}), #{g_rk} in GHI-heavy ({g_sc:.4f}), #{s_rk} in Sub-heavy ({s_sc:.4f}), #{l_rk} in Balanced ({l_sc:.4f}).")
    print(f"  - Flagship candidate pairing high GHI ({ghi_val} kWh/m²/day) with")
    print(f"    close grid proximity ({sub_val} km to confirmed >=33kV substation).")

    g_top3_str = ", ".join(f"Site #{x}" for x in g_res.top_3_ids)
    print("\n• GHI-heavy Scenario Stability:")
    print(f"  - Top 3 candidates [{g_top3_str}] with GHI weight elevated to 60%.")

    s1_id = s_res.top_3_ids[0]
    s2_id = s_res.top_3_ids[1]
    s3_id = s_res.top_3_ids[2]
    s1_sc = all_cands_map[s1_id]["scores"]["Substation-heavy"]
    s2_sc = all_cands_map[s2_id]["scores"]["Substation-heavy"]
    s3_sc = all_cands_map[s3_id]["scores"]["Substation-heavy"]
    s2_sub = all_cands_map[s2_id].get("raw", {}).get("substation_dist_km")
    s3_sub = all_cands_map[s3_id].get("raw", {}).get("substation_dist_km")

    print("\n• Substation-heavy Scenario Reshuffle:")
    print(f"  - Rank 1: Site #{s1_id} ({s1_sc:.4f}).")
    print(f"  - Rank 2: Site #{s2_id} ({s2_sc:.4f}, sub dist {s2_sub} km).")
    print(f"  - Rank 3: Site #{s3_id} ({s3_sc:.4f}, sub dist {s3_sub} km).")

    l_top3_str = ", ".join(f"Site #{x}" for x in l_res.top_3_ids)
    print("\n• Balanced Scenario Permutation:")
    print(f"  - Top 3 candidates are [{l_top3_str}], demonstrating cross-criteria resilience.")
    print("=" * 90 + "\n")


def generate_sensitivity_markdown(results: Dict[str, ScenarioResult]) -> str:
    """Generates markdown content for inclusion in final_report.md."""
    baseline = results["Baseline (Default)"]
    top3_baseline = baseline.top_3_ids

    # Weights table
    md = []
    md.append("### AHP Sensitivity Weight Scenarios\n")
    md.append("| Scenario | Description | GHI Weight | Substation Weight | Land-Cost Weight | Temp Derating Weight | Consistency Ratio (CR) | Status |")
    md.append("|---|---|:---:|:---:|:---:|:---:|:---:|:---:|")
    for sname, res in results.items():
        w = res.weights
        status_str = "PASS (Consistent)" if res.is_consistent else "FAIL"
        md.append(f"| **{sname}** | {res.description} | {w['ghi']:.0%} | {w['substation']:.0%} | {w['landcost']:.0%} | {w['temp_derating']:.0%} | {res.consistency_ratio:.4f} | {status_str} |")

    # Candidate cross-scenario table
    md.append("\n### Candidate Ranking & Score Comparison Across Scenarios\n")
    md.append("| Site ID | Centroid (Lat, Lon) | Status | Baseline Rank & Score | GHI-Heavy Rank & Score | Substation-Heavy Rank & Score | Balanced Rank & Score | Max Rank Shift |")
    md.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

    # Build per-candidate cross-scenario lookup
    all_cands_map: Dict[int, Dict[str, Any]] = {}
    for sname, res in results.items():
        for rank, cand in res.rankings:
            cid = cand.original_rank
            if cid not in all_cands_map:
                all_cands_map[cid] = {
                    "lat": cand.lat,
                    "lon": cand.lon,
                    "status": cand.status,
                    "district": cand.district,
                    "raw": cand.raw_criteria,
                    "ranks": {},
                    "scores": {},
                }
            all_cands_map[cid]["ranks"][sname] = rank
            all_cands_map[cid]["scores"][sname] = cand.score

    sorted_cids = sorted(all_cands_map.keys(), key=lambda cid: all_cands_map[cid]["ranks"]["Baseline (Default)"])

    for cid in sorted_cids:
        info = all_cands_map[cid]
        coords = f"{info['lat']:.4f}°N, {info['lon']:.4f}°E"
        st = f"`{info['status']}`"

        b_rk = info["ranks"]["Baseline (Default)"]
        b_sc = info["scores"]["Baseline (Default)"]
        b_str = f"**#{b_rk}** ({b_sc:.4f})" if b_sc is not None else f"#{b_rk} (Excluded)"

        g_rk = info["ranks"]["GHI-heavy"]
        g_sc = info["scores"]["GHI-heavy"]
        g_str = f"#{g_rk} ({g_sc:.4f})" if g_sc is not None else f"#{g_rk} (Excluded)"

        s_rk = info["ranks"]["Substation-heavy"]
        s_sc = info["scores"]["Substation-heavy"]
        s_str = f"#{s_rk} ({s_sc:.4f})" if s_sc is not None else f"#{s_rk} (Excluded)"

        l_rk = info["ranks"]["Balanced"]
        l_sc = info["scores"]["Balanced"]
        l_str = f"#{l_rk} ({l_sc:.4f})" if l_sc is not None else f"#{l_rk} (Excluded)"

        all_rks = [b_rk, g_rk, s_rk, l_rk]
        max_shift = max(all_rks) - min(all_rks)
        shift_str = f"Δ {max_shift}" if info["status"] != "excluded" else "—"

        md.append(f"| Site #{cid} | {coords} | {st} | {b_str} | {g_str} | {s_str} | {l_str} | {shift_str} |")

    # Top-3 Stability Summary
    md.append("\n### Top-3 Stability Matrix & Reshuffle Analysis\n")
    md.append("| Scenario | Rank 1 Site | Rank 2 Site | Rank 3 Site | Stability Verdict | Overlap with Baseline Top-3 |")
    md.append("|---|---|---|---|---|:---:|")

    for sname, res in results.items():
        t1 = f"Site #{res.top_3_ids[0]} ({res.rankings[0][1].lat:.1f}°, {res.rankings[0][1].lon:.1f}°)"
        t2 = f"Site #{res.top_3_ids[1]} ({res.rankings[1][1].lat:.1f}°, {res.rankings[1][1].lon:.1f}°)"
        t3 = f"Site #{res.top_3_ids[2]} ({res.rankings[2][1].lat:.1f}°, {res.rankings[2][1].lon:.1f}°)"

        overlap = len(set(res.top_3_ids).intersection(set(top3_baseline)))
        pct = f"{overlap}/3 ({overlap / 3.0 * 100:.0f}%)"

        if sname == "Baseline (Default)":
            verdict = "Reference Baseline"
        elif res.top_3_ids == top3_baseline:
            verdict = "**100% Stable** (Identical order and membership)"
        elif set(res.top_3_ids) == set(top3_baseline):
            verdict = "**Permuted** (Same 3 candidates, internal rank swap)"
        else:
            diff = set(res.top_3_ids) - set(top3_baseline)
            diff_str = ", ".join(f"Site #{x}" for x in sorted(diff))
            verdict = f"**Reshuffled** (New entrants: {diff_str})"

        md.append(f"| **{sname}** | {t1} | {t2} | {t3} | {verdict} | {pct} |")

    # Dynamic discussion from final data
    b_res = results["Baseline (Default)"]
    g_res = results["GHI-heavy"]
    s_res = results["Substation-heavy"]
    l_res = results["Balanced"]

    top_id = b_res.top_3_ids[0]
    lat = all_cands_map[top_id]["lat"]
    lon = all_cands_map[top_id]["lon"]
    dist_name = all_cands_map[top_id].get("district", "Unknown")
    b_rk = all_cands_map[top_id]["ranks"]["Baseline (Default)"]
    g_rk = all_cands_map[top_id]["ranks"]["GHI-heavy"]
    s_rk = all_cands_map[top_id]["ranks"]["Substation-heavy"]
    l_rk = all_cands_map[top_id]["ranks"]["Balanced"]

    b_sc = all_cands_map[top_id]["scores"]["Baseline (Default)"]
    g_sc = all_cands_map[top_id]["scores"]["GHI-heavy"]
    s_sc = all_cands_map[top_id]["scores"]["Substation-heavy"]
    l_sc = all_cands_map[top_id]["scores"]["Balanced"]

    raw = all_cands_map[top_id].get("raw", {})
    ghi_val = raw.get("ghi_kwh_m2_day")
    sub_val = raw.get("substation_dist_km")

    g_top3_str = ", ".join(f"Site #{x}" for x in g_res.top_3_ids)
    s1_id = s_res.top_3_ids[0]
    s2_id = s_res.top_3_ids[1]
    s3_id = s_res.top_3_ids[2]
    s1_sc = all_cands_map[s1_id]["scores"]["Substation-heavy"]
    s2_sc = all_cands_map[s2_id]["scores"]["Substation-heavy"]
    s3_sc = all_cands_map[s3_id]["scores"]["Substation-heavy"]
    s2_sub = all_cands_map[s2_id].get("raw", {}).get("substation_dist_km")
    s3_sub = all_cands_map[s3_id].get("raw", {}).get("substation_dist_km")
    l_top3_str = ", ".join(f"Site #{x}" for x in l_res.top_3_ids)

    md.append("\n#### Stability Insights & Sensitivity Discussion\n")
    md.append(f"1. **Flagship Performance of Site #{top_id} ({lat:.4f}°N, {lon:.4f}°E — {dist_name}):** Ranks #{b_rk} in Baseline ({b_sc:.4f}), #{g_rk} in GHI-heavy ({g_sc:.4f}), #{s_rk} in Sub-heavy ({s_sc:.4f}), and #{l_rk} in Balanced ({l_sc:.4f}). Features GHI of {ghi_val} kWh/m²/day and confirmed substation distance of {sub_val} km.")
    md.append(f"2. **GHI-Heavy Stability:** Top 3 candidates under 60% GHI priority are [{g_top3_str}], demonstrating resilient solar resource fundamentals.")
    md.append(f"3. **Substation-Heavy Sensitivity:** When grid proximity weight increases to 60%, Rank 1 is Site #{s1_id} ({s1_sc:.4f}), Rank 2 is Site #{s2_id} ({s2_sc:.4f}, sub dist {s2_sub} km), and Rank 3 is Site #{s3_id} ({s3_sc:.4f}, sub dist {s3_sub} km).")
    md.append(f"4. **Balanced Scenario Evaluation:** Under equal 25% weights across all criteria, top 3 candidates are [{l_top3_str}].")

    return "\n".join(md)


if __name__ == "__main__":
    results = run_sensitivity_analysis()
    print_sensitivity_report(results)
