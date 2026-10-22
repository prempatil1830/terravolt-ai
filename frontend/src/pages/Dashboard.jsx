import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Sliders,
  Search,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  Plus,
  Trash2,
  RefreshCw,
  Sun,
  Zap,
  MapPin,
  CheckCircle2,
  ExternalLink,
  Layers,
  Sparkles,
} from 'lucide-react';

const PRECOMPUTED_RADII = [0.25, 0.40, 0.54, 0.70, 0.85, 1.00, 1.10];

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

export default function Dashboard({ candidatesData = null }) {
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
  const [topN, setTopN] = useState(12);

  // Feature 5: Human-in-the-loop: Constraints Catalogue & Selection
  const [catalogue, setCatalogue] = useState([]);
  const [extraConstraints, setExtraConstraints] = useState([]);
  const [reviewerName, setReviewerName] = useState('Operator');
  const [isConstraintDropdownOpen, setIsConstraintDropdownOpen] = useState(false);
  const [constraintSearchQuery, setConstraintSearchQuery] = useState('');

  // Status & Navigation state
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);

  // Load district names
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

  // Live footprint calculation
  const footprint = useMemo(() => computeFootprint(sizeMwp), [sizeMwp]);
  const sliderFillPct = Math.round(((sizeMwp - 5) / (200 - 5)) * 100);

  // Filtered districts for search
  const filteredDistricts = useMemo(() => {
    return districtList.filter((d) =>
      d.name.toLowerCase().includes(districtSearchQuery.toLowerCase())
    );
  }, [districtList, districtSearchQuery]);

  const toggleDistrict = (name) => {
    setSelectedDistricts((prev) =>
      prev.includes(name) ? prev.filter((d) => d !== name) : [...prev, name]
    );
  };

  // Extra prefer weights
  const totalExtraWeightPct = useMemo(() => {
    return extraConstraints
      .filter((c) => c.mode === 'prefer')
      .reduce((sum, c) => {
        const w = c.importance === 'high' ? 20 : c.importance === 'medium' ? 10 : 5;
        return sum + w;
      }, 0);
  }, [extraConstraints]);

  // Spatial conflict check
  const hasTownLandCostConflict = useMemo(() => {
    const ids = extraConstraints.map((c) => c.id);
    return ids.includes('close_to_town') && ids.includes('lower_land_cost');
  }, [extraConstraints]);

  // Available constraints to add
  const availableCatalogue = useMemo(() => {
    return catalogue.filter(
      (c) =>
        !extraConstraints.some((added) => added.id === c.id) &&
        (c.label.toLowerCase().includes(constraintSearchQuery.toLowerCase()) ||
          c.group.toLowerCase().includes(constraintSearchQuery.toLowerCase()))
    );
  }, [catalogue, extraConstraints, constraintSearchQuery]);

  const addConstraint = (item) => {
    const defaultVal = item.allowed_values ? item.allowed_values[0] : 0;
    const defaultMode = item.allowed_modes?.includes('prefer') ? 'prefer' : 'must';
    setExtraConstraints((prev) => [
      ...prev,
      {
        id: item.id,
        label: item.label,
        group: item.group,
        allowed_values: item.allowed_values || [],
        allowed_modes: item.allowed_modes || ['must', 'prefer'],
        value: defaultVal,
        mode: defaultMode,
        importance: 'medium',
      },
    ]);
    setIsConstraintDropdownOpen(false);
    setConstraintSearchQuery('');
  };

  const removeConstraint = (id) => {
    setExtraConstraints((prev) => prev.filter((c) => c.id !== id));
  };

  const updateConstraint = (id, updates) => {
    setExtraConstraints((prev) =>
      prev.map((c) => (c.id === id ? { ...c, ...updates } : c))
    );
  };

  // Execute Search and Redirect to Results Page
  const handleSearchSubmit = async (e) => {
    if (e) e.preventDefault();

    if (totalExtraWeightPct > 50) {
      setSearchError(
        `Total extra prefer weights exceed 50% limit (${totalExtraWeightPct}%). Please reduce before running search.`
      );
      return;
    }

    setSearching(true);
    setSearchError(null);

    const formattedExtras = extraConstraints.map((c) => ({
      id: c.id,
      value: Number(c.value),
      mode: c.mode,
      importance: c.mode === 'prefer' ? c.importance || 'medium' : undefined,
    }));

    const params = {
      districts: selectedDistricts.length === 0 ? 'all' : selectedDistricts,
      size_mwp: Number(sizeMwp),
      priority,
      top_n: Number(topN),
      min_kv: 33,
      max_slope_pct: 5,
      avoid_cropland: false,
      pa_buffer_km: 10.0,
      extra_constraints: formattedExtras,
      reviewer_name: reviewerName || 'Operator',
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

      // Redirect to the dedicated Results page with search results data
      navigate('/results', {
        state: {
          candidatesData: data,
          searchResult: data,
          searchParams: params,
        },
      });
    } catch (err) {
      console.error('Search failed:', err);
      setSearchError(err.message || 'Search execution failed');
      setSearching(false);
    }
  };

  const handleQuickBaseline = () => {
    navigate('/results', {
      state: {
        candidatesData: candidatesData,
      },
    });
  };

  return (
    <div className="content-wrapper page-fade-in" style={{ paddingBottom: 64 }}>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          flexWrap: 'wrap',
          gap: 16,
          marginBottom: 24,
        }}
      >
        <div>
          <h1 className="page-title">Terravolt AI Dashboard</h1>
          <p className="page-subtitle">
            Configure siting parameters, select regional districts, and apply human-in-the-loop constraints to screen utility-scale solar candidate sites across Karnataka.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleQuickBaseline}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 13,
              padding: '8px 16px',
            }}
          >
            <span>View Baseline 12 Sites</span>
            <ArrowRight size={14} />
          </button>
        </div>
      </div>

      {searchError && (
        <div
          className="card"
          style={{
            marginBottom: 24,
            borderLeft: '4px solid var(--excluded)',
            background: 'var(--surface)',
            padding: '12px 16px',
            fontSize: 13,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <AlertTriangle size={18} color="var(--excluded)" style={{ flexShrink: 0 }} />
          <div>
            <strong style={{ color: 'var(--text)' }}>Search notice: </strong>
            <span>{searchError}</span>
          </div>
        </div>
      )}

      {/* Main Search Configuration Card */}
      <form onSubmit={handleSearchSubmit}>
        <div
          className="card"
          style={{
            padding: 24,
            backgroundColor: 'var(--surface)',
            borderRadius: 16,
            border: '1px solid var(--border)',
            boxShadow: 'var(--card-shadow)',
          }}
        >
          {/* Top Row: Section Title */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 20,
              paddingBottom: 16,
              borderBottom: '1px solid var(--border)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <Sliders size={20} color="var(--primary)" />
              <h2 className="section-title" style={{ margin: 0, fontSize: 18 }}>
                Siting Screening Parameters
              </h2>
            </div>
            <span className="caption-text" style={{ fontSize: 12 }}>
              3,266 Precomputed Evaluation Points
            </span>
          </div>

          {/* Grid Layout for Core Parameters */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
              gap: 24,
              marginBottom: 24,
            }}
          >
            {/* Feature 1: Region / Districts Multiselect */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
                <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                  1. Target Districts
                </label>
                {selectedDistricts.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setSelectedDistricts([])}
                    style={{
                      background: 'none',
                      border: 'none',
                      color: 'var(--primary)',
                      fontSize: 11,
                      cursor: 'pointer',
                      padding: 0,
                    }}
                  >
                    Clear All
                  </button>
                )}
              </div>

              <div style={{ position: 'relative' }}>
                <button
                  type="button"
                  style={{
                    width: '100%',
                    padding: '9px 12px',
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
                      : `${selectedDistricts.length} Selected: ${selectedDistricts.slice(0, 2).join(', ')}${
                          selectedDistricts.length > 2 ? '...' : ''
                        }`}
                  </span>
                  {isDistrictDropdownOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                </button>

                {isDistrictDropdownOpen && (
                  <div
                    style={{
                      position: 'absolute',
                      top: 'calc(100% + 4px)',
                      left: 0,
                      right: 0,
                      maxHeight: 240,
                      overflowY: 'auto',
                      backgroundColor: 'var(--surface)',
                      border: '1px solid var(--border)',
                      borderRadius: 8,
                      boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
                      zIndex: 60,
                      padding: 10,
                    }}
                  >
                    <input
                      type="text"
                      placeholder="Filter districts..."
                      value={districtSearchQuery}
                      onChange={(e) => setDistrictSearchQuery(e.target.value)}
                      style={{
                        width: '100%',
                        padding: '6px 10px',
                        fontSize: 12,
                        borderRadius: 6,
                        border: '1px solid var(--border)',
                        background: 'var(--surface-2)',
                        color: 'var(--text)',
                        marginBottom: 8,
                      }}
                    />
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {filteredDistricts.map((d) => {
                        const checked = selectedDistricts.includes(d.name);
                        return (
                          <label
                            key={d.name}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: 8,
                              fontSize: 12.5,
                              padding: '5px 8px',
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
              <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 4 }}>
                Leave empty to evaluate all 31 districts across Karnataka.
              </div>
            </div>

            {/* Feature 2: Target Plant Size (MWp) */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
                <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                  2. Plant Size (MWp)
                </label>
                <span className="tabular-nums" style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
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
                  width: '100%',
                }}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-2)' }}>
                <span>{footprint.acres} acres ({footprint.radiusKm} km radius)</span>
                <span className="tabular-nums">5 – 200 MWp</span>
              </div>
            </div>

            {/* Feature 3: Siting Priority Objective */}
            <div>
              <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 6 }}>
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
                      onClick={() => {
                        setPriority(item.id);
                        if (searchError) setSearchError(null);
                      }}
                      style={{ textAlign: 'center', padding: '6px 4px', fontSize: 12 }}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 4 }}>
                Adjusts MCDM weight balance across GHI and substation distance.
              </div>
            </div>

            {/* Feature 4: Candidates Count */}
            <div>
              <label style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', display: 'block', marginBottom: 6 }}>
                4. Candidates to Shortlist
              </label>
              <div className="segmented-control" style={{ width: '100%', display: 'flex' }}>
                {[5, 10, 12, 15, 20].map((n) => (
                  <button
                    key={n}
                    type="button"
                    className={`segmented-option ${topN === n ? 'active' : ''}`}
                    style={{ flex: 1, textAlign: 'center', padding: '6px 2px', fontSize: 12 }}
                    onClick={() => setTopN(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 4 }}>
                Top N ranked candidate sites to return in final dossier.
              </div>
            </div>
          </div>

          {/* Feature 5: Human-in-the-Loop Constraint Refinement */}
          <div
            style={{
              paddingTop: 20,
              borderTop: '1px solid var(--border)',
              marginBottom: 24,
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 12,
                marginBottom: 14,
              }}
            >
              <div>
                <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: 'var(--text)' }}>
                  Your Constraints (Human-in-the-Loop Refinement)
                </h3>
                <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--text-2)' }}>
                  Add soft preferences (+score) or hard screening gates (&lt;threshold) using real spatial layers.
                </p>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {extraConstraints.length > 0 && (
                  <span
                    style={{
                      fontSize: 11.5,
                      fontWeight: 600,
                      color: totalExtraWeightPct > 50 ? 'var(--excluded)' : 'var(--primary)',
                      background: 'var(--surface-2)',
                      padding: '4px 10px',
                      borderRadius: 999,
                      border: '1px solid var(--border)',
                    }}
                  >
                    Extra Weight: {totalExtraWeightPct}% / 50% max
                  </span>
                )}

                {/* Add Constraint Dropdown */}
                <div style={{ position: 'relative' }}>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setIsConstraintDropdownOpen(!isConstraintDropdownOpen)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 6,
                      fontSize: 12.5,
                      padding: '6px 12px',
                    }}
                  >
                    <Plus size={14} />
                    <span>Add Constraint</span>
                  </button>

                  {isConstraintDropdownOpen && (
                    <div
                      style={{
                        position: 'absolute',
                        top: 'calc(100% + 6px)',
                        right: 0,
                        width: 320,
                        maxHeight: 300,
                        overflowY: 'auto',
                        backgroundColor: 'var(--surface)',
                        border: '1px solid var(--border)',
                        borderRadius: 8,
                        boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
                        zIndex: 70,
                        padding: 10,
                      }}
                    >
                      <input
                        type="text"
                        placeholder="Search criteria (e.g. slope, road, sun)..."
                        value={constraintSearchQuery}
                        onChange={(e) => setConstraintSearchQuery(e.target.value)}
                        style={{
                          width: '100%',
                          padding: '6px 8px',
                          fontSize: 12,
                          borderRadius: 6,
                          border: '1px solid var(--border)',
                          background: 'var(--surface-2)',
                          color: 'var(--text)',
                          marginBottom: 8,
                        }}
                      />
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {availableCatalogue.map((item) => (
                          <button
                            key={item.id}
                            type="button"
                            onClick={() => addConstraint(item)}
                            style={{
                              textAlign: 'left',
                              padding: '6px 8px',
                              borderRadius: 6,
                              border: 'none',
                              background: 'transparent',
                              cursor: 'pointer',
                              display: 'flex',
                              flexDirection: 'column',
                              transition: 'background 0.1s ease',
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--surface-2)')}
                            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
                          >
                            <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
                              {item.label}
                            </span>
                            <span style={{ fontSize: 10.5, color: 'var(--text-2)' }}>
                              {item.group}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Spatial Conflict Warning */}
            {hasTownLandCostConflict && (
              <div
                style={{
                  padding: '8px 12px',
                  borderRadius: 8,
                  backgroundColor: 'rgba(199, 125, 10, 0.1)',
                  border: '1px solid rgba(199, 125, 10, 0.3)',
                  fontSize: 12,
                  color: 'var(--text)',
                  marginBottom: 14,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <AlertTriangle size={16} color="#C77D0A" />
                <span>
                  <strong>Spatial Notice:</strong> "Close to city/town" and "Lower land cost" exert opposing geographic pulls.
                </span>
              </div>
            )}

            {/* Active Constraints List */}
            {extraConstraints.length === 0 ? (
              <div
                style={{
                  padding: '16px',
                  borderRadius: 8,
                  background: 'var(--surface-2)',
                  textAlign: 'center',
                  fontSize: 12.5,
                  color: 'var(--text-2)',
                }}
              >
                No extra constraints configured. Standard multi-criteria baseline (GHI + Substation) will be applied. Click <strong>+ Add Constraint</strong> to refine.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {extraConstraints.map((c) => (
                  <div
                    key={c.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      flexWrap: 'wrap',
                      gap: 12,
                      padding: '10px 14px',
                      borderRadius: 8,
                      border: '1px solid var(--border)',
                      background: 'var(--surface-2)',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>
                        {c.label}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-2)' }}>{c.group}</div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      {/* Mode toggle: Prefer vs Must */}
                      {c.allowed_modes?.length > 1 && (
                        <div className="segmented-control" style={{ padding: 2 }}>
                          <button
                            type="button"
                            className={`segmented-option ${c.mode === 'prefer' ? 'active' : ''}`}
                            onClick={() => updateConstraint(c.id, { mode: 'prefer' })}
                            style={{ padding: '3px 8px', fontSize: 11 }}
                          >
                            Prefer
                          </button>
                          <button
                            type="button"
                            className={`segmented-option ${c.mode === 'must' ? 'active' : ''}`}
                            onClick={() => updateConstraint(c.id, { mode: 'must' })}
                            style={{ padding: '3px 8px', fontSize: 11 }}
                          >
                            Must
                          </button>
                        </div>
                      )}

                      {/* Threshold value select */}
                      {c.allowed_values?.length > 0 && (
                        <select
                          value={c.value}
                          onChange={(e) => updateConstraint(c.id, { value: Number(e.target.value) })}
                          style={{
                            padding: '4px 8px',
                            fontSize: 12,
                            borderRadius: 6,
                            border: '1px solid var(--border)',
                            background: 'var(--surface)',
                            color: 'var(--text)',
                          }}
                        >
                          {c.allowed_values.map((v) => (
                            <option key={v} value={v}>
                              {v} {c.id.includes('km') || c.id.includes('dist') || c.id.includes('buffer') ? 'km' : c.id.includes('kv') ? 'kV' : ''}
                            </option>
                          ))}
                        </select>
                      )}

                      {/* Importance if mode is prefer */}
                      {c.mode === 'prefer' && (
                        <select
                          value={c.importance || 'medium'}
                          onChange={(e) => updateConstraint(c.id, { importance: e.target.value })}
                          style={{
                            padding: '4px 8px',
                            fontSize: 12,
                            borderRadius: 6,
                            border: '1px solid var(--border)',
                            background: 'var(--surface)',
                            color: 'var(--text)',
                          }}
                        >
                          <option value="high">High (+20%)</option>
                          <option value="medium">Medium (+10%)</option>
                          <option value="low">Low (+5%)</option>
                        </select>
                      )}

                      <button
                        type="button"
                        onClick={() => removeConstraint(c.id)}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: 'var(--excluded)',
                          cursor: 'pointer',
                          padding: 4,
                          display: 'flex',
                          alignItems: 'center',
                        }}
                        title="Remove constraint"
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Action Row */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 16,
              paddingTop: 16,
              borderTop: '1px solid var(--border)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--text-2)' }}>
              <span>Operator:</span>
              <input
                type="text"
                value={reviewerName}
                onChange={(e) => setReviewerName(e.target.value)}
                placeholder="Operator name"
                style={{
                  padding: '4px 8px',
                  borderRadius: 4,
                  border: '1px solid var(--border)',
                  background: 'var(--surface-2)',
                  color: 'var(--text)',
                  fontSize: 12,
                  width: 120,
                }}
              />
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <button
                type="submit"
                disabled={searching}
                className="btn-primary"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '10px 24px',
                  fontSize: 14,
                  fontWeight: 600,
                }}
              >
                {searching ? (
                  <>
                    <RefreshCw size={16} className="spin-animation" />
                    <span>Evaluating 3,266 Sites...</span>
                  </>
                ) : (
                  <>
                    <Search size={16} />
                    <span>Screen & Rank Candidates</span>
                    <ArrowRight size={15} />
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </form>
    </div>
  );
}
