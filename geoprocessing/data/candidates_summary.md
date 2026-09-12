# Karnataka Solar PV — Selected Candidate Sites

**Generated:** 2026-09-12 16:41 UTC  
**Grid step:** 0.1deg (~11 km)  
**Total grid points:** 3266  
**Phase-1 eligible / excluded:** 1888 / 1378  
**Phase-2 (final) eligible / excluded / needs_review:** 10 / 1 / 1  

> **Selection rule:** FROZEN v1 (2026-09-12): 5x4 region-grid diversity; top-Phase1-score-per-region; top 12 by Phase-1 score. Final ranking uses full Phase-2 AHP score. See generate_candidates.py docstring.

## Selected Candidates (sorted by full AHP score)

| Rank | Lat | Lon | AHP Score | /100 | GHI (kWh/m2/d) | Sub (km) | Tx line (km) | LC proxy | T2m (C) | Status |
|------|-----|-----|-----------|------|----------------|----------|--------------|----------|---------|--------|
| 1 | 14.3000 | 77.4000 | 0.727 | 72.7 | 5.6566 | 0.9 | 0.21 | 0.3826 | 26.67 | eligible |
| 2 | 14.0000 | 77.0000 | 0.6746 | 67.46 | 5.6566 | 12.1 | 6.06 | 0.603 | 25.15 | eligible |
| 3 | 14.3000 | 77.0000 | 0.6471 | 64.71 | 5.6566 | 13.46 | 0.53 | 0.5049 | 25.83 | eligible |
| 4 | 14.1000 | 76.0000 | 0.6456 | 64.56 | 5.617 | 8.88 | 3.49 | 0.1556 | 24.57 | eligible |
| 5 | 14.2000 | 75.0000 | 0.6354 | 63.54 | 5.4881 | 3.24 | 2.52 | 0.0937 | 24.33 | eligible |
| 6 | 12.8000 | 77.8000 | 0.6275 | 62.75 | 5.4809 | 4.42 | 1.3 | 0.0788 | 23.74 | eligible |
| 7 | 14.0000 | 77.4000 | 0.6073 | 60.73 | 5.6566 | 16.49 | 1.19 | 0.3051 | 25.39 | eligible |
| 8 | 14.3000 | 75.0000 | 0.5712 | 57.12 | 5.4881 | 13.13 | 5.09 | 0.2491 | 24.39 | NEEDS_REVIEW |
| 9 | 16.8000 | 77.9000 | 0.5535 | 55.35 | 5.5447 | 16.17 | 4.72 | 0.2334 | 25.84 | eligible |
| 10 | 14.3000 | 76.1000 | 0.5454 | 54.54 | 5.617 | 22.53 | 0.67 | 0.304 | 25.16 | eligible |
| 11 | 11.5000 | 77.1000 | 0.2899 | 28.99 | 5.4833 | None | None | 0.3163 | 23.5 | eligible |
| 12 | 15.8000 | 77.0000 | 0.5114 | 51.14 | 5.5687 | - | - | - | 27.18 | excluded |

## needs_review count

Of the **12** selected candidates, **1** returned `needs_review` (Overpass unreachable during water-body check). These require offline water-body verification before final eligibility.

## Notes
- Phase-1 screening uses slope + land-use + WDPA (all local).
- Phase-1 preliminary score uses GHI + T2m from NASA POWER only.
- Phase-2 full AHP score adds substation distance, land-cost proxy, and water-body buffer (Overpass, 12 calls only).
- Centroid-only evaluation at grid scale; shortlisted sites should be re-evaluated with 9-point footprint sampling.
