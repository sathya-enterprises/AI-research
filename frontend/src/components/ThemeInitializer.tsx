'use client';

import { useEffect } from 'react';

/** Restore the user's light/dark preference before interactive pages mount. */
export default function ThemeInitializer() {
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem('gg-theme');
      const theme = saved === 'dark' ? 'dark' : 'light';
      document.documentElement.dataset.theme = theme;
    } catch {
      document.documentElement.dataset.theme = 'light';
    }
  }, []);

  return null;
}
