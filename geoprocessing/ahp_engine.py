"""
geoprocessing/ahp_engine.py
----------------------------
Analytic Hierarchy Process (AHP) & Multi-Criteria Decision Making (MCDM) scoring engine.

Criteria Reference (criteria.md):
----------------------------------
The 7 criteria are classified as follows:
  1. Solar Irradiation (GHI)             : Evaluated criterion (Weight Class: Medium)
  2. Slope                               : Hard constraint pre-filter (<= 5% max slope)
  3. Land-use suitability                : Hard constraint pre-filter (forest, urban, water excluded)
  4. Distance to substation              : Evaluated criterion (Weight Class: Medium)
  5. Protected area & water buffer       : Hard constraint pre-filter (10km PA, 500m water buffer)
  6. Land-cost proxy                     : Evaluated criterion (Weight Class: Low)
  7. Temperature derating                : Evaluated criterion (Weight Class: Low)

Because criteria 2, 3, and 5 are non-negotiable hard constraints applied as boolean
pre-filters, only the 4 evaluable criteria (GHI, Substation, Land-cost, Temp derating)
receive positive AHP weights from the pairwise comparison matrix.
"""

from typing import Dict, List, NamedTuple, Optional, Tuple
import numpy as np

# Criteria identifiers
CRITERIA_ALL: List[str] = [
    "ghi",               # Index 0: Solar Irradiation (GHI) - Medium
    "slope",             # Index 1: Slope - Hard constraint (no weight)
    "landuse",           # Index 2: Land-use suitability - Hard constraint (no weight)
    "substation",        # Index 3: Distance to substation - Medium
    "protected_buffer",  # Index 4: Protected/water buffer - Hard constraint (no weight)
    "landcost",          # Index 5: Land-cost proxy - Low
    "temp_derating",     # Index 6: Temperature derating - Low
]

WEIGHTED_CRITERIA: List[str] = ["ghi", "substation", "landcost", "temp_derating"]
WEIGHTED_INDICES: List[int] = [0, 3, 5, 6]

# Saaty Random Index (RI) lookup table for matrix dimension n
RI_TABLE: Dict[int, float] = {
    1: 0.00,
    2: 0.00,
    3: 0.58,
    4: 0.90,
    5: 1.12,
    6: 1.24,
    7: 1.32,
    8: 1.41,
    9: 1.45,
    10: 1.49,
}

# ---------------------------------------------------------------------------
# Starting Pairwise Comparison Matrices
# ---------------------------------------------------------------------------
# Scale:
#   1 = Equal importance
#   3 = Moderate importance
#   4 = Moderate to strong importance
#
# Comparative Rationale based on criteria.md:
#   - GHI vs Substation: 1.0 (both Medium weight class)
#   - GHI vs Land-cost: 4.0 (Medium vs Low proxy; land-cost weight capped <= 10% per Criterion 6 rule)
#   - GHI vs Temp derating: 4.0 (Medium vs Low; temp derating has flat ~1.2% spread)
#   - Substation vs Land-cost: 4.0 (Medium vs Low proxy; land-cost weight capped <= 10% per Criterion 6 rule)
#   - Substation vs Temp derating: 4.0 (Medium vs Low; substation CAPEX >> temp spread)
#   - Land-cost vs Temp derating: 1.0 (both Low weight class, kept <= 10%)

DEFAULT_4X4_MATRIX = np.array([
    # GHI   Sub    Land   Temp
    [1.0,   1.0,   4.0,   4.0],   # GHI
    [1.0,   1.0,   4.0,   4.0],   # Substation
    [1/4.0, 1/4.0, 1.0,   1.0],   # Land-cost
    [1/4.0, 1/4.0, 1.0,   1.0],   # Temp derating
], dtype=float)

# Starting 7x7 matrix embedding the 4 evaluable criteria at indices [0, 3, 5, 6]
# and unweighted hard constraints on the diagonal.
DEFAULT_7X7_MATRIX = np.eye(7, dtype=float)
for i_idx, r in enumerate(WEIGHTED_INDICES):
    for j_idx, c in enumerate(WEIGHTED_INDICES):
        DEFAULT_7X7_MATRIX[r, c] = DEFAULT_4X4_MATRIX[i_idx, j_idx]


class AHPResult(NamedTuple):
    weights: Dict[str, float]           # Evaluated criteria weights summing to 1.0
    all_weights: Dict[str, float]       # All 7 criteria weights (hard constraints = 0.0)
    consistency_ratio: float            # CR
    consistency_index: float            # CI
    lambda_max: float                   # Principal eigenvalue
    is_consistent: bool                 # True if CR <= 0.10
    method: str                         # "eigenvector" or "column_average"
    matrix: np.ndarray                  # The 4x4 comparison submatrix evaluated


def compute_ahp_weights(
    matrix: Optional[np.ndarray] = None,
    method: str = "eigenvector",
    verbose: bool = True,
) -> AHPResult:
    """
    Computes priority weights and Consistency Ratio (CR) from a pairwise comparison matrix.

    Parameters
    ----------
    matrix : np.ndarray, optional
        Either a 7x7 comparison matrix (where indices [0, 3, 5, 6] correspond to
        GHI, Substation, Land-cost, Temp derating) or a 4x4 matrix for those 4 criteria.
        If None, DEFAULT_7X7_MATRIX is used.
    method : str
        "eigenvector" (exact principal eigenvector) or "column_average" (normalized column sum).
    verbose : bool
        If True, prints computed weights, lambda_max, CI, and CR. If CR > 0.1, prints an
        explicit warning.

    Returns
    -------
    AHPResult
    """
    if matrix is None:
        raw_matrix = DEFAULT_7X7_MATRIX.copy()
    else:
        raw_matrix = np.array(matrix, dtype=float)

    if raw_matrix.shape == (7, 7):
        # Extract the 4x4 submatrix for the 4 evaluable criteria
        sub_matrix = raw_matrix[np.ix_(WEIGHTED_INDICES, WEIGHTED_INDICES)].copy()
    elif raw_matrix.shape == (4, 4):
        sub_matrix = raw_matrix.copy()
    else:
        raise ValueError(
            f"Expected matrix of shape (7, 7) or (4, 4), got {raw_matrix.shape}"
        )

    n = len(sub_matrix)
    ri = RI_TABLE.get(n, 0.90)

    # 1. Compute priority weights
    if method == "eigenvector":
        eigenvalues, eigenvectors = np.linalg.eig(sub_matrix)
        max_idx = int(np.argmax(np.real(eigenvalues)))
        lambda_max = float(np.real(eigenvalues[max_idx]))
        raw_weights = np.real(eigenvectors[:, max_idx])
        weights_vec = raw_weights / np.sum(raw_weights)
    elif method == "column_average":
        col_sums = np.sum(sub_matrix, axis=0)
        norm_matrix = sub_matrix / col_sums
        weights_vec = np.mean(norm_matrix, axis=1)
        # Approximate lambda_max from A * w = lambda * w
        Aw = np.dot(sub_matrix, weights_vec)
        lambda_max = float(np.mean(Aw / weights_vec))
    else:
        raise ValueError(f"Unknown method '{method}', must be 'eigenvector' or 'column_average'")

    # 2. Consistency Index and Consistency Ratio
    ci = (lambda_max - n) / (n - 1) if n > 1 else 0.0
    if ci < 0.0 and abs(ci) < 1e-10:
        ci = 0.0
    cr = (ci / ri) if ri > 0 else 0.0
    is_consistent = (cr <= 0.10)

    weights_dict = {name: float(w) for name, w in zip(WEIGHTED_CRITERIA, weights_vec)}

    all_weights_dict = {name: 0.0 for name in CRITERIA_ALL}
    for name, w in weights_dict.items():
        all_weights_dict[name] = w

    if verbose:
        print("=" * 60)
        print("AHP / MCDM Weight Computation Engine")
        print("=" * 60)
        print(f"Matrix Dimension: {n}x{n} (Evaluated Criteria: {', '.join(WEIGHTED_CRITERIA)})")
        print(f"Calculation Method: {method}")
        print("-" * 60)
        print("Pairwise Comparison Matrix:")
        col_header = "       " + " ".join(f"{c:>12}" for c in WEIGHTED_CRITERIA)
        print(col_header)
        for i, row_name in enumerate(WEIGHTED_CRITERIA):
            row_str = f"{row_name:<7}" + " ".join(f"{sub_matrix[i, j]:12.4f}" for j in range(n))
            print(row_str)
        print("-" * 60)
        print("Derived Priority Weights:")
        for name, w in weights_dict.items():
            print(f"  - {name:<16}: {w:.4f} ({w * 100:.2f}%)")
        print("-" * 60)
        print(f"Principal Eigenvalue (λ_max) : {lambda_max:.4f}")
        print(f"Consistency Index (CI)        : {ci:.4f}")
        print(f"Random Index (RI, n={n})       : {ri:.2f}")
        print(f"Consistency Ratio (CR)        : {cr:.4f} ({cr * 100:.2f}%)")

        if not is_consistent:
            print("\n" + "!" * 60)
            print(f"WARNING: Consistency Ratio (CR = {cr:.4f}) EXCEEDS 0.10!")
            print("The pairwise comparison matrix is inconsistent and the weights are unreliable.")
            print("!" * 60 + "\n")
        else:
            print(f"Status: PASS — Matrix is consistent (CR <= 0.10)\n")

    return AHPResult(
        weights=weights_dict,
        all_weights=all_weights_dict,
        consistency_ratio=cr,
        consistency_index=ci,
        lambda_max=lambda_max,
        is_consistent=is_consistent,
        method=method,
        matrix=sub_matrix,
    )


if __name__ == "__main__":
    compute_ahp_weights()
