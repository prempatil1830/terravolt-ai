# Karnataka Solar PV — Selected Candidate Sites

**Generated:** 2026-10-18 10:37 UTC  
**Grid step:** 0.1deg (~11 km)  
**Total grid points:** 3266  
**Phase-1 eligible / excluded:** 377 / 2889  
**Phase-2 (final) eligible / excluded / needs_review:** 9 / 0 / 3  

> **Selection rule:** FROZEN v2 (2026-10-15): 5x4 region-grid diversity; region winners ranked by preliminary AHP score including substation distance (GHI 0.40, Substation 0.40, Temperature 0.10, renormalized to 1.0). Final evaluation applies full 4-criteria AHP.

## Selected Candidates (sorted by full AHP score)

| Rank | District | Lat | Lon | AHP Score | /100 | GHI (kWh/m2/d) | Sub (km) | Tx line (km) | LC proxy | T2m (C) | Status |
|------|----------|-----|-----|-----------|------|----------------|----------|--------------|----------|---------|--------|
| 1 | Chitradurga | 14.2000 | 76.5000 | 0.7091 | 70.91 | 5.617 | 1.78 | 0.25 | 0.2225 | 24.57 | eligible |
| 2 | Bengaluru North | 13.2000 | 77.4000 | 0.6989 | 69.89 | 5.6083 | 3.78 | 0.94 | 0.233 | 23.74 | NEEDS_REVIEW |
| 3 | Chitradurga | 14.4000 | 76.7000 | 0.6888 | 68.88 | 5.617 | 2.61 | 0.16 | 0.2118 | 25.83 | eligible |
| 4 | Vijayanagara | 15.0000 | 76.1000 | 0.6747 | 67.47 | 5.617 | 4.87 | 0.26 | 0.2333 | 25.65 | NEEDS_REVIEW |
| 5 | Raichur | 16.0000 | 76.9000 | 0.6714 | 67.14 | 5.5375 | 0.44 | 0.55 | 0.3167 | 27.18 | eligible |
| 6 | Vijayapura | 16.3000 | 76.2000 | 0.6498 | 64.98 | 5.5202 | 0.98 | 0.14 | 0.1611 | 26.66 | eligible |
| 7 | Bagalkote | 16.4000 | 75.1000 | 0.6246 | 62.46 | 5.4571 | 0.42 | 0.01 | 0.0364 | 25.86 | eligible |
| 8 | Davanagere | 14.2000 | 75.6000 | 0.6198 | 61.98 | 5.4881 | 6.23 | 0.39 | 0.1313 | 23.87 | eligible |
| 9 | Mysuru | 12.2000 | 76.6000 | 0.6124 | 61.24 | 5.413 | 4.88 | 1.12 | 0.248 | 23.86 | eligible |
| 10 | Kalaburagi | 17.1000 | 76.8000 | 0.6063 | 60.63 | 5.4091 | 0.66 | 0.56 | 0.2022 | 27.23 | eligible |
| 11 | Vijayapura | 17.2000 | 75.9000 | 0.5992 | 59.92 | 5.461 | 4.12 | 1.4 | 0.1306 | 26.53 | eligible |
| 12 | Dharwad | 15.4000 | 75.1000 | 0.5968 | 59.68 | 5.4247 | 4.76 | 1.25 | 0.1346 | 24.84 | NEEDS_REVIEW |

## needs_review count

Of the **12** selected candidates, **3** returned `needs_review` (Site #2: primary substation voltage untagged in OSM; Sites #4 and #12: untagged substation closer than confirmed >=33kV). These require field / KPTCL SLD verification before final eligibility.

## Notes
- Phase-1 screening uses slope + land-use + WDPA (all local).
- Phase-1 preliminary score uses GHI + T2m from NASA POWER only.
- Phase-2 full AHP score adds substation distance, land-cost proxy, and water-body buffer (Overpass, 12 calls only).
- Centroid-only evaluation at grid scale; shortlisted sites should be re-evaluated with 9-point footprint sampling.
