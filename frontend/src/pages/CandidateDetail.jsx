import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link, useLocation } from 'react-router-dom';
import { ArrowLeft, Sparkles, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import MapView from '../components/MapView';

function cleanPlainText(text) {
  if (!text) return '';
  return text
    .replace(/\|[-:\s|]+\|/g, ' ')
    .replace(/\|/g, ' ')
    .replace(/#{2,}\s*/g, '')
    .replace(/#(?!\d)/g, '')
    .replace(/\*/g, '')
    .replace(/(?<!\w)_+|_+(?!\w)/g, '')
    .replace(/[-–—]{2,}/g, ' ')
    .replace(/\s*-\s*-\s*-\s*/g, ' ')
    .replace(/^\s*[-•]\s+/gm, '')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

export default function CandidateDetail({ candidates = [] }) {
  const { id } = useParams();
  const location = useLocation();
  const rank = parseInt(id, 10);

  // Look for candidate from location.state first, then sessionStorage (dynamic search), then baseline candidates prop
  const candidate = useMemo(() => {
    if (
      location.state?.candidate &&
      (location.state.candidate.rank === rank ||
        location.state.candidate.site_id === rank ||
        location.state.candidate.id === rank)
    ) {
      return location.state.candidate;
    }

    try {
      const saved = sessionStorage.getItem('last_search_results');
      if (saved) {
        const parsed = JSON.parse(saved);
        const list = Array.isArray(parsed.candidates)
          ? parsed.candidates
          : Array.isArray(parsed.sites)
          ? parsed.sites
          : Array.isArray(parsed)
          ? parsed
          : [];
        const found = list.find((c) => c.rank === rank || c.site_id === rank || c.id === rank);
        if (found) return found;
      }
    } catch (e) {
      // ignore
    }

    return candidates.find((c) => c.rank === rank);
  }, [location.state, rank, candidates]);

  const [provenanceRecords, setProvenanceRecords] = useState([]);
  const [provenanceLoading, setProvenanceLoading] = useState(true);
  const [explanation, setExplanation] = useState(null);
  const [explanationLoading, setExplanationLoading] = useState(true);
  const [isExpanded, setIsExpanded] = useState(false);

  useEffect(() => {
    let ignore = false;
    async function loadProvenance() {
      if (!rank) return;
      try {
        setProvenanceLoading(true);
        const res = await fetch(`/api/candidates/${rank}/provenance`);
        if (res.ok) {
          const data = await res.json();
          if (!ignore) {
            setProvenanceRecords(data.records || []);
          }
        }
      } catch (err) {
        console.error('Failed to load provenance:', err);
      } finally {
        if (!ignore) setProvenanceLoading(false);
      }
    }

    async function loadExplanation() {
      if (!rank && !candidate) return;
      try {
        setExplanationLoading(true);
        let res;

        // If active candidate object exists, POST it so that dynamic search criteria,
        // modified weights, and needs_review status are accurately explained
        if (candidate) {
          res = await fetch('/api/candidates/explain', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(candidate),
          });
        }

        // Fallback to GET by rank
        if (!res || !res.ok) {
          res = await fetch(`/api/candidates/${rank}/explain`);
        }

        if (res && res.ok) {
          const data = await res.json();
          if (!ignore) {
            setExplanation(data);
          }
        }
      } catch (err) {
        console.error('Failed to load explanation:', err);
      } finally {
        if (!ignore) setExplanationLoading(false);
      }
    }

    loadProvenance();
    loadExplanation();
    return () => {
      ignore = true;
    };
  }, [rank, candidate]);

  if (!candidate && candidates.length > 0) {
    return (
      <div className="content-wrapper page-fade-in" style={{ paddingTop: 36 }}>
        <Link to="/results" className="back-link">
          <ArrowLeft size={15} strokeWidth={1.5} />
          <span>Back to Results</span>
        </Link>
        <h1 className="page-title">Candidate #{id} Not Found</h1>
        <p className="page-subtitle">The requested candidate site does not exist in the dataset.</p>
      </div>
    );
  }

  if (!candidate) {
    return (
      <div className="content-wrapper page-fade-in" style={{ paddingTop: 36, textAlign: 'center' }}>
        <p className="caption-text">Loading candidate details...</p>
      </div>
    );
  }

  const isNeedsReview = candidate.status === 'needs_review' || candidate.needs_review;

  const raw = candidate.raw_criteria || {};
  const norm = candidate.normalized_criteria || {};
  const weights = candidate.weights || {
    ghi: 0.40,
    substation: 0.40,
    landcost: 0.10,
    temp_derating: 0.10,
  };
  const constraints = candidate.constraints || {};

  const criteriaRows = [
    {
      id: 'ghi',
      label: 'Solar Resource (GHI)',
      raw: raw.ghi_kwh_m2_day != null ? `${raw.ghi_kwh_m2_day.toFixed(2)} kWh/m²/day` : '—',
      normalized: norm.ghi != null ? norm.ghi.toFixed(4) : '—',
      weight: `${Math.round((weights.ghi || 0.4) * 100)}%`,
      contribution: norm.ghi != null ? (norm.ghi * (weights.ghi || 0.4)).toFixed(4) : null,
      barWidthPct: norm.ghi != null ? Math.min(100, Math.round(norm.ghi * 100)) : 0,
    },
    {
      id: 'substation',
      label: 'Substation Proximity (≥33 kV)',
      raw: raw.substation_dist_km != null ? `${raw.substation_dist_km.toFixed(1)} km` : 'No data',
      normalized: norm.substation != null ? norm.substation.toFixed(4) : '—',
      weight: `${Math.round((weights.substation || 0.4) * 100)}%`,
      contribution: norm.substation != null ? (norm.substation * (weights.substation || 0.4)).toFixed(4) : null,
      barWidthPct: norm.substation != null ? Math.min(100, Math.round(norm.substation * 100)) : 0,
    },
    {
      id: 'landcost',
      label: 'Land Cost Proxy',
      raw: raw.landcost_proxy_score != null ? raw.landcost_proxy_score.toFixed(3) : '—',
      normalized: norm.landcost != null ? norm.landcost.toFixed(4) : '—',
      weight: `${Math.round((weights.landcost || 0.1) * 100)}%`,
      contribution: norm.landcost != null ? (norm.landcost * (weights.landcost || 0.1)).toFixed(4) : null,
      barWidthPct: norm.landcost != null ? Math.min(100, Math.round(norm.landcost * 100)) : 0,
    },
    {
      id: 'temp_derating',
      label: 'Temperature Derating (T2m)',
      raw: raw.t2m_celsius != null ? `${raw.t2m_celsius.toFixed(1)} °C` : '—',
      normalized: norm.temp_derating != null ? norm.temp_derating.toFixed(4) : '—',
      weight: `${Math.round((weights.temp_derating || 0.1) * 100)}%`,
      contribution: norm.temp_derating != null ? (norm.temp_derating * (weights.temp_derating || 0.1)).toFixed(4) : null,
      barWidthPct: norm.temp_derating != null ? Math.min(100, Math.round(norm.temp_derating * 100)) : 0,
    },
  ];

  const pbStatus = constraints.protected_buffer?.status;
  const pbTripped = constraints.protected_buffer?.tripped;

  return (
    <div className="content-wrapper page-fade-in" style={{ paddingBottom: 64 }}>
      <Link to="/results" className="back-link">
        <ArrowLeft size={15} strokeWidth={1.5} />
        <span>Back to Candidates</span>
      </Link>

      {/* Header and Hero Score */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 24 }}>
        <div>
          <h1 className="page-title">Candidate #{candidate.rank}</h1>
          <p className="page-subtitle tabular-nums">
            Centroid: {candidate.latitude != null ? `${candidate.latitude.toFixed(4)}°N` : '—'}
            {candidate.longitude != null ? `, ${candidate.longitude.toFixed(4)}°E` : ''}
            {candidate.district && ` · District: ${candidate.district}`}
          </p>

          {isNeedsReview && !candidate.reviewer_decision && (
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
                marginTop: 10,
                padding: '4px 10px',
                borderRadius: 9999,
                background: 'var(--surface-2)',
                border: '1px solid var(--border)',
                fontSize: 12.5,
              }}
            >
              <span className="status-dot status-dot-review" />
              <span>
                {candidate.needs_review_reasons && candidate.needs_review_reasons.length > 0
                  ? candidate.needs_review_reasons[0]
                  : 'Requires manual verification.'}
              </span>
            </div>
          )}
        </div>

        {/* Hero Score (Source Serif 4 weight 600) */}
        <div style={{ textAlign: 'right' }}>
          {candidate.score != null ? (
            <div>
              <div className="hero-score tabular-nums" style={{ color: 'var(--primary)' }}>
                {(candidate.score * 100).toFixed(1)}
              </div>
              <div className="hero-score-label">Suitability Score / 100</div>
            </div>
          ) : (
            <div>
              <div className="hero-score" style={{ color: 'var(--excluded)' }}>
                Excluded
              </div>
              <div className="hero-score-label">Filtered by Constraint</div>
            </div>
          )}
        </div>
      </div>

      {/* Reviewer Decision if present */}
      {candidate.reviewer_decision && (
        <div
          className="card"
          style={{
            marginTop: 20,
            borderLeft: `3px solid ${
              candidate.reviewer_decision.decision === 'cleared'
                ? 'var(--eligible)'
                : 'var(--excluded)'
            }`,
            background: 'var(--surface)',
            padding: '14px 18px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                className={`status-dot ${
                  candidate.reviewer_decision.decision === 'cleared'
                    ? 'status-dot-eligible'
                    : 'status-dot-excluded'
                }`}
              />
              <span style={{ fontWeight: 600, fontSize: 14 }}>
                Reviewer Decision: {candidate.reviewer_decision.decision === 'cleared' ? 'Cleared' : 'Excluded'}
              </span>
            </div>
            <span className="caption-text tabular-nums">
              {candidate.reviewer_decision.date}
            </span>
          </div>
          <p style={{ margin: '4px 0 0 0', fontSize: 13.5, color: 'var(--text)', lineHeight: 1.5 }}>
            {candidate.reviewer_decision.note}
          </p>
          <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--text-2)' }}>
            Verified by: {candidate.reviewer_decision.reviewer || 'lead_operator'}
          </div>
        </div>
      )}

      {/* LLM Explanation of Ranking and Needs Review */}
      <div
        className="card"
        style={{
          marginTop: 24,
          padding: 20,
          borderRadius: 12,
          border: '1px solid var(--border)',
          backgroundColor: 'var(--surface)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <Sparkles size={18} color="var(--primary)" />
          <h2 className="section-title" style={{ margin: 0, fontSize: 16 }}>
            Multi-Criteria Ranking & Review Rationale
          </h2>
        </div>

        {explanationLoading ? (
          <p className="caption-text" style={{ fontSize: 13 }}>
            Generating comprehensive multi-criteria ranking explanation...
          </p>
        ) : (() => {
          const isNeedsReview =
            candidate.status === 'needs_review' ||
            candidate.needs_review ||
            explanation?.is_needs_review;

          const reviewReason =
            explanation?.needs_review_reason ||
            (candidate.needs_review_reasons?.length
              ? `Why this site is marked under Needs Review: ${candidate.needs_review_reasons.join('; ')}. In utility-scale solar development (50 MW or larger), a plant requires high-voltage interconnection of at least 33,000 volts (33 kV) to evacuate generated power into the grid. Because community map records do not confirm this substation's voltage tier or an untagged substation is closer, an electrical engineer must consult KPTCL single-line diagrams to confirm operating voltage and bay capacity before financial commitment.`
              : 'Why this site is marked under Needs Review: Substation operating voltage not confirmed in OpenStreetMap or untagged substation closer than 33kV. Requires KPTCL single-line diagram verification to confirm operating voltage and bay capacity before financial commitment.');

          const rawFullText = cleanPlainText(
            explanation?.ranking_position_reason || explanation?.explanation || ''
          );
          const allParagraphs = rawFullText.split('\n\n').filter(Boolean);

          const briefText = explanation?.brief_explanation
            ? cleanPlainText(explanation.brief_explanation)
            : allParagraphs[0] || '';

          const detailedParagraphs = explanation?.detailed_explanation
            ? cleanPlainText(explanation.detailed_explanation).split('\n\n').filter(Boolean)
            : allParagraphs.slice(1);

          return (
            <div>
              {/* Explicit Needs Review Explanation Banner if site requires review */}
              {isNeedsReview && (
                <div
                  style={{
                    backgroundColor: 'rgba(199, 125, 10, 0.08)',
                    border: '1px solid rgba(199, 125, 10, 0.3)',
                    borderLeft: '4px solid var(--review)',
                    borderRadius: 8,
                    padding: '14px 18px',
                    marginBottom: 16,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      color: 'var(--review)',
                      fontWeight: 700,
                      fontSize: 14,
                    }}
                  >
                    <AlertTriangle size={18} />
                    <span>Why This Site Is Marked Under Needs Review</span>
                  </div>
                  <p
                    style={{
                      margin: '8px 0 0',
                      fontSize: 13.5,
                      lineHeight: 1.6,
                      color: 'var(--text)',
                    }}
                  >
                    {reviewReason}
                  </p>
                </div>
              )}

              {/* Main Analysis Card */}
              <div
                style={{
                  background: 'var(--surface-2)',
                  borderRadius: 8,
                  padding: '16px 18px',
                  border: '1px solid var(--border)',
                }}
              >
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 700,
                    color: 'var(--primary)',
                    marginBottom: 10,
                  }}
                >
                  Candidate #{candidate.rank} Evaluation & Comparative Analysis
                </div>

                {/* Brief 3-4 Line Summary */}
                <div style={{ margin: 0, fontSize: 13.5, lineHeight: 1.6, color: 'var(--text)' }}>
                  <p style={{ margin: 0 }}>{briefText}</p>
                </div>

                {/* Detailed Analysis (shown when expanded) */}
                {isExpanded && detailedParagraphs.length > 0 && (
                  <div
                    style={{
                      marginTop: 14,
                      paddingTop: 14,
                      borderTop: '1px dashed var(--border)',
                    }}
                  >
                    {detailedParagraphs.map((p, idx) => (
                      <p
                        key={idx}
                        style={{
                          margin: idx === 0 ? 0 : '12px 0 0',
                          fontSize: 13.5,
                          lineHeight: 1.6,
                          color: 'var(--text)',
                        }}
                      >
                        {p}
                      </p>
                    ))}
                  </div>
                )}

                {/* Read More / Show Less Toggle Button */}
                {detailedParagraphs.length > 0 && (
                  <div style={{ marginTop: 12 }}>
                    <button
                      type="button"
                      onClick={() => setIsExpanded(!isExpanded)}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: 'var(--primary)',
                        fontWeight: 600,
                        fontSize: 13,
                        cursor: 'pointer',
                        padding: '4px 0',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 6,
                      }}
                    >
                      <span>
                        {isExpanded
                          ? 'Show less ↑'
                          : 'Read more: full comparative analysis ↓'}
                      </span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })()}
      </div>

      {/* Two-Column Grid: Location Map Card & Constraint Checks Card */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
          gap: 24,
          marginTop: 28,
        }}
      >
        {/* Karnataka-only map showing candidate pin */}
        <div
          className="card"
          style={{
            padding: 0,
            overflow: 'hidden',
            borderRadius: 12,
            border: '1px solid var(--border)',
          }}
        >
          <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
            <h3 className="section-title" style={{ fontSize: 15 }}>
              Candidate Location in Karnataka
            </h3>
            <p className="caption-text">
              {candidate.latitude != null && candidate.longitude != null
                ? `${candidate.latitude.toFixed(4)}°N, ${candidate.longitude.toFixed(4)}°E`
                : 'Coordinates pending'}
            </p>
          </div>
          <MapView
            candidates={[candidate]}
            selectedId={candidate.rank}
            height={340}
          />
        </div>

        {/* Constraint Checks Card */}
        <div
          className="card"
          style={{
            padding: 20,
            borderRadius: 12,
            border: '1px solid var(--border)',
          }}
        >
          <h3 className="section-title" style={{ fontSize: 16 }}>
            Constraint Gating Checks
          </h3>
          <p className="caption-text" style={{ marginTop: 2 }}>
            Boolean pre-feasibility thresholds evaluated over candidate footprint disk.
          </p>

          <div className="constraint-list" style={{ marginTop: 16 }}>
            {/* Slope */}
            <div className="constraint-item">
              <div className="constraint-left">
                <span className={`status-dot ${constraints.slope?.tripped ? 'status-dot-excluded' : 'status-dot-eligible'}`} />
                <div>
                  <div className="constraint-title">Terrain Slope</div>
                  <div className="constraint-desc">Copernicus DEM GLO-30 (≤5% slope over ≥80% footprint)</div>
                </div>
              </div>
              <div className="constraint-right">
                {constraints.slope?.f_slope_usable != null
                  ? `${(constraints.slope.f_slope_usable * 100).toFixed(1)}% usable`
                  : constraints.slope?.value_pct != null
                  ? `${constraints.slope.value_pct.toFixed(2)}%`
                  : 'Passed'}
              </div>
            </div>

            {/* Land cover */}
            <div className="constraint-item">
              <div className="constraint-left">
                <span className={`status-dot ${constraints.landuse?.tripped ? 'status-dot-excluded' : 'status-dot-eligible'}`} />
                <div>
                  <div className="constraint-title">Land-Cover Suitability</div>
                  <div className="constraint-desc">ESA WorldCover 2021 (no forest, water, or urban)</div>
                </div>
              </div>
              <div className="constraint-right">
                {constraints.landuse?.f_lu_usable != null
                  ? `${(constraints.landuse.f_lu_usable * 100).toFixed(1)}% usable`
                  : 'Eligible'}
              </div>
            </div>

            {/* Protected buffer */}
            <div className="constraint-item">
              <div className="constraint-left">
                <span className={`status-dot ${pbTripped || pbStatus === 'excluded' ? 'status-dot-excluded' : pbStatus === 'needs_review' ? 'status-dot-review' : 'status-dot-eligible'}`} />
                <div>
                  <div className="constraint-title">Reserve & Water Buffers</div>
                  <div className="constraint-desc">10 km WDPA buffer & 500 m waterbody setback</div>
                </div>
              </div>
              <div className="constraint-right">
                {pbStatus ? pbStatus.replace('_', ' ') : 'Passed'}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Multi-Criteria Score Breakdown Card */}
      <div
        className="card"
        style={{
          marginTop: 24,
          padding: 20,
          borderRadius: 12,
          border: '1px solid var(--border)',
        }}
      >
        <h3 className="section-title" style={{ fontSize: 16 }}>
          Multi-Criteria Scoring Breakdown
        </h3>
        <p className="caption-text" style={{ marginTop: 2 }}>
          Analytical Hierarchy Process (AHP) weighted contributions to composite suitability score.
        </p>

        <div className="table-wrapper" style={{ marginTop: 16 }}>
          <table className="apple-table" aria-label="Criteria Breakdown Table">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Raw Value</th>
                <th className="th-numeric">Normalized</th>
                <th className="th-numeric">Weight</th>
                <th className="th-numeric">Contribution</th>
                <th style={{ width: 140, textAlign: 'right' }}>Score Bar</th>
              </tr>
            </thead>
            <tbody>
              {criteriaRows.map((row) => (
                <tr key={row.id}>
                  <td style={{ fontWeight: 600 }}>{row.label}</td>
                  <td className="tabular-nums">{row.raw}</td>
                  <td className="th-numeric tabular-nums">{row.normalized}</td>
                  <td className="th-numeric tabular-nums">{row.weight}</td>
                  <td className="th-numeric tabular-nums" style={{ fontWeight: 600 }}>
                    {row.contribution != null ? row.contribution : '—'}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'inline-flex', alignItems: 'center', width: 110, height: 6, background: 'var(--surface-2)', borderRadius: 9999, overflow: 'hidden', border: '1px solid var(--border)' }}>
                      <div
                        style={{
                          height: '100%',
                          width: `${row.barWidthPct}%`,
                          backgroundColor: 'var(--accent)',
                          borderRadius: 9999,
                        }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Provenance Audit Records Card */}
      <div
        className="card"
        style={{
          marginTop: 24,
          padding: 20,
          borderRadius: 12,
          border: '1px solid var(--border)',
        }}
      >
        <h3 className="section-title" style={{ fontSize: 16 }}>
          Data Lineage & Provenance
        </h3>
        <p className="caption-text" style={{ marginTop: 2 }}>
          Audited source data timestamps and ingestion integrity from MongoDB provenance collection.
        </p>

        <div className="table-wrapper" style={{ marginTop: 16 }}>
          {provenanceLoading ? (
            <p className="caption-text" style={{ padding: '12px 0' }}>Loading provenance records...</p>
          ) : provenanceRecords.length === 0 ? (
            <p className="caption-text" style={{ padding: '12px 0' }}>No provenance records available for this candidate.</p>
          ) : (
            <table className="apple-table" aria-label="Provenance Table">
              <thead>
                <tr>
                  <th>Criterion</th>
                  <th>Value</th>
                  <th>Source</th>
                  <th>Timestamp</th>
                  <th style={{ textAlign: 'right' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {provenanceRecords.map((rec) => (
                  <tr key={rec.criterion}>
                    <td style={{ fontWeight: 500 }}>{rec.criterion}</td>
                    <td className="tabular-nums">{String(rec.value ?? '—')} {rec.unit || ''}</td>
                    <td style={{ color: 'var(--text-2)', fontSize: 12.5 }}>{rec.source}</td>
                    <td className="tabular-nums" style={{ color: 'var(--text-2)', fontSize: 12 }}>
                      {rec.retrieved_at ? new Date(rec.retrieved_at).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <span className="status-chip" style={{ fontSize: 11.5, padding: '2px 8px' }}>
                        {rec.status || 'sourced'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
