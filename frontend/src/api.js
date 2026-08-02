/**
 * Backend base URL.
 * - Local dev: defaults to http://127.0.0.1:8000
 * - Same-origin free host (Render): set VITE_API_URL= (empty) at build time
 * - Split host (Vercel UI + Render API): set VITE_API_URL=https://your-api.onrender.com
 */
const raw = import.meta.env.VITE_API_URL;
export const API_BASE = (
  raw === undefined || raw === null
    ? 'http://127.0.0.1:8000'
    : String(raw)
).replace(/\/$/, '');

export const apiUrl = (path = '') => {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE}${p}`;
};
