import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Sun, Moon } from 'lucide-react';
import { useTheme } from '../utils/useDarkMode';

export default function TopNav() {
  const location = useLocation();
  const { isDark, toggleTheme } = useTheme();

  const navLinks = [
    { path: '/', label: 'Dashboard' },
    { path: '/results', label: 'Results' },
    { path: '/methodology', label: 'Methodology' },
  ];

  return (
    <header className="top-nav" role="banner">
      <div className="top-nav-inner">
        <Link to="/" className="top-nav-brand">
          Terravolt AI
        </Link>

        <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
          <nav className="top-nav-links" aria-label="Main Navigation">
            {navLinks.map((item) => {
              const isActive = location.pathname === item.path;
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`top-nav-link ${isActive ? 'active' : ''}`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <button
            type="button"
            className="theme-toggle-btn"
            onClick={toggleTheme}
            aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {isDark ? (
              <Sun size={16} strokeWidth={1.5} />
            ) : (
              <Moon size={16} strokeWidth={1.5} />
            )}
          </button>
        </div>
      </div>
    </header>
  );
}
