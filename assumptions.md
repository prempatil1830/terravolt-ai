# Assumptions & Framing — Solar PV Site Suitability, Karnataka

> Last updated: 2026-09-08. Revisit before each analysis run.

---

## 1. Administrative Boundary Source

**Dataset used:** GADM (Global Administrative Areas) version 4.1  
**Layer:** `gadm41_IND_1.shp` — Level-1 administrative boundaries for India  
**Karnataka feature:** `NAME_1 = "Karnataka"`  
**Coordinate reference system:** Geographic (EPSG:4326, WGS84)  
**Download URL:** https://gadm.org/download_country.html (select India → Shapefile)

### Why GADM?
GADM is openly licensed for academic and non-commercial use, globally consistent,
and regularly updated. The Level-1 boundary for Karnataka aligns well with the
Census of India 2011 state boundary and is sufficient for a regional-scale
suitability screening at utility-scale resolution (≥200 m pixel).

### Limitations
| Limitation | Impact |
|---|---|
| GADM boundaries may lag official Survey of India (SoI) delimitation orders by 1–2 years. | Minor — no districts were reorganised in Karnataka after 2020. |
| GADM does not carry forest/revenue/cadastral sub-division data. | Forest exclusion layers must come from FSI or ISFR shapefiles separately. |
| Coastline smoothing in GADM may offset the western coastal strip (Uttara Kannada, Udupi) by up to 100–200 m relative to SoI 1:50,000 topo sheets. | Negligible at the 250 m analysis resolution used in Phase 1. |
| Not suitable for final land-acquisition or legal clearance purposes. | This tool is a pre-feasibility screening tool only. |

**Alternative considered:** Bhuvan (ISRO) state boundaries — higher accuracy for India
but require registration and carry a non-commercial licence that may complicate
downstream publication. GADM chosen for openness in this phase; can be swapped.

---

## 2. Reference Plant Size Assumption

**Reference plant:** 50 MWp (megawatt-peak) utility-scale solar PV plant  
**Land requirement:** ~200–250 acres (≈ 80–100 hectares) per 50 MWp  

### Reasoning & Source
The Ministry of New and Renewable Energy (MNRE), Government of India, guidelines
(Revision 2023) use a benchmark of **4–5 acres per MWp** for ground-mounted
utility-scale crystalline-silicon plants in India, accounting for:
- Panel footprint and inter-row spacing (to avoid mutual shading at ≈GHI-weighted
  solar angles for Karnataka latitude ~14°N)
- Internal access roads, inverter stations, and a 33/132 kV substation bay
- Setback from the boundary fence (typically 5 m)

At 4 acres/MWp → 50 MWp requires **200 acres** (minimum, flat terrain).  
At 5 acres/MWp → 50 MWp requires **250 acres** (moderate terrain with inter-row
spacing adjustment).

50 MWp is chosen as the minimum viable utility-scale threshold for Karnataka
because:
1. KREDL (Karnataka Renewable Energy Development Ltd) tenders are routinely issued
   at 50–250 MWp blocks.
2. Below 50 MWp, grid injection at 33 kV is feasible but evacuation costs
   disproportionately erode project IRR.
3. 50 MWp / 200–250 acres sets a minimum contiguous parcel size that drives the
   spatial filter (candidate polygons must have ≥ 200 acres of unbroken suitability).

**This assumption directly sets the minimum patch-size filter in the geoprocessing
module.** Revisit if the decision-maker wishes to evaluate smaller distributed
installations or agri-voltaic configurations.

---

## 3. Decision-Maker Framing

**Method:** Single, deterministic **Analytic Hierarchy Process (AHP)** ranking  
**No machine-learning or learned ranking model** is used in this phase.

### Rationale
AHP is chosen because:
- It produces an auditable, explainable weight vector — a requirement for
  government and investor-facing reports in the Indian renewable energy context.
- It requires only pairwise comparison matrices from domain experts (or published
  literature), with no labelled training data.
- It is well-established in solar PV suitability literature for Indian states
  (e.g., Sánchez-Lozano et al.; Uyan 2013; Pillai & Banerjee 2009 for India).

**Weight derivation in Phase 1:** Weights will be derived from literature (MNRE
guidelines + published Karnataka-specific studies) and treated as fixed inputs.
Sensitivity analysis (±20% on each weight) will be run to identify rank
reversals; results flagged to the decision-maker.

**Not included in this phase:**
- Ensemble ranking / uncertainty-weighted ranking (reserved for Phase 2 if needed)
- Spatial MCDM variants (TOPSIS, VIKOR, ELECTRE)
- Reinforcement-learning or preference-learning ranking models

---

## 4. Primary Objective Statement

> **Rank candidate areas within the state of Karnataka, India, by their
> suitability for utility-scale (≥ 50 MWp) ground-mounted solar photovoltaic
> power plant development, using a reproducible AHP-based multi-criteria
> spatial analysis.**

This objective encompasses:
- **Identification** of physically viable land parcels (slope, land cover,
  proximity to grid, proximity to roads, exclusion of protected areas and
  water bodies, solar irradiance threshold).
- **Scoring** of each candidate parcel on those criteria.
- **Ranking** of candidates via AHP-weighted composite score.
- **Output** of a ranked shortlist (GeoJSON / tabular) for further due diligence.

It explicitly **excludes:**
- Financial / techno-economic modelling (LCOE, IRR) — Phase 2.
- Grid capacity headroom analysis — Phase 2.
- Community/social impact assessment — out of scope.
- Real-time or near-real-time satellite data ingestion — out of scope.

---

## 5. Geoprocessing Architecture Note

All heavy spatial computation — including raster analysis, vector overlays,
buffer operations, slope derivation from DEM, land-cover reclassification, and
AHP scoring — runs in a **standalone Python geoprocessing module**
(`backend/app/geoprocessing/`) that is entirely separate from the FastAPI
request/response layer and from MongoDB.

**The MongoDB database stores only final processed results:**
- Candidate site polygons (as GeoJSON geometry)
- Per-criterion scores and composite AHP score
- Metadata (area_ha, nearest_grid_kV, data_vintage)

**MongoDB does NOT store:**
- Raw raster data
- Intermediate computation state
- Geopandas DataFrames or rasterio datasets

This separation ensures that:
1. The API remains thin and stateless.
2. Geoprocessing jobs can be run as independent CLI scripts or Celery tasks
   without coupling to HTTP lifecycle.
3. The database layer can be replaced (e.g., PostGIS) without rewriting
   computation logic.

---

*This document is a living record. Update whenever assumptions are revised.*
