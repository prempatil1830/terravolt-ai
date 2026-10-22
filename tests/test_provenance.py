"""
tests/test_provenance.py
------------------------
Validates that every criterion value entering the decision matrix has a valid
provenance record in MongoDB. Fails if any matrix value has no provenance record.
"""

import os
import sys
import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

GEO_DIR = os.path.join(PROJECT_ROOT, "geoprocessing")
if GEO_DIR not in sys.path:
    sys.path.insert(0, GEO_DIR)

from backend.db import get_candidates_col, get_provenance_col
from geoprocessing.provenance import (
    record_value,
    get_provenance_record,
    get_site_provenance,
    VALID_STATUSES,
)

# The core criteria that feed the AHP decision matrix
MATRIX_CRITERIA = ["ghi", "substation", "landcost", "temp_derating"]


@pytest.fixture(autouse=True)
def cleanup_test_records():
    """Ensure temporary test records are removed from MongoDB before and after tests."""
    col = get_provenance_col()
    if col is not None:
        try:
            col.delete_many({"site_id": {"$regex": "^test_"}})
            col.delete_many({"site_id": {"$in": [999, "999"]}})
        except Exception:
            pass
    yield
    if col is not None:
        try:
            col.delete_many({"site_id": {"$regex": "^test_"}})
            col.delete_many({"site_id": {"$in": [999, "999"]}})
        except Exception:
            pass


def test_invalid_status_raises_value_error():
    """Verify record_value strictly rejects any status outside the 4 allowed values."""
    with pytest.raises(ValueError, match="Invalid provenance status"):
        record_value(
            site_id="test_site",
            criterion="ghi",
            value=0.5,
            source="Test Source",
            retrieved_at="2026-09-12T00:00:00Z",
            unit="dimensionless",
            spatial_ref="Centroid",
            status="unauthorized_status",
        )


def test_valid_status_records_successfully():
    """Verify record_value succeeds with all allowed statuses."""
    for valid_st in VALID_STATUSES:
        rec = record_value(
            site_id="test_site_valid",
            criterion=f"crit_{valid_st}",
            value=1.0,
            source="Test Source",
            retrieved_at="2026-09-12T00:00:00Z",
            unit="dimensionless",
            spatial_ref="Centroid",
            status=valid_st,
        )
        assert rec["status"] == valid_st
        assert rec["site_id"] == "test_site_valid"


def test_sourced_records_must_not_have_null_retrieved_at_or_backfill_origin():
    """
    CRITICAL PROVENANCE AUDIT TEST:
    Fails if any 'sourced' record has a null retrieved_at or origin 'migrated_backfill'.
    Status 'sourced' is strictly reserved for live fetches captured at fetch time.
    """
    provenance_col = get_provenance_col()
    invalid_records = list(
        provenance_col.find({
            "status": "sourced",
            "$or": [
                {"retrieved_at": None},
                {"origin": "migrated_backfill"},
            ],
        })
    )
    assert len(invalid_records) == 0, (
        f"Integrity violation: Found {len(invalid_records)} invalid 'sourced' records with "
        f"null retrieved_at or 'migrated_backfill' origin: {invalid_records}"
    )

    # Direct validation check: 'sourced' with null retrieved_at must raise ValueError
    with pytest.raises(ValueError, match="Integrity violation"):
        record_value(
            site_id="test_invalid_sourced_null",
            criterion="ghi",
            value=0.5,
            source="Live Fetch",
            retrieved_at=None,
            unit="dimensionless",
            spatial_ref="Centroid",
            status="sourced",
        )

    # Direct validation check: 'sourced' with origin 'migrated_backfill' must raise ValueError
    with pytest.raises(ValueError, match="Integrity violation"):
        record_value(
            site_id="test_invalid_sourced_backfill",
            criterion="ghi",
            value=0.5,
            source="Offline Backfill",
            retrieved_at="2026-09-12T00:00:00Z",
            unit="dimensionless",
            spatial_ref="Centroid",
            status="sourced",
            origin="migrated_backfill",
        )


def test_all_matrix_values_have_provenance_records():
    """
    CRITICAL INVARIANT TEST:
    Fails if any matrix value feeding the candidate evaluation matrix
    has no provenance record in MongoDB.
    """
    candidates_col = get_candidates_col()
    provenance_col = get_provenance_col()

    candidates = list(candidates_col.find({}))
    assert len(candidates) == 12, f"Expected 12 candidates in MongoDB, found {len(candidates)}"

    missing_records = []

    for cand in candidates:
        rank = cand.get("rank")
        status = cand.get("status")
        lat = cand.get("latitude")
        lon = cand.get("longitude")
        coord_key = f"{lat:.4f}_{lon:.4f}"
        norm = cand.get("normalized_criteria", {})

        # Candidates that are evaluated in the matrix (eligible or needs_review)
        # must have provenance for every criterion entering the matrix
        if status in ("eligible", "needs_review") and norm:
            for criterion in MATRIX_CRITERIA:
                rec = provenance_col.find_one({
                    "$or": [
                        {"site_id": coord_key},
                        {"site_id": rank},
                        {"site_id": str(rank)},
                        {"candidate_rank": rank},
                    ],
                    "criterion": criterion,
                })

                if rec is None:
                    missing_records.append(f"Site #{rank} ({status}) is missing provenance for matrix criterion '{criterion}'")
                else:
                    assert rec["status"] in VALID_STATUSES, (
                        f"Site #{rank} criterion '{criterion}' has invalid provenance status '{rec.get('status')}'"
                    )
                    assert rec["source"], f"Site #{rank} criterion '{criterion}' is missing source lineage"
                    # If marked sourced, must have retrieved_at and cannot be migrated_backfill
                    if rec["status"] == "sourced":
                        assert rec["retrieved_at"] is not None
                        assert rec.get("origin") != "migrated_backfill"
                    # If migrated_backfill, must be calculated and retrieved_at must be null
                    if rec.get("origin") == "migrated_backfill":
                        assert rec["status"] == "calculated"
                        assert rec["retrieved_at"] is None

    if missing_records:
        pytest.fail(
            "Matrix integrity violation: The following matrix values have no provenance record:\n"
            + "\n".join(f"  - {m}" for m in missing_records)
        )


def test_site_12_excluded_has_only_exclusion_constraint():
    """Verify that excluded candidates only have the exclusion constraint, not uncomputed criteria."""
    from backend.db import get_candidates_col
    from geoprocessing.provenance import record_value
    provenance_col = get_provenance_col()
    candidates_col = get_candidates_col()
    excl_cand = candidates_col.find_one({"status": "excluded"})
    if excl_cand:
        target_id = excl_cand["rank"]
        docs = list(provenance_col.find({"$or": [{"site_id": target_id}, {"site_id": str(target_id)}]}))
        assert len(docs) == 1, f"Excluded site #{target_id} should have exactly 1 record, found {len(docs)}"
        assert docs[0]["criterion"] == "protected_buffer"
        assert docs[0]["value"] == "excluded"
    else:
        # In Frozen v2 all 12 shortlisted candidates are eligible or needs_review.
        # Test the exclusion provenance invariant directly using record_value:
        test_rank = 999
        provenance_col.delete_many({"$or": [{"site_id": test_rank}, {"site_id": str(test_rank)}]})
        record_value(
            site_id=test_rank,
            criterion="protected_buffer",
            value="excluded",
            source="UNEP-WCMC WDPA Protected Areas & OpenStreetMap Waterbodies",
            retrieved_at=None,
            unit="buffer_status",
            spatial_ref="10 km PA & 500 m waterbody buffer / EPSG:4326",
            status="calculated",
            origin="migrated_backfill",
        )
        docs = list(provenance_col.find({"$or": [{"site_id": test_rank}, {"site_id": str(test_rank)}]}))
        assert len(docs) == 1, f"Excluded site #{test_rank} should have exactly 1 record, found {len(docs)}"
        assert docs[0]["criterion"] == "protected_buffer"
        assert docs[0]["value"] == "excluded"
        assert docs[0]["origin"] == "migrated_backfill"
        assert docs[0]["retrieved_at"] is None
        assert docs[0]["status"] == "calculated"
        provenance_col.delete_many({"$or": [{"site_id": test_rank}, {"site_id": str(test_rank)}]})



def test_site_11_provenance_handles_unavailable_or_backfilled_substation():
    """Verify candidate provenance correctly reflects real substation lineage and handles unavailable live scoring."""
    provenance_col = get_provenance_col()
    from backend.db import get_candidates_col
    cand11 = get_candidates_col().find_one({"rank": 11})
    c11_key = f"{cand11['latitude']:.4f}_{cand11['longitude']:.4f}" if cand11 else "17.2000_75.9000"
    rec = provenance_col.find_one({
        "$or": [{"site_id": c11_key}, {"candidate_rank": 11}, {"site_id": 11}, {"site_id": "11"}],
        "criterion": "substation",
    })
    assert rec is not None, "Candidate #11 must have a provenance record for substation"
    assert rec["source"] is not None
    assert rec["status"] in ["calculated", "sourced"]

    # Test live wrap_score_candidate marks missing substation as 'unavailable'
    from geoprocessing.provenance import wrap_score_candidate

    def mock_score_fn(lat, lon, **kwargs):
        return {
            "status": "needs_review",
            "constraints": {},
            "raw_criteria": {"substation_dist_km": None},
            "normalized_criteria": {"substation": 0.0},
            "score": 0.28,
        }

    wrapped = wrap_score_candidate(mock_score_fn)
    wrapped(11.5, 77.1, site_id="test_site_sub_unavail")
    live_rec = get_provenance_record("test_site_sub_unavail", "substation")
    assert live_rec is not None
    assert live_rec["status"] == "unavailable"


def test_score_candidate_wrapper_enforces_provenance():
    """Verify that wrap_score_candidate records provenance for all computed matrix values."""
    from geoprocessing.provenance import wrap_score_candidate, get_provenance_record

    def mock_score_fn(lat, lon, **kwargs):
        return {
            "status": "eligible",
            "constraints": {
                "slope": {"value_pct": 1.2},
                "landuse": {"class_name": "Shrubland"},
                "protected_buffer": {"status": "eligible"},
            },
            "raw_criteria": {
                "ghi_kwh_m2_day": 5.65,
                "substation_dist_km": 1.5,
                "landcost_proxy_score": 0.35,
                "t2m_celsius": 26.5,
            },
            "normalized_criteria": {
                "ghi": 0.65,
                "substation": 0.97,
                "landcost": 0.35,
                "temp_derating": 0.35,
            },
            "score": 0.72,
        }

    wrapped = wrap_score_candidate(mock_score_fn)
    result = wrapped(14.3, 77.4, site_id="test_site_wrap")
    assert result["score"] == 0.72

    # Verify each criterion feeding the matrix was written to provenance with sourced status and timestamp
    for crit in MATRIX_CRITERIA:
        rec = get_provenance_record("test_site_wrap", crit)
        assert rec is not None, f"Expected provenance record for criterion '{crit}'"
        assert rec["status"] in VALID_STATUSES
        assert rec["retrieved_at"] is not None


def test_matrix_entry_fails_if_provenance_record_missing():
    """Verify that no value may enter the matrix without a provenance record."""
    from geoprocessing.provenance import wrap_score_candidate
    from unittest.mock import patch

    def mock_score_fn(lat, lon, **kwargs):
        return {
            "status": "eligible",
            "constraints": {},
            "raw_criteria": {},
            "normalized_criteria": {
                "ghi": 0.65,
                "substation": 0.97,
                "landcost": 0.35,
                "temp_derating": 0.35,
            },
            "score": 0.72,
        }

    wrapped = wrap_score_candidate(mock_score_fn)
    # Simulate get_provenance_record returning None for a matrix criterion
    with patch("geoprocessing.provenance.get_provenance_record", return_value=None):
        with pytest.raises(RuntimeError, match="Matrix integrity violation"):
            wrapped(14.3, 77.4, site_id="test_missing_prov")


def test_every_sourced_record_resolves_to_existing_file_and_matches_timestamp():
    """
    Provenance verification test:
    Validates that every sourced provenance record in MongoDB has a source_key
    that resolves to an existing file on disk and that retrieved_at matches
    the stored timestamp in the raw cache file.
    Also verifies that for primary substations, substations_karnataka.geojson,
    the matched OSM id, and the bulk tile file are cited.
    """
    import json
    from raw_cache import hash_request, CACHE_DIR

    prov_col = get_provenance_col()
    from backend.db import get_candidates_col
    cand_docs = list(get_candidates_col().find({}))
    cand_coord_keys = [f"{c['latitude']:.4f}_{c['longitude']:.4f}" for c in cand_docs]
    cand_ranks = [c["rank"] for c in cand_docs]
    sourced_records = list(prov_col.find({
        "$or": [
            {"site_id": {"$in": cand_coord_keys}},
            {"site_id": {"$in": cand_ranks}},
            {"site_id": {"$in": [str(r) for r in cand_ranks]}},
            {"candidate_rank": {"$in": cand_ranks}},
        ],
        "status": "sourced",
    }))
    assert len(sourced_records) > 0, "No sourced records found in provenance collection"

    for rec in sourced_records:
        site_id = rec.get("site_id")
        criterion = rec.get("criterion")
        source_key = rec.get("source_key") or rec.get("source")
        retrieved_at = rec.get("retrieved_at")

        assert source_key, f"Record {site_id}::{criterion} missing source_key"
        assert retrieved_at, f"Record {site_id}::{criterion} missing retrieved_at"

        # Check if source_key is a raw cache request key
        raw_hash = hash_request(source_key)
        raw_path = os.path.join(CACHE_DIR, f"{raw_hash}.json")

        if os.path.exists(raw_path):
            with open(raw_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            stored_cached_at = data.get("cached_at")
            assert stored_cached_at == retrieved_at, (
                f"Timestamp mismatch for {site_id}::{criterion}: "
                f"record retrieved_at={retrieved_at} != cache cached_at={stored_cached_at}"
            )
        else:
            # Check if source_key is a dataset file path
            resolved_file = None
            for cand_path in [
                source_key,
                os.path.join(PROJECT_ROOT, source_key),
                os.path.join(GEO_DIR, "data", source_key),
            ]:
                if os.path.exists(cand_path) and os.path.isfile(cand_path):
                    resolved_file = cand_path
                    break

            assert resolved_file is not None, (
                f"Source key for {site_id}::{criterion} did not resolve to a file: {source_key}"
            )

        # For substations, verify citation of substations_karnataka.geojson, OSM ID, and stored distance
        if criterion == "raw_substation":
            source_desc = rec.get("source", "")
            assert "substations_karnataka.geojson" in source_desc, (
                f"Substation provenance for site #{site_id} must cite substations_karnataka.geojson"
            )
            assert "osm_id" in source_desc, (
                f"Substation provenance for site #{site_id} must cite osm_id"
            )
            assert "distance" in source_desc or f"{rec.get('value')} km" in source_desc, (
                f"Substation provenance for site #{site_id} must cite stored distance"
            )


# ---------------------------------------------------------------------------
# Tests (a), (b), (c) for Rebuilt Coordinate-Keyed Provenance
# ---------------------------------------------------------------------------

def test_every_provenance_record_coordinates_correspond_to_site():
    """
    Test (a): Validates that every provenance record's coordinates correspond
    to its site (NASA native cell snapping is allowed).
    """
    import urllib.parse
    import re
    from backend.db import get_provenance_col, get_candidates_col

    prov_col = get_provenance_col()
    cand_col = get_candidates_col()
    candidates = list(cand_col.find({}))
    assert len(candidates) == 12

    cands_by_coord = {
        f"{c['latitude']:.4f}_{c['longitude']:.4f}": c
        for c in candidates
    }

    records = list(prov_col.find({"site_id": {"$in": list(cands_by_coord.keys())}}))
    assert len(records) > 0, "No provenance records found for candidate coordinate keys"

    for rec in records:
        site_id = rec.get("site_id")
        criterion = rec.get("criterion")
        assert site_id in cands_by_coord, f"Provenance record {criterion} has site_id '{site_id}' not in candidates"
        cand = cands_by_coord[site_id]
        c_lat = cand["latitude"]
        c_lon = cand["longitude"]

        # Verify lat/lon fields on the record
        if "latitude" in rec and rec["latitude"] is not None:
            assert rec["latitude"] == c_lat
        if "longitude" in rec and rec["longitude"] is not None:
            assert rec["longitude"] == c_lon

        source_key = rec.get("source_key") or ""

        # NASA POWER coordinates check (with snapping tolerance <= 0.5 degrees)
        if criterion in ("raw_ghi", "raw_temperature"):
            assert "latitude=" in source_key and "longitude=" in source_key, f"Missing coords in {source_key}"
            parsed = urllib.parse.urlparse(source_key.split("url=")[-1])
            q = urllib.parse.parse_qs(parsed.query)
            q_lat = float(q["latitude"][0])
            q_lon = float(q["longitude"][0])
            assert abs(q_lat - c_lat) <= 0.5, f"NASA lat {q_lat} snapped too far from candidate lat {c_lat}"
            assert abs(q_lon - c_lon) <= 0.5, f"NASA lon {q_lon} snapped too far from candidate lon {c_lon}"

        # Land cost bounding box check
        elif criterion == "raw_landcost":
            m = re.search(r"bbox=([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)", source_key)
            if m:
                s, w, n, e = map(float, m.groups())
                center_lat = (s + n) / 2.0
                center_lon = (w + e) / 2.0
                assert abs(center_lat - c_lat) <= 0.01, f"LC center {center_lat} != candidate lat {c_lat}"
                assert abs(center_lon - c_lon) <= 0.01, f"LC center {center_lon} != candidate lon {c_lon}"


def test_provenance_values_equal_candidate_stored_criteria_values():
    """
    Test (b): Validates that provenance values equal the candidate's stored criteria values.
    """
    from backend.db import get_provenance_col, get_candidates_col

    prov_col = get_provenance_col()
    cand_col = get_candidates_col()
    candidates = list(cand_col.find({}))
    assert len(candidates) == 12

    for cand in candidates:
        lat = cand["latitude"]
        lon = cand["longitude"]
        coord_key = f"{lat:.4f}_{lon:.4f}"
        raw = cand.get("raw_criteria", {})
        norm = cand.get("normalized_criteria", {})
        score = cand.get("score")

        expected_map = {
            "raw_ghi": raw.get("ghi_kwh_m2_day"),
            "raw_substation": raw.get("substation_dist_km"),
            "raw_landcost": raw.get("landcost_proxy_score"),
            "raw_temperature": raw.get("t2m_celsius"),
            "ghi": norm.get("ghi"),
            "substation": norm.get("substation"),
            "landcost": norm.get("landcost"),
            "temp_derating": norm.get("temp_derating"),
            "score": score,
        }

        for crit, expected in expected_map.items():
            if expected is not None:
                rec = prov_col.find_one({"site_id": coord_key, "criterion": crit})
                assert rec is not None, f"Missing provenance record for {coord_key}::{crit}"
                actual = rec.get("value")
                assert actual is not None, f"Null value in record for {coord_key}::{crit}"
                if isinstance(actual, float) and isinstance(expected, float):
                    assert round(actual, 4) == round(expected, 4), (
                        f"Mismatch for {coord_key}::{crit}: actual={actual} vs expected={expected}"
                    )
                else:
                    assert actual == expected, (
                        f"Mismatch for {coord_key}::{crit}: actual={actual} vs expected={expected}"
                    )


def test_no_provenance_document_for_site_not_in_shortlist():
    """
    Test (c): Validates that no provenance document exists for a site
    that is not in the current shortlist.
    """
    from backend.db import get_provenance_col, get_candidates_col

    prov_col = get_provenance_col()
    cand_col = get_candidates_col()
    candidates = list(cand_col.find({}))
    assert len(candidates) == 12

    shortlist_keys = {f"{c['latitude']:.4f}_{c['longitude']:.4f}" for c in candidates}
    all_docs = list(prov_col.find({}))
    assert len(all_docs) > 0

    for doc in all_docs:
        site_id = doc.get("site_id")
        assert site_id in shortlist_keys, (
            f"Provenance record has site_id '{site_id}' which does not belong to the 12 shortlisted sites!"
        )


