import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  RotateCcw,
  Layers,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  ArrowRight,
  Plus,
  Trash2,
  Undo2,
  RefreshCw,
  Info,
  CheckCircle2,
  Sliders,
  TrendingUp,
  FileText,
  X,
  Search as SearchIcon,
} from 'lucide-react';
import MapView from '../components/MapView';

const PRECOMPUTED_RADII = [0.25, 0.40, 0.54, 0.70, 0.85, 1.00, 1.10];

const CONSTRAINT_GROUPS = [
  'Location and access',
  'Land',
  'Sun and climate',
  'Grid',
  'Environment',
];

function computeFootprint(sizeMwp) {
  const acres = sizeMwp * 4.5;
  const areaM2 = acres * 4046.8564224;
  const radiusKm = Math.sqrt(areaM2 / Math.PI) / 1000.0;
  let usedRadius = PRECOMPUTED_RADII[PRECOMPUTED_RADII.length - 1];
  for (const r of PRECOMPUTED_RADII) {
    if (r >= radiusKm - 1e-6) {
      usedRadius = r;
      break;
    }
  }
  const usedAcres = (Math.PI * Math.pow(usedRadius * 1000, 2)) / 4046.8564224;
  return {
    acres: acres.toFixed(1),
    radiusKm: radiusKm.toFixed(2),
    usedRadiusKm: usedRadius.toFixed(2),
    usedAcres: usedAcres.toFixed(1),
  };
}

export default function Search() {
  const navigate = useNavigate();

  // Feature 1: Districts
  const [districtList, setDistrictList] = useState([]);
  const [selectedDistricts, setSelectedDistricts] = useState([]);
  const [districtSearchQuery, setDistrictSearchQuery] = useState('');
  const [isDistrictDropdownOpen, setIsDistrictDropdownOpen] = useState(false);

  // Feature 2: Plant Size (MWp)
  const [sizeMwp, setSizeMwp] = useState(50);

  // Feature 3: Priority
  const [priority, setPriority] = useState('default');

  // Feature 4: Number of Sites
  const [topN, setTopN] = useState(10);

  // State
  const [loading, setLoading] = useState(false);
  const [searchResult, setSearchResult] = useState(null);
  const [error, setError] = useState(null);
  const [highlightedId, setHighlightedId] = useState(null);
  const [referenceCandidates, setReferenceCandidates] = useState([]);
  const [viewMode, setViewMode] = useState('search'); // 'search' | 'reference'

  // Human-in-the-loop: Constraints Catalogue & Selection
  const [catalogue, setCatalogue] = useState([]);
  const [extraConstraints, setExtraConstraints] = useState([]);
  const [constraintHistory, setConstraintHistory] = useState([]);
  const [reviewerName, setReviewerName] = useState('Reviewer');
  const [isConstraintDropdownOpen, setIsConstraintDropdownOpen] = useState(false);
  const [constraintSearchQuery, setConstraintSearchQuery] = useState('');

  // Human-in-the-loop: Change Tracking & Decision Log
  const [changeBanner, setChangeBanner] = useState(null);
  const [_previousSites, setPreviousSites] = useState([]);
  const [decisionLog, setDecisionLog] = useState([]);
  const [isDecisionLogOpen, setIsDecisionLogOpen] = useState(false);

  // Load district names & GeoJSON
  useEffect(() => {
    let ignore = false;
    async function fetchDistricts() {
      try {
        const res = await fetch('/api/districts');
        if (res.ok) {
          const data = await res.json();
          if (!ignore) {
            setDistrictList(data.districts || []);
          }
        }
      } catch (err) {
        console.error('Failed to load districts:', err);
      }
    }
    fetchDistricts();
    return () => {
      ignore = true;
    };
  }, []);

  // Load reference 12 candidates
  useEffect(() => {
    let ignore = false;
    async function fetchRef() {
      try {
        const res = await fetch('/api/candidates');
        if (res.ok) {
          const data = await res.json();
          if (!ignore) {
            setReferenceCandidates(data.candidates || []);
          }
        }
      } catch (err) {
        console.error('Failed to load reference candidates:', err);
      }
    }
    fetchRef();
    return () => {
      ignore = true;
    };
  }, []);

  // Load constraints catalogue
  useEffect(() => {
    let ignore = false;
    async function fetchCatalogue() {
      try {
        const res = await fetch('/api/constraints');
        if (res.ok) {
          const data = await res.json();
          if (!ignore) {
            setCatalogue(data.constraints || []);
          }
        }
      } catch (err) {
        console.error('Failed to load constraints catalogue:', err);
      }
    }
    fetchCatalogue();
    return () => {
      ignore = true;
    };
  }, []);

  // Load Decision Log history
  const fetchDecisionLog = async () => {
    try {
      const res = await fetch('/api/decision-log?limit=25');
      if (res.ok) {
        const data = await res.json();
        setDecisionLog(data.entries || []);
      }
    } catch (err) {
      console.error('Failed to load decision log:', err);
    }
  };

  useEffect(() => {
    fetchDecisionLog();
  }, []);

  // Compute live footprint metrics for plant size
  const footprint = useMemo(() => computeFootprint(sizeMwp), [sizeMwp]);

  // Construct selected districts GeoJSON FeatureCollection for MapView outline
  const districtGeoJSON = useMemo(() => {
    if (selectedDistricts.length === 0) return null;
    const selectedNames = new Set(selectedDistricts);
    const features = districtList
      .filter((d) => selectedNames.has(d.name) && d.geojson)
      .map((d) => ({
        type: 'Feature',
        properties: { name: d.name },
        geometry: d.geojson,
      }));
    if (features.length === 0) return null;
    return {
      type: 'FeatureCollection',
      features,
    };
  }, [selectedDistricts, districtList]);

  // Total extra weight calculation
  const totalExtraWeightPct = useMemo(() => {
    return extraConstraints
      .filter((c) => c.mode === 'prefer')
      .reduce((sum, c) => {
        const w = c.importance === 'high' ? 20 : c.importance === 'medium' ? 10 : 5;
        return sum + w;
      }, 0);
  }, [extraConstraints]);

  // Conflict detection: close_to_town vs lower_land_cost
  const hasTownLandCostConflict = useMemo(() => {
    const ids = extraConstraints.map((c) => c.id);
    return ids.includes('close_to_town') && ids.includes('lower_land_cost');
  }, [extraConstraints]);

  const hasLowerLandCost = useMemo(() => {
    return extraConstraints.some((c) => c.id === 'lower_land_cost');
  }, [extraConstraints]);

  // Execute Search or Re-evaluate
  const handleSearch = async (overrideParams = null, isReevaluate = false) => {
    if (totalExtraWeightPct > 50) {
      setError(`Total extra prefer weights exceed 50% limit (${totalExtraWeightPct}%). Please reduce before running search.`);
      return;
    }

    setLoading(true);
    setError(null);
    setViewMode('search');

    const currentSites = searchResult?.sites || [];

    const formattedExtras = extraConstraints.map((c) => ({
      id: c.id,
      value: Number(c.value),
      mode: c.mode,
      importance: c.mode === 'prefer' ? (c.importance || 'medium') : undefined,
    }));

    const params = overrideParams || {
      districts: selectedDistricts.length === 0 ? 'all' : selectedDistricts,
      size_mwp: Number(sizeMwp),
      priority,
      top_n: Number(topN),
      min_kv: 33,
      max_slope_pct: 5,
      avoid_cropland: false,
      pa_buffer_km: 10.0,
      extra_constraints: formattedExtras,
      reviewer_name: reviewerName || 'Reviewer',
      is_reevaluate: isReevaluate,
      counts_before: currentSites.length,
    };

    try {
      const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `HTTP ${res.status}: ${res.statusText}`);
      }

      const data = await res.json();
      setSearchResult(data);

      // Track changes if this was a re-evaluate
      if (isReevaluate && currentSites.length > 0) {
        const newSites = data.sites || [];
        const prevKeySet = new Set(
          currentSites.map((s) => `${s.latitude.toFixed(4)}_${s.longitude.toFixed(4)}`)
        );
        const prevRankMap = new Map(
          currentSites.map((s) => [`${s.latitude.toFixed(4)}_${s.longitude.toFixed(4)}`, s.rank])
        );
        const newKeySet = new Set(
          newSites.map((s) => `${s.latitude.toFixed(4)}_${s.longitude.toFixed(4)}`)
        );

        let addedCount = 0;
        let removedCount = 0;
        let movedUpCount = 0;

        newSites.forEach((ns) => {
          const key = `${ns.latitude.toFixed(4)}_${ns.longitude.toFixed(4)}`;
          if (!prevKeySet.has(key)) {
            addedCount++;
          } else {
            const oldRank = prevRankMap.get(key);
            if (oldRank != null && ns.rank < oldRank) {
              movedUpCount++;
            }
          }
        });

        currentSites.forEach((ps) => {
          const key = `${ps.latitude.toFixed(4)}_${ps.longitude.toFixed(4)}`;
          if (!newKeySet.has(key)) {
            removedCount++;
          }
        });

        setChangeBanner({
          visible: true,
          addedCount,
          removedCount,
          movedUpCount,
          totalNow: newSites.length,
          prevCount: currentSites.length,
        });

        fetchDecisionLog();
      } else if (!isReevaluate) {
        setChangeBanner(null);
      }

      setPreviousSites(data.sites || []);
    } catch (err) {
      setError(err.message || 'Search failed');
    } finally {
      setLoading(false);
    }
  };

  // Run initial search on mount
  useEffect(() => {
    handleSearch();
  }, []);

  // Constraint refinement actions
  const addConstraint = (constraintId) => {
    const meta = catalogue.find((c) => c.id === constraintId);
    if (!meta) return;
    if (extraConstraints.some((c) => c.id === constraintId)) return;

    setConstraintHistory((prev) => [...prev, [...extraConstraints]]);

    const defaultVal = meta.allowed_values[0];
    const defaultMode = meta.modes_allowed.includes('must') ? 'must' : 'prefer';
    const newConstraint = {
      id: meta.id,
      label: meta.label,
      group: meta.group,
      unit: meta.unit,
      allowed_values: meta.allowed_values,
      value: defaultVal,
      mode: defaultMode,
      importance: 'medium',
      direction: meta.direction,
      modes_allowed: meta.modes_allowed,
      note: meta.note,
    };

    setExtraConstraints((prev) => [...prev, newConstraint]);
    setIsConstraintDropdownOpen(false);
    setConstraintSearchQuery('');
  };

  const updateConstraint = (id, field, val) => {
    setExtraConstraints((prev) =>
      prev.map((c) => (c.id === id ? { ...c, [field]: val } : c))
    );
  };

  const removeConstraint = (id) => {
    setConstraintHistory((prev) => [...prev, [...extraConstraints]]);
    setExtraConstraints((prev) => prev.filter((c) => c.id !== id));
  };

  const handleUndo = () => {
    if (constraintHistory.length === 0) return;
    const previousState = constraintHistory[constraintHistory.length - 1];
    setConstraintHistory((prev) => prev.slice(0, prev.length - 1));
    setExtraConstraints(previousState);
  };

  const handleResetConstraints = () => {
    setConstraintHistory((prev) => [...prev, [...extraConstraints]]);
    setExtraConstraints([]);
    setChangeBanner(null);
  };

  // Load default example
  const handleLoadDefault = () => {
    setSelectedDistricts([]);
    setSizeMwp(50);
    setPriority('default');
    setTopN(10);
    setExtraConstraints([]);
    setConstraintHistory([]);
    setChangeBanner(null);
    handleSearch({
      districts: 'all',
      size_mwp: 50.0,
      priority: 'default',
      top_n: 10,
      min_kv: 33,
      max_slope_pct: 5,
      avoid_cropland: false,
      pa_buffer_km: 10.0,
      extra_constraints: [],
      reviewer_name: reviewerName || 'Reviewer',
      is_reevaluate: false,
      counts_before: 0,
    });
  };

  const toggleDistrict = (name) => {
    if (selectedDistricts.includes(name)) {
      setSelectedDistricts(selectedDistricts.filter((d) => d !== name));
    } else {
      setSelectedDistricts([...selectedDistricts, name]);
    }
  };

  const filteredDistrictList = districtList.filter((d) =>
    d.name.toLowerCase().includes(districtSearchQuery.toLowerCase())
  );

  const activeCandidates =
    viewMode === 'reference'
      ? referenceCandidates
      : searchResult?.sites || [];

  const eligibleCount = activeCandidates.filter((c) => c.status === 'eligible').length;
  const needsReviewCount = activeCandidates.filter((c) => c.status === 'needs_review' || c.needs_review).length;
  const feasiblePointsCount = searchResult?.funnel?.raw_evaluated || searchResult?.funnel?.total_considered || 3266;
  const sitesFoundCount = activeCandidates.length;

  const funnel = searchResult?.funnel || null;
  const sliderFillPct = Math.round(((sizeMwp - 5) / (200 - 5)) * 100);

  // Available constraints for dropdown (filter out already added)
  const availableCatalogue = catalogue.filter(
    (c) =>
      !extraConstraints.some((added) => added.id === c.id) &&
      (c.label.toLowerCase().includes(constraintSearchQuery.toLowerCase()) ||
        c.group.toLowerCase().includes(constraintSearchQuery.toLowerCase()))
  );

  return (
    <div className="content-wrapper page-fade-in" style={{ paddingBottom: 64 }}>
      {/* 1. Title and One-Line Subtitle */}
      <div>
        <h1 className="page-title">Interactive Site Search</h1>
        <p className="page-subtitle">
          Screen and rank utility-scale solar PV sites across Karnataka in real-time using precomputed multi-criteria spatial features with human-in-the-loop constraint refinement.
        </p>
      </div>

      {/* 2. Stat Chips */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          marginTop: 20,
          marginBottom: 24,
        }}
      >
        <div className="stat-chip">
          <span className="stat-chip-label">Feasible Points</span>
          <span className="stat-chip-value tabular-nums">{feasiblePointsCount.toLocaleString()}</span>
        </div>
        <div className="stat-chip">
          <span className="stat-chip-label">Sites Found</span>
          <span className="stat-chip-value tabular-nums">{sitesFoundCount}</span>
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
      </div>

      {error && (
        <div
          className="card"
          style={{
            marginBottom: 20,
            borderLeft: '4px solid var(--review)',
            background: 'var(--surface)',
            padding: '12px 16px',
            fontSize: 13,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <AlertTriangle size={18} color="var(--review)" style={{ flexShrink: 0 }} />
          <div>
            <strong style={{ color: 'var(--text)' }}>Search notice: </strong>
            <span>{error}</span>
          </div>
        </div>
      )}

      {/* 3. Two Columns: Sticky 380 px Input Panel and Map Card */}
      <div
        style={{
          display: 'flex',
          gap: 24,
          alignItems: 'flex-start',
        }}
      >
        {/* Sticky 380 px Input Panel */}
        <aside
          style={{
            width: 380,
            flexShrink: 0,
            position: 'sticky',
            top: 72,
            zIndex: 10,
            maxHeight: 'calc(100vh - 90px)',
            overflowY: 'auto',
          }}
        >
          <div
            className="card"
            style={{
              padding: 20,
              backgroundColor: 'var(--surface)',
              borderRadius: 12,
              border: '1px solid var(--border)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h2 className="section-title" style={{ fontSize: 17, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Sliders size={18} strokeWidth={1.8} color="var(--primary)" /> Parameters
              </h2>
              {selectedDistricts.length > 0 && (
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ padding: '2px 8px', fontSize: 11 }}
                  onClick={() => setSelectedDistricts([])}
                >
                  Clear Districts
                </button>
              )}
            </div>

            {/* Region / District multiselect */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 6 }}>
                1. Districts
              </label>
              <div style={{ position: 'relative' }}>
                <button
                  type="button"
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: 8,
                    border: '1px solid var(--border)',
                    background: 'var(--surface-2)',
                    color: 'var(--text)',
                    fontSize: 13,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    cursor: 'pointer',
                  }}
                  onClick={() => setIsDistrictDropdownOpen(!isDistrictDropdownOpen)}
                >
                  <span style={{ textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap' }}>
                    {selectedDistricts.length === 0
                      ? 'All Karnataka (31 Districts)'
                      : `${selectedDistricts.length} Selected: ${selectedDistricts.slice(0, 2).join(', ')}${selectedDistricts.length > 2 ? '...' : ''}`}
                  </span>
                  {isDistrictDropdownOpen ? <ChevronUp size={14} strokeWidth={1.5} /> : <ChevronDown size={14} strokeWidth={1.5} />}
                </button>

                {isDistrictDropdownOpen && (
                  <div
                    style={{
                      position: 'absolute',
                      top: 'calc(100% + 4px)',
                      left: 0,
                      right: 0,
                      maxHeight: 220,
                      overflowY: 'auto',
                      backgroundColor: 'var(--surface)',
                      border: '1px solid var(--border)',
                      borderRadius: 8,
                      boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
                      zIndex: 50,
                      padding: 8,
                    }}
                  >
                    <input
                      type="text"
                      placeholder="Search districts..."
                      value={districtSearchQuery}
                      onChange={(e) => setDistrictSearchQuery(e.target.value)}
                      style={{
                        width: '100%',
                        padding: '6px 8px',
                        fontSize: 12,
                        borderRadius: 6,
                        border: '1px solid var(--border)',
                        background: 'var(--surface-2)',
                        color: 'var(--text)',
                        marginBottom: 6,
                      }}
                    />
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                      {filteredDistrictList.map((d) => {
                        const checked = selectedDistricts.includes(d.name);
                        return (
                          <label
                            key={d.name}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 8,
                              fontSize: 12.5,
                              padding: '4px 6px',
                              borderRadius: 4,
                              cursor: 'pointer',
                              backgroundColor: checked ? 'var(--surface-2)' : 'transparent',
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleDistrict(d.name)}
                              style={{ accentColor: 'var(--primary)' }}
                            />
                            <span>{d.name}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* Plant Size (MWp) with Amber Slider */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
                <label style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
                  2. Plant Size
                </label>
                <span className="tabular-nums" style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>
                  {sizeMwp} MWp
                </span>
              </div>
              <input
                type="range"
                min={5}
                max={200}
                step={5}
                value={sizeMwp}
                onChange={(e) => setSizeMwp(Number(e.target.value))}
                style={{
                  '--range-pct': `${sliderFillPct}%`,
                  marginBottom: 6,
                }}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-2)' }}>
                <span>{footprint.acres} acres ({footprint.radiusKm} km radius)</span>
                <span className="tabular-nums">5 – 200 MWp</span>
              </div>
            </div>

            {/* Priority Weights */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 6 }}>
                3. Priority Objective
              </label>
              <div
                className="segmented-control"
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(2, 1fr)',
                  gap: 4,
                  width: '100%',
                }}
              >
                {[
                  { id: 'default', label: 'Default' },
                  { id: 'solar_first', label: 'Solar Resource' },
                  { id: 'grid_first', label: 'Grid Proximity' },
                  { id: 'balanced', label: 'Balanced' },
                ].map((item) => {
                  const isActive =
                    priority === item.id ||
                    (item.id === 'solar_first' && priority === 'solar') ||
                    (item.id === 'grid_first' && priority === 'grid');
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={`segmented-option ${isActive ? 'active' : ''}`}
                      onClick={() => setPriority(item.id)}
                      style={{ textAlign: 'center', padding: '6px 4px' }}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Number of sites Top N */}
            <div style={{ marginBottom: 20 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 6 }}>
                4. Candidates to Shortlist
              </label>
              <div className="segmented-control" style={{ width: '100%', display: 'flex' }}>
                {[5, 10, 15, 20].map((n) => (
                  <button
                    key={n}
                    type="button"
                    className={`segmented-option ${topN === n ? 'active' : ''}`}
                    style={{ flex: 1, textAlign: 'center' }}
                    onClick={() => setTopN(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>

            {/* ── Section: Your Constraints (HITL Refinement) ──────────────── */}
            <div
              style={{
                marginTop: 20,
                paddingTop: 18,
                borderTop: '1px solid var(--border)',
                marginBottom: 20,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <h3 style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 6 }}>
                  Your constraints
                </h3>
                {extraConstraints.length > 0 && (
                  <span className="caption-text" style={{ fontSize: 11, color: 'var(--primary)', fontWeight: 600 }}>
                    {extraConstraints.length} active
                  </span>
                )}
              </div>

              {/* Reviewer Name Input */}
              <div style={{ marginBottom: 14 }}>
                <label style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-2)', display: 'block', marginBottom: 4 }}>
                  Reviewer Name
                </label>
                <input
                  type="text"
                  value={reviewerName}
                  onChange={(e) => setReviewerName(e.target.value)}
                  placeholder="Enter your name..."
                  style={{
                    width: '100%',
                    padding: '6px 10px',
                    fontSize: 12.5,
                    borderRadius: 6,
                    border: '1px solid var(--border)',
                    background: 'var(--surface-2)',
                    color: 'var(--text)',
                  }}
                />
              </div>

              {/* Conflict Warning: close_to_town + lower_land_cost */}
              {hasTownLandCostConflict && (
                <div
                  style={{
                    padding: '8px 10px',
                    borderRadius: 6,
                    backgroundColor: 'rgba(199, 125, 10, 0.12)',
                    border: '1px solid var(--review)',
                    fontSize: 11.5,
                    color: 'var(--text)',
                    marginBottom: 12,
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 6,
                  }}
                >
                  <AlertTriangle size={15} color="var(--review)" style={{ flexShrink: 0, marginTop: 1 }} />
                  <div>
                    <strong>Spatial Conflict Notice:</strong> "Close to city/town" (requires proximity) and "Lower land cost" (farther proxy) pull in opposing geographic directions.
                  </div>
                </div>
              )}

              {/* Note on lower_land_cost */}
              {hasLowerLandCost && !hasTownLandCostConflict && (
                <div
                  style={{
                    padding: '6px 8px',
                    borderRadius: 6,
                    backgroundColor: 'var(--surface-2)',
                    border: '1px solid var(--border)',
                    fontSize: 11,
                    color: 'var(--text-2)',
                    marginBottom: 10,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  <Info size={13} color="var(--primary)" style={{ flexShrink: 0 }} />
                  <span>
                    <strong>Note:</strong> Lower land cost uses distance-from-towns proxy, not real market land prices.
                  </span>
                </div>
              )}

              {/* Total Extra Prefer Weight Budget Meter */}
              {totalExtraWeightPct > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-2)' }}>Extra Scoring Weight:</span>
                    <span
                      style={{
                        fontWeight: 600,
                        color: totalExtraWeightPct > 50 ? 'var(--excluded)' : 'var(--primary)',
                      }}
                    >
                      {totalExtraWeightPct}% / 50% max
                    </span>
                  </div>
                  <div
                    style={{
                      height: 5,
                      background: 'var(--surface-2)',
                      borderRadius: 3,
                      overflow: 'hidden',
                      border: '1px solid var(--border)',
                    }}
                  >
                    <div
                      style={{
                        width: `${Math.min(100, (totalExtraWeightPct / 50) * 100)}%`,
                        height: '100%',
                        backgroundColor: totalExtraWeightPct > 50 ? 'var(--excluded)' : 'var(--primary)',
                        transition: 'width 0.2s ease',
                      }}
                    />
                  </div>
                  {totalExtraWeightPct > 50 && (
                    <div style={{ fontSize: 11, color: 'var(--excluded)', marginTop: 4, fontWeight: 500 }}>
                      ⚠️ Total extras exceed 50% max limit. Reduce importance to re-evaluate.
                    </div>
                  )}
                </div>
              )}

              {/* Added Constraint Rows List */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 14 }}>
                {extraConstraints.map((item) => {
                  const isPrefer = item.mode === 'prefer';
                  const isMust = item.mode === 'must';
                  const canBeMust = item.modes_allowed?.includes('must') ?? (item.id !== 'lower_land_cost');

                  return (
                    <div
                      key={item.id}
                      style={{
                        padding: '10px 12px',
                        borderRadius: 8,
                        backgroundColor: 'var(--surface-2)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      {/* Row Top: Label and Remove */}
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
                          {item.label}
                        </span>
                        <button
                          type="button"
                          onClick={() => removeConstraint(item.id)}
                          style={{
                            background: 'transparent',
                            border: 'none',
                            color: 'var(--text-2)',
                            cursor: 'pointer',
                            padding: 2,
                            display: 'flex',
                            alignItems: 'center',
                          }}
                          title="Remove constraint"
                        >
                          <Trash2 size={13} strokeWidth={1.6} />
                        </button>
                      </div>

                      {/* Row Middle: Value Selector */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <span style={{ fontSize: 11.5, color: 'var(--text-2)', width: 44 }}>Limit:</span>
                        <select
                          value={item.value}
                          onChange={(e) => updateConstraint(item.id, 'value', Number(e.target.value))}
                          style={{
                            flex: 1,
                            padding: '4px 8px',
                            fontSize: 12,
                            borderRadius: 6,
                            border: '1px solid var(--border)',
                            backgroundColor: 'var(--surface)',
                            color: 'var(--text)',
                          }}
                        >
                          {item.allowed_values.map((v) => (
                            <option key={v} value={v}>
                              {item.direction} {v} {item.unit}
                            </option>
                          ))}
                        </select>
                      </div>

                      {/* Row Bottom: Must vs Prefer Toggle & Importance */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 11.5, color: 'var(--text-2)', width: 44 }}>Mode:</span>
                        <div className="segmented-control" style={{ display: 'flex', flex: 1, padding: 2 }}>
                          {canBeMust ? (
                            <button
                              type="button"
                              className={`segmented-option ${isMust ? 'active' : ''}`}
                              onClick={() => updateConstraint(item.id, 'mode', 'must')}
                              style={{ flex: 1, padding: '3px 6px', fontSize: 11 }}
                            >
                              Must
                            </button>
                          ) : (
                            <span style={{ flex: 1, fontSize: 10, color: 'var(--text-2)', textAlign: 'center' }}>
                              Prefer only
                            </span>
                          )}
                          <button
                            type="button"
                            className={`segmented-option ${isPrefer ? 'active' : ''}`}
                            onClick={() => updateConstraint(item.id, 'mode', 'prefer')}
                            style={{ flex: 1, padding: '3px 6px', fontSize: 11 }}
                          >
                            Prefer
                          </button>
                        </div>

                        {/* If Prefer: Importance Selector */}
                        {isPrefer && (
                          <select
                            value={item.importance || 'medium'}
                            onChange={(e) => updateConstraint(item.id, 'importance', e.target.value)}
                            style={{
                              padding: '3px 6px',
                              fontSize: 11,
                              borderRadius: 6,
                              border: '1px solid var(--border)',
                              backgroundColor: 'var(--surface)',
                              color: 'var(--text)',
                              width: 100,
                            }}
                          >
                            <option value="low">Low (5%)</option>
                            <option value="medium">Med (10%)</option>
                            <option value="high">High (20%)</option>
                          </select>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Searchable Dropdown: "Add a constraint" */}
              <div style={{ position: 'relative', marginBottom: 16 }}>
                <button
                  type="button"
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    borderRadius: 8,
                    border: '1px dashed var(--border)',
                    background: 'var(--surface-2)',
                    color: 'var(--text)',
                    fontSize: 12.5,
                    fontWeight: 500,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 6,
                    cursor: 'pointer',
                  }}
                  onClick={() => setIsConstraintDropdownOpen(!isConstraintDropdownOpen)}
                >
                  <Plus size={15} strokeWidth={2} color="var(--primary)" />
                  <span>Add a constraint</span>
                  <ChevronDown size={14} style={{ marginLeft: 'auto' }} />
                </button>

                {isConstraintDropdownOpen && (
                  <div
                    style={{
                      position: 'absolute',
                      top: 'calc(100% + 4px)',
                      left: 0,
                      right: 0,
                      maxHeight: 280,
                      overflowY: 'auto',
                      backgroundColor: 'var(--surface)',
                      border: '1px solid var(--border)',
                      borderRadius: 8,
                      boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
                      zIndex: 60,
                      padding: 8,
                    }}
                  >
                    <div style={{ position: 'relative', marginBottom: 8 }}>
                      <input
                        type="text"
                        placeholder="Search constraint catalogue..."
                        value={constraintSearchQuery}
                        onChange={(e) => setConstraintSearchQuery(e.target.value)}
                        style={{
                          width: '100%',
                          padding: '6px 28px 6px 8px',
                          fontSize: 12,
                          borderRadius: 6,
                          border: '1px solid var(--border)',
                          background: 'var(--surface-2)',
                          color: 'var(--text)',
                        }}
                        autoFocus
                      />
                      <SearchIcon
                        size={13}
                        style={{
                          position: 'absolute',
                          right: 8,
                          top: '50%',
                          transform: 'translateY(-50%)',
                          color: 'var(--text-2)',
                        }}
                      />
                    </div>

                    {availableCatalogue.length === 0 ? (
                      <div style={{ padding: 12, textAlign: 'center', fontSize: 12, color: 'var(--text-2)' }}>
                        No matching constraints or all added
                      </div>
                    ) : (
                      CONSTRAINT_GROUPS.map((group) => {
                        const items = availableCatalogue.filter((c) => c.group === group);
                        if (items.length === 0) return null;
                        return (
                          <div key={group} style={{ marginBottom: 8 }}>
                            <div
                              style={{
                                fontSize: 10.5,
                                fontWeight: 700,
                                textTransform: 'uppercase',
                                letterSpacing: '0.04em',
                                color: 'var(--text-2)',
                                padding: '4px 6px',
                              }}
                            >
                              {group}
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                              {items.map((item) => (
                                <button
                                  key={item.id}
                                  type="button"
                                  onClick={() => addConstraint(item.id)}
                                  style={{
                                    textAlign: 'left',
                                    padding: '6px 8px',
                                    borderRadius: 5,
                                    border: 'none',
                                    backgroundColor: 'transparent',
                                    color: 'var(--text)',
                                    fontSize: 12.5,
                                    cursor: 'pointer',
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'space-between',
                                  }}
                                  onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--surface-2)')}
                                  onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                                >
                                  <div>
                                    <div style={{ fontWeight: 500 }}>{item.label}</div>
                                    <div style={{ fontSize: 10.5, color: 'var(--text-2)' }}>
                                      {item.allowed_values.join('/')} {item.unit}
                                    </div>
                                  </div>
                                  <Plus size={13} color="var(--primary)" />
                                </button>
                              ))}
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                )}
              </div>

              {/* HITL Action Controls: Re-evaluate, Undo, Reset */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <button
                  type="button"
                  className="btn-primary"
                  style={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 6,
                  }}
                  onClick={() => handleSearch(null, true)}
                  disabled={loading || totalExtraWeightPct > 50}
                >
                  <RefreshCw size={14} className={loading ? 'spin' : ''} />
                  <span>{loading ? 'Re-evaluating...' : 'Re-evaluate'}</span>
                </button>

                <div style={{ display: 'flex', gap: 6 }}>
                  <button
                    type="button"
                    className="btn-secondary"
                    style={{ flex: 1, fontSize: 11.5, padding: '5px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}
                    onClick={handleUndo}
                    disabled={constraintHistory.length === 0}
                    title="Undo last constraint modification"
                  >
                    <Undo2 size={13} />
                    <span>Undo</span>
                  </button>

                  <button
                    type="button"
                    className="btn-secondary"
                    style={{ flex: 1, fontSize: 11.5, padding: '5px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}
                    onClick={handleResetConstraints}
                    disabled={extraConstraints.length === 0}
                    title="Clear all extra constraints"
                  >
                    <RotateCcw size={13} />
                    <span>Reset</span>
                  </button>
                </div>
              </div>
            </div>

            {/* Base Search / Reset Defaults */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingTop: 12, borderTop: '1px solid var(--border)' }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={handleLoadDefault}
                style={{ fontSize: 11.5, padding: '4px 8px' }}
                title="Reset to 50 MWp, Default Priority, 10 Sites"
              >
                <RotateCcw size={12} strokeWidth={1.5} />
                <span>Reset Defaults</span>
              </button>

              <button
                type="button"
                className="btn-secondary"
                onClick={() => setViewMode(viewMode === 'reference' ? 'search' : 'reference')}
                style={{ fontSize: 11.5, padding: '4px 8px' }}
              >
                <Layers size={12} strokeWidth={1.5} />
                <span>{viewMode === 'reference' ? 'Back to Search' : 'Reference 12'}</span>
              </button>
            </div>
          </div>
        </aside>

        {/* Right Main Column: Karnataka Map Card */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            className="card"
            style={{
              padding: 0,
              overflow: 'hidden',
              borderRadius: 12,
              border: '1px solid var(--border)',
            }}
          >
            <MapView
              candidates={activeCandidates}
              highlightedId={highlightedId}
              onSelectCandidate={setHighlightedId}
              districtGeoJSON={districtGeoJSON}
              selectedDistricts={selectedDistricts}
              height={510}
            />
          </div>
        </div>
      </div>

      {/* ── Banner: Change Feedback After Re-evaluation ─────────────────── */}
      {changeBanner && changeBanner.visible && (
        <div
          className="card"
          style={{
            marginTop: 20,
            padding: '14px 18px',
            borderRadius: 10,
            borderLeft: '4px solid var(--primary)',
            backgroundColor: 'var(--surface)',
            border: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div
              style={{
                width: 34,
                height: 34,
                borderRadius: '50%',
                backgroundColor: 'rgba(31, 92, 69, 0.1)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--primary)',
                flexShrink: 0,
              }}
            >
              <TrendingUp size={18} />
            </div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text)' }}>
                Re-evaluation Summary
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 2 }}>
                <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{changeBanner.addedCount} new sites</span> entered shortlist,{' '}
                <span style={{ color: 'var(--review)', fontWeight: 600 }}>{changeBanner.removedCount} removed</span>, and{' '}
                <span style={{ color: 'var(--primary)', fontWeight: 600 }}>{changeBanner.movedUpCount} moved up</span> in ranking. (Total: {changeBanner.totalNow} shortlisted)
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setChangeBanner(null)}
            style={{ background: 'transparent', border: 'none', color: 'var(--text-2)', cursor: 'pointer' }}
          >
            <X size={16} />
          </button>
        </div>
      )}

      {/* ── 4. Screening Funnel ────────────────────────────────────────── */}
      {funnel && viewMode === 'search' && (
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
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
            <h3 className="section-title" style={{ fontSize: 16 }}>
              Screening Funnel
            </h3>
            <span className="caption-text">
              {sizeMwp} MWp footprint ({footprint.radiusKm} km radius)
            </span>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
              gap: 14,
            }}
          >
            <div style={{ padding: '12px 14px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
              <div className="caption-text">Initial Centroids</div>
              <div className="stat-number tabular-nums" style={{ fontSize: 22, marginTop: 4 }}>
                {funnel.total_considered?.toLocaleString() || '3,266'}
              </div>
              <div className="caption-text" style={{ marginTop: 2 }}>Statewide grid</div>
            </div>

            <div style={{ padding: '12px 14px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
              <div className="caption-text">Base Screening</div>
              <div className="stat-number tabular-nums" style={{ fontSize: 22, marginTop: 4 }}>
                {funnel.base_survivors?.toLocaleString() || funnel.survivors?.toLocaleString() || '—'}
              </div>
              <div className="caption-text" style={{ marginTop: 2 }}>Slope, Land-cover, Reserves</div>
            </div>

            {funnel.must_constraints && funnel.must_constraints.length > 0 && (
              <div style={{ padding: '12px 14px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                <div className="caption-text">Must Constraints</div>
                <div className="stat-number tabular-nums" style={{ fontSize: 22, marginTop: 4, color: 'var(--review)' }}>
                  -{funnel.filtered_by_must?.toLocaleString() || 0}
                </div>
                <div className="caption-text" style={{ marginTop: 2 }}>
                  {funnel.must_constraints.length} hard filter{funnel.must_constraints.length > 1 ? 's' : ''} applied
                </div>
              </div>
            )}

            <div style={{ padding: '12px 14px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
              <div className="caption-text">Passed Constraints</div>
              <div className="stat-number tabular-nums" style={{ fontSize: 22, marginTop: 4, color: 'var(--primary)' }}>
                {funnel.survivors?.toLocaleString() || '—'}
              </div>
              <div className="caption-text" style={{ marginTop: 2 }}>Ready for scoring</div>
            </div>

            <div style={{ padding: '12px 14px', borderRadius: 8, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
              <div className="caption-text">Shortlisted Sites</div>
              <div className="stat-number tabular-nums" style={{ fontSize: 22, marginTop: 4, color: 'var(--accent)' }}>
                {activeCandidates.length}
              </div>
              <div className="caption-text" style={{ marginTop: 2 }}>≥20 km spacing isolation</div>
            </div>
          </div>

          {/* Breakdown for individual Must constraints */}
          {funnel.must_constraints && funnel.must_constraints.length > 0 && (
            <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)', marginBottom: 6 }}>
                Must Funnel Breakdown:
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                {funnel.must_constraints.map((step, idx) => (
                  <div
                    key={idx}
                    style={{
                      fontSize: 12,
                      padding: '4px 10px',
                      borderRadius: 6,
                      background: 'var(--surface-2)',
                      border: '1px solid var(--border)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}
                  >
                    <span style={{ fontWeight: 500, color: 'var(--text)' }}>{step.label}</span>
                    <span style={{ color: 'var(--review)', fontWeight: 600 }}>
                      (-{step.removed})
                    </span>
                    <span style={{ color: 'var(--text-2)' }}>→ {step.remaining} left</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── 5. Ranked Table Card with Extra Constraint Columns ────────── */}
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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div>
            <h2 className="section-title">
              {viewMode === 'reference'
                ? 'Baseline Reference Candidates (Frozen 12)'
                : `Ranked Candidate Shortlist (${activeCandidates.length} Sites)`}
            </h2>
            <p className="section-subtitle">
              {viewMode === 'reference'
                ? 'Deterministic baseline candidate evaluation with provenance records.'
                : `Ranked by multi-criteria suitability with ≥20 km spatial spacing under ${sizeMwp} MWp footprint.`}
            </p>
          </div>
        </div>

        {activeCandidates.length === 0 ? (
          <div
            style={{
              padding: 40,
              textAlign: 'center',
              color: 'var(--text-2)',
            }}
          >
            <AlertTriangle size={28} color="var(--review)" strokeWidth={1.5} style={{ marginBottom: 8 }} />
            <div style={{ fontWeight: 600, color: 'var(--text)', fontSize: 15 }}>No Eligible Sites Found</div>
            <p style={{ marginTop: 4, fontSize: 13 }}>
              Your Must constraints or footprint size excluded all evaluated candidate locations.
            </p>
            <div style={{ marginTop: 14, display: 'flex', justifyContent: 'center', gap: 10 }}>
              <button type="button" className="btn-secondary" onClick={handleUndo} disabled={constraintHistory.length === 0}>
                Undo Last Change
              </button>
              <button type="button" className="btn-secondary" onClick={handleResetConstraints}>
                Clear Constraints
              </button>
            </div>
          </div>
        ) : (
          <div className="table-wrapper">
            <table className="apple-table" aria-label="Candidate Sites Table">
              <thead>
                <tr>
                  <th style={{ width: 60 }}>Rank</th>
                  <th>District</th>
                  <th>Coordinates</th>
                  <th>Status</th>
                  <th className="th-numeric">Score</th>
                  <th className="th-numeric">GHI (kWh/m²)</th>
                  <th className="th-numeric">Substation</th>
                  <th className="th-numeric">Voltage</th>

                  {/* Extra Columns for each active constraint */}
                  {extraConstraints.map((ec) => (
                    <th key={ec.id} className="th-numeric" style={{ minWidth: 120 }}>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
                        <span>{ec.label}</span>
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 600,
                            padding: '1px 5px',
                            borderRadius: 4,
                            backgroundColor: ec.mode === 'must' ? 'rgba(199, 125, 10, 0.15)' : 'rgba(31, 92, 69, 0.12)',
                            color: ec.mode === 'must' ? 'var(--review)' : 'var(--primary)',
                            textTransform: 'uppercase',
                          }}
                        >
                          {ec.mode === 'must' ? 'Must' : `Prefer ${ec.importance === 'high' ? '20%' : ec.importance === 'medium' ? '10%' : '5%'}`}
                        </span>
                      </div>
                    </th>
                  ))}

                  <th style={{ width: 80, textAlign: 'center' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {activeCandidates.map((cand) => {
                  const raw = cand.raw_criteria || {};
                  const ghiVal = raw.ghi_kwh_m2_day != null ? raw.ghi_kwh_m2_day.toFixed(2) : '—';
                  const subDist = raw.substation_dist_km != null ? `${raw.substation_dist_km.toFixed(1)} km` : '—';
                  const subDetails = cand.substation_details || raw.substation || {};
                  const subKv = subDetails.voltage_tag || '—';
                  const scoreVal = cand.score != null ? (cand.score * 100).toFixed(1) : '—';
                  const isHighlighted = cand.rank === highlightedId;
                  const candExtras = cand.extra_constraints || {};

                  return (
                    <tr
                      key={cand.rank ?? `${cand.latitude}_${cand.longitude}`}
                      className={isHighlighted ? 'row-highlighted' : ''}
                      onMouseEnter={() => setHighlightedId(cand.rank)}
                      onMouseLeave={() => setHighlightedId(null)}
                      onClick={() => navigate(`/candidates/${cand.rank}`, { state: { candidate: cand } })}
                      style={{ cursor: 'pointer' }}
                    >
                      <td className="tabular-nums" style={{ fontWeight: 600 }}>
                        #{cand.rank}
                      </td>
                      <td style={{ fontWeight: 500 }}>
                        {cand.district || 'Karnataka'}
                      </td>
                      <td className="tabular-nums" style={{ color: 'var(--text-2)', fontSize: 13 }}>
                        {cand.latitude.toFixed(4)}°N, {cand.longitude.toFixed(4)}°E
                      </td>
                      <td>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                          <span
                            className={`status-dot status-dot-${
                              cand.status === 'needs_review' || cand.needs_review ? 'review' : cand.status
                            }`}
                          />
                          <span style={{ textTransform: 'capitalize', fontSize: 13 }}>
                            {cand.status === 'needs_review' || cand.needs_review ? 'Needs Review' : cand.status}
                          </span>
                        </span>
                      </td>
                      <td className="th-numeric tabular-nums" style={{ fontWeight: 600, color: 'var(--text)' }}>
                        {scoreVal} / 100
                      </td>
                      <td className="th-numeric tabular-nums">
                        {ghiVal}
                      </td>
                      <td className="th-numeric tabular-nums">
                        {subDist}
                      </td>
                      <td className="th-numeric tabular-nums" style={{ color: 'var(--text-2)' }}>
                        {subKv}
                      </td>

                      {/* Extra constraint values per site */}
                      {extraConstraints.map((ec) => {
                        const cellData = candExtras[ec.id];
                        if (!cellData) {
                          return (
                            <td key={ec.id} className="th-numeric tabular-nums" style={{ color: 'var(--text-2)' }}>
                              —
                            </td>
                          );
                        }
                        const rawVal = cellData.raw_value;
                        const formattedRaw =
                          rawVal == null
                            ? '—'
                            : typeof rawVal === 'number'
                            ? ec.unit === '%' || ec.unit === '% cropland' || ec.unit === '% share'
                              ? `${(rawVal * 100).toFixed(1)}%`
                              : `${rawVal.toFixed(1)} ${ec.unit}`
                            : String(rawVal);

                        return (
                          <td key={ec.id} className="th-numeric tabular-nums">
                            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                              <span style={{ fontWeight: 500 }}>{formattedRaw}</span>
                              {cellData.mode === 'prefer' && cellData.contribution > 0 && (
                                <span style={{ fontSize: 11, color: 'var(--primary)', fontWeight: 600 }}>
                                  +{(cellData.contribution * 100).toFixed(1)}%
                                </span>
                              )}
                              {cellData.mode === 'must' && (
                                <span style={{ fontSize: 11, color: 'var(--eligible)', display: 'flex', alignItems: 'center', gap: 2 }}>
                                  <CheckCircle2 size={11} /> Pass
                                </span>
                              )}
                            </div>
                          </td>
                        );
                      })}

                      <td style={{ textAlign: 'center' }}>
                        <button
                          type="button"
                          className="btn-secondary"
                          style={{ padding: '2px 8px', fontSize: 12 }}
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/candidates/${cand.rank}`, { state: { candidate: cand } });
                          }}
                        >
                          View <ArrowRight size={12} strokeWidth={1.5} style={{ marginLeft: 2 }} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── 6. Collapsible Decision Log Panel ──────────────────────────── */}
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
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            cursor: 'pointer',
          }}
          onClick={() => setIsDecisionLogOpen(!isDecisionLogOpen)}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <FileText size={18} color="var(--primary)" />
            <div>
              <h3 className="section-title" style={{ fontSize: 16, margin: 0 }}>
                Decision Log
              </h3>
              <p className="caption-text" style={{ margin: 0, marginTop: 2 }}>
                Audit trail of human-in-the-loop parameter re-evaluations and constraint changes.
              </p>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span
              style={{
                fontSize: 12,
                fontWeight: 600,
                padding: '2px 8px',
                borderRadius: 12,
                backgroundColor: 'var(--surface-2)',
                border: '1px solid var(--border)',
                color: 'var(--text)',
              }}
            >
              {decisionLog.length} recorded
            </span>
            {isDecisionLogOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>
        </div>

        {isDecisionLogOpen && (
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
            {decisionLog.length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-2)', fontSize: 13 }}>
                No re-evaluations recorded yet. Click "Re-evaluate" to log a decision run.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {decisionLog.map((entry, idx) => {
                  const dateStr = entry.timestamp ? new Date(entry.timestamp).toLocaleString() : 'Recent';
                  const constraints = entry.constraints || [];
                  return (
                    <div
                      key={idx}
                      style={{
                        padding: '12px 14px',
                        borderRadius: 8,
                        backgroundColor: 'var(--surface-2)',
                        border: '1px solid var(--border)',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 6,
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>
                            {entry.reviewer_name || entry.reviewer || 'Anonymous Reviewer'}
                          </span>
                          <span
                            style={{
                              fontSize: 10.5,
                              textTransform: 'uppercase',
                              padding: '1px 6px',
                              borderRadius: 4,
                              backgroundColor: 'rgba(31, 92, 69, 0.12)',
                              color: 'var(--primary)',
                              fontWeight: 600,
                            }}
                          >
                            {entry.action}
                          </span>
                        </div>
                        <span style={{ fontSize: 11.5, color: 'var(--text-2)' }}>{dateStr}</span>
                      </div>

                      {/* Constraints details */}
                      <div style={{ fontSize: 12, color: 'var(--text-2)', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        <strong style={{ color: 'var(--text)' }}>Constraints:</strong>
                        {constraints.length === 0 ? (
                          <span>None (Defaults)</span>
                        ) : (
                          constraints.map((c, cIdx) => (
                            <span
                              key={cIdx}
                              style={{
                                padding: '1px 6px',
                                borderRadius: 4,
                                backgroundColor: 'var(--surface)',
                                border: '1px solid var(--border)',
                                fontSize: 11,
                                color: 'var(--text)',
                              }}
                            >
                              {c.id} ({c.mode}: {c.value}{c.importance ? `, ${c.importance}` : ''})
                            </span>
                          ))
                        )}
                      </div>

                      {/* Counts before/after */}
                      <div style={{ fontSize: 12, color: 'var(--text-2)', display: 'flex', gap: 16 }}>
                        <span>
                          Shortlisted: <strong>{entry.counts_before ?? '—'}</strong> → <strong>{entry.counts_after ?? entry.site_count ?? '—'}</strong>
                        </span>
                        {entry.survivors_before != null && (
                          <span>
                            Survivors: <strong>{entry.survivors_before}</strong> → <strong>{entry.survivors_after}</strong>
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
