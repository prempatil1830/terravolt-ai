import React from 'react';
import { Layers, CheckCircle2, Sliders, Database, ShieldAlert } from 'lucide-react';
import MapView from '../components/MapView';

export default function Methodology() {
  return (
    <div className="content-wrapper page-fade-in" style={{ paddingBottom: 64 }}>
      {/* Title & One-line Subtitle */}
      <div>
        <h1 className="page-title">Screening Methodology</h1>
        <p className="page-subtitle">
          Deterministic Analytic Hierarchy Process (AHP) multi-criteria decision model for utility-scale solar PV development in Karnataka.
        </p>
      </div>

      {/* Statewide Scope Map Card */}
      <div
        className="card"
        style={{
          marginTop: 24,
          padding: 0,
          overflow: 'hidden',
          borderRadius: 12,
          border: '1px solid var(--border)',
        }}
      >
        <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
          <h2 className="section-title" style={{ fontSize: 16 }}>
            Geospatial Study Area: Karnataka State Scope
          </h2>
          <p className="caption-text">
            Bbox: 11.59°N–18.46°N, 74.09°E–78.59°E · 31 Revenue Districts · EPSG:4326 WGS84
          </p>
        </div>
        <MapView height={360} />
      </div>

      {/* Methodology Phases Grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: 20,
          marginTop: 24,
        }}
      >
        {/* Phase 1 */}
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <Layers size={18} strokeWidth={1.5} color="var(--primary)" />
            <h3 className="section-title" style={{ fontSize: 16 }}>
              Phase 1: Regional Discretization
            </h3>
          </div>
          <p className="caption-text" style={{ fontSize: 13, lineHeight: 1.6 }}>
            A regular spatial lattice of <strong>3,266 grid centroids</strong> spaced at 0.1° intervals (~11 km) is cast across Karnataka's terrestrial landmass. Centroids are partitioned into a 5×4 geographic mesh to guarantee balanced territorial coverage and prevent clustering bias.
          </p>
          <div style={{ marginTop: 14, padding: '8px 12px', background: 'var(--surface-2)', borderRadius: 8, fontSize: 12 }}>
            <span style={{ color: 'var(--text-2)' }}>Grid Resolution:</span> <strong>0.1° (~11.1 km)</strong> · <span style={{ color: 'var(--text-2)' }}>Total Nodes:</span> <strong>3,266</strong>
          </div>
        </div>

        {/* Phase 2 */}
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <ShieldAlert size={18} strokeWidth={1.5} color="var(--primary)" />
            <h3 className="section-title" style={{ fontSize: 16 }}>
              Phase 2: Hard Constraint Gating
            </h3>
          </div>
          <p className="caption-text" style={{ fontSize: 13, lineHeight: 1.6 }}>
            Boolean exclusion filters eliminate unsuitable footprints before evaluation:
          </p>
          <ul style={{ paddingLeft: 18, marginTop: 8, fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.6 }}>
            <li><strong style={{ color: 'var(--text)' }}>Slope &gt; 5.0%:</strong> Copernicus DEM GLO-30 (≥80% footprint usable).</li>
            <li><strong style={{ color: 'var(--text)' }}>Land Cover:</strong> ESA WorldCover (excludes forests, wetlands, permanent water, built-up urban).</li>
            <li><strong style={{ color: 'var(--text)' }}>Protected Reserves:</strong> 10 km WDPA buffer setback.</li>
            <li><strong style={{ color: 'var(--text)' }}>Water Setback:</strong> 500 m buffer from permanent bodies ≥1 ha.</li>
          </ul>
        </div>

        {/* Phase 3 */}
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <Sliders size={18} strokeWidth={1.5} color="var(--primary)" />
            <h3 className="section-title" style={{ fontSize: 16 }}>
              Phase 3: Multi-Criteria Weighting (AHP)
            </h3>
          </div>
          <p className="caption-text" style={{ fontSize: 13, lineHeight: 1.6 }}>
            Cleared candidates are scored using min-max normalized Analytical Hierarchy Process weights:
          </p>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12.5 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 8px', background: 'var(--surface-2)', borderRadius: 6 }}>
              <span>Solar Resource (GHI)</span>
              <strong className="tabular-nums">40%</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 8px', background: 'var(--surface-2)', borderRadius: 6 }}>
              <span>Substation Proximity (≥33 kV)</span>
              <strong className="tabular-nums">40%</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 8px', background: 'var(--surface-2)', borderRadius: 6 }}>
              <span>Land Cost Distance Proxy</span>
              <strong className="tabular-nums">10%</strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 8px', background: 'var(--surface-2)', borderRadius: 6 }}>
              <span>Temperature Derating (T2m)</span>
              <strong className="tabular-nums">10%</strong>
            </div>
          </div>
        </div>

        {/* Phase 4 */}
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <CheckCircle2 size={18} strokeWidth={1.5} color="var(--primary)" />
            <h3 className="section-title" style={{ fontSize: 16 }}>
              Phase 4: Spatial Isolation & Non-Maximum Suppression
            </h3>
          </div>
          <p className="caption-text" style={{ fontSize: 13, lineHeight: 1.6 }}>
            To prevent site clustering and ensure statewide grid diversification, candidates are ranked greedily and filtered through a <strong>≥20 km mutual Euclidean distance isolation radius</strong>, yielding an actionable, geographically diversified shortlist.
          </p>
          <div style={{ marginTop: 14, padding: '8px 12px', background: 'var(--surface-2)', borderRadius: 8, fontSize: 12 }}>
            <span style={{ color: 'var(--text-2)' }}>Minimum Inter-Site Spacing:</span> <strong>20.0 km</strong>
          </div>
        </div>
      </div>

      {/* Authoritative Data Lineage Table Card */}
      <div
        className="card"
        style={{
          marginTop: 24,
          padding: 20,
          borderRadius: 12,
          border: '1px solid var(--border)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
          <Database size={18} strokeWidth={1.5} color="var(--primary)" />
          <h3 className="section-title" style={{ fontSize: 16 }}>
            Authoritative Geospatial Datasets
          </h3>
        </div>

        <div className="table-wrapper">
          <table className="apple-table" aria-label="Authoritative Data Sources">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Provider</th>
                <th>Resolution</th>
                <th>Application</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ fontWeight: 600 }}>Copernicus DEM GLO-30</td>
                <td>European Space Agency (ESA)</td>
                <td className="tabular-nums">30 m</td>
                <td>Terrain elevation and slope filtering (≤5.0%)</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>ESA WorldCover 2021 v200</td>
                <td>ESA / VITO</td>
                <td className="tabular-nums">10 m</td>
                <td>Land-cover classification and ecological buffer exclusion</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>WDPA Protected Planet</td>
                <td>UNEP-WCMC / IUCN</td>
                <td>Vector Polygons</td>
                <td>10 km reserve setback gating</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>OpenStreetMap Infrastructure</td>
                <td>OSM Contributors / Overpass API</td>
                <td>Vector Nodes & Corridors</td>
                <td>≥33 kV electrical transmission lines and substations</td>
              </tr>
              <tr>
                <td style={{ fontWeight: 600 }}>NASA POWER Climatology</td>
                <td>NASA Langley Research Center</td>
                <td className="tabular-nums">1.0° × 1.0°</td>
                <td>20-year all-sky solar insolation (GHI) and 2m ambient temperature</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
