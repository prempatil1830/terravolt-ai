import { useState, useEffect } from 'react';

const STORAGE_KEY = 'solar_pv_theme';

function getInitialTheme() {
  if (typeof window === 'undefined') return 'light';
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') {
      return saved;
    }
  } catch {
    // Ignore localStorage errors
  }
  // Light is the default regardless of OS setting
  return 'light';
}

function applyThemeToDocument(theme) {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.style.colorScheme = theme;
}

// Initial application on script evaluation
if (typeof window !== 'undefined') {
  applyThemeToDocument(getInitialTheme());
}

export function useTheme() {
  const [theme, setThemeState] = useState(getInitialTheme);

  useEffect(() => {
    applyThemeToDocument(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Ignore
    }

    const handler = (e) => {
      if (e.detail && (e.detail === 'light' || e.detail === 'dark')) {
        setThemeState(e.detail);
      }
    };
    window.addEventListener('solar-pv-theme-change', handler);
    return () => window.removeEventListener('solar-pv-theme-change', handler);
  }, [theme]);

  const toggleTheme = () => {
    setThemeState((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark';
      applyThemeToDocument(next);
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Ignore
      }
      window.dispatchEvent(new CustomEvent('solar-pv-theme-change', { detail: next }));
      return next;
    });
  };

  const setTheme = (next) => {
    applyThemeToDocument(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore
    }
    setThemeState(next);
    window.dispatchEvent(new CustomEvent('solar-pv-theme-change', { detail: next }));
  };

  return {
    theme,
    isDark: theme === 'dark',
    toggleTheme,
    setTheme,
  };
}

export function useDarkMode() {
  const { isDark } = useTheme();
  return isDark;
}
