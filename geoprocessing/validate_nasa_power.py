"""
geoprocessing/validate_nasa_power.py
--------------------------------------
Smoke-test the NASA POWER connector against three known Karnataka points
whose GHI ranges are described in criteria.md.

Usage:
    python geoprocessing/validate_nasa_power.py

Expected GHI ranges (criteria.md § Criterion 1, § Compounding Data Uncertainty):
  - Northern arid zone (Kalaburagi): ~5.8–6.2 kWh/m²/day
  - Coastal zone (Mangalore):        ~4.5–5.0 kWh/m²/day
  - Bengaluru:                        no expected range; printed for sanity check
"""

import sys
from fetch_nasa_power import NASAPowerResult, fetch_climatology

# ---------------------------------------------------------------------------
# Validation fixtures
# ---------------------------------------------------------------------------

# Each entry: (label, lat, lon, ghi_min, ghi_max)
# ghi_min/ghi_max are None where criteria.md gives no specific expected range.
VALIDATION_POINTS: list[tuple[str, float, float, float | None, float | None]] = [
    (
        "Kalaburagi (northern arid zone)",
        17.33, 76.84,
        5.8, 6.2,   # criteria.md: "northern arid zone, Kalaburagi/Bidar/Raichur districts"
    ),
    (
        "Mangalore / Dakshina Kannada (coastal zone)",
        12.87, 74.88,
        4.5, 5.0,   # criteria.md: "4.5 kWh/m²/day (coastal/Western Ghats)"
    ),
    (
        "Bengaluru (sanity spot-check — no expected range in criteria.md)",
        12.97, 77.59,
        None, None,
    ),
    (
        "Jaisalmer, Rajasthan (far outside Karnataka, desert expected >7.0)",
        26.92, 70.90,
        7.0, 8.0,
    ),
    (
        "Bidar",
        17.91, 77.52,
        None, None,
    ),
    (
        "Raichur",
        16.21, 77.36,
        None, None,
    ),
    (
        "Yadgir",
        16.77, 77.14,
        None, None,
    ),
    (
        "Tumakuru (plateau, near Bengaluru)",
        13.34, 77.10,
        None, None,
    ),
    (
        "Chikkaballapur (plateau, near Bengaluru)",
        13.44, 77.73,
        None, None,
    ),
]

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_COL = 60   # label column width

def _divider() -> None:
    print("-" * 72)

def _header() -> None:
    _divider()
    print("NASA POWER Climatology — Karnataka Validation")
    print("  Source: https://power.larc.nasa.gov/api/temporal/climatology/point")
    print("  Parameters: ALLSKY_SFC_SW_DWN (GHI), T2M, T2M_MAX")
    print("  Temporal coverage: 1994–2023 climatological average")
    _divider()

def _print_result(
    label: str,
    result: NASAPowerResult,
    ghi_min: float | None,
    ghi_max: float | None,
) -> bool:
    """
    Print a formatted result block. Returns True if validation passed
    (or no expected range defined), False if a WARNING was triggered.
    """
    print(f"\nPoint : {label}")
    print(f"  Lat / Lon     : {result.latitude:.4f}°N, {result.longitude:.4f}°E")
    print(f"  GHI           : {result.ghi_kwh_m2_day:.4f} kWh/m²/day")
    print(f"  T2M (mean)    : {result.t2m_celsius:.2f} °C")
    print(f"  T2M_MAX (mean): {result.t2m_max_celsius:.2f} °C")

    passed = True
    if ghi_min is not None and ghi_max is not None:
        expected_str = f"{ghi_min}–{ghi_max} kWh/m²/day (criteria.md)"
        if ghi_min <= result.ghi_kwh_m2_day <= ghi_max:
            print(f"  GHI check     : ✓ PASS — within expected {expected_str}")
        else:
            print(
                f"\n  ⚠  WARNING: GHI value {result.ghi_kwh_m2_day:.4f} kWh/m²/day is "
                f"OUTSIDE the expected range {expected_str}\n"
                f"     Possible causes:\n"
                f"       • Wrong parameter code (check ALLSKY_SFC_SW_DWN vs ALLSKY_SFC_SW_DNI)\n"
                f"       • Lat/lon swapped in the API call\n"
                f"       • API returned a different unit than kWh/m²/day\n"
                f"       • NASA POWER grid cell straddles a coast/mountain boundary\n"
                f"     Do NOT proceed to scoring until this is investigated."
            )
            passed = False
    else:
        print("  GHI check     : ℹ no expected range specified — inspect value manually")

    return passed

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _header()

    all_passed = True
    warnings: list[str] = []
    results_list = []

    for label, lat, lon, ghi_min, ghi_max in VALIDATION_POINTS:
        print(f"\nFetching: {label}  ({lat}, {lon}) …")
        try:
            result = fetch_climatology(lat, lon)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ ERROR fetching data: {exc}")
            all_passed = False
            warnings.append(f"{label}: fetch failed — {exc}")
            continue

        ok = _print_result(label, result, ghi_min, ghi_max)
        results_list.append((label, result.ghi_kwh_m2_day))
        
        if not ok:
            all_passed = False
            warnings.append(
                f"{label}: GHI {result.ghi_kwh_m2_day:.4f} outside "
                f"expected {ghi_min}–{ghi_max} kWh/m²/day"
            )

    # Summary Table
    _divider()
    print("\nSummary Table (Sorted by GHI Descending)")
    print(f"{'Location':<70} | {'GHI (kWh/m²/day)':<15}")
    print("-" * 88)
    results_list.sort(key=lambda x: x[1], reverse=True)
    for label, ghi in results_list:
        print(f"{label:<70} | {ghi:.4f}")

    _divider()
    if all_passed:
        print("\n✓ All validation checks passed. NASA POWER connector is operating correctly.")
    else:
        print("\n⚠  VALIDATION FAILED — do not proceed to scoring/matrix phase.")
        print("   Issues detected:")
        for w in warnings:
            print(f"   • {w}")
        sys.exit(1)

    _divider()


if __name__ == "__main__":
    main()
