import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import TopNav from './components/TopNav';
import Dashboard from './pages/Dashboard';
import Results from './pages/Results';
import CandidateDetail from './pages/CandidateDetail';
import Methodology from './pages/Methodology';

export default function App() {
  const [candidatesData, setCandidatesData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let ignore = false;

    async function loadCandidates() {
      try {
        setLoading(true);
        setError(null);
        const res = await fetch('/api/candidates');
        if (!res.ok) {
          throw new Error(`Server returned HTTP ${res.status}: ${res.statusText}`);
        }
        const data = await res.json();
        if (!ignore) {
          setCandidatesData(data);
        }
      } catch (err) {
        if (!ignore) {
          setError(err.message || 'Unable to connect to FastAPI backend');
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    }

    loadCandidates();

    return () => {
      ignore = true;
    };
  }, []);

  const candidates = candidatesData?.candidates || [];

  return (
    <BrowserRouter>
      <div className="app-container">
        <TopNav />
        <main className="main-content">
          <Routes>
            <Route
              path="/"
              element={
                <Dashboard
                  candidatesData={candidatesData}
                  loading={loading}
                  error={error}
                />
              }
            />
            <Route path="/results" element={<Results />} />
            <Route path="/search" element={<Navigate to="/" replace />} />
            <Route
              path="/candidates/:id"
              element={<CandidateDetail candidates={candidates} />}
            />
            <Route path="/run" element={<Navigate to="/" replace />} />
            <Route path="/methodology" element={<Methodology />} />
            {/* Fallback to Dashboard */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
