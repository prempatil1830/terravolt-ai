import { useEffect } from 'react';
import { MapContainer, TileLayer, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import './App.css';

/**
 * Karnataka bounding box (approx):
 *   South: 11.5°N   North: 18.5°N
 *   West:  74.0°E   East:  78.5°E
 *
 * Map centre: midpoint ≈ 15.0°N, 76.25°E
 * Initial zoom: 7 shows the full state comfortably.
 */
const KARNATAKA_CENTER = [15.0, 76.25];
const KARNATAKA_BOUNDS = [
  [11.5, 74.0],   // SW corner
  [18.5, 78.5],   // NE corner
];
const INITIAL_ZOOM = 7;

/**
 * BoundsFitter — inner component that fits the map to Karnataka's bounding
 * box on mount. react-leaflet's MapContainer `bounds` prop can fight with
 * `center`/`zoom`, so we handle it imperatively here.
 */
function BoundsFitter() {
  const map = useMap();
  useEffect(() => {
    map.fitBounds(KARNATAKA_BOUNDS, { padding: [20, 20] });
  }, [map]);
  return null;
}

export default function App() {
  return (
    <div id="app-shell">
      {/* ── Header ─────────────────────────────────── */}
      <header id="site-header" role="banner">
        <div className="logo-icon" aria-hidden="true">☀️</div>
        <h1>Solar PV Karnataka</h1>
        <span className="subtitle">Phase 0 — Scaffold</span>
      </header>

      {/* ── Map ────────────────────────────────────── */}
      <main id="map-container" role="main" aria-label="Karnataka map">
        <MapContainer
          id="karnataka-map"
          center={KARNATAKA_CENTER}
          zoom={INITIAL_ZOOM}
          minZoom={6}
          maxZoom={18}
          scrollWheelZoom={true}
          style={{ width: '100%', height: '100%' }}
        >
          {/* Dark basemap — CartoDB Dark Matter, no labels variant */}
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
            subdomains="abcd"
            maxZoom={19}
          />

          {/* Fit viewport to Karnataka on mount */}
          <BoundsFitter />
        </MapContainer>

        {/* Phase badge overlay */}
        <div className="phase-badge" role="status" aria-live="polite">
          Karnataka · Phase 0 · No data loaded
        </div>
      </main>
    </div>
  );
}
