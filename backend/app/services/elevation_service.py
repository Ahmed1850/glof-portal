"""
Shared Open-Meteo Elevation service with 24h TTL cache, 7d stale fallback,
batch coordinate querying, and passive provider telemetry.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

import httpx

from app.services.provider_telemetry import record_provider_event, register_cache_counter

_ELEV_LOCK = threading.Lock()
_ELEV_CACHE: dict[str, tuple[float, float]] = {}  # key -> (epoch_ts, elevation_m)
_ELEV_TTL_SEC = float(os.getenv("ELEV_CACHE_TTL_SEC", str(24 * 3600)))       # 24h
_ELEV_STALE_SEC = float(os.getenv("ELEV_STALE_SEC", str(7 * 24 * 3600)))     # 7d stale fallback
_ELEV_TIMEOUT_SEC = min(5.0, float(os.getenv("ELEV_TIMEOUT_SEC", "4.5")))    # <= 5.0s
_ELEV_MAX_ENTRIES = 1000

_OPEN_METEO_ELEV_URL = "https://api.open-meteo.com/v1/elevation"
_HEADERS = {"User-Agent": "GLOF-Portal/1.0 (elevation; educational)"}


def _elev_key(lat: float, lon: float) -> str:
    return f"{round(float(lat), 3):.3f}:{round(float(lon), 3):.3f}"


register_cache_counter("Open-Meteo Elevation", lambda: len(_ELEV_CACHE))


def get_cached_elevation(lat: Optional[float], lon: Optional[float]) -> Optional[float]:
    """Return cached elevation (fresh or stale) without performing network I/O."""
    if lat is None or lon is None:
        return None
    key = _elev_key(lat, lon)
    now = time.time()
    with _ELEV_LOCK:
        hit = _ELEV_CACHE.get(key)
        if hit and now - hit[0] <= _ELEV_STALE_SEC:
            return float(hit[1])
    return None


def _store_elevations(items: list[tuple[str, float]]) -> int:
    now = time.time()
    with _ELEV_LOCK:
        if len(_ELEV_CACHE) + len(items) > _ELEV_MAX_ENTRIES:
            oldest = sorted(_ELEV_CACHE.items(), key=lambda kv: kv[1][0])[: max(100, len(items))]
            for k, _ in oldest:
                _ELEV_CACHE.pop(k, None)
        for key, val in items:
            _ELEV_CACHE[key] = (now, float(val))
        return len(_ELEV_CACHE)


def fetch_elevation_open_meteo(lat: float, lon: float) -> Optional[float]:
    """Open-Meteo elevation API (no key) with 24h TTL cache + 7d stale fallback."""
    if lat is None or lon is None:
        return None

    key = _elev_key(lat, lon)
    now = time.time()
    stale: Optional[tuple[float, float]] = None

    with _ELEV_LOCK:
        hit = _ELEV_CACHE.get(key)
        if hit:
            if now - hit[0] <= _ELEV_TTL_SEC:
                n_entries = len(_ELEV_CACHE)
                record_provider_event(
                    "Open-Meteo Elevation",
                    status="healthy",
                    cached_entries=n_entries,
                    cache_hit=True,
                )
                return float(hit[1])
            if now - hit[0] <= _ELEV_STALE_SEC:
                stale = hit

    t0 = time.perf_counter()
    try:
        resp = httpx.get(
            _OPEN_METEO_ELEV_URL,
            params={"latitude": float(lat), "longitude": float(lon)},
            headers=_HEADERS,
            timeout=_ELEV_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = resp.json()
        elev = data.get("elevation")
        val: Optional[float] = None
        if isinstance(elev, list) and elev and elev[0] is not None:
            val = float(elev[0])
        elif elev is not None and not isinstance(elev, list):
            val = float(elev)

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if val is not None:
            n_entries = _store_elevations([(key, val)])
            record_provider_event(
                "Open-Meteo Elevation",
                status="healthy",
                latency_ms=latency_ms,
                cached_entries=n_entries,
                fallback_mode="stale cache -> Open-Elevation -> alpine slope heuristic",
            )
            return val
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        with _ELEV_LOCK:
            n_entries = len(_ELEV_CACHE)
        if stale is not None:
            record_provider_event(
                "Open-Meteo Elevation",
                status="degraded",
                latency_ms=latency_ms,
                cached_entries=n_entries,
                fallback_mode="stale_cache",
                error=str(e),
            )
            return float(stale[1])
        record_provider_event(
            "Open-Meteo Elevation",
            status="degraded",
            latency_ms=latency_ms,
            cached_entries=n_entries,
            fallback_mode="open_elevation_fallback",
            error=str(e),
        )
        print(f"Open-Meteo elevation failed for {lat},{lon}: {e}")
        return None

    return float(stale[1]) if stale is not None else None


def fetch_elevations_batch(
    coords: list[tuple[float, float]],
) -> tuple[list[Optional[float]], str]:
    """
    Resolve elevations for a sequence of (lat, lon) points using the shared 24h cache
    and a single batch Open-Meteo Elevation query for any uncached coordinates.

    Returns (elevations_m_list, source_label).
    """
    if not coords:
        return [], "Open-Meteo Elevation"

    now = time.time()
    results: list[Optional[float]] = [None] * len(coords)
    stale_map: dict[int, float] = {}
    missing_indices: list[int] = []
    missing_keys: list[str] = []

    with _ELEV_LOCK:
        for idx, (lat, lon) in enumerate(coords):
            key = _elev_key(lat, lon)
            hit = _ELEV_CACHE.get(key)
            if hit and now - hit[0] <= _ELEV_TTL_SEC:
                results[idx] = float(hit[1])
            else:
                missing_indices.append(idx)
                missing_keys.append(key)
                if hit and now - hit[0] <= _ELEV_STALE_SEC:
                    stale_map[idx] = float(hit[1])
        n_entries = len(_ELEV_CACHE)

    if not missing_indices:
        record_provider_event(
            "Open-Meteo Elevation",
            status="healthy",
            cached_entries=n_entries,
            cache_hit=True,
        )
        return results, "Open-Meteo Elevation (cached)"

    lat_param = ",".join(f"{float(coords[i][0]):.5f}" for i in missing_indices)
    lon_param = ",".join(f"{float(coords[i][1]):.5f}" for i in missing_indices)

    t0 = time.perf_counter()
    try:
        resp = httpx.get(
            _OPEN_METEO_ELEV_URL,
            params={"latitude": lat_param, "longitude": lon_param},
            headers=_HEADERS,
            timeout=_ELEV_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = resp.json()
        elevs = data.get("elevation")
        if not isinstance(elevs, list):
            elevs = [elevs]

        to_store: list[tuple[str, float]] = []
        for pos, orig_idx in enumerate(missing_indices):
            raw_v = elevs[pos] if pos < len(elevs) else None
            if raw_v is not None:
                val = float(raw_v)
                results[orig_idx] = val
                to_store.append((missing_keys[pos], val))
            elif orig_idx in stale_map:
                results[orig_idx] = stale_map[orig_idx]

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        new_count = _store_elevations(to_store) if to_store else n_entries
        record_provider_event(
            "Open-Meteo Elevation",
            status="healthy",
            latency_ms=latency_ms,
            cached_entries=new_count,
            fallback_mode="stale cache -> Open-Elevation -> alpine slope heuristic",
        )
        return results, "Open-Meteo Elevation"
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        used_stale = False
        for orig_idx in missing_indices:
            if orig_idx in stale_map:
                results[orig_idx] = stale_map[orig_idx]
                used_stale = True
        with _ELEV_LOCK:
            cur_count = len(_ELEV_CACHE)
        record_provider_event(
            "Open-Meteo Elevation",
            status="degraded",
            latency_ms=latency_ms,
            cached_entries=cur_count,
            fallback_mode="stale_cache" if used_stale else "offline_basin_gradient",
            error=str(e),
        )
        return results, "Open-Meteo Elevation (stale cache)" if used_stale else "offline_basin_gradient"
