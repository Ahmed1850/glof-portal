import axios from 'axios';

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
  if (typeof window !== 'undefined') {
    const host = window.location.hostname || '';
    // Single-service Render deploy: always same-origin
    if (host.endsWith('onrender.com')) {
      return '';
    }
  }
  if (import.meta.env.VITE_API_URL === undefined || import.meta.env.VITE_API_URL === null) {
    return 'http://127.0.0.1:8000';
  }
  return String(import.meta.env.VITE_API_URL).replace(/\/$/, '');
}

export const API_BASE = resolveApiBase();

/**
 * Resolve an API endpoint path against API_BASE.
 * @param {string} [path='']
 * @returns {string}
 */
export const apiUrl = (path = '') => {
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE}${p}`;
};

/**
 * Determine whether an error originated from an aborted/canceled request.
 * @param {unknown} err
 * @returns {boolean}
 */
export function isAbortError(err) {
  if (!err || typeof err !== 'object') return false;
  return (
    axios.isCancel(err) ||
    err.name === 'AbortError' ||
    err.name === 'CanceledError' ||
    err.code === 'ERR_CANCELED'
  );
}

/**
 * Strip null, undefined, empty string, and optionally 'All' filter values.
 * @param {Record<string, unknown>} [params]
 * @param {{ omitAll?: boolean }} [opts]
 * @returns {Record<string, string | number | boolean>}
 */
function cleanParams(params = {}, { omitAll = false } = {}) {
  const cleaned = {};
  for (const [key, val] of Object.entries(params || {})) {
    if (val === undefined || val === null || val === '') continue;
    if (omitAll && val === 'All') continue;
    cleaned[key] = val;
  }
  return cleaned;
}

/**
 * Build a URL with sanitized query parameters.
 * @param {string} path
 * @param {Record<string, unknown>} [params]
 * @param {{ omitAll?: boolean }} [opts]
 * @returns {string}
 */
function buildEndpointUrl(path, params = {}, opts = {}) {
  const cleaned = cleanParams(params, opts);
  const qs = new URLSearchParams();
  for (const [key, val] of Object.entries(cleaned)) {
    qs.set(key, String(val));
  }
  const query = qs.toString();
  return apiUrl(query ? `${path}?${query}` : path);
}

/**
 * Normalize an axios failure into an Error carrying `status` and `detail`.
 * @param {unknown} err
 * @returns {Error & { status?: number, detail?: string }}
 */
function normalizeApiError(err) {
  if (isAbortError(err)) return err;
  const status = err?.response?.status;
  const rawDetail = err?.response?.data?.detail;
  const detail =
    typeof rawDetail === 'string' && rawDetail.trim()
      ? rawDetail.trim()
      : err?.message || (status ? `Request failed (${status})` : 'Network request failed');
  const normalized = new Error(detail);
  normalized.status = status;
  normalized.detail = detail;
  normalized.cause = err;
  return normalized;
}

/**
 * Support passing either `{ signal, timeout, omitAll }` or a bare `AbortSignal`.
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number, omitAll?: boolean }} [optionsOrSignal]
 * @returns {{ signal?: AbortSignal, timeout?: number, omitAll?: boolean }}
 */
function normalizeOptions(optionsOrSignal) {
  if (!optionsOrSignal) return {};
  if (typeof AbortSignal !== 'undefined' && optionsOrSignal instanceof AbortSignal) {
    return { signal: optionsOrSignal };
  }
  if (typeof optionsOrSignal.aborted === 'boolean' && typeof optionsOrSignal.addEventListener === 'function') {
    return { signal: optionsOrSignal };
  }
  return optionsOrSignal;
}

/**
 * Perform an AbortSignal-aware GET request via axios.
 * @template T
 * @param {string} path
 * @param {{ params?: Record<string, unknown>, timeout?: number, signal?: AbortSignal, omitAll?: boolean }} [options]
 * @returns {Promise<T>}
 */
async function apiGet(path, { params = {}, timeout = 20000, signal, omitAll = false } = {}) {
  try {
    const res = await axios.get(apiUrl(path), {
      params: cleanParams(params, { omitAll }),
      timeout,
      signal,
    });
    return res.data;
  } catch (err) {
    throw normalizeApiError(err);
  }
}

/**
 * @typedef {Object} BreachSimulationParams
 * @property {number} [breach_fraction=0.75]
 * @property {'moraine'|'ice'|'landslide'} [dam_type='moraine']
 * @property {number} [breach_duration_min=45]
 * @property {number} [mean_depth_m]
 */

/**
 * @typedef {Object} BreachSimulationResponse
 * @property {number} lake_id
 * @property {string} lake_name
 * @property {string} basin_id
 * @property {string} basin_name
 * @property {Object} params
 * @property {number} volume_m3
 * @property {{ froehlich_m3s: number, costa_m3s: number, walder_oconnor_m3s: number, ensemble_m3s: number }} peak_discharge
 * @property {Array<{ distance_km: number, elevation_m: number, label: string }>} elevation_profile
 * @property {Array<{ time_min: number, discharge_m3s: number }>} hydrograph
 * @property {Array<{ name: string, distance_km: number, elevation_m: number, eta_min: number, recession_min: number, attenuated_q_m3s: number, stage_rise_m: number, severity: string }>} downstream_nodes
 * @property {{ elevation_source: string, fallback_used: boolean }} provenance
 */

/**
 * Fetch physics-based dam-breach and flood-wave routing simulation for a lake.
 * @param {number|string} lakeId
 * @param {BreachSimulationParams} [params]
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<BreachSimulationResponse>}
 */
export function fetchBreachSimulation(lakeId, params = {}, options = {}) {
  const opts = normalizeOptions(options);
  return apiGet(`/analytics/breach-simulation/${encodeURIComponent(lakeId)}`, {
    params: {
      breach_fraction: params.breach_fraction ?? 0.75,
      dam_type: params.dam_type ?? 'moraine',
      breach_duration_min: params.breach_duration_min ?? 45,
      mean_depth_m: params.mean_depth_m,
    },
    ...opts,
  });
}

/**
 * Fetch multi-hazard triggers (thermal melt, GloFAS discharge, USGS seismic) for one lake.
 * @param {number|string} lakeId
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<Object>}
 */
export function fetchLakeTriggers(lakeId, options = {}) {
  return apiGet(`/analytics/triggers/${encodeURIComponent(lakeId)}`, normalizeOptions(options));
}

/**
 * Fetch regional multi-hazard trigger board across all monitored lakes.
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<Object>}
 */
export function fetchRegionalTriggers(options = {}) {
  return apiGet('/analytics/triggers/regional', normalizeOptions(options));
}

/**
 * Fetch point-in-polygon district spatial hazard aggregation (`GET /analytics/districts`).
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<{ total_districts: number, districts: Array<Object> }>}
 */
export function fetchDistrictAnalytics(options = {}) {
  return apiGet('/analytics/districts', normalizeOptions(options));
}

/**
 * Fetch basin domino cascade scores and chokepoints.
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<{ basins: Array<Object> }>}
 */
export function fetchBasinCascade(options = {}) {
  return apiGet('/analytics/basins/cascade', normalizeOptions(options));
}

/**
 * Fetch 6-axis multi-lake comparison metrics.
 * @param {Array<number|string>|string} [lakeIds]
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<{ lakes: Array<Object> }>}
 */
export function fetchLakeComparison(lakeIds, options = {}) {
  const serialized = Array.isArray(lakeIds) ? lakeIds.join(',') : lakeIds;
  return apiGet('/analytics/compare', {
    params: serialized ? { lake_ids: serialized } : {},
    ...normalizeOptions(options),
  });
}

/**
 * Fetch Monte Carlo uncertainty & SSP climate sensitivity analysis for a lake.
 * @param {number|string} lakeId
 * @param {{ temp_delta_c?: number, n_samples?: number }} [params]
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<Object>}
 */
export function fetchSensitivity(lakeId, params = {}, options = {}) {
  return apiGet(`/analytics/sensitivity/${encodeURIComponent(lakeId)}`, {
    params: {
      temp_delta_c: params.temp_delta_c ?? 0.0,
      n_samples: params.n_samples ?? 500,
    },
    ...normalizeOptions(options),
  });
}

/**
 * Fetch upstream external provider health & cache telemetry.
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<{ providers: Array<Object> }>}
 */
export function fetchProviderTelemetry(options = {}) {
  return apiGet('/analytics/providers', normalizeOptions(options));
}

/**
 * Fetch structured JSON SITREP briefing for a lake.
 * @param {number|string} lakeId
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<Object>}
 */
export function fetchSitrepBriefing(lakeId, options = {}) {
  return apiGet(`/reports/sitrep/${encodeURIComponent(lakeId)}/briefing`, normalizeOptions(options));
}

/**
 * Fetch spatial risk assessment profile for a lake (`GET /risk/{lake_id}`).
 * @param {number|string} lakeId
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<Object>}
 */
export function fetchLakeRisk(lakeId, options = {}) {
  return apiGet(`/risk/${encodeURIComponent(lakeId)}`, normalizeOptions(options));
}

/**
 * Fetch RFC 7946 flood routing corridor GeoJSON for a lake (`GET /lakes/{lake_id}/flood-corridor`).
 * @param {number|string} lakeId
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<Object>}
 */
export function fetchFloodCorridor(lakeId, options = {}) {
  return apiGet(`/lakes/${encodeURIComponent(lakeId)}/flood-corridor`, normalizeOptions(options));
}

/**
 * Execute spatial proximity or bounding-box lake search (`GET /lakes/spatial-search`).
 * @param {{ lat?: number, lon?: number, radius_km?: number, min_lon?: number, min_lat?: number, max_lon?: number, max_lat?: number, risk_level?: string }} [params]
 * @param {AbortSignal | { signal?: AbortSignal, timeout?: number }} [options]
 * @returns {Promise<Object>}
 */
export function fetchSpatialSearch(params = {}, options = {}) {
  return apiGet('/lakes/spatial-search', {
    params,
    ...normalizeOptions(options),
  });
}

/**
 * Build GeoJSON export download URL (`/reports/export/geojson`).
 * @param {{ risk_level?: string, basin_id?: string }} [params]
 * @returns {string}
 */
export function getExportGeoJsonUrl(params = {}) {
  return buildEndpointUrl('/reports/export/geojson', params, { omitAll: true });
}

/**
 * Build CSV inventory export download URL (`/reports/export/csv`).
 * @param {{ risk_level?: string, basin_id?: string }} [params]
 * @returns {string}
 */
export function getExportCsvUrl(params = {}) {
  return buildEndpointUrl('/reports/export/csv', params, { omitAll: true });
}

/**
 * Build JSON SITREP briefing URL (`/reports/sitrep/{lakeId}/briefing`).
 * @param {number|string} lakeId
 * @returns {string}
 */
export function getSitrepBriefingUrl(lakeId) {
  return apiUrl(`/reports/sitrep/${encodeURIComponent(lakeId)}/briefing`);
}

/**
 * Build PDF SITREP download URL (`/reports/sitrep/{lakeId}`).
 * @param {number|string} lakeId
 * @returns {string}
 */
export function getSitrepPdfUrl(lakeId) {
  return apiUrl(`/reports/sitrep/${encodeURIComponent(lakeId)}`);
}

/**
 * Build lakes GeoJSON endpoint URL (`/lakes/geojson`).
 * @param {{ risk_level?: string, basin_id?: string }} [params]
 * @returns {string}
 */
export function getLakesGeoJsonUrl(params = {}) {
  return buildEndpointUrl('/lakes/geojson', params, { omitAll: true });
}
