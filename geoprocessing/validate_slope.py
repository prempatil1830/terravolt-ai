"""
geoprocessing/validate_slope.py
---------------------------------
Smoke-test the slope connector against three terrain-distinct Karnataka points
whose expected slope ranges are physically well-understood.

Usage:
    python geoprocessing/validate_slope.py

Exit codes:
    0  -- all checks passed (flat points low, escarpment point high)
    1  -- one or more checks FAILED (do NOT proceed to scoring)

Validation logic
----------------
  Point A -- Kalaburagi (17.33N, 76.84E): Deccan Plateau interior.
             This is classic flat tableland; SRTM/COP-DEM slope should be
             well under 3%.  WARN if >= 3%.

  Point B -- Near Madikeri/Kodagu (12.42N, 75.74E): Western Ghats escarpment.
             One of the steepest gradients in Karnataka; slope should be
             clearly above 10%.  WARN if < 10%.

  Point C -- Tannirbhavi beach, Mangalore coast (12.899N, 74.800E): tidal
             coastal flat.  Should be flat (< 3%).  WARN if >= 3%.
             NOTE: 12.87N/74.88E is inland Mangalore lateritic plateau (~45m
             elev, genuinely 3.5% slope) -- NOT coastal flat.  The Karnataka
             coast is narrow; the coastal plain lies west of ~74.84E here.

  Cross-check: escarpment slope MUST be strictly greater than both flat points.
             WARN if that ordering is violated -- this is the sentinel for a
             raster-wide systematic error (wrong CRS, inverted gradient, etc.).

Rationale for thresholds
-------------------------
  The 3% flat-point ceiling and 10% escarpment floor are conservative:
  Karnataka SRTM studies (e.g. KSRDB reports) record Kodagu gradients of
  15-40% and Kalaburagi gradients of 0.5-1.5%.  Using 3% / 10% leaves a
  wide margin for the ~30m pixel to straddle gentle local relief.

Silent-wrong-output risks guarded against
------------------------------------------
  1. Wrong CRS (e.g. metres treated as degrees) -> wildly wrong slopes.
  2. Gradient computed in pixel units instead of metres -> ~111x error.
  3. Wrong tile downloaded (lat/lon swapped in URL) -> ocean/flat tile.
  4. DEM void-fill not applied -> -32768 sentinel propagated as elevation.
  5. Mosaic resampling artefact at tile boundaries -> checked at interior pts.
"""

from __future__ import annotations

import sys
import os

# Allow running from the project root as well as from within geoprocessing/.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from fetch_slope import build_slope_raster, get_slope   # noqa: E402


# ---------------------------------------------------------------------------
# Validation fixtures
# ---------------------------------------------------------------------------

# Each entry: (label, lat, lon, slope_min_expected, slope_max_expected, terrain_type)
# slope_min/max are the sanity bounds:
#   flat  points: expect slope < flat_ceil
#   steep points: expect slope > steep_floor
# terrain_type: "flat" or "steep" (controls which direction to warn)

_FLAT_CEIL  = 3.0    # % -- flat points MUST be below this
_STEEP_FLOOR = 10.0  # % -- escarpment point MUST be above this

VALIDATION_POINTS = [
    {
        "label": "Kalaburagi — Deccan Plateau interior (flat)",
        "lat": 17.33,
        "lon": 76.84,
        "terrain": "flat",
        "expected_range": f"< {_FLAT_CEIL}%",
    },
    {
        "label": "Madikeri / Kodagu — Western Ghats escarpment (steep)",
        "lat": 12.42,
        "lon": 75.74,
        "terrain": "steep",
        "expected_range": f"> {_STEEP_FLOOR}%",
    },
    {
        # Tannirbhavi beach: unambiguous tidal coastal flat, DEM shows 0-1m elev.
        # NOTE: 12.87N/74.88E is inland lateritic plateau (~45m, genuinely 3.5%)
        #       -- NOT representative of the flat coastal zone for this check.
        "label": "Tannirbhavi beach, Mangalore — tidal coastal flat (flat)",
        "lat": 12.899,
        "lon": 74.800,
        "terrain": "flat",
        "expected_range": f"< {_FLAT_CEIL}%",
    },
]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _divider() -> None:
    print("-" * 72)


def _header() -> None:
    _divider()
    print("Slope Connector Validation — Karnataka Terrain Sanity Check")
    print("  Source  : Copernicus DEM GLO-30 (AWS public bucket, no auth)")
    print("  Method  : numpy.gradient finite differences, WGS-84 ellipsoid")
    print(f"  Flat ceil   : {_FLAT_CEIL}%  (Deccan Plateau, coastal plains)")
    print(f"  Steep floor : {_STEEP_FLOOR}%  (Western Ghats escarpment)")
    _divider()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _header()

    print("\nStep 1: Ensure slope raster is built (may download on first run) ...")
    build_slope_raster()
    print()

    _divider()
    print("Step 2: Query three terrain-distinct points\n")

    results: dict[str, float] = {}
    warnings_list: list[str] = []
    all_passed = True

    for pt in VALIDATION_POINTS:
        label   = pt["label"]
        lat     = pt["lat"]
        lon     = pt["lon"]
        terrain = pt["terrain"]
        expected_range = pt["expected_range"]

        print(f"Point : {label}")
        print(f"  Coordinates   : {lat:.4f}N, {lon:.4f}E")
        print(f"  Terrain type  : {terrain}")
        print(f"  Expected slope: {expected_range}")

        try:
            slope = get_slope(lat, lon)
        except Exception as exc:
            msg = f"ERROR fetching slope: {exc}"
            print(f"  Result        : ✗ {msg}\n")
            warnings_list.append(f"{label}: {msg}")
            all_passed = False
            continue

        results[label] = slope
        print(f"  Measured slope: {slope:.3f}%")

        if terrain == "flat":
            if slope < _FLAT_CEIL:
                print(f"  Check         : PASS  (slope {slope:.3f}% is below flat ceiling {_FLAT_CEIL}%)")
            else:
                msg = (
                    f"slope {slope:.3f}% >= flat ceiling {_FLAT_CEIL}% -- "
                    f"expected flat terrain here. Possible causes: wrong CRS, "
                    f"wrong tile downloaded, or DEM nodata propagation."
                )
                print(f"\n  WARNING: {msg}\n")
                warnings_list.append(f"{label}: {msg}")
                all_passed = False

        elif terrain == "steep":
            if slope > _STEEP_FLOOR:
                print(f"  Check         : PASS  (slope {slope:.3f}% is above steep floor {_STEEP_FLOOR}%)")
            else:
                msg = (
                    f"slope {slope:.3f}% <= steep floor {_STEEP_FLOOR}% -- "
                    f"expected steep escarpment here. Possible causes: wrong CRS, "
                    f"gradient computed in pixel units instead of metres, "
                    f"or wrong tile (ocean/flat)."
                )
                print(f"\n  WARNING: {msg}\n")
                warnings_list.append(f"{label}: {msg}")
                all_passed = False

        print()

    # -----------------------------------------------------------------------
    # Cross-check: escarpment MUST be clearly steeper than both flat points.
    # This catches systematic errors that shift all values in the same
    # direction (e.g. wrong resampling, inverted sign, flat DEM).
    # -----------------------------------------------------------------------
    _divider()
    print("Step 3: Cross-check terrain ordering (escarpment > both flat points)\n")

    escarpment_label = VALIDATION_POINTS[1]["label"]  # Madikeri
    flat_labels = [VALIDATION_POINTS[0]["label"], VALIDATION_POINTS[2]["label"]]

    if escarpment_label in results and all(f in results for f in flat_labels):
        s_steep = results[escarpment_label]
        s_flat_a = results[flat_labels[0]]
        s_flat_b = results[flat_labels[1]]

        print(f"  Escarpment (Madikeri)  : {s_steep:.3f}%")
        print(f"  Flat A (Kalaburagi)    : {s_flat_a:.3f}%")
        print(f"  Flat B (Mangalore)     : {s_flat_b:.3f}%")

        if s_steep > s_flat_a and s_steep > s_flat_b:
            print(
                f"\n  Cross-check           : PASS  "
                f"(escarpment {s_steep:.1f}% > both flat points "
                f"{s_flat_a:.1f}% and {s_flat_b:.1f}%)"
            )
        else:
            msg = (
                f"Terrain ordering VIOLATED: escarpment slope {s_steep:.3f}% "
                f"is NOT greater than at least one flat point "
                f"(Kalaburagi={s_flat_a:.3f}%, Mangalore={s_flat_b:.3f}%). "
                f"This indicates a systematic raster error -- check CRS, "
                f"gradient axis ordering, or DEM void-fill."
            )
            print(f"\n  WARNING: {msg}\n")
            warnings_list.append(f"Cross-check: {msg}")
            all_passed = False
    else:
        missing = [
            l for l in [escarpment_label] + flat_labels if l not in results
        ]
        print(f"  Cross-check skipped (missing results for: {missing})")

    print()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    _divider()
    if results:
        print("\nSummary Table")
        print(f"  {'Location':<55} {'Slope %':>9}  {'Terrain':>8}")
        print("  " + "-" * 76)
        for pt in VALIDATION_POINTS:
            label   = pt["label"]
            terrain = pt["terrain"]
            slope_str = f"{results[label]:.3f}" if label in results else "ERROR"
            print(f"  {label:<55} {slope_str:>9}  {terrain:>8}")
        print()

    _divider()
    if all_passed:
        print(
            "\nALL CHECKS PASSED -- Slope connector is operating correctly.\n"
            "  Flat points are low, escarpment is high, ordering is correct.\n"
            "  Safe to proceed to scoring / criteria matrix phase."
        )
    else:
        print(
            "\nVALIDATION FAILED -- Do NOT proceed to scoring/matrix phase."
        )
        print("  Issues detected:")
        for w in warnings_list:
            print(f"    * {w}")
        print(
            "\n  Recommended investigation steps:\n"
            "    1. Delete geoprocessing/data/ and re-run to force a clean download.\n"
            "    2. Inspect the slope raster in QGIS: check CRS is EPSG:4326.\n"
            "    3. Verify gradient axes: dz_dy should be N-S, dz_dx should be E-W.\n"
            "    4. Check that pixel spacing was converted from degrees to metres\n"
            "       using WGS-84 ellipsoid (not a constant 111,000 m/degree).\n"
            "    5. Confirm nodata sentinel (-32768) is masked before gradient.\n"
        )
        sys.exit(1)

    _divider()


if __name__ == "__main__":
    main()
