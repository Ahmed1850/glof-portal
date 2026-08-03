/**
 * Backend base URL.
 * - Local dev: defaults to http://127.0.0.1:8000
 * - Same-origin free host (Render): set VITE_API_URL= (empty) at build time
 * - Split host (Vercel UI + Render API): set VITE_API_URL=https://your-api.onrender.com
 *
 * Also auto-detects onrender.com at runtime so a bad/missing build env never
 * points the production UI at 127.0.0.1.
 */
function resolveApiBase() {
  const raw = import.meta.env.VITE_API_URL;
  if (typeof window !== 'undefined') {
    const host = window.location.hostname || '';
    // Single-service Render deploy: always same-origin
    if (host.endsWith('onrender.com')) {
      return '';
    }
  }
  if (raw === undefined || raw === null) {
    return 'http://127.0.0.1:8000';
  }
  return String(raw).replace(/\/$/, '');
}

export const API_BASE = resolveApiBase();

export const apiUrl = (path = '') => {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE}${p}`;
};
