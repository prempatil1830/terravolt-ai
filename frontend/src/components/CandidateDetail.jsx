import React, { useState, useEffect } from 'react';

export default function CandidateDetail({
  candidate,
  allCandidates,
  onBack,
  onSelectCandidate,
}) {
  const [provenanceRecords, setProvenanceRecords] = useState([]);
  const [provenanceLoading, setProvenanceLoading] = useState(false);

  useEffect(() => {
    if (!candidate || !candidate.rank) return;
    setProvenanceLoading(true);
    fetch(`/api/candidates/${candidate.rank}/provenance`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && data.records) {
          setProvenanceRecords(data.records);
        } else {
          setProvenanceRecords([]);
        }
      })
      .catch(() => setProvenanceRecords([]))
      .finally(() => setProvenanceLoading(false));
  }, [candidate]);

  if (!candidate) return null;

  const currentIndex = allCandidates.findIndex((c) => c.rank === candidate.rank);
  const prevCandidate = currentIndex > 0 ? allCandidates[currentIndex - 1] : null;
  const nextCandidate = currentIndex < allCandidates.length - 1 ? allCandidates[currentIndex + 1] : null;

  const raw = candidate.raw_criteria || {};
  const norm = candidate.normalized_criteria || {};
  const weights = candidate.weights || {
    ghi: 0.40,
    substation: 0.40,
    landcost: 0.10,
    temp_derating: 0.10,
  };
  const constraints = candidate.constraints || {};

  const isNeedsReview = candidate.status === 'needs_review' || candidate.needs_review;
  const isExcluded = candidate.status === 'excluded';
  const isEligible = candidate.status === 'eligible';

  // Criteria calculations
  const criteriaData = [
    {
      id: 'ghi',
      name: 'Solar Irradiation (GHI)',
      classType: 'Benefit',
      rawDisplay: raw.ghi_kwh_m2_day != null ? `${raw.ghi_kwh_m2_day.toFixed(4)} kWh/m²/day` : 'No Data',
      rawNote: 'NASA POWER 30-yr climatology',
      normValue: norm.ghi ?? 0,
      weight: weights.ghi ?? 0.40,
      contribution: norm.ghi != null ? (norm.ghi * (weights.ghi ?? 0.40)).toFixed(4) : '0.0000',
      description: 'Primary revenue driver. Normalized between [5.0, 6.0] kWh/m²/day.',
    },
    {
      id: 'substation',
      name: 'Distance to Substation (>=33kV)',
      classType: 'Benefit (Inverted Cost)',
      rawDisplay: raw.substation_dist_km != null ? `${raw.substation_dist_km.toFixed(2)} km` : 'None within 50 km',
      rawNote: raw.transmission_line_dist_km != null ? `Nearest Tx line: ${raw.transmission_line_dist_km.toFixed(2)} km` : 'No transmission line mapped',
      normValue: norm.substation ?? 0,
      weight: weights.substation ?? 0.40,
      contribution: norm.substation != null ? (norm.substation * (weights.substation ?? 0.40)).toFixed(4) : '0.0000',
      description: 'Grid evacuation CAPEX. Requires >=33kV qualifying substation. Inverted [0, 50] km.',
    },
    {
      id: 'landcost',
      name: 'Land-Cost Proxy',
      classType: 'Cost (Inverted)',
      rawDisplay: raw.landcost_proxy_score != null ? `${raw.landcost_proxy_score.toFixed(4)}` : 'No Data',
      rawNote: 'Distance-to-town bid-rent proxy score [0, 1]',
      normValue: norm.landcost ?? 0,
      weight: weights.landcost ?? 0.10,
      contribution: norm.landcost != null ? (norm.landcost * (weights.landcost ?? 0.10)).toFixed(4) : '0.0000',
      description: 'Coarse proxy based on distance to nearest district town (capped <=10% weight).',
    },
    {
      id: 'temp_derating',
      name: 'Temperature Derating',
      classType: 'Cost (Inverted)',
      rawDisplay: raw.t2m_celsius != null ? `${raw.t2m_celsius.toFixed(2)} °C` : 'No Data',
      rawNote: raw.computed_derating_factor != null ? `Est. Cell Derating Factor: ${raw.computed_derating_factor.toFixed(4)}` : '',
      normValue: norm.temp_derating ?? 0,
      weight: weights.temp_derating ?? 0.10,
      contribution: norm.temp_derating != null ? (norm.temp_derating * (weights.temp_derating ?? 0.10)).toFixed(4) : '0.0000',
      description: 'Panel efficiency losses (-0.4%/°C above 25°C cell temp). Inverted [20, 30] °C.',
    },
  ];

  return (
    <div className="detail-view-container">
      {/* ── Top Bar & Navigation ── */}
      <div className="detail-top-nav">
        <button className="back-btn" onClick={onBack}>
          ← Back to Dashboard
        </button>

        <div className="candidate-switcher">
          <button
            className="switch-btn"
            disabled={!prevCandidate}
            onClick={() => prevCandidate && onSelectCandidate(prevCandidate.rank)}
          >
            ← Prev (#{prevCandidate?.rank || '—'})
          </button>
          <span className="switcher-current">
            Site #{candidate.rank} of {allCandidates.length}
          </span>
          <button
            className="switch-btn"
            disabled={!nextCandidate}
            onClick={() => nextCandidate && onSelectCandidate(nextCandidate.rank)}
          >
            Next (#{nextCandidate?.rank || '—'}) →
          </button>
        </div>
      </div>

      {/* ── Candidate Overview Banner ── */}
      <div className="detail-overview-card">
        <div className="overview-header">
          <div>
            <div className="overview-title-group">
              <h2>Candidate Site #{candidate.rank}</h2>
              <span className={`badge badge-${candidate.status}`}>
                {candidate.status.toUpperCase()}
              </span>
            </div>
            <p className="overview-coords">
              Centroid: <strong>{candidate.latitude.toFixed(4)}°N</strong>, <strong>{candidate.longitude.toFixed(4)}°E</strong>
              {candidate.region && (
                <span className="overview-region"> · Spatial Bin: Row {candidate.region[0]}, Col {candidate.region[1]}</span>
              )}
            </p>
          </div>

          <div className="overview-score-box">
            <div className="score-box-label">Composite AHP Score</div>
            {candidate.score != null ? (
              <>
                <div className="score-box-value">{(candidate.score * 100).toFixed(2)} <span className="score-denom">/ 100</span></div>
                <div className="score-box-sub">Normalized: {candidate.score.toFixed(4)}</div>
              </>
            ) : (
              <div className="score-box-value text-red">EXCLUDED</div>
            )}
          </div>
        </div>
      </div>

      {/* ── Visible Status Banner ── */}
      {isNeedsReview && (
        <div className="alert-banner banner-warning">
          <div className="banner-icon">[REVIEW]</div>
          <div className="banner-content">
            <h4>REVIEW REQUIRED: Field Verification Recommended</h4>
            <p>
              This candidate passed baseline screening but requires human engineering review:
            </p>
            <div className="banner-reasons-list">
              <strong>Review Reasons:</strong>
              <ul>
                {(candidate.needs_review_reasons || candidate.reasons || []).map((reason, idx) => (
                  <li key={idx}>{reason}</li>
                ))}
              </ul>
            </div>
            <div className="banner-footer-note">
              * Commercial Status: <strong>NOT CLEARED</strong>. Offline GIS shapefile validation with KSRSAC water atlas is required before site acquisition filings.
            </div>
          </div>
        </div>
      )}

      {isExcluded && (
        <div className="alert-banner banner-danger">
          <div className="banner-icon">❌</div>
          <div className="banner-content">
            <h4>SITE EXCLUDED BY HARD CONSTRAINT</h4>
            <p>
              This candidate was screened out during Phase 2 evaluation because it violates the 
              <strong> 10 km Protected Area / 500 m Water-Body Buffer constraint</strong>.
            </p>
            <p className="banner-subnote">
              No AHP score is computed for excluded coordinates per MCDM pre-filter rules.
            </p>
          </div>
        </div>
      )}

      {isEligible && (
        <div className="alert-banner banner-success">
          <div className="banner-icon">✅</div>
          <div className="banner-content">
            <h4>PRE-FEASIBILITY ELIGIBLE & CLEARED</h4>
            <p>
              All three non-negotiable hard constraints (Terrain Slope $\le 5\%$, ESA Land-Cover clearance, and 10 km Protected Area Setback) have been successfully validated. Grid connection and solar resource are confirmed.
            </p>
          </div>
        </div>
      )}

      {/* ── Section 1: Hard Constraints Pass / Fail ── */}
      <div className="section-block">
        <div className="section-header">
          <h3>1. Non-Negotiable Hard Constraints (Pre-Filters)</h3>
          <span className="section-badge">Boolean Gating</span>
        </div>

        <div className="constraints-grid">
          {/* Slope Card */}
          <div className={`constraint-card ${constraints.slope?.tripped ? 'card-tripped' : 'card-passed'}`}>
            <div className="constraint-top">
              <span className="constraint-name">Terrain Slope</span>
              <span className={`status-pill ${constraints.slope?.tripped ? 'pill-fail' : 'pill-pass'}`}>
                {constraints.slope?.tripped ? 'FAIL' : 'PASS'}
              </span>
            </div>
            <div className="constraint-metric">
              {constraints.slope?.f_slope_usable != null
                ? `${(constraints.slope.f_slope_usable * 100).toFixed(1)}% usable`
                : constraints.slope?.value_pct != null
                  ? `${constraints.slope.value_pct.toFixed(2)}%`
                  : '—'}
            </div>
            <div className="constraint-threshold">
              3x3 mean-smoothed Copernicus DEM; at least 80% of the 0.54 km footprint at or under 5% slope
            </div>
            <div className="constraint-note">
              Gentle slope minimizes racking civil expenditure and inter-row shading losses.
            </div>
          </div>

          {/* Land-Use Card */}
          <div className={`constraint-card ${constraints.landuse?.tripped ? 'card-tripped' : 'card-passed'}`}>
            <div className="constraint-top">
              <span className="constraint-name">Land-Cover Suitability</span>
              <span className={`status-pill ${constraints.landuse?.tripped ? 'pill-fail' : 'pill-pass'}`}>
                {constraints.landuse?.tripped ? 'FAIL' : 'PASS'}
              </span>
            </div>
            <div className="constraint-metric">
              {constraints.landuse?.f_lu_usable != null
                ? `${(constraints.landuse.f_lu_usable * 100).toFixed(1)}% usable`
                : constraints.landuse?.class_name || 'Eligible Class'}
            </div>
            <div className="constraint-threshold">
              ESA WorldCover 2021; at least 80% of the 0.54 km footprint in eligible land-use classes (Centroid: {constraints.landuse?.centroid_class || constraints.landuse?.class_name || 'Eligible'})
            </div>
            <div className="constraint-note">
              Forest (10), Urban (50), Water (80), Wetlands (90), and Mangroves (95) are hard-excluded.
            </div>
          </div>

          {/* Protected Buffer Card */}
          <div className={`constraint-card ${isExcluded ? 'card-tripped' : isNeedsReview ? 'card-review' : 'card-passed'}`}>
            <div className="constraint-top">
              <span className="constraint-name">Protected Area & Water Buffer</span>
              <span className={`status-pill ${isExcluded ? 'pill-fail' : isNeedsReview ? 'pill-review' : 'pill-pass'}`}>
                {isExcluded ? 'FAIL' : isNeedsReview ? 'NEEDS REVIEW' : 'PASS'}
              </span>
            </div>
            <div className="constraint-metric">
              {isExcluded ? 'Inside Buffer' : isNeedsReview ? 'Unconfirmed' : 'Outside Buffer'}
            </div>
            <div className="constraint-threshold">
              Rule: 10 km PA (WDPA) & 500 m Water Setback
            </div>
            <div className="constraint-note">
              Protects ecologically sensitive zones and complies with river setback regulations.
            </div>
          </div>
        </div>
      </div>

      {/* ── Section 2: Evaluated Criteria Breakdown ── */}
      <div className="section-block">
        <div className="section-header">
          <h3>2. Evaluated Criteria Breakdown & AHP Scoring</h3>
          <span className="section-badge">AHP Weight Sum = 100%</span>
        </div>

        <div className="criteria-table-card">
          <table className="detail-table">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Type</th>
                <th>Raw Measured Value</th>
                <th>Normalized [0–1]</th>
                <th>AHP Weight</th>
                <th>Score Contribution</th>
              </tr>
            </thead>
            <tbody>
              {criteriaData.map((crit) => (
                <tr key={crit.id}>
                  <td>
                    <div className="crit-name">{crit.name}</div>
                    <div className="crit-desc">{crit.description}</div>
                  </td>
                  <td>
                    <span className="crit-badge">{crit.classType}</span>
                  </td>
                  <td>
                    <div className="crit-raw">{crit.rawDisplay}</div>
                    {crit.rawNote && <div className="crit-note">{crit.rawNote}</div>}
                  </td>
                  <td>
                    <div className="norm-score-wrap">
                      <span>{(crit.normValue).toFixed(4)}</span>
                      <div className="mini-bar">
                        <div
                          className="mini-bar-fill"
                          style={{ width: `${crit.normValue * 100}%` }}
                        />
                      </div>
                    </div>
                  </td>
                  <td className="text-center font-bold">
                    {(crit.weight * 100).toFixed(0)}%
                  </td>
                  <td className="text-right font-bold text-accent">
                    {crit.contribution}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={4} className="font-bold text-right">
                  Total Composite Suitability Score:
                </td>
                <td className="text-center font-bold">100%</td>
                <td className="text-right font-bold text-gold score-total-cell">
                  {candidate.score != null ? `${candidate.score.toFixed(4)} (${(candidate.score * 100).toFixed(2)} / 100)` : 'EXCLUDED'}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      {/* ── Section 3: Substation Facts & Grid Interconnection ── */}
      <div className="section-block">
        <div className="section-header">
          <h3>3. Primary Substation Facts & Grid Interconnection</h3>
          <span className="section-badge">Data Only (No Hallucinations)</span>
        </div>

        <div className="technical-notes-grid">
          <div className="tech-card">
            <h4>Primary Qualifying Substation (≥33 kV)</h4>
            <div className="substation-facts-table" style={{ marginTop: '0.75rem', fontSize: '0.9rem' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', rowGap: '6px' }}>
                <div><strong>OSM ID:</strong></div>
                <div><code>{candidate.substation_details?.osm_id || raw.substation_osm_id || 'unnamed'}</code></div>
                
                <div><strong>Name Tag:</strong></div>
                <div>{candidate.substation_details?.name || raw.substation_name || 'unnamed'}</div>
                
                <div><strong>Operator Tag:</strong></div>
                <div>{candidate.substation_details?.operator || raw.substation_operator || 'unnamed'}</div>
                
                <div><strong>Voltage Tag:</strong></div>
                <div>{candidate.substation_details?.voltage_tag || raw.substation_voltage_tag || 'no voltage tag'}</div>
                
                <div><strong>Distance:</strong></div>
                <div><strong>{raw.substation_dist_km != null ? `${raw.substation_dist_km} km` : 'None mapped'}</strong></div>

                {raw.transmission_line_dist_km != null && (
                  <>
                    <div><strong>Nearest Tx Line:</strong></div>
                    <div>{raw.transmission_line_dist_km} km (OSM ID: <code>{raw.transmission_line_osm_id || '—'}</code>)</div>
                  </>
                )}
              </div>
            </div>
            <p className="tech-hint" style={{ marginTop: '0.75rem' }}>
              Verified transmission kind substation excluding captive, electrode, traction, and generation plants.
            </p>
          </div>

          {(candidate.substation_details?.closer_untagged || raw.closer_untagged_osm_id) && (
            <div className="tech-card card-review" style={{ border: '1px solid rgba(245, 158, 11, 0.4)' }}>
              <h4>Closer Untagged Structure (Review Flag)</h4>
              {(() => {
                const closer = candidate.substation_details?.closer_untagged || {
                  osm_id: raw.closer_untagged_osm_id,
                  lat: raw.closer_untagged_lat,
                  lon: raw.closer_untagged_lon,
                  distance_km: raw.closer_untagged_dist_km,
                  tags: raw.closer_untagged_tags,
                };
                return (
                  <div style={{ marginTop: '0.75rem', fontSize: '0.88rem' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', rowGap: '5px' }}>
                      <div><strong>OSM ID:</strong></div>
                      <div><code>{closer.osm_id}</code></div>
                      <div><strong>Location:</strong></div>
                      <div>{closer.lat?.toFixed(5)}°N, {closer.lon?.toFixed(5)}°E</div>
                      <div><strong>Distance:</strong></div>
                      <div><strong>{closer.distance_km?.toFixed(2)} km</strong> (closer than primary)</div>
                      <div><strong>Tags:</strong></div>
                      <div><code>{JSON.stringify(closer.tags || {})}</code></div>
                    </div>
                    <p style={{ marginTop: '0.6rem', color: 'var(--color-amber-400)', fontSize: '0.82rem' }}>
                      Unverified voltage tags. Elevated to needs_review for KPTCL SLD field verification.
                    </p>
                  </div>
                );
              })()}
            </div>
          )}

          <div className="tech-card">
            <h4>Thermal Derating & Generation Yield</h4>
            <p>
              Mean annual ambient temperature is <strong>{raw.t2m_celsius != null ? `${raw.t2m_celsius}°C` : '—'}</strong>.
              Estimated cell operating temperature is ~{raw.t2m_celsius != null ? (raw.t2m_celsius + 28).toFixed(1) : '—'}°C under full insolation,
              yielding a cell derating factor of <strong>{raw.computed_derating_factor != null ? raw.computed_derating_factor.toFixed(4) : '—'}</strong>.
            </p>
            <p className="tech-hint">
              Crystalline silicon panels derate at ~0.4% per °C above Standard Test Condition (25°C).
            </p>
          </div>
        </div>
      </div>

      {/* ── Section 4: Data Sources & Lineage ── */}
      <div className="section-block">
        <div className="section-header">
          <h3>4. Authoritative Data Sources & Lineage</h3>
          <span className="section-badge">Audited Provenance</span>
        </div>

        <div className="technical-notes-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
          <div className="tech-card">
            <h4>Solar & Temperature</h4>
            <p><strong>NASA POWER Climatology:</strong> 20-year climatology (2001–2020) on a 1 x 1 degree grid (about 110 km) for GHI, and 0.5 x 0.625 degree for temperature.</p>
          </div>
          <div className="tech-card">
            <h4>Grid & Substation Infrastructure</h4>
            <p><strong>OpenStreetMap (OSM):</strong> Overpass API grid tiles filtered at ≥33 kV transmission substations and line corridors.</p>
          </div>
          <div className="tech-card">
            <h4>Topography & Land Cover</h4>
            <p><strong>Copernicus DEM GLO-30:</strong> 30 m smoothed slope.<br /><strong>ESA WorldCover 2021:</strong> 10 m land cover raster over 0.54 km disk.</p>
          </div>
          <div className="tech-card">
            <h4>Conservation & Buffers</h4>
            <p><strong>UNEP-WCMC WDPA:</strong> 10 km statutory buffer.<br /><strong>OSM Waterbodies:</strong> Local water layer with 500 m buffer check.</p>
          </div>
        </div>

        {/* Database Provenance Records Table */}
        <div style={{ marginTop: '1.5rem' }}>
          <h4 style={{ marginBottom: '0.75rem', fontSize: '0.95rem', color: 'var(--color-slate-200)' }}>
            Audited Lineage Matrix Records (Database Provenance)
          </h4>
          {provenanceLoading ? (
            <p className="tech-hint">Loading provenance records from MongoDB...</p>
          ) : provenanceRecords.length > 0 ? (
            <div className="table-responsive">
              <table className="criteria-table" style={{ fontSize: '0.82rem' }}>
                <thead>
                  <tr>
                    <th>Criterion / Field</th>
                    <th>Value</th>
                    <th>Data Source / Derivation</th>
                    <th>Retrieved / Calculated At</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {provenanceRecords.map((rec, idx) => (
                    <tr key={idx}>
                      <td><code>{rec.criterion}</code></td>
                      <td><strong>{rec.value != null ? String(rec.value) : '—'}</strong></td>
                      <td>{rec.source}</td>
                      <td><code style={{ fontSize: '0.75rem' }}>{rec.retrieved_at ? new Date(rec.retrieved_at).toLocaleString() : '—'}</code></td>
                      <td>
                        <span className={`badge badge-${rec.status}`} style={{ fontSize: '0.7rem', padding: '2px 6px' }}>
                          {rec.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="tech-hint">Provenance records available via API: <code>/api/candidates/{candidate.rank}/provenance</code></p>
          )}
        </div>
      </div>
    </div>
  );
}
