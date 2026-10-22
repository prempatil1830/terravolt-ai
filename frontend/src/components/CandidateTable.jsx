import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { calculateCandidateScore } from '../utils/scenarios';

export default function CandidateTable({
  candidates = [],
  activeScenario = 'baseline',
  onScenarioChange,
  highlightedId,
  onHoverCandidate,
}) {
  const navigate = useNavigate();

  // Compute scores according to current scenario and sort
  const scoredCandidates = useMemo(() => {
    return candidates.map((cand) => {
      const computedScore = calculateCandidateScore(cand, activeScenario);
      return {
        ...cand,
        scenarioScore: computedScore,
      };
    }).sort((a, b) => {
      if (a.scenarioScore == null && b.scenarioScore == null) return a.rank - b.rank;
      if (a.scenarioScore == null) return 1;
      if (b.scenarioScore == null) return -1;
      return b.scenarioScore - a.scenarioScore;
    });
  }, [candidates, activeScenario]);

  return (
    <div className="section-gap">
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 16 }}>
        <div>
          <h2 className="section-title">Ranked Candidates</h2>
          <p className="section-subtitle">
            Multi-criteria suitability evaluation across Karnataka candidate sites.
          </p>
        </div>

        {/* Segmented control (Apple style) for scenario switching */}
        <div className="segmented-control" role="tablist" aria-label="MCDM Weight Scenarios">
          <button
            type="button"
            role="tab"
            aria-selected={activeScenario === 'baseline'}
            className={`segmented-option ${activeScenario === 'baseline' ? 'active' : ''}`}
            onClick={() => onScenarioChange('baseline')}
          >
            Baseline
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeScenario === 'ghi_heavy'}
            className={`segmented-option ${activeScenario === 'ghi_heavy' ? 'active' : ''}`}
            onClick={() => onScenarioChange('ghi_heavy')}
          >
            GHI-heavy
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeScenario === 'substation_heavy'}
            className={`segmented-option ${activeScenario === 'substation_heavy' ? 'active' : ''}`}
            onClick={() => onScenarioChange('substation_heavy')}
          >
            Substation-heavy
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeScenario === 'balanced'}
            className={`segmented-option ${activeScenario === 'balanced' ? 'active' : ''}`}
            onClick={() => onScenarioChange('balanced')}
          >
            Balanced
          </button>
        </div>
      </div>

      <div className="table-wrapper">
        <table className="apple-table" aria-label="Candidates Table">
          <thead>
            <tr>
              <th style={{ width: 64 }}>Rank</th>
              <th>Coordinates</th>
              <th>Status</th>
              <th className="th-numeric">Score</th>
              <th className="th-numeric">GHI (kWh/m²/d)</th>
              <th className="th-numeric">Substation</th>
              <th className="th-numeric">Land Proxy</th>
              <th className="th-numeric">Temp</th>
            </tr>
          </thead>
          <tbody>
            {scoredCandidates.map((cand) => {
              const raw = cand.raw_criteria || {};
              const ghiVal = raw.ghi_kwh_m2_day != null ? raw.ghi_kwh_m2_day.toFixed(2) : '—';
              const subDist = raw.substation_dist_km != null ? `${raw.substation_dist_km.toFixed(1)} km` : 'No data';
              const lcProxy = raw.landcost_proxy_score != null ? raw.landcost_proxy_score.toFixed(3) : '—';
              const tempVal = raw.t2m_celsius != null ? `${raw.t2m_celsius.toFixed(1)} °C` : '—';

              const displayScore = cand.scenarioScore != null
                ? (cand.scenarioScore * 100).toFixed(1)
                : 'Excluded';

              let statusText = 'Eligible';
              let statusDotClass = 'status-dot-eligible';
              let statusTextClass = 'status-text-eligible';

              if (cand.status === 'needs_review' || cand.needs_review) {
                statusText = 'Needs Review';
                statusDotClass = 'status-dot-review';
                statusTextClass = 'status-text-review';
              } else if (cand.status === 'excluded') {
                statusText = 'Excluded';
                statusDotClass = 'status-dot-excluded';
                statusTextClass = 'status-text-excluded';
              }

              const isHighlighted = cand.rank === highlightedId;

              return (
                <tr
                  key={cand.rank}
                  style={isHighlighted ? { backgroundColor: 'var(--color-border-subtle)' } : undefined}
                  onClick={() => navigate(`/candidates/${cand.rank}`, { state: { candidate: cand } })}
                  onMouseEnter={() => onHoverCandidate && onHoverCandidate(cand.rank)}
                  onMouseLeave={() => onHoverCandidate && onHoverCandidate(null)}
                >
                  <td className="tabular-nums" style={{ color: 'var(--color-text-secondary)' }}>
                    #{cand.rank}
                  </td>
                  <td className="tabular-nums">
                    {cand.latitude.toFixed(2)}°N, {cand.longitude.toFixed(2)}°E
                  </td>
                  <td>
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                      <span className={`status-dot ${statusDotClass}`} />
                      <span className={statusTextClass} style={{ fontSize: 13, fontWeight: 400 }}>
                        {statusText}
                      </span>
                    </div>
                  </td>
                  <td className="td-numeric" style={{ fontWeight: 600 }}>
                    {displayScore}
                  </td>
                  <td className="td-numeric">{ghiVal}</td>
                  <td className="td-numeric">{subDist}</td>
                  <td className="td-numeric">{lcProxy}</td>
                  <td className="td-numeric">{tempVal}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
