# Terravolt AI — Pre-Feasibility Screening Pipeline

> **Target Scope:** State of Karnataka, India (GADM 4.1 Boundary / EPSG:4326 / UTM Zone 43N).  
> **Target Plant Size:** Utility-scale (≥ 50 MWp) ground-mounted solar PV installations.

A deterministic spatial decision-support platform and Multi-Criteria Decision Making (MCDM) pipeline using the Analytic Hierarchy Process (AHP), bounded specialist evidence-gathering agents, and human-in-the-loop (HITL) gates.

---

## 1. System Architecture

The pipeline pairs automated spatial evidence gathering with deterministic scoring and human governance:

1. **Deterministic MCDM Engine (`geoprocessing/`):**
   - Applies strict boolean constraint filtering (Copernicus DEM GLO-30 terrain slope ≤ 5.0%, ESA WorldCover 2021 non-forest/non-urban land-use, and WDPA 10 km protected area setback).
   - Computes normalized criteria and canonical AHP priority weights.
2. **Bounded Specialist Agents (`geoprocessing/agents/`):**
   - Four decoupled specialist agents (`SolarClimateAgent`, `LandEnvironmentAgent`, `InfraGridAgent`, `EconomicRiskAgent`) execute only fixed, read-only tools wrapping spatial data connectors.
   - Agents have **zero access** to scoring weights or ranking algorithms.
3. **Deterministic Orchestrator (`PlannerAgent`):**
   - Dispatches specialist agents, assembles the decision matrix, and scores candidates deterministically in Python code (no non-deterministic LLM routing).
4. **Human-in-the-Loop Governance (`backend/hitl.py`):**
   - Four governance checkpoints (`scope`, `weights`, `matrix`, `ranking`) pause execution for human verification.
   - Revised weights are strictly rejected unless they sum to 1.0, land-cost weight is ≤ 10%, and the AHP Consistency Ratio (CR) is ≤ 0.10.
5. **Data Provenance & Audit Trail:**
   - Real provenance captured live for interactive runs, and backfilled provenance (`migrated_backfill`) for the initial sweep.
   - Immutable audit log tracking every user action, timestamp, version, and payload snapshot.
6. **Raw-Output Cache & REPLAY Mode (`geoprocessing/raw_cache.py`):**
   - Every external response (NASA POWER REST API, Overpass OSM queries) is saved to `geoprocessing/data/raw_cache/` keyed by request hash with timestamp.
   - Setting `REPLAY=1` enables zero-network offline reproduction of all candidate evaluations and scores.

---

## 2. Selection Rules

### FROZEN Rule v1 (2026-09-12)
- 5×4 spatial grid diversity binning across Karnataka.
- Coarse Phase 1 screening based on GHI and temperature derating alone.
- Shortlisted candidates evaluated in Phase 2 with full Overpass grid connection data.

### FROZEN Rule v2 (2026-10-15)
- 5×4 spatial grid diversity binning across Karnataka.
- Regional candidate shortlisting ranked by a **preliminary AHP score that includes substation distance**:
  - `Weights: GHI (0.40) + Substation Distance (0.40) + Temperature Derating (0.10)` (renormalized to 1.0).
  - Formula: `(0.40 * norm_ghi + 0.40 * norm_sub + 0.10 * norm_temp) / 0.90`.
- Ensures grid evacuation proximity directly drives candidate shortlisting.

### 2.1 Current Shortlist (12 Sites)

| Rank | District | Centroid Coordinates | Status | AHP Score | Reasons |
|:---:|:---|:---:|:---:|:---:|:---|
| #1 | Chitradurga | 14.2000°N, 76.5000°E | `eligible` | 0.7091 | — |
| #2 | Bengaluru North | 13.2000°N, 77.4000°E | `needs_review` | 0.6989 | Primary substation voltage not tagged in OSM; >=33 kV unconfirmed |
| #3 | Chitradurga | 14.4000°N, 76.7000°E | `eligible` | 0.6888 | — |
| #4 | Vijayanagara | 15.0000°N, 76.1000°E | `needs_review` | 0.6747 | Untagged substation at 2.48 km is closer than nearest confirmed >=33kV (4.87 km) |
| #5 | Raichur | 16.0000°N, 76.9000°E | `eligible` | 0.6714 | — |
| #6 | Vijayapura | 16.3000°N, 76.2000°E | `eligible` | 0.6498 | — |
| #7 | Bagalkote | 16.4000°N, 75.1000°E | `eligible` | 0.6246 | — |
| #8 | Davanagere | 14.2000°N, 75.6000°E | `eligible` | 0.6198 | — |
| #9 | Mysuru | 12.2000°N, 76.6000°E | `eligible` | 0.6124 | — |
| #10 | Kalaburagi | 17.1000°N, 76.8000°E | `eligible` | 0.6063 | — |
| #11 | Vijayapura | 17.2000°N, 75.9000°E | `eligible` | 0.5992 | — |
| #12 | Dharwad | 15.4000°N, 75.1000°E | `needs_review` | 0.5968 | Untagged substation at 2.29 km is closer than nearest confirmed >=33kV (4.76 km) |

---

## 3. Quick Start & Setup

### Option A: Docker Compose (Recommended)

Start backend, frontend, and MongoDB services with a single command:

```bash
docker-compose up --build
```

- **Frontend:** http://localhost:5173
- **FastAPI Backend:** http://localhost:8000
- **Health Check:** http://localhost:8000/health
- **MongoDB:** `localhost:27017`

### Option B: Local Environment Setup

**Prerequisites:**
- Python ≥ 3.11 with GDAL/GEOS libraries
- Node.js ≥ 18
- MongoDB ≥ 6.0 running locally on port 27017

```bash
# 1. Virtual environment & dependencies
python3 -m venv tenv
source tenv/bin/activate
pip install -r requirements.txt pytest

# 2. Start backend
cd backend
python -m uvicorn app.main:app --port 8000 --reload

# 3. Start frontend
cd ../frontend
npm install
npm run dev
```

---

## 4. Exact Reproduction Steps (REPLAY Mode)

To rebuild all results, run tests, and regenerate documentation without any external network calls:

```bash
# Run one-shot reproduction script
./run_all.sh
```

`run_all.sh` automatically performs:
1. Full candidate selection under Frozen Rule v2 from raw cache (`REPLAY=1`).
2. Synchronization of candidates and provenance records into MongoDB.
3. Automated test suite execution in REPLAY mode (`pytest tests/`).
4. Multi-scenario sensitivity analysis and stability audit.
5. Final report regeneration (`final_report.md`).

---

## 5. Documented Limitations & Data Confidence

In accordance with project reporting standards, the following 4 critical limitations must be taken into account before financial or engineering commitments:

1. **OSM-Only Grid Data with No Capacity Check:**  
   Substation and transmission corridor locations originate exclusively from OpenStreetMap. While coordinates identify physical infrastructure, OSM provides **zero data regarding substation transformer capacity (MVA), busbar fault headroom, or operational loading**. A substation may be 1 km away but operate at 100% capacity with zero injection headroom for a 50 MWp plant. Official KPTCL single-line diagram (SLD) verification is mandatory.

2. **1 x 1 degree grid (about 110 km) GHI Spatial Resolution:**  
   Solar irradiation data from NASA POWER Climatology operates on a 1 x 1 degree grid (about 110 km). As a result, candidates within this coarse grid receive byte-identical GHI values, offering zero discriminating power between geographically adjacent sites in the central solar belt. Microclimates and terrain shadowing along the Western Ghats cannot be resolved at this macro scale. Temperature operates on a 0.5 x 0.625 degree grid.

3. **5% Slope Threshold Not Confirmed from the Literature:**  
   The ≤ 5.0% terrain gradient maximum applied as a hard constraint is an industrial engineering working rule to minimize civil cut-and-fill grading costs for single-axis tracker racking. This exact 5% cutoff is **not confirmed from peer-reviewed solar siting literature**; fixed-tilt installations frequently accommodate up to 7–10% slope with civil grading cost adjustments.

4. **Operator Names in the Audit Trail Being Self-Declared:**  
   Reviewer and operator names recorded in the Human-in-the-Loop audit trail (`audit_log` collection) are self-declared parameter strings supplied in HTTP request payloads. The system does not currently enforce cryptographic identity verification, PKI signatures, or enterprise SSO authentication.

5. **Protected Area Dataset Provenance & Known Gaps Review Rule:**  
   The primary statutory conservation layer combines 51 features from WDPA September 2026 (`origin: wdpa`) with verified OpenStreetMap boundary relations for Kudremukh National Park (Relation 9482054) and Kali/Dandeli-Anshi Tiger Reserve (Relation 13414729) (`origin: osm_supplement`). While statutory key parks have verified boundaries, state wildlife sanctuaries omitted from the global WDPA release remain documented in `KNOWN_GAPS` with authoritative point centroids. The pipeline enforces a formal `KNOWN_GAPS` review rule: any candidate site within 25 km of an unmapped protected area coordinate receives status `needs_review` with the reason `"Possible unmapped protected area: [park name]"`.

   **Catalog of Documented Known Gaps:**
   - **Cauvery Wildlife Sanctuary:** 12.1798°N, 77.6416°E (*Source:* OSM Relation 9329112, Hanur/Chamarajanagar)
   - **Bhimgad Wildlife Sanctuary:** 15.5701°N, 74.3299°E (*Source:* OSM Relation 19153153, Mendil/Khanapura, Belagavi)
   - **Mookambika Wildlife Sanctuary:** 13.8116°N, 74.8825°E (*Source:* OSM Way 670548929, Bainduru/Kollur, Udupi)
   - **Talakaveri Wildlife Sanctuary:** 12.3704°N, 75.4630°E (*Source:* OSM Relation 21656432, Madikeri, Kodagu)
   - **Pushpagiri Wildlife Sanctuary:** 12.5950°N, 75.6779°E (*Source:* OSM Relation 21656434, Madikeri, Kodagu)
   - **Sharavathi Valley Wildlife Sanctuary:** 13.8281°N, 75.1181°E (*Source:* WDPA Oct 2026 Public IND part1 Reserve Forest 'Sharavathi' centroid)
   - **Someshwara Wildlife Sanctuary:** 13.5278°N, 75.0500°E (*Source:* OSM Relation 9328427, Hebri, Udupi)
   - **Brahmagiri Wildlife Sanctuary:** 12.0387°N, 75.8194°E (*Source:* OSM Relation 21656433, Kutta/Ponnampete, Kodagu)
   - **Rangayyanadurga Antelope Sanctuary:** 14.7523°N, 76.6678°E (*Source:* OSM Node 3595950201, Molakalmuru, Chitradurga)
   - **Daroji Sloth Bear Sanctuary:** 15.2599°N, 76.5800°E (*Source:* OSM Relation 19033358, Kampli, Ballari)
   - **Melkote Temple Wildlife Sanctuary:** 12.7008°N, 76.6626°E (*Source:* OSM Relation 9329621, Pandavapura, Mandya)
   - **Ranebennur Blackbuck Sanctuary:** 14.6166°N, 75.6342°E (*Source:* OSM Node 245628328, Ranebennuru, Haveri)

6. **Boundary Audit of Historical Candidates & Disproven All-Inside Claim:**  
   A retrospective geospatial boundary audit was conducted on the historical 12 candidates using the GADM 4.1 Karnataka boundary layer and the 800 m footprint disk constraint (requiring ≥ 90% containment).  
   **Audit Findings:** **It is NOT the case that all 12 historical candidates were inside Karnataka.** In reality, **only 6 of the 12 candidates passed** the boundary constraint, while 6 failed:
   - **5 candidates** had centroids entirely outside Karnataka:
     - Candidate #2 (14.0°N, 77.0°E): Andhra Pradesh (0.0% inside)
     - Candidate #3 (14.3°N, 77.0°E): Andhra Pradesh (0.0% inside)
     - Candidate #7 (14.0°N, 77.4°E): Andhra Pradesh (0.0% inside)
     - Candidate #9 (16.8°N, 77.9°E): Telangana (0.0% inside)
     - Candidate #11 (11.5°N, 77.1°E): Tamil Nadu (0.0% inside)
   - **1 candidate** had centroid inside Karnataka, but its footprint disk clipped across the state border:
     - Candidate #6 (12.8°N, 77.8°E): Centroid inside, but disk only 77.0% inside Karnataka (< 90% threshold)
   - **Only 6 candidates** (#1, #4, #5, #8, #10, #12) were strictly ≥ 90% inside Karnataka.

   | Old Rank | Coordinates | Centroid Status | 800m Disk Inside % | Boundary Audit Verdict |
   |:---:|:---:|:---:|:---:|:---|
   | #1 | 14.3000°N, 77.4000°E | INSIDE | 100.0% | PASSED |
   | #2 | 14.0000°N, 77.0000°E | OUTSIDE (Andhra Pradesh) | 0.0% | FAILED: Outside Karnataka state boundary |
   | #3 | 14.3000°N, 77.0000°E | OUTSIDE (Andhra Pradesh) | 0.0% | FAILED: Outside Karnataka state boundary |
   | #4 | 14.1000°N, 76.0000°E | INSIDE | 100.0% | PASSED |
   | #5 | 14.2000°N, 75.0000°E | INSIDE | 100.0% | PASSED |
   | #6 | 12.8000°N, 77.8000°E | INSIDE | 77.0% | FAILED: Footprint disk (77.0%) < 90% inside boundary |
   | #7 | 14.0000°N, 77.4000°E | OUTSIDE (Andhra Pradesh) | 0.0% | FAILED: Outside Karnataka state boundary |
   | #8 | 14.3000°N, 75.0000°E | INSIDE | 100.0% | PASSED |
   | #9 | 16.8000°N, 77.9000°E | OUTSIDE (Telangana) | 0.0% | FAILED: Outside Karnataka state boundary |
   | #10 | 14.3000°N, 76.1000°E | INSIDE | 100.0% | PASSED |
   | #11 | 11.5000°N, 77.1000°E | OUTSIDE (Tamil Nadu) | 0.0% | FAILED: Outside Karnataka state boundary |
   | #12 | 15.8000°N, 77.0000°E | INSIDE | 100.0% | PASSED |

   **Resolution:** The pipeline now strictly enforces Karnataka state boundary polygon containment and ≥ 90% footprint disk containment as **Filter 0** in candidate screening. 100% of all current candidate points and their entire footprint disks are verified within Karnataka.


