"""
tests/test_wdpa_parks.py
------------------------
Validates that the filtered Karnataka WDPA protected areas dataset contains
all essential statutory conservation areas, and verifies buffer checks.
"""

import os
import json
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
FILTERED_GEOJSON = os.path.join(
    os.path.dirname(_HERE), "geoprocessing", "data", "wdpa", "wdpa_karnataka_filtered.geojson"
)

REQUIRED_PARKS = [
    "Bandipur",
    "Nagarhole",
    "Anshi",
    "Bannerghatta",
    "Kudremukh",
    "Bhadra",
    "BRT",
]


def test_required_karnataka_protected_areas_exist():
    """
    Fail unless Bandipur, Nagarhole, Anshi, Bannerghatta, Kudremukh, Bhadra, BRT are all present.
    Reports per park whether it came from WDPA or OSM supplement.
    Fails if any feature has origin 'synthetic', lacks an origin, or lacks a source string.
    """
    assert os.path.exists(FILTERED_GEOJSON), f"Missing {FILTERED_GEOJSON}"

    with open(FILTERED_GEOJSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    features = data.get("features", [])
    assert len(features) > 0, "Filtered WDPA dataset has 0 features"

    # Validate origin and source for every single feature
    for idx, feat in enumerate(features):
        props = feat.get("properties", {})
        name = props.get("NAME") or props.get("NAME_ENG") or f"feature_{idx}"
        origin = props.get("origin")
        source = props.get("source")

        assert origin is not None, f"Feature '{name}' (idx {idx}) lacks an 'origin' field"
        assert origin != "synthetic", f"Feature '{name}' (idx {idx}) has forbidden origin 'synthetic'"
        assert origin in ["wdpa", "osm_supplement"], (
            f"Feature '{name}' (idx {idx}) has unexpected origin '{origin}'; expected 'wdpa' or 'osm_supplement'"
        )

        assert source is not None and str(source).strip() != "", (
            f"Feature '{name}' (idx {idx}) lacks a valid 'source' string"
        )

    # Per-park provenance audit
    print("\n" + "=" * 70)
    print("KEY PROTECTED AREAS ORIGIN AUDIT")
    print("=" * 70)
    park_provenance = {}
    for park in REQUIRED_PARKS:
        found_feat = None
        for feat in features:
            props = feat.get("properties", {})
            blob = f"{props.get('NAME', '')} {props.get('NAME_ENG', '')}".lower()
            if park.lower() in blob:
                found_feat = props
                break
        if found_feat:
            origin = found_feat.get("origin")
            source = found_feat.get("source")
            park_provenance[park] = (origin, source)
            print(f"  [{park:<15}] Origin: {origin:<15} | Source: {source}")
        else:
            park_provenance[park] = (None, None)
            print(f"  [{park:<15}] MISSING")
    print("=" * 70 + "\n")

    missing = [p for p, (orig, _) in park_provenance.items() if orig is None]
    assert not missing, f"The following required Karnataka protected areas are missing from {FILTERED_GEOJSON}: {missing}"


def test_known_gaps_review_rule():
    """Verify that any candidate site within 25 km of a KNOWN_GAPS point triggers needs_review."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "geoprocessing"))
    from fetch_protected_buffer import KNOWN_GAPS, check_known_gaps_review

    # Inject temporary test gap point
    test_gap = {
        "name": "Test Conservation Sanctuary",
        "latitude": 15.0000,
        "longitude": 75.0000,
        "reason": "Test statutory boundary gap"
    }
    KNOWN_GAPS.append(test_gap)
    try:
        # Point ~10 km away (15.05N, 75.00E) -> within 25 km, must trigger review reason
        warnings_near = check_known_gaps_review(15.0500, 75.0000, review_dist_km=25.0)
        assert len(warnings_near) == 1
        assert "Possible unmapped protected area: Test Conservation Sanctuary" in warnings_near[0]

        # Point ~100 km away (16.00N, 75.00E) -> beyond 25 km, must NOT trigger review reason
        warnings_far = check_known_gaps_review(16.0000, 75.0000, review_dist_km=25.0)
        assert len(warnings_far) == 0
    finally:
        KNOWN_GAPS.remove(test_gap)


def test_buffer_validation():
    """Verify Bandipur core is excluded and rural Bidar coordinates remain eligible."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "geoprocessing"))
    from fetch_protected_buffer import check_wdpa_buffer

    # Force re-load
    import fetch_protected_buffer
    fetch_protected_buffer._wdpa_gdf = None
    fetch_protected_buffer._wdpa_tree = None

    # Bandipur Core (11.6667N, 76.6283E) must be EXCLUDED
    bandipur_excluded = check_wdpa_buffer(11.6667, 76.6283, buffer_km=10)
    assert bandipur_excluded is True, "Bandipur Core (11.6667, 76.6283) must be excluded within 10km buffer"

    # Rural Bidar points must be ELIGIBLE
    bidar1_excluded = check_wdpa_buffer(17.91, 77.51, buffer_km=10)
    assert bidar1_excluded is False, "Rural Bidar (17.91, 77.51) must be eligible (not excluded)"

    bidar2_excluded = check_wdpa_buffer(17.90, 77.50, buffer_km=10)
    assert bidar2_excluded is False, "Rural Bidar (17.90, 77.50) must be eligible (not excluded)"
