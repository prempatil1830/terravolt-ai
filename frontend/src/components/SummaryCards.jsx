import React from 'react';

export default function SummaryCards({ metadata, candidates }) {
  const totalEvaluated = metadata?.total_points || 3266;
  const p1Eligible = metadata?.p1_eligible || 1888;
  const p1Excluded = metadata?.p1_excluded || 1378;

  const eligibleCount = candidates.filter((c) => c.status === 'eligible').length;
  const needsReviewCount = candidates.filter((c) => c.status === 'needs_review' || c.needs_review).length;
  const excludedCount = candidates.filter((c) => c.status === 'excluded').length;

  const topCandidate = candidates.find((c) => c.rank === 1);

  return (
    <div className="summary-cards-container">
      <div className="summary-card">
        <div className="card-label">Screened Grid Points</div>
        <div className="card-value">{totalEvaluated.toLocaleString()}</div>
        <div className="card-subtext">
          <span className="accent-green">{p1Eligible} eligible</span> · {p1Excluded} excluded
        </div>
      </div>

      <div className="summary-card">
        <div className="card-label">Shortlisted Candidates</div>
        <div className="card-value">{candidates.length}</div>
        <div className="card-subtext">5×4 Regional Diversity Grid</div>
      </div>

      <div className="summary-card card-eligible">
        <div className="card-label">Eligible Sites</div>
        <div className="card-value text-green">{eligibleCount}</div>
        <div className="card-subtext">Cleared across all 7 criteria</div>
      </div>

      <div className="summary-card card-review">
        <div className="card-label">Needs Review</div>
        <div className="card-value text-amber">{needsReviewCount}</div>
        <div className="card-subtext">Water buffer API timeout (Site #8)</div>
      </div>

      <div className="summary-card card-excluded">
        <div className="card-label">Excluded Sites</div>
        <div className="card-value text-red">{excludedCount}</div>
        <div className="card-subtext">Tripped protected buffer (Site #12)</div>
      </div>

      <div className="summary-card card-top">
        <div className="card-label">Top Ranked Site</div>
        <div className="card-value text-gold">#{topCandidate?.rank || 1}</div>
        <div className="card-subtext">
          Score: {topCandidate?.score_100 || '72.70'} / 100 · Pavagada
        </div>
      </div>
    </div>
  );
}
