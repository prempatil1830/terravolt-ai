# MCDM Criteria — Solar PV Site Suitability, Karnataka

> Last updated: 2026-09-08  
> Methodology: AHP (Analytic Hierarchy Process) — see `assumptions.md §3`  
> Analysis unit: 250 m raster grid cells, clipped to Karnataka state boundary (GADM 4.1)

This document defines the seven criteria used in the multi-criteria decision matrix.
No data has been fetched at this stage. This is a design-specification document only.

---

## Criterion 1 — Solar Irradiation (GHI)

| Field | Value |
|---|---|
| **Type** | Benefit — higher is better |
| **Unit** | kWh / m² / day (daily mean annual average) |
| **Source** | NASA POWER (Prediction Of Worldwide Energy Resources) API |
| **Source URL** | https://power.larc.nasa.gov/api/temporal/climatology/point |
| **Parameter code** | `ALLSKY_SFC_SW_DWN` (all-sky surface shortwave downward irradiance) |
| **Spatial resolution** | 1 x 1 degree grid (about 110 km); interpolated to analysis resolution |
| **Temporal coverage** | 20-year climatological average (2001–2020, per API header) |
| **Access** | Free, no API key required; JSON response |

### Role in MCDM
GHI is the primary energy-yield driver. Higher GHI directly increases annual energy
production (AEP) and therefore project revenue.

NASA POWER 2001–2020 climatology for nine validation points across Karnataka and
adjacent Rajasthan reveals a narrower and regionally different pattern than commonly
cited in general Karnataka solar literature: the range observed is roughly **5.29
(coastal, Mangalore) to 5.61 kWh/m²/day (central plateau, Tumakuru/Chikkaballapur)**.
North-Karnataka interior points — Kalaburagi (5.41), Bidar (5.36), Raichur (5.54),
Yadgir (5.54) — score *lower* than the central Deccan plateau, not highest as often
assumed based on general Karnataka solar atlases (which likely draw on PVGIS or
ground-calibrated atlas products and show a more pronounced northward GHI gradient).

This is an **open discrepancy between NASA POWER and other public atlases, not yet
resolved**. It may reflect differences in base period (NASA POWER uses 2001–2020
for the pre-computed climatology), cloud-fraction modelling differences, or the
1 x 1 degree grid (about 110 km) smoothing disproportionately affecting the arid north. **A PVGIS
cross-check should be added in Phase 2 geoprocessing to bias-correct or at minimum
sanity-check NASA POWER GHI values before final site scoring.**

### Known Limitations
- **Coarse spatial resolution (1 x 1 degree grid (about 110 km)):** The NASA POWER grid cannot resolve
  localised orographic shading caused by the Western Ghats ridgeline. Cells
  straddling the Ghats escarpment will average an unrealistically high GHI for
  the leeward side. Sites in Dakshina Kannada, Kodagu, and Hassan districts that
  fall partly in Ghats shadow will be over-scored. **Mitigation: apply slope
  hard-constraint (Criterion 2) to exclude Ghats terrain independently of GHI.**
- **No sub-daily variability captured:** The climatological mean does not encode
  cloud intermittency or monsoon-season generation losses. Phase 2 should
  supplement with NASA POWER's monthly breakdown to compute P90 yield estimates.
- **Not Karnataka-specific validation / atlas discrepancy:** MNRE's Solar Radiation
  Resource Assessment (SRRA) mast network in Karnataka (~10 stations) would offer
  ground-truth calibration but the data is not publicly downloadable. NASA POWER
  values diverge from general Karnataka solar atlas expectations (see Role in MCDM
  above); if SRRA or PVGIS data becomes accessible, it should be used to
  bias-correct NASA POWER values before scoring.
- **Grid-cell duplication risk:** Candidate sites closer together than the 1 x 1 degree grid (about 110 km)
  native grid resolution will receive identical GHI values. This was confirmed
  empirically during validation testing: Raichur and Yadgir (~66 km apart) returned
  byte-identical annual GHI (5.5447), as did Tumakuru and Chikkaballapur (~65 km
  apart, 5.6083). This means GHI has limited power to discriminate between
  geographically close candidates in the final 8–15 site shortlist. Where two
  shortlisted sites fall within the same 1 x 1 degree grid (about 110 km) cell, GHI must not be used as
  a tiebreaker; differentiation must come from other criteria.

---

## Criterion 2 — Slope

| Field | Value |
|---|---|
| **Type** | Hard constraint — exclude footprint disks where usable fraction (<5% slope) < 80% |
| **Unit** | Percentage slope (rise / run × 100) |
| **Source** | Copernicus DEM GLO-30 (AWS Open Data), processed with a $3 \times 3$ mean smoothing filter |
| **Source URL** | https://copernicus-dem-30m.s3.amazonaws.com / https://registry.opendata.aws/copernicus-dem/ |
| **Alternate / Previous source** | SRTM 1-arc-second (~30 m; USGS EarthExplorer / dwtkns — note: unavailable without login) |
| **Spatial resolution** | 30 m native pixel; derived from $3 \times 3$ mean-smoothed DEM (~90 m effective spatial scale) to filter high-frequency DSM surface roughness |
| **Vertical accuracy** | <4 m absolute (90% linear error, Copernicus DEM mission specs) |
| **Access** | Free, public access via AWS S3 Cloud-Optimized GeoTIFF; no authentication or login required |

### Exclusion Threshold & Justification
Footprint disks must achieve at least 80% usable area with slope $\le 5.0\%$. Justification:

1. **Unverified Project Assumption:** The 5.0% maximum slope threshold remains an **unverified project design assumption**. While commonly cited in utility solar feasibility studies, no primary empirical cost curve or official MNRE standard formally fixing 5.0% has been independently verified.
2. **Civil engineering & racking cost:** Industry rules of thumb state that fixed-tilt racking above 5% slope requires terracing, grading, or custom piling (often estimated at ₹15–25 lakh/MW). This remains an unverified engineering estimate pending geotechnical site investigation.
3. **Inter-row shading geometry:** On slopes >5%, standard fixed-tilt N–S row spacing calculations (for Karnataka latitude ~14°N) require custom row-by-row tilt-pitch adjustments to avoid inter-row shading.
4. **Precedent in literature:** Sánchez-Lozano et al. (2013) and Uyan (2013) are real, topically relevant GIS-MCDM solar papers, but their specific slope thresholds were adopted as scoping heuristics rather than legally mandated cutoffs.

### Calibration vs. Validation Note
- **Calibration (Not Validation):** Evaluating slope over 21 operating solar parks in the region (e.g., Pavagada, Varavoo Kaval, Guttigoli) to evaluate raw vs. 3x3-smoothed DEM slope is **empirical screening calibration**, NOT independent validation of the ranking model. Its purpose is solely to ensure the physical feasibility envelope does not reject proven operational terrain due to panel-row surface roughness in Copernicus DSM. It does not validate the AHP criteria weightings.
- **Effective Scale:** The $3 \times 3$ mean filter operates over a 3-pixel window, yielding an effective spatial scale of approximately **90 m**, which suppresses panel arrays, fencing, and berm micro-roughness while preserving true regional terrain gradients. Known steep terrain (such as the Western Ghats escarpment at Madikeri, 14.5% mean slope, and rugged granite ridges) remains decisively excluded.

### Known Limitations
- Copernicus DEM GLO-30 is a digital surface model (DSM) capturing canopy and structures, not a bare-earth DTM.
- High vertical accuracy (<4 m) is maintained across regional plains, but steep escarpment edges may present interpolation artefacts.
- Free AWS Open Data Cloud-Optimized GeoTIFFs provide open reproducibility without authentication barriers.

---

## Criterion 3 — Land-Use Suitability

| Field | Value |
|---|---|
| **Type** | Hard constraint — exclude forest, urban, and water classes |
| **Unit** | Categorical (class code → binary: suitable / excluded) |
| **Source** | ESA WorldCover 2021 (v200), 10 m resolution |
| **Source URL** | https://worldcover2021.esa.int/ (direct download, GeoTIFF tiles) |
| **Alternate access** | Google Earth Engine: `ESA/WorldCover/v200` image collection |
| **Spatial resolution** | 10 m; aggregated to 250 m majority class for analysis |
| **License** | Creative Commons Attribution 4.0 International (CC BY 4.0) |
| **Temporal coverage** | 2021 land-cover epoch |

### Excluded Class Codes

| WorldCover Class Code | Class Name | Decision | Reason |
|---|---|---|---|
| 10 | Tree cover | **EXCLUDED** | Forest / likely protected under Indian Forest Act 1927 |
| 20 | Shrubland | Eligible | Scrubland is generally suitable |
| 30 | Grassland | Eligible | Wastelands eligible under MNRE guidelines |
| 40 | Cropland | Eligible (penalised) | Not hard-excluded; carries strong AHP cost modifier; food-security implications flagged |
| 50 | Built-up / Urban | **EXCLUDED** | Not available for utility-scale ground-mount |
| 60 | Bare / sparse vegetation | Eligible | Highest suitability class |
| 80 | Permanent water bodies | **EXCLUDED** | Structural and ecological constraint |
| 90 | Herbaceous wetland | **EXCLUDED** | Ecological sensitivity; likely subject to wetland regulations |
| 95 | Mangroves | **EXCLUDED** | Legally protected under Coastal Regulation Zone rules |

### Why ESA WorldCover Instead of Bhuvan LULC?
The Bhuvan LULC 50K dataset (NRSC, ISRO) is the most authoritative LULC product
for India but bulk state-level download requires registration and approval from NRSC,
and carries a non-commercial licence clause. ESA WorldCover 2021 is:
- Freely downloadable without registration
- CC BY 4.0 licensed (no restriction on research/commercial use)
- 10 m resolution — finer than Bhuvan LULC 50K (minimum mapping unit ~2.5 ha)
- India accuracy reported at 74–81% overall (WorldCover 2021 validation report, ESA 2022)

**Recommendation:** If regulatory clearance filings are generated from this tool,
substitute ESA WorldCover with the NRSC Bhuvan LULC 50K or the ISFR 2023 forest map
from the Forest Survey of India for the forest exclusion layer, as Indian statutory
bodies will expect FSI/MoEFCC-aligned forest classifications.

### Known Limitations
- **Accuracy for India scrubland/open forest boundary:** The ESA product has known
  commission errors in distinguishing open forest from dense scrubland in the Deccan
  plateau region of Karnataka (Bellary, Kalaburagi, Bidar). Some open scrub cells are
  mis-classified as tree cover, causing false exclusions.
- **2021 vintage:** Land-use change after 2021 is not captured.
- **Cropland not hard-excluded:** Including cropland as a candidate requires
  acknowledgement that acquiring agricultural land in Karnataka is subject to the
  Karnataka Land Reforms Act (Section 79-A/B). Cropland cells should carry a strong
  negative AHP score modifier.

---

## Criterion 4 — Distance to Substation / Transmission Line

| Field | Value |
|---|---|
| **Type** | Benefit — closer is better (lower cost of grid connection) |
| **Unit** | Kilometres (Euclidean distance from cell centroid to nearest feature) |
| **Source** | OpenStreetMap (OSM) power infrastructure tags |
| **Source URL** | https://www.openstreetmap.org / Overpass API: https://overpass-api.de |
| **Relevant OSM tags** | `power=substation`, `power=line`, `voltage=*` |
| **Access** | Free, no authentication required |
| **Data extraction** | Overpass API query; or pre-extracted via Geofabrik India extract |
| **Geofabrik extract** | https://download.geofabrik.de/asia/india.html |

> **⚠️ ESTIMATED / PROXY** — This criterion is explicitly marked as an estimate.
> OSM power data in Karnataka is substantially incomplete. See limitations below.

### Scoring Direction
Euclidean distance is inverted for AHP scoring: cells closer to the nearest ≥33 kV
substation or ≥66 kV transmission line receive higher scores. A practical cost-distance
function (e.g., ₹1.5–2 Cr/km for 33 kV line construction, CERC benchmark 2022) can
convert distance to monetised evacuation cost in Phase 2.

### Known Limitations
- **OSM power infrastructure in Karnataka is substantially incomplete.** Many 33 kV
  and 11 kV substations operated by BESCOM, CESC, HESCOM, GESCOM, MESCOM, and CHESCOM
  are absent from OSM entirely. Rural substations in northern Karnataka (Kalaburagi,
  Bidar, Yadgir — which also have the best GHI) are particularly under-mapped.
- **No voltage-level filtering at this stage:** A 220 kV transmission corridor is not
  equivalent to a 33 kV distribution substation for project evacuation purposes. Phase 2
  must filter by minimum acceptable voltage tier (≥33 kV for 50 MWp).
- **Euclidean distance ≠ cable routing cost:** Actual evacuation line routing follows
  road/right-of-way corridors and avoids forest land. Straight-line distance
  systematically underestimates true connection cost.
- **Official alternative (not yet accessible):** KPTCL publishes a GIS layer of their
  transmission network, but it is not available for public download. A formal
  data-sharing request to KPTCL/KREDL is recommended before final site ranking.

---

## Criterion 5 — Distance from Protected Areas & Water Bodies

| Field | Value |
|---|---|
| **Type** | Hard constraint — buffer exclusion zone |
| **Unit** | Binary (inside buffer = excluded; outside = eligible) |
| **Buffer — National Parks & Wildlife Sanctuaries** | **10 km from boundary** |
| **Buffer — Rivers, lakes, reservoirs, ponds ≥ 1 ha, WorldCover 80** | **500 m from footprint disk edge (conservative project setback assumption)** |
| **Canals & Streams** | **Excluded only if crossing footprint disk itself; non-crossing distance recorded as context** |

### Data Sources

#### Protected Areas
| Sub-source | Details |
|---|---|
| **Dataset** | World Database on Protected Areas (WDPA), Karnataka subset |
| **URL** | https://www.protectedplanet.net/en/thematic-areas/wdpa |
| **Format** | Shapefile or GeoPackage, downloadable by country (India) |
| **License** | Free for non-commercial and research use (UNEP-WCMC terms) |
| **Coverage** | National Parks, Wildlife Sanctuaries, Tiger Reserves, Ramsar sites, Biosphere Reserves registered in Karnataka |

#### Water Bodies
| Sub-source | Details |
|---|---|
| **Dataset** | OSM water features (`natural=water`, `waterway=river`, `waterway=canal`) |
| **URL** | https://download.geofabrik.de/asia/india.html (Geofabrik India extract) |
| **Supplementary** | ESA WorldCover 2021 class 80 (permanent water) as raster mask |

### Buffer Distance Justification

**Protected areas — 10 km:**
This project uses a 10 km buffer as a deliberately conservative screening threshold — well above the 1 km binding SC minimum — chosen to also cover the upper range of the 2011 MoEF guideline's notification width and reduce false inclusions pending site-specific ESZ verification. This is a conservative project choice, not a claimed legal mandate.

**Water bodies — 500 m:**
- The 500 m buffer is a conservative project setback assumption, not a claimed legal requirement.
- Provides an engineering and environmental safety margin against floodplains, bund instability, and seasonal water level variations.
- Applied from the footprint disk edge to WorldCover class 80 permanent water and OSM rivers, reservoirs, and tanks/ponds >= 1 ha.
- For canals and streams, exclusion applies only if they cross the footprint disk itself; otherwise, the distance is recorded as context.

### Known Limitations
- **WDPA completeness:** Some recently declared Karnataka State Biodiversity Heritage
  Sites and Community Reserves (gazetted post-2020) may not yet appear in WDPA.
  Cross-check with Karnataka Biodiversity Board records before final clearance analysis.
- **OSM water bodies are incomplete in rural Karnataka:** Many minor tanks and irrigation
  tanks (*keres*) — ecologically sensitive and protected under the Karnataka Tank
  Conservation and Development Authority Act — are under-mapped in OSM. The 500 m water
  buffer may miss tanks not captured by OSM or ESA WorldCover.
- **ESZ notifications are site-specific and evolving:** The 10 km uniform buffer is a
  conservative legal proxy, not a surveyed boundary. For shortlisted sites within 10–20 km
  of any protected area, the current ESZ notification status must be verified with the
  Karnataka Forest Department before due diligence.

---

## Criterion 6 — Land-Cost Proxy

| Field | Value |
|---|---|
| **Type** | Cost — lower is better |
| **Unit** | Dimensionless proxy score (distance-derived, normalised 0–1) |
| **Proxy used** | Euclidean distance to nearest district headquarters / major town |
| **Source** | OSM `place=city` and `place=town` nodes |
| **Source URL** | https://download.geofabrik.de/asia/india.html (Geofabrik India extract) |

> **⚠️ ESTIMATED — rough proxy only. Per-district rupee land prices are NOT used.**
> See rationale and alternatives considered below.

### Proxy Logic & Rationale
No authoritative, consistently georeferenced, programmatically accessible dataset of
land transaction prices exists for Karnataka that covers all 31 districts and is recent
enough (post-2020) to be reliable for cross-district comparison. Therefore, this
analysis uses **distance to the nearest district headquarters town as an inverse proxy
for land cost**, based on bid-rent theory: periurban land commands a price premium
driven by urban expansion demand, while remote cells in the same district tend to be
lower-value agricultural or wasteland parcels.

This proxy explicitly does **NOT** represent actual land acquisition costs in ₹/acre.
It is an ordinal ranking device only. The proxy will systematically mis-rank cells where:
- A remote cell is on revenue-classified land with land reform ceiling restrictions
- A cell near a town is designated government wasteland (available cheaply via KREDL)

### Alternatives Considered But Rejected

| Source | Reason Rejected |
|---|---|
| District guidance values (circle rates) — Karnataka Dept. of Stamps & Registration | Not machine-readable; published as PDF district schedules only; no bulk API |
| CBRE / JLL commercial land transaction data | Proprietary; requires purchase |
| Village-level patta/RTC records (Bhoomi portal, Karnataka) | Parcel-level; no API for bulk query; licensing unclear for derivative analysis |

**Phase 2 recommendation:** Procure district-level guidance values from Karnataka
Stamps & Registration Department to replace this proxy with actual circle-rate data
before any investment decision is made on the output.

### Known Limitations
- Distance to district HQ is a very coarse proxy; land costs within a single district
  can vary by 10× depending on road access, irrigation status, and proximity to
  industrial corridors (e.g., Bellary mining belt).
- The proxy does not account for Karnataka's reservation of certain wastelands for solar
  under KREDL's state solar policy, where land may be available at nominal lease rates
  regardless of location.
- AHP weight for this criterion should be kept low (≤10%) given the acknowledged
  imprecision of the proxy.

---

## Criterion 7 — Temperature-Related Yield Derating

| Field | Value |
|---|---|
| **Type** | Cost — higher temperature means lower panel efficiency |
| **Unit** | °C (mean annual ambient temperature used to estimate PV derating) |
| **Source** | NASA POWER API |
| **Source URL** | https://power.larc.nasa.gov/api/temporal/climatology/point |
| **Parameter codes** | `T2M` (Temperature at 2 m, °C) and `T2M_MAX` (daily max temperature) |
| **Temporal coverage** | 20-year climatological average (2001–2020, per API header) |
| **Spatial resolution** | 0.5 x 0.625 degree |
| **Access** | Free, no API key required; same API call as GHI fetch |

### Role in MCDM
Standard crystalline-silicon PV panels (dominant technology in Karnataka utility-scale
deployments) exhibit a temperature coefficient of approximately **−0.35 to −0.45 %/°C**
relative to Standard Test Condition (25°C cell temperature). Cell temperature typically
runs 20–30°C above ambient under full irradiance.

Since northern Karnataka does *not* actually have the irradiance advantage initially
assumed (per NASA POWER validation data in Criterion 1), the high summer ambient
temperatures in districts like Kalaburagi (T2M_MAX = 44.6°C) act purely as an
efficiency penalty with no GHI upside to offset. This criterion captures that penalty.

**Validation Data Computed Derating:**
Using the stated scoring function below, empirical validation yields:

| Point | T2M (Annual °C) | Estimated T_cell | Computed Derating Factor |
|---|---|---|---|
| Bengaluru | 23.74 | 51.74°C | 0.893 (89.3%) |
| Kalaburagi | 26.58 | 54.58°C | 0.882 (88.2%) |
| Mangalore | 26.74 | 54.74°C | 0.881 (88.1%) |
| Jaisalmer (ref) | 27.60 | 55.60°C | 0.878 (87.8%) |

**Note on discriminatory power:** The maximum spread in computed derating across
Karnataka's distinct climatic zones (Bengaluru plateau vs. Mangalore coast/arid north)
is barely ~1.2%. Like GHI, temperature derating using annual averages offers very
limited mathematical power to meaningfully differentiate candidate sites in the AHP
matrix.

### Scoring Function (to be applied in geoprocessing module)
```
T_cell_estimated = T2M_annual_mean + 28        # NOCT-based approximation
derating_factor  = 1 + temp_coeff × (T_cell_estimated − 25)
# temp_coeff ≈ −0.004 /°C (conservative mid-range value)
# derating_factor < 1.0 → yield loss relative to STC
```
Higher ambient temperature → derating_factor further below 1.0 → higher cost score
in AHP matrix.

### Known Limitations
- **Coarse spatial resolution:** The 0.5 x 0.625 degree NASA POWER grid cannot
  resolve elevation-driven temperature inversions or urban heat island effects.
- **Cell temperature approximation is simplified:** The NOCT correction (+28°C above
  ambient) does not account for wind speed, mounting configuration, or tracker vs.
  fixed-tilt layout. A Sandia / King model is deferred to Phase 2 energy yield modelling.
- **Flatness of signal (Lack of discriminatory power):** As shown in the computed
  derating table above, the variance in annual average temperature across Karnataka
  translates to a negligible ~1.2% difference in panel efficiency. This criterion may
  be too flat to warrant a significant weight in the final MCDM.
- **Interaction with Criterion 1 (GHI):** Temperature derating (0.5 x 0.625 degree) and GHI (1 x 1 degree grid (about 110 km)) both suffer from
  coarse NASA POWER resolution limitations, and both showed limited discriminating
  power in validation testing (GHI: ~0.32 kWh/m²/day spread across 9 points; derating:
  ~1.2% spread across 4 points). These are two independently weak signals, not a
  compounding correlated advantage.

---

## Compounding Data Uncertainty

Two of the three weakest data layers — land-use classification (ESA WorldCover
misclassification risk in Deccan scrubland) and substation distance (OSM
incompleteness in rural areas) — geographically cluster in northern Karnataka
(Kalaburagi, Bidar, Yadgir, Raichur districts). These weaknesses are independently
documented from source dataset properties and are not contingent on GHI assumptions.

Note: An earlier version of this section also cited GHI as a compounding weakness
for this region, on the assumption that northern Karnataka has the highest GHI and
therefore most at risk of over-scoring. Validation testing against NASA POWER does
*not* support the premise that northern Karnataka scores highest on GHI (see Criterion
1, Role in MCDM); GHI has therefore been removed from this regional compounding
argument pending Phase 2 atlas reconciliation. Temperature data from NASA POWER
remains subject to its coarse 0.5 x 0.625 degree resolution caveat.

Candidates in northern Karnataka with high suitability scores driven primarily by
land-use and substation criteria should be flagged with a "low data confidence" tag
rather than presented with the same confidence as candidates in better-mapped regions.
This should be implemented as a simple per-candidate data confidence indicator in the
later scoring/matrix phase.

---

## Summary Table

| # | Criterion | Type | Suggested Weight Class | Source | Completeness for Karnataka |
|---|---|---|---|---|---|
| 1 | Solar Irradiation (GHI) | Benefit | Medium ¹ | NASA POWER | ★★★☆☆ Coarse 1 x 1 degree grid (about 110 km); grid-cell duplication risk |
| 2 | Slope | Hard constraint | — | Copernicus DEM GLO-30 (AWS Open Data) | ★★★★☆ Good; superior to SRTM; no login required |
| 3 | Land-use suitability | Hard constraint | — | ESA WorldCover 2021 (10 m) | ★★★☆☆ Open forest mis-classification risk |
| 4 | Distance to substation | Benefit | Medium | OpenStreetMap | ★★☆☆☆ Substantially incomplete in rural Karnataka |
| 5 | Protected areas & water bodies buffer | Hard constraint | — | WDPA + OSM | ★★★☆☆ Post-2020 protected areas may be missing |
| 6 | Land-cost proxy | Cost | Low | OSM distance-to-town proxy | ★★☆☆☆ Very rough — proxy only |
| 7 | Temperature derating | Cost | Low ² | NASA POWER | ★★★☆☆ 0.5 x 0.625 degree grid; flat signal |

> **Completeness rating key:** ★★★★★ = comprehensive · ★★★★☆ = good with minor gaps ·
> ★★★☆☆ = usable with significant caveats · ★★☆☆☆ = coarse proxy / known incompleteness ·
> ★☆☆☆☆ = not recommended for final decision without supplementary data

> ¹ **GHI weight downgraded from High to Medium** for two reasons: (1) the 1 x 1 degree grid (about 110 km)
> native grid resolution causes geographically close candidate sites to receive
> byte-identical GHI values (empirically confirmed — Raichur/Yadgir and
> Tumakuru/Chikkaballapur), removing GHI's discriminating power within clusters;
> (2) NASA POWER values show an unresolved discrepancy with other public atlases
> in the regional GHI pattern for Karnataka, meaning the ranking signal itself is
> uncertain pending Phase 2 PVGIS cross-check. See Criterion 1 Known Limitations.

> ² **Temperature derating weight downgraded from Medium to Low** because the flat ~1.2%
> spread in panel efficiency translates to only ~1,000 MWh/year difference for a standard
> 50 MWp plant (assuming ~85,000 MWh total annual yield). In the context of the MCDM, this
> difference is negligible compared to the massive CAPEX variations driven by land cost
> (Criterion 6) and distance to substations (Criterion 4).

---

*This document is a pre-execution specification. No data has been fetched.
Update criterion definitions, sources, or thresholds before beginning the
data ingestion phase.*
