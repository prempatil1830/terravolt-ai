import React, { useState, useEffect, useMemo } from 'react';
import { useLocation, useNavigate, Link } from 'react-router-dom';
import { Download, Sliders, ArrowRight, ArrowLeft } from 'lucide-react';
import MapView from '../components/MapView';
import { calculateCandidateScore } from '../utils/scenarios';

export default function Results() {
  const location = useLocation();
  const navigate = useNavigate();

  // Retrieve state passed from search navigation, or fallback to storage/api
  const initialData = location.state?.candidatesData || location.state?.searchResult || null;
  const [candidatesData, setCandidatesData] = useState(initialData);
  const [loading, setLoading] = useState(!initialData);
  const [error, setError] = useState(null);

  const [activeScenario, setActiveScenario] = useState('baseline');
  const [highlightedId, setHighlightedId] = useState(null);

  // If no candidates data passed via navigation state, fetch default baseline candidates
  useEffect(() => {
    if (candidatesData) {
      // Save in session storage so page reloads don't lose search results
      try {
        sessionStorage.setItem('last_search_results', JSON.stringify(candidatesData));
      } catch (e) {
        // ignore storage errors
      }
      return;
    }

    // Try reading from session storage first
    try {
      const saved = sessionStorage.getItem('last_search_results');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed?.candidates?.length || parsed?.sites?.length) {
          setCandidatesData(parsed);
          setLoading(false);
          return;
        }
      }
    } catch (e) {
      // ignore
    }

    let ignore = false;
    async function loadDefaultCandidates() {
      try {
        setLoading(true);
        setError(null);
        const res = await fetch('/api/candidates');
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}: ${res.statusText}`);
        }
        const data = await res.json();
        if (!ignore) {
          setCandidatesData(data);
        }
      } catch (err) {
        if (!ignore) {
          setError(err.message || 'Unable to load candidate sites');
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }

    loadDefaultCandidates();

    return () => {
      ignore = true;
    };
  }, [candidatesData]);

  // Normalize candidate list from either /api/candidates format or /api/search format
  const rawCandidateList = useMemo(() => {
    if (!candidatesData) return [];
    if (Array.isArray(candidatesData.candidates)) return candidatesData.candidates;
    if (Array.isArray(candidatesData.sites)) return candidatesData.sites;
    if (Array.isArray(candidatesData)) return candidatesData;
    return [];
  }, [candidatesData]);

  // Compute scores according to current MCDM scenario and sort descending
  const scoredCandidates = useMemo(() => {
    return rawCandidateList
      .map((cand, idx) => {
        const computedScore = calculateCandidateScore(cand, activeScenario);
        const rank = cand.rank != null ? cand.rank : idx + 1;
        return {
          ...cand,
          rank,
          scenarioScore: computedScore,
        };
      })
      .sort((a, b) => {
        if (a.scenarioScore == null && b.scenarioScore == null) return a.rank - b.rank;
        if (a.scenarioScore == null) return 1;
        if (b.scenarioScore == null) return -1;
        return b.scenarioScore - a.scenarioScore;
      });
  }, [rawCandidateList, activeScenario]);

  const totalCandidates = scoredCandidates.length;
  const eligibleCount = scoredCandidates.filter((c) => c.status === 'eligible').length;
  const needsReviewCount = scoredCandidates.filter(
    (c) => c.status === 'needs_review' || c.needs_review
  ).length;
  const excludedCount = scoredCandidates.filter((c) => c.status === 'excluded').length;

  const handleDownload = () => {
    const exportPayload = {
      exported_at: new Date().toISOString(),
      scenario: activeScenario,
      total_candidates: totalCandidates,
      candidates: scoredCandidates,
    };
    const dataStr =
      'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(exportPayload, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', 'terravolt_ai_candidates.json');
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  if (loading) {
    return (
      <div className="content-wrapper page-fade-in" style={{ paddingTop: 64, textAlign: 'center' }}>
        <p className="caption-text" style={{ fontSize: 16 }}>Loading ranked candidate sites...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="content-wrapper page-fade-in" style={{ paddingTop: 48 }}>
        <h1 className="page-title">Unable to Load Candidates</h1>
        <p className="page-subtitle" style={{ color: 'var(--excluded)' }}>
          {error}
        </p>
        <div style={{ marginTop: 24, display: 'flex', gap: 12 }}>
          <button type="button" className="btn-primary" onClick={() => navigate('/')}>
            Back to Search
          </button>
          <button type="button" className="btn-secondary" onClick={() => window.location.reload()}>
            Retry Connection
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="content-wrapper page-fade-in" style={{ paddingBottom: 64 }}>
      {/* Title & Subtitle + Export JSON button */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          flexWrap: 'wrap',
          gap: 16,
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <h1 className="page-title" style={{ margin: 0 }}>Terravolt AI</h1>
          </div>
          <p className="page-subtitle" style={{ marginTop: 6, marginBottom: 0 }}>
            Pre-feasibility multi-criteria evaluation for utility-scale solar PV developments across Karnataka.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Link
            to="/"
            className="btn-secondary"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 13,
              padding: '7px 14px',
              textDecoration: 'none',
            }}
            title="Modify search filters"
          >
            <ArrowLeft size={15} strokeWidth={1.5} />
            <span>Refine Criteria</span>
          </Link>

          <button
            type="button"
            className="btn-secondary"
            onClick={handleDownload}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 13,
              padding: '7px 14px',
            }}
            title="Download candidate dataset JSON"
          >
            <Download size={15} strokeWidth={1.5} />
            <span>Export JSON</span>
          </button>
        </div>
      </div>

      {/* 4 Stat Chips */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          marginTop: 20,
          marginBottom: 20,
        }}
      >
        <div className="stat-chip">
          <span className="stat-chip-label">Total Evaluated</span>
          <span className="stat-chip-value tabular-nums">{totalCandidates}</span>
        </div>
        <div className="stat-chip">
          <span className="stat-chip-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="status-dot status-dot-eligible" /> Eligible
          </span>
          <span className="stat-chip-value tabular-nums">{eligibleCount}</span>
        </div>
        <div className="stat-chip">
          <span className="stat-chip-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="status-dot status-dot-review" /> Needs Review
          </span>
          <span className="stat-chip-value tabular-nums">{needsReviewCount}</span>
        </div>
        <div className="stat-chip">
          <span className="stat-chip-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="status-dot status-dot-excluded" /> Excluded
          </span>
          <span className="stat-chip-value tabular-nums">{excludedCount}</span>
        </div>
      </div>

      {/* Side-by-side Section: Map (about 60%) and Compact Ranked List (about 40%) */}
      <div
        style={{
          display: 'flex',
          gap: 20,
          alignItems: 'stretch',
          flexWrap: 'wrap',
        }}
      >
        {/* Karnataka Map Card (about 60%, 70vh) */}
        <div
          style={{
            flex: '1 1 58%',
            minWidth: 360,
          }}
        >
          <div
            className="card"
            style={{
              padding: 0,
              overflow: 'hidden',
              borderRadius: 12,
              border: '1px solid var(--border)',
              height: '70vh',
            }}
          >
            <MapView
              candidates={scoredCandidates}
              highlightedId={highlightedId}
              onSelectCandidate={(id) => {
                setHighlightedId(id);
                const found = scoredCandidates.find((c) => c.rank === id);
                if (found) {
                  navigate(`/candidates/${found.rank}`, { state: { candidate: found } });
                }
              }}
              height="100%"
            />
          </div>
        </div>

        {/* Compact Ranked List Card (about 40%, 70vh) */}
        <div
          style={{
            flex: '1 1 38%',
            minWidth: 320,
          }}
        >
          <div
            className="card"
            style={{
              padding: 16,
              borderRadius: 12,
              border: '1px solid var(--border)',
              height: '70vh',
              display: 'flex',
              flexDirection: 'column',
              backgroundColor: 'var(--surface)',
            }}
          >
            {/* Header with Title and Scenario Picker */}
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 10,
                }}
              >
                <h2 className="section-title" style={{ fontSize: 16, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Sliders size={16} strokeWidth={1.8} color="var(--primary)" /> Ranked Candidates
                </h2>
                <span className="caption-text tabular-nums" style={{ fontWeight: 600 }}>
                  {scoredCandidates.length} Sites
                </span>
              </div>

              {/* Segmented control for MCDM scenarios */}
              <div className="segmented-control" role="tablist" aria-label="MCDM Weight Scenarios" style={{ width: '100%' }}>
                {[
                  { id: 'baseline', label: 'Baseline' },
                  { id: 'ghi_heavy', label: 'GHI-heavy' },
                  { id: 'substation_heavy', label: 'Grid-heavy' },
                  { id: 'balanced', label: 'Balanced' },
                ].map((sc) => (
                  <button
                    key={sc.id}
                    type="button"
                    role="tab"
                    aria-selected={activeScenario === sc.id}
                    className={`segmented-option ${activeScenario === sc.id ? 'active' : ''}`}
                    onClick={() => setActiveScenario(sc.id)}
                    style={{ flex: 1, padding: '4px 6px', fontSize: 11, textAlign: 'center' }}
                  >
                    {sc.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Scrollable Compact List */}
            <div
              style={{
                flex: 1,
                overflowY: 'auto',
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                paddingRight: 4,
              }}
            >
              {scoredCandidates.map((cand) => {
                const raw = cand.raw_criteria || {};
                const ghiVal =
                  cand.ghi_kwh_m2_day != null
                    ? cand.ghi_kwh_m2_day.toFixed(2)
                    : raw.ghi_kwh_m2_day != null
                    ? raw.ghi_kwh_m2_day.toFixed(2)
                    : cand.ghi != null
                    ? Number(cand.ghi).toFixed(2)
                    : '—';

                const subDist =
                  cand.substation_dist_km != null
                    ? `${cand.substation_dist_km.toFixed(1)} km`
                    : raw.substation_dist_km != null
                    ? `${raw.substation_dist_km.toFixed(1)} km`
                    : cand.dist_substation_km != null
                    ? `${Number(cand.dist_substation_km).toFixed(1)} km`
                    : '—';

                const displayScore =
                  cand.scenarioScore != null
                    ? `${(cand.scenarioScore * 100).toFixed(1)} / 100`
                    : cand.score != null
                    ? `${(cand.score > 1 ? cand.score : cand.score * 100).toFixed(1)} / 100`
                    : 'Excluded';

                const isHighlighted = cand.rank === highlightedId;
                const isNeedsReview = cand.status === 'needs_review' || cand.needs_review;
                const statusColor = isNeedsReview ? '#C77D0A' : cand.status === 'excluded' ? '#B5452F' : '#1F5C45';

                return (
                  <div
                    key={cand.rank}
                    onClick={() => navigate(`/candidates/${cand.rank}`, { state: { candidate: cand } })}
                    onMouseEnter={() => setHighlightedId(cand.rank)}
                    onMouseLeave={() => setHighlightedId(null)}
                    style={{
                      padding: '9px 12px',
                      borderRadius: 8,
                      border: '1px solid var(--border)',
                      backgroundColor: isHighlighted ? 'var(--surface-2)' : 'transparent',
                      cursor: 'pointer',
                      transition: 'all 0.15s ease',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                    }}
                    title="Click for full candidate engineering dossier"
                  >
                    {/* Rank Circle badge matching map marker style */}
                    <div
                      style={{
                        width: 26,
                        height: 26,
                        borderRadius: '50%',
                        backgroundColor: statusColor,
                        color: '#FFFFFF',
                        fontSize: 11.5,
                        fontWeight: 600,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        boxShadow: '0 0 0 1.5px var(--surface)',
                        flexShrink: 0,
                      }}
                    >
                      {cand.rank}
                    </div>

                    {/* Site Summary Info */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                        <span
                          style={{
                            fontWeight: 600,
                            fontSize: 13,
                            color: 'var(--text)',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                        >
                          {cand.district || 'Karnataka'}
                        </span>
                        <span className="tabular-nums" style={{ fontWeight: 700, fontSize: 13, color: 'var(--text)' }}>
                          {displayScore}
                        </span>
                      </div>
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          fontSize: 11,
                          color: 'var(--text-2)',
                          marginTop: 2,
                        }}
                      >
                        <span className="tabular-nums">
                          {cand.latitude != null && cand.longitude != null
                            ? `${cand.latitude.toFixed(2)}°N, ${cand.longitude.toFixed(2)}°E`
                            : ''}
                        </span>
                        <span className="tabular-nums">
                          {ghiVal !== '—' ? `${ghiVal} kWh/m² · Sub ${subDist}` : `Sub ${subDist}`}
                        </span>
                      </div>
                    </div>

                    <ArrowRight size={14} color="var(--text-2)" style={{ flexShrink: 0 }} />
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
