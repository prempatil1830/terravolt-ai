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

## 2. Reference Plant Size & Footprint Assumption

**Reference plant:** 50 MWp (megawatt-peak) utility-scale solar PV plant  
**Design assumption (unverified rule of thumb):** **50 MWp × 4.5 acres/MWp = 225 acres** (≈ 91.05 hectares / 0.9105 km²).  
**Circular footprint disk:**
$$\text{Radius } r = \sqrt{\frac{225 \text{ acres} \times 4046.8564\text{ m}^2/\text{acre}}{\pi}} \approx 538.36\text{ m} \approx \mathbf{0.54\text{ km}} \quad (\texttt{FOOTPRINT\_RADIUS\_KM} = 0.54)$$
$$\text{Disk Area} = \pi \times (0.54\text{ km})^2 = 0.91609\text{ km}^2 \approx \mathbf{226.37\text{ acres}} \text{ (91.61 hectares)}$$

### Reasoning & Source
The 4.5 acres/MWp factor is adopted as an unverified engineering rule of thumb based on typical Indian utility-scale benchmark ranges (4–5 acres/MWp for ground-mounted crystalline-silicon plants):
- Panel footprint and tilt-row pitch (to avoid inter-row shading at Karnataka latitude ~14°N)
- Internal access corridors, PCS/inverter stations, pooling switchyard, and perimeter security setbacks
- 50 MWp is chosen as the standard utility-scale block tender size (KREDL / SECI).

**This 0.54 km footprint radius (226.37 acres) directly sets the spatial screening disk across all candidate coordinates.** Revisit if the decision-maker evaluates alternate plant capacities or higher-density tracker systems.

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

## 6. Water Body Constraint & 1-Hectare Threshold Assumption

**Water Layer Sources:**
- ESA WorldCover 10m (2021 v200) Class 80 (Permanent Water Bodies)
- OpenStreetMap hydrographic data: `natural=water`, `waterway=river`, `waterway=canal`, and multipolygon water bodies.

**1-Hectare ($10,000\text{ m}^2$) Size Threshold (Project Engineering Assumption):**
A minimum size threshold of **1 hectare** is applied to OSM water body polygons for regional feasibility screening:
1. **Rationale:** At utility-scale PV resolution (50 MWp requiring ~225 acres / 226.4-acre disk), sub-hectare surface depressions, small seasonal cattle dugouts, and minor village farm ponds do not represent permanent regional hydrologic systems or regional flooding hazards that preclude utility development. Such micro-features are routinely resolved during civil site grading or detailed UAV topographical surveys.
2. **Exclusion Scope:** Permanent water bodies $\ge 1\text{ ha}$ (lakes, reservoirs, major irrigation tanks) along with all mapped river and canal corridors are strictly preserved in the hard-exclusion water layer.
3. **Footprint Disk Buffer:** A **500 m conservative project setback assumption** (not a claimed legal requirement) is applied from the **footprint disk edge** (nominal 0.54 km / 540 m radius disk + 500 m setback = 1,040 m from site centroid) to permanent water bodies (WorldCover Class 80, OSM rivers, reservoirs, and tanks/ponds $\ge 1\text{ ha}$). For canals and streams, exclusion applies only if they cross the footprint disk itself, otherwise their distance is recorded as context.

---

## 7. Slope Calibration & 3x3 Mean-Smoothed DEM Assumption

**Slope Dataset:** Copernicus GLO-30 Digital Elevation Model (30 m resolution).  
**Feasibility Criterion:** At least 80% of the footprint disk pixels must have slope $\le 5.0\%$ (utility-scale ground mount PV limit).

### DSM Artefacts & Smoothing Justification
1. **Copernicus DEM is a Digital Surface Model (DSM):** The Copernicus GLO-30 dataset captures surface elevations including physical structures rather than bare-earth ground elevation (DTM). Over existing solar installations, steep elevation jumps between panel tops, mounting structures, inverter shelters, perimeter security fences, and access trenches introduce high-frequency surface noise into finite-difference slope algorithms.
2. **Calibration against Operating Solar Parks:** Evaluating raw 30 m DEM slopes across the Pavagada Solar Park polygon and 20 other large operating plants ($\ge 1\text{ km}^2$) in the region demonstrated that raw slope falsely penalizes operating plants due to panel-row surface roughness (e.g., Varavoo Kaval achieves 85.4% raw, Ananthapuramu achieves 70.9% raw).
3. **Screening Check (Not Ranking Validation):** This check serves strictly as an empirical screening sanity-check against operating installations to ensure our physical feasibility envelope does not reject proven operational terrain; it does not validate the relative AHP ranking weights.
4. **Adopted 3x3 Mean Smoothing:** A standard $3 \times 3$ mean smoothing filter ($1/9 \times [1]_{3\times 3}$) applied to the DEM prior to gradient computation effectively filters out array-level surface roughness while preserving the authentic regional topographic gradient. Under 3x3 smoothed slope:
   - 100% of all Karnataka operating solar parks pass at $\ge 90\%$ usable slope (Pavagada: 100.0%, Varavoo Kaval: 90.9%, Guttigoli: 100.0%, Gollapalli: 100.0%, Gadag: 100.0%, Cleanmax: 100.0%).
   - Across all 21 large regional solar polygons, 17 pass at $\ge 80\%$ (vs. 16 raw).
5. **Standard Retention:** The conservative 80% usable footprint threshold ($\ge 80\%$ pixels with slope $\le 5\%$) is retained as the project's standard feasibility requirement.

### Solar Resource & Climate Data Resolution
1. **Global Horizontal Irradiance (GHI):** Modeled on a 1 x 1 degree grid (about 110 km) via NASA POWER 20-year climatology (2001–2020, `ALLSKY_SFC_SW_DWN`). At this macro resolution, localized terrain shading and microclimatic variations cannot be distinguished, leading to coarse, near-uniform values across adjacent candidate sites in the central solar belt.
2. **Ambient Temperature (T2m):** Sourced at 0.5 x 0.625 degree resolution from the NASA POWER climatology (`T2M`), used to evaluate temperature derating factors for crystalline PV modules.

---

*This document is a living record. Update whenever assumptions are revised.*
