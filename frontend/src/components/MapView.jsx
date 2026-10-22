import React, { useEffect, useState, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  MapContainer,
  TileLayer,
  Marker,
  Tooltip,
  GeoJSON,
  useMap,
} from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useTheme } from '../utils/useDarkMode';

// Exact Karnataka bounding box
const KARNATAKA_BOUNDS = [
  [11.5946, 74.0857],
  [18.4588, 78.5877],
];

// Karnataka bounds plus 0.3 degrees padding
const KARNATAKA_MAX_BOUNDS = [
  [11.5946 - 0.3, 74.0857 - 0.3],
  [18.4588 + 0.3, 78.5877 + 0.3],
];

// World bounding rectangle for inverse mask
const WORLD_RING = [
  [-180, -90],
  [180, -90],
  [180, 90],
  [-180, 90],
  [-180, -90],
];

// In-memory module caches so maps across pages load instantly without refetching
let _cachedBoundaryGeoJSON = null;
let _cachedDistrictsList = null;

/**
 * Controller to enforce Karnataka-only bounds, maxBounds, viscosity, minZoom,
 * and track zoom levels for marker scaling.
 */
function MapController({
  highlightedCandidate,
  onZoomChange,
  onFittedZoom,
}) {
  const map = useMap();
  const initializedRef = useRef(false);

  useEffect(() => {
    // Custom mask pane: above tiles (200), below overlay (400) and markers (600)
    if (!map.getPane('maskPane')) {
      const pane = map.createPane('maskPane');
      pane.style.zIndex = '350';
      pane.style.pointerEvents = 'none';
    }

    // Set max bounds, viscosity, zoomSnap and zoomDelta
    map.setMaxBounds(KARNATAKA_MAX_BOUNDS);
    map.options.maxBoundsViscosity = 1.0;
    map.options.zoomSnap = 0.25;
    map.options.zoomDelta = 0.5;

    if (!initializedRef.current) {
      // fitBounds with 20 px padding so Karnataka fills its card
      map.fitBounds(KARNATAKA_BOUNDS, { padding: [20, 20] });
      const fittedZoom = map.getZoom();
      map.setMinZoom(fittedZoom);
      if (onFittedZoom) onFittedZoom(fittedZoom);
      if (onZoomChange) onZoomChange(fittedZoom);
      initializedRef.current = true;
    }
  }, [map, onZoomChange, onFittedZoom]);

  useEffect(() => {
    const handleZoom = () => {
      if (onZoomChange) onZoomChange(map.getZoom());
    };
    map.on('zoomend', handleZoom);
    return () => {
      map.off('zoomend', handleZoom);
    };
  }, [map, onZoomChange]);

  useEffect(() => {
    if (highlightedCandidate && highlightedCandidate.latitude && highlightedCandidate.longitude) {
      map.flyTo([highlightedCandidate.latitude, highlightedCandidate.longitude], Math.max(map.getZoom(), 8), {
        duration: 0.35,
      });
    }
  }, [map, highlightedCandidate]);

  return null;
}

/**
 * Renders numbered markers with dynamic zoom scaling (22px at fitted zoom, 28px at higher zooms)
 * and overlap avoidance so no marker is fully hidden.
 */
function CandidateMarkers({
  candidates = [],
  selectedId,
  highlightedId,
  onSelectCandidate,
  markerSize = 22,
}) {
  const map = useMap();
  const navigate = useNavigate();

  // Compute collision offsets at current zoom level so overlapping markers remain fully visible
  const markerOffsets = useMemo(() => {
    if (!candidates || candidates.length === 0) return new Map();

    const points = candidates
      .filter((c) => c && c.latitude && c.longitude)
      .map((c) => {
        const pt = map.latLngToLayerPoint([c.latitude, c.longitude]);
        return { rank: c.rank, x: pt.x, y: pt.y };
      });

    // Detect collision clusters where centers are closer than markerSize * 0.9
    const collisionThreshold = markerSize * 0.9;
    const visited = new Set();
    const offsets = new Map();

    for (let i = 0; i < points.length; i++) {
      if (visited.has(points[i].rank)) continue;
      const cluster = [points[i]];
      visited.add(points[i].rank);

      for (let j = i + 1; j < points.length; j++) {
        if (visited.has(points[j].rank)) continue;
        const dist = Math.hypot(points[i].x - points[j].x, points[i].y - points[j].y);
        if (dist < collisionThreshold) {
          cluster.push(points[j]);
          visited.add(points[j].rank);
        }
      }

      if (cluster.length === 1) {
        offsets.set(cluster[0].rank, { x: 0, y: 0 });
      } else {
        const k = cluster.length;
        // Radial spread offset so all circles and rank numbers remain distinctly visible
        const spreadRadius = markerSize * 0.55;
        cluster.forEach((item, idx) => {
          const angle = (2 * Math.PI * idx) / k - Math.PI / 2;
          offsets.set(item.rank, {
            x: Math.round(spreadRadius * Math.cos(angle)),
            y: Math.round(spreadRadius * Math.sin(angle)),
          });
        });
      }
    }

    return offsets;
  }, [candidates, map, markerSize]);

  const createMarkerIcon = (candidate, offset) => {
    const isSelected = candidate.rank === selectedId;
    const isHighlighted = candidate.rank === highlightedId;
    const status = candidate.status;

    let bgColor = '#1F5C45';
    if (status === 'needs_review' || candidate.needs_review) {
      bgColor = '#C77D0A';
    } else if (status === 'excluded') {
      bgColor = '#B5452F';
    }

    const scale = isSelected || isHighlighted ? 'scale(1.15)' : 'scale(1)';
    const rankLabel = candidate.rank != null ? candidate.rank : '';
    const fontSize = markerSize <= 22 ? 10.5 : 12;

    const html = `
      <div style="
        width: ${markerSize}px;
        height: ${markerSize}px;
        border-radius: 50%;
        background-color: ${bgColor};
        color: #FFFFFF;
        box-shadow: 0 0 0 2px #FFFFFF, 0 2px 5px rgba(0,0,0,0.28);
        display: flex;
        align-items: center;
        justify-content: center;
        font-family: 'Inter', -apple-system, sans-serif;
        font-size: ${fontSize}px;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
        transition: transform 120ms ease-out;
        cursor: pointer;
        user-select: none;
        transform: translate(${offset.x}px, ${offset.y}px) ${scale};
      ">
        ${rankLabel}
      </div>
    `;

    return L.divIcon({
      className: 'karnataka-numbered-marker',
      html,
      iconSize: [markerSize, markerSize],
      iconAnchor: [markerSize / 2, markerSize / 2],
      popupAnchor: [offset.x, -markerSize / 2 + offset.y],
    });
  };

  const handleMarkerClick = (rank) => {
    const found = candidates.find((c) => c.rank === rank);
    if (onSelectCandidate) {
      onSelectCandidate(rank);
    } else {
      navigate(`/candidates/${rank}`, { state: { candidate: found } });
    }
  };

  return (
    <>
      {candidates.map((cand) => {
        if (!cand.latitude || !cand.longitude) return null;
        const offset = markerOffsets.get(cand.rank) || { x: 0, y: 0 };
        const icon = createMarkerIcon(cand, offset);
        const scoreDisplay =
          cand.score != null ? `${(cand.score * 100).toFixed(1)} / 100` : 'Excluded';
        const districtName = cand.district || 'Karnataka';

        return (
          <Marker
            key={cand.rank ?? `${cand.latitude}_${cand.longitude}`}
            position={[cand.latitude, cand.longitude]}
            icon={icon}
            zIndexOffset={
              cand.rank === highlightedId || cand.rank === selectedId
                ? 3000
                : 1000 - (cand.rank || 0) * 5
            }
            eventHandlers={{
              click: () => handleMarkerClick(cand.rank),
            }}
          >
            <Tooltip
              direction="top"
              offset={[offset.x, -markerSize / 2 - 2 + offset.y]}
              opacity={1}
              className="karnataka-map-tooltip"
            >
              <div style={{ padding: '2px 4px', fontSize: 12, lineHeight: 1.35 }}>
                <div style={{ fontWeight: 600, color: 'var(--text)' }}>
                  Site #{cand.rank} · {districtName}
                </div>
                <div style={{ color: 'var(--text-2)', marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>
                  Score: <strong>{scoreDisplay}</strong>
                </div>
              </div>
            </Tooltip>
          </Marker>
        );
      })}
    </>
  );
}

export default function MapView({
  candidates = [],
  selectedId = null,
  highlightedId = null,
  onSelectCandidate = null,
  districtGeoJSON = null,
  selectedDistricts = [],
  height = 460,
}) {
  const { isDark } = useTheme();

  const [boundaryData, setBoundaryData] = useState(_cachedBoundaryGeoJSON);
  const [districtsData, setDistrictsData] = useState(_cachedDistrictsList);
  const [currentZoom, setCurrentZoom] = useState(null);
  const [fittedZoom, setFittedZoom] = useState(null);

  // Fetch simplified Karnataka boundary if not yet cached
  useEffect(() => {
    let ignore = false;
    if (_cachedBoundaryGeoJSON) {
      return;
    }

    async function loadBoundary() {
      try {
        const res = await fetch('/api/boundary');
        if (res.ok) {
          const data = await res.json();
          _cachedBoundaryGeoJSON = data;
          if (!ignore) setBoundaryData(data);
        } else {
          const fallback = await fetch('/karnataka_boundary.json');
          if (fallback.ok) {
            const data = await fallback.json();
            _cachedBoundaryGeoJSON = data;
            if (!ignore) setBoundaryData(data);
          }
        }
      } catch (err) {
        console.error('Error fetching Karnataka boundary:', err);
      }
    }

    loadBoundary();
    return () => {
      ignore = true;
    };
  }, []);

  // Fetch districts for boundaries and tooltips
  useEffect(() => {
    let ignore = false;
    if (_cachedDistrictsList) {
      return;
    }

    async function loadDistricts() {
      try {
        const res = await fetch('/api/districts');
        if (res.ok) {
          const data = await res.json();
          _cachedDistrictsList = data.districts || [];
          if (!ignore) setDistrictsData(_cachedDistrictsList);
        }
      } catch (err) {
        console.error('Error fetching districts:', err);
      }
    }

    loadDistricts();
    return () => {
      ignore = true;
    };
  }, []);

  const highlightedCandidate = candidates.find((c) => c.rank === highlightedId) || null;

  // Mask GeoJSON: Polygon covering the world with a hole shaped like Karnataka
  const maskGeoJSON = useMemo(() => {
    if (!boundaryData || !boundaryData.features || boundaryData.features.length === 0) {
      return null;
    }
    const feat = boundaryData.features[0];
    if (!feat || !feat.geometry) return null;

    let karnatakaRings = [];
    if (feat.geometry.type === 'Polygon') {
      karnatakaRings = [feat.geometry.coordinates[0]];
    } else if (feat.geometry.type === 'MultiPolygon') {
      karnatakaRings = feat.geometry.coordinates.map((poly) => poly[0]);
    }

    if (karnatakaRings.length === 0) return null;

    return {
      type: 'Feature',
      properties: { name: 'karnataka-mask' },
      geometry: {
        type: 'Polygon',
        coordinates: [WORLD_RING, ...karnatakaRings],
      },
    };
  }, [boundaryData]);

  // Combined districts GeoJSON collection with district names
  const allDistrictsGeoJSON = useMemo(() => {
    if (districtGeoJSON) return districtGeoJSON;
    if (!districtsData || districtsData.length === 0) return null;
    const features = districtsData
      .filter((d) => d.geojson)
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
  }, [districtsData, districtGeoJSON]);

  const selectedSet = useMemo(() => new Set(selectedDistricts || []), [selectedDistricts]);

  // Dynamic marker size: 22 px at fitted zoom, 28 px at higher zooms
  const markerSize =
    currentZoom != null && fittedZoom != null && currentZoom > fittedZoom ? 28 : 22;

  const mapApiKey =
    import.meta.env.VITE_CARTO_API_KEY ||
    import.meta.env.VITE_MAP_API_KEY ||
    'cb1_4o4q_1_6812e578da69fd55754ee2c4';
  const keyParam = mapApiKey ? `?key=${mapApiKey}` : '';

  // Requirement 1: CartoDB light_nolabels tiles (no basemap labels)
  const tileUrl = `https://{s}.basemaps.cartocdn.com/rastertiles/light_nolabels/{z}/{x}/{y}.png${keyParam}`;

  // Page background color to fill the exterior mask at full opacity
  const pageBgColor = isDark ? '#1C1A16' : '#FBF7F0';
  // District line color: 1 px in sand
  const sandColor = isDark ? '#7D7465' : '#C8BBA6';

  // District styling: 1px sand outline, selected filled amber at 18% with 2px amber outline
  const getDistrictStyle = (feature) => {
    const dName = feature?.properties?.name;
    const isSelected = selectedSet.has(dName);

    if (isSelected) {
      return {
        color: '#E8A317',
        weight: 2,
        fillColor: '#E8A317',
        fillOpacity: 0.18,
        interactive: true,
      };
    }
    return {
      color: sandColor,
      weight: 1,
      fillColor: '#000000',
      fillOpacity: 0.0001, // Captures pointer events for hover across district polygon
      interactive: true,
    };
  };

  // Requirement 1: Show a district name ONLY on hover and for selected districts using our own GeoJSON
  const onEachDistrictFeature = (feature, layer) => {
    const dName = feature?.properties?.name;
    if (!dName) return;

    const isSelected = selectedSet.has(dName);

    if (isSelected) {
      // Selected district: permanent label centered on district
      layer.bindTooltip(dName, {
        permanent: true,
        direction: 'center',
        className: 'karnataka-district-tooltip-selected',
      });
    } else {
      // Unselected district: district name appears ONLY on hover!
      layer.bindTooltip(dName, {
        permanent: false,
        sticky: true,
        direction: 'top',
        offset: [0, -10],
        className: 'karnataka-district-tooltip-hover',
      });
    }
  };

  return (
    <div
      className="map-container-card"
      style={{
        height,
        borderRadius: 12,
        border: '1px solid var(--border)',
        overflow: 'hidden',
        backgroundColor: pageBgColor,
      }}
    >
      <MapContainer
        center={[15.0, 76.25]}
        zoom={7}
        zoomSnap={0.25}
        zoomDelta={0.5}
        maxBounds={KARNATAKA_MAX_BOUNDS}
        maxBoundsViscosity={1.0}
        scrollWheelZoom={true}
        className="leaflet-map-root"
        style={{ width: '100%', height: '100%' }}
      >
        {/* CartoDB Positron light_nolabels tiles only */}
        <TileLayer
          url={tileUrl}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
          subdomains="abcd"
          maxZoom={18}
        />

        <MapController
          highlightedCandidate={highlightedCandidate}
          onZoomChange={setCurrentZoom}
          onFittedZoom={setFittedZoom}
        />

        {/* 1. Custom Leaflet pane (maskPane) covering world with Karnataka hole, full opacity page background */}
        {maskGeoJSON && (
          <GeoJSON
            key={`mask-${pageBgColor}`}
            pane="maskPane"
            data={maskGeoJSON}
            style={{
              stroke: false,
              fillColor: pageBgColor,
              fillOpacity: 1.0,
              interactive: false,
            }}
          />
        )}

        {/* 2. District boundaries & tooltips (hover only, permanent for selected) */}
        {allDistrictsGeoJSON && (
          <GeoJSON
            key={`districts-${allDistrictsGeoJSON.features.length}-${Array.from(selectedSet).sort().join('-')}-${sandColor}`}
            data={allDistrictsGeoJSON}
            style={getDistrictStyle}
            onEachFeature={onEachDistrictFeature}
          />
        )}

        {/* 3. Karnataka boundary outline: 2 px in forest green */}
        {boundaryData && (
          <GeoJSON
            key={`karnataka-outline-${boundaryData.features.length}`}
            data={boundaryData}
            style={{
              color: '#1F5C45', // Forest green
              weight: 2, // 2 px outline
              fill: false,
              interactive: false,
            }}
          />
        )}

        {/* 4. Numbered markers with zoom scaling (22px fitted / 28px higher) & overlap protection */}
        <CandidateMarkers
          candidates={candidates}
          selectedId={selectedId}
          highlightedId={highlightedId}
          onSelectCandidate={onSelectCandidate}
          markerSize={markerSize}
        />
      </MapContainer>
    </div>
  );
}
