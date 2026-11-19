"""
Multi-Hazard GLOF Trigger Engine.

Combines three free, unauthenticated external telemetry pillars with strict
<= 5.0s timeouts, TTL caching, stale fallback, and deterministic offline fallbacks:
  1. Thermal Melt Pillar (Open-Meteo 14-day Forecast + freezing level, weight 0.45)
  2. Hydrological Discharge Pillar (Open-Meteo GloFAS Flood API, weight 0.30)
  3. Seismic Trigger Pillar (USGS ComCat M >= 3.5 within 150 km / 30d, weight 0.25)
"""

from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.lake import Lake as LakeModel
from app.services.elevation_service import fetch_elevation_open_meteo, fetch_elevations_batch
from app.services.geo_helpers import haversine_km
from app.services.glof_basins import assign_basin
from app.services.provider_telemetry import record_provider_event, register_cache_counter

_TRIGGER_LOCK = threading.Lock()
_FORECAST_CACHE: dict[str, tuple[float, dict]] = {}
_GLOFAS_CACHE: dict[str, tuple[float, dict]] = {}
_USGS_CACHE: dict[str, tuple[float, dict]] = {}

_TRIGGER_TTL_SEC = float(os.getenv("TRIGGER_CACHE_TTL_SEC", "1800"))        # 30 min
_TRIGGER_STALE_SEC = float(os.getenv("TRIGGER_STALE_SEC", str(6 * 3600)))   # 6h stale
_HTTP_TIMEOUT_SEC = min(5.0, float(os.getenv("TRIGGER_TIMEOUT_SEC", "4.5")))
_MAX_CACHE_ENTRIES = 250

_REGIONAL_CENTER = (35.85, 74.50)
_USGS_REGIONAL_KEY = "regional:35.85:74.50:420"
_HEADERS = {"User-Agent": "GLOF-Portal/1.0 (trigger-engine; educational)"}
_ORIG_FETCH_ELEV = fetch_elevation_open_meteo

_OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_OPEN_METEO_GLOFAS_URL = "https://flood-api.open-meteo.com/v1/flood"
_USGS_COMCAT_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

register_cache_counter("Open-Meteo Weather", lambda: len(_FORECAST_CACHE))
register_cache_counter("Open-Meteo GloFAS", lambda: len(_GLOFAS_CACHE))
register_cache_counter("USGS ComCat", lambda: len(_USGS_CACHE))


def _to_finite_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _validate_numeric_list(arr: Any, *, min_len: int = 0, min_finite: int = 0, name: str = "array") -> None:
    if not isinstance(arr, list) or len(arr) < min_len:
        raise ValueError(f"Invalid {name}: expected list of length >= {min_len}")
    finite_count = 0
    for item in arr:
        if item is None:
            continue
        if _to_finite_float(item) is None:
            raise ValueError(f"Invalid non-finite element in {name}")
        finite_count += 1
    if finite_count < min_finite:
        raise ValueError(f"Insufficient finite elements in {name}")


def _validate_forecast_payload(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Open-Meteo Forecast payload is not a JSON object")
    if data.get("elevation") is not None and _to_finite_float(data.get("elevation")) is None:
        raise ValueError("Open-Meteo Forecast returned non-finite elevation")
    daily = data.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("Open-Meteo Forecast missing daily object")
    times = daily.get("time")
    if not isinstance(times, list) or len(times) < 14 or not all(isinstance(t, str) and t.strip() for t in times[:14]):
        raise ValueError("Open-Meteo Forecast daily.time must have >= 14 date strings")
    _validate_numeric_list(daily.get("temperature_2m_max"), min_len=14, min_finite=7, name="daily.temperature_2m_max")
    _validate_numeric_list(daily.get("temperature_2m_min"), min_len=14, min_finite=7, name="daily.temperature_2m_min")
    _validate_numeric_list(daily.get("precipitation_sum"), min_len=14, min_finite=1, name="daily.precipitation_sum")

    hourly = data.get("hourly")
    if hourly is not None:
        if not isinstance(hourly, dict):
            raise ValueError("Open-Meteo Forecast hourly must be an object")
        if hourly.get("time") is not None and not isinstance(hourly.get("time"), list):
            raise ValueError("Open-Meteo Forecast hourly.time must be a list")
        if hourly.get("freezing_level_height") is not None:
            _validate_numeric_list(hourly.get("freezing_level_height"), name="hourly.freezing_level_height")
        if hourly.get("precipitation") is not None:
            _validate_numeric_list(hourly.get("precipitation"), name="hourly.precipitation")
    return data


def _validate_glofas_payload(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValueError("GloFAS payload is not a JSON object")
    daily = data.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("GloFAS missing daily object")
    times = daily.get("time")
    if not isinstance(times, list) or len(times) < 14 or not all(isinstance(t, str) and t.strip() for t in times[:14]):
        raise ValueError("GloFAS daily.time must have >= 14 date strings")
    _validate_numeric_list(daily.get("river_discharge"), min_len=14, min_finite=1, name="daily.river_discharge")
    if daily.get("river_discharge_mean") is not None:
        _validate_numeric_list(daily.get("river_discharge_mean"), name="daily.river_discharge_mean")
    if daily.get("river_discharge_max") is not None:
        _validate_numeric_list(daily.get("river_discharge_max"), name="daily.river_discharge_max")
    return data


def _validate_usgs_payload(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValueError("USGS ComCat payload is not a JSON object")
    features = data.get("features")
    if not isinstance(features, list):
        raise ValueError("USGS ComCat features must be a list")
    for feat in features:
        if not isinstance(feat, dict):
            raise ValueError("USGS ComCat feature entry must be an object")
        geom = feat.get("geometry")
        if not isinstance(geom, dict):
            raise ValueError("USGS ComCat feature missing geometry object")
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            raise ValueError("USGS ComCat feature missing coordinate pair")
        if _to_finite_float(coords[0]) is None or _to_finite_float(coords[1]) is None:
            raise ValueError("USGS ComCat feature has non-finite coordinates")
        if len(coords) > 2 and coords[2] is not None and _to_finite_float(coords[2]) is None:
            raise ValueError("USGS ComCat feature has non-finite depth")
        props = feat.get("properties")
        if props is not None:
            if not isinstance(props, dict):
                raise ValueError("USGS ComCat feature properties must be an object")
            if props.get("mag") is not None and _to_finite_float(props.get("mag")) is None:
                raise ValueError("USGS ComCat feature has non-finite magnitude")
            if props.get("time") is not None and _to_finite_float(props.get("time")) is None:
                raise ValueError("USGS ComCat feature has non-finite timestamp")
    return data


def _summarize_sources(sources: set[str]) -> str:
    if not sources:
        return "offline_fallback"
    if len(sources) == 1:
        return next(iter(sources))
    if "offline_fallback" in sources:
        return "mixed (offline_fallback)"
    stale = [s for s in sources if "stale" in s.lower()]
    if stale:
        return f"mixed ({stale[0]})"
    return sorted(sources)[0]


def _grid_key(lat: float, lon: float) -> str:
    return f"{round(float(lat) * 2.0) / 2.0:.1f}:{round(float(lon) * 2.0) / 2.0:.1f}"


def _evict_if_full(store: dict[str, tuple[float, dict]]) -> None:
    if len(store) >= _MAX_CACHE_ENTRIES:
        oldest = sorted(store.items(), key=lambda kv: kv[1][0])[:50]
        for k, _ in oldest:
            store.pop(k, None)


def _offline_forecast_payload(lat: float, lon: float, lake_elev_m: float) -> dict:
    now = datetime.now(timezone.utc)
    # Seasonal melt cycle peaking in late July (month ~7.5)
    seasonal_sin = math.sin(((now.month - 4.0) / 12.0) * 2.0 * math.pi)
    base_mean_at_3500 = 3.0 + 11.0 * seasonal_sin
    lapse = -0.0065 * (lake_elev_m - 3500.0)
    mean_c = base_mean_at_3500 + lapse
    freezing_lvl = max(1800.0, 3400.0 + 1650.0 * seasonal_sin)

    dates = [(now + timedelta(days=i - 7)).strftime("%Y-%m-%d") for i in range(14)]
    tmax_list = [round(mean_c + 4.2, 1) for _ in dates]
    tmin_list = [round(mean_c - 4.5, 1) for _ in dates]
    precip_list = [1.5 for _ in dates]

    hourly_times: list[str] = []
    hourly_fl: list[float] = []
    hourly_p: list[float] = []
    for d in dates:
        hourly_times.append(f"{d}T12:00")
        hourly_fl.append(round(freezing_lvl, 1))
        hourly_p.append(0.3)

    return {
        "elevation": lake_elev_m,
        "daily": {
            "time": dates,
            "temperature_2m_max": tmax_list,
            "temperature_2m_min": tmin_list,
            "precipitation_sum": precip_list,
        },
        "hourly": {
            "time": hourly_times,
            "freezing_level_height": hourly_fl,
            "precipitation": hourly_p,
        },
    }


def _offline_glofas_payload(basin_id: str) -> dict:
    now = datetime.now(timezone.utc)
    seasonal = max(0.35, 0.7 + 0.5 * math.sin(((now.month - 4.0) / 12.0) * 2.0 * math.pi))
    base_q = 18.0 if basin_id in ("hunza", "shigar_baltoro") else 8.5
    q_val = round(base_q * seasonal, 2)
    dates = [(now + timedelta(days=i - 7)).strftime("%Y-%m-%d") for i in range(14)]
    return {
        "daily": {
            "time": dates,
            "river_discharge": [q_val for _ in dates],
            "river_discharge_mean": [q_val for _ in dates],
            "river_discharge_max": [round(q_val * 1.15, 2) for _ in dates],
        }
    }


def _fetch_forecast_14d(lat: float, lon: float, lake_elev_m: float) -> tuple[dict, str, bool]:
    key = _grid_key(lat, lon)
    now = time.time()
    stale: Optional[tuple[float, dict]] = None

    with _TRIGGER_LOCK:
        hit = _FORECAST_CACHE.get(key)
        if hit:
            if now - hit[0] <= _TRIGGER_TTL_SEC:
                record_provider_event(
                    "Open-Meteo Weather",
                    status="healthy",
                    cached_entries=len(_FORECAST_CACHE),
                    cache_hit=True,
                )
                return hit[1], "Open-Meteo Forecast (cached)", False
            if now - hit[0] <= _TRIGGER_STALE_SEC:
                stale = hit

    t0 = time.perf_counter()
    try:
        resp = httpx.get(
            _OPEN_METEO_FORECAST_URL,
            params={
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "hourly": "freezing_level_height,precipitation",
                "past_days": 7,
                "forecast_days": 7,
                "timezone": "UTC",
            },
            headers=_HEADERS,
            timeout=_HTTP_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = _validate_forecast_payload(resp.json())
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        with _TRIGGER_LOCK:
            _evict_if_full(_FORECAST_CACHE)
            _FORECAST_CACHE[key] = (time.time(), data)
            n_entries = len(_FORECAST_CACHE)
        record_provider_event(
            "Open-Meteo Weather",
            status="healthy",
            latency_ms=latency_ms,
            cached_entries=n_entries,
        )
        return data, "Open-Meteo Forecast", False
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        with _TRIGGER_LOCK:
            n_entries = len(_FORECAST_CACHE)
        if stale is not None:
            record_provider_event(
                "Open-Meteo Weather",
                status="degraded",
                latency_ms=latency_ms,
                cached_entries=n_entries,
                fallback_mode="stale_cache",
                error=str(e),
            )
            return stale[1], "Open-Meteo Forecast (stale cache)", True
        record_provider_event(
            "Open-Meteo Weather",
            status="degraded",
            latency_ms=latency_ms,
            cached_entries=n_entries,
            fallback_mode="offline_climatology",
            error=str(e),
        )
        return _offline_forecast_payload(lat, lon, lake_elev_m), "offline_fallback", True


def _fetch_glofas_14d(lat: float, lon: float, basin_id: str) -> tuple[dict, str, bool]:
    key = _grid_key(lat, lon)
    now = time.time()
    stale: Optional[tuple[float, dict]] = None

    with _TRIGGER_LOCK:
        hit = _GLOFAS_CACHE.get(key)
        if hit:
            if now - hit[0] <= _TRIGGER_TTL_SEC:
                record_provider_event(
                    "Open-Meteo GloFAS",
                    status="healthy",
                    cached_entries=len(_GLOFAS_CACHE),
                    cache_hit=True,
                )
                return hit[1], "Open-Meteo GloFAS (cached)", False
            if now - hit[0] <= _TRIGGER_STALE_SEC:
                stale = hit

    t0 = time.perf_counter()
    try:
        resp = httpx.get(
            _OPEN_METEO_GLOFAS_URL,
            params={
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "daily": "river_discharge,river_discharge_mean,river_discharge_max",
                "past_days": 7,
                "forecast_days": 7,
            },
            headers=_HEADERS,
            timeout=_HTTP_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = _validate_glofas_payload(resp.json())
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        with _TRIGGER_LOCK:
            _evict_if_full(_GLOFAS_CACHE)
            _GLOFAS_CACHE[key] = (time.time(), data)
            n_entries = len(_GLOFAS_CACHE)
        record_provider_event(
            "Open-Meteo GloFAS",
            status="healthy",
            latency_ms=latency_ms,
            cached_entries=n_entries,
        )
        return data, "Open-Meteo GloFAS", False
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        with _TRIGGER_LOCK:
            n_entries = len(_GLOFAS_CACHE)
        if stale is not None:
            record_provider_event(
                "Open-Meteo GloFAS",
                status="degraded",
                latency_ms=latency_ms,
                cached_entries=n_entries,
                fallback_mode="stale_cache",
                error=str(e),
            )
            return stale[1], "Open-Meteo GloFAS (stale cache)", True
        record_provider_event(
            "Open-Meteo GloFAS",
            status="degraded",
            latency_ms=latency_ms,
            cached_entries=n_entries,
            fallback_mode="seasonal basin runoff climatology",
            error=str(e),
        )
        return _offline_glofas_payload(basin_id), "offline_fallback", True


def _fetch_usgs_events(lat: float, lon: float, *, force_regional: bool = False) -> tuple[dict, str, bool]:
    use_regional = force_regional or haversine_km(lat, lon, _REGIONAL_CENTER[0], _REGIONAL_CENTER[1]) <= 200.0
    if use_regional:
        key = _USGS_REGIONAL_KEY
        q_lat, q_lon, q_rad = _REGIONAL_CENTER[0], _REGIONAL_CENTER[1], 420
    else:
        key = f"{_grid_key(lat, lon)}:150"
        q_lat, q_lon, q_rad = round(lat, 3), round(lon, 3), 150

    now = time.time()
    stale: Optional[tuple[float, dict]] = None
    with _TRIGGER_LOCK:
        hit = _USGS_CACHE.get(key)
        if hit:
            if now - hit[0] <= _TRIGGER_TTL_SEC:
                record_provider_event(
                    "USGS ComCat",
                    status="healthy",
                    cached_entries=len(_USGS_CACHE),
                    cache_hit=True,
                )
                return hit[1], "USGS ComCat (cached)", False
            if now - hit[0] <= _TRIGGER_STALE_SEC:
                stale = hit

    starttime = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    t0 = time.perf_counter()
    try:
        resp = httpx.get(
            _USGS_COMCAT_URL,
            params={
                "format": "geojson",
                "latitude": q_lat,
                "longitude": q_lon,
                "maxradiuskm": q_rad,
                "minmagnitude": 3.5,
                "starttime": starttime,
                "orderby": "time",
            },
            headers=_HEADERS,
            timeout=_HTTP_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = _validate_usgs_payload(resp.json())
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        with _TRIGGER_LOCK:
            _evict_if_full(_USGS_CACHE)
            _USGS_CACHE[key] = (time.time(), data)
            n_entries = len(_USGS_CACHE)
        record_provider_event(
            "USGS ComCat",
            status="healthy",
            latency_ms=latency_ms,
            cached_entries=n_entries,
        )
        return data, "USGS ComCat", False
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        with _TRIGGER_LOCK:
            n_entries = len(_USGS_CACHE)
        if stale is not None:
            record_provider_event(
                "USGS ComCat",
                status="degraded",
                latency_ms=latency_ms,
                cached_entries=n_entries,
                fallback_mode="stale_cache",
                error=str(e),
            )
            return stale[1], "USGS ComCat (stale cache)", True
        record_provider_event(
            "USGS ComCat",
            status="degraded",
            latency_ms=latency_ms,
            cached_entries=n_entries,
            fallback_mode="zero recent seismicity (baseline M<3.5)",
            error=str(e),
        )
        return {"type": "FeatureCollection", "features": []}, "offline_fallback", True


def _compute_triggers_from_raw(
    *,
    lake: LakeModel,
    lake_elev_m: float,
    forecast_data: dict,
    om_source: str,
    om_fb: bool,
    glofas_data: dict,
    glofas_source: str,
    glofas_fb: bool,
    usgs_data: dict,
    usgs_source: str,
    usgs_fb: bool,
    elev_fb: bool = False,
) -> dict[str, Any]:
    lat = float(lake.latitude)
    lon = float(lake.longitude)

    # 1. Thermal Melt Pillar
    daily_f = forecast_data.get("daily") if isinstance(forecast_data.get("daily"), dict) else {}
    f_dates = list(daily_f.get("time") or [])
    tmax_raw = list(daily_f.get("temperature_2m_max") or [])
    tmin_raw = list(daily_f.get("temperature_2m_min") or [])
    precip_raw = list(daily_f.get("precipitation_sum") or [])

    grid_elev = _to_finite_float(forecast_data.get("elevation"))
    if grid_elev is None:
        grid_elev = lake_elev_m
    delta_t = -0.0065 * (lake_elev_m - grid_elev)

    hourly_f = forecast_data.get("hourly") if isinstance(forecast_data.get("hourly"), dict) else {}
    h_times = list(hourly_f.get("time") or [])
    h_fl = list(hourly_f.get("freezing_level_height") or [])
    h_precip = list(hourly_f.get("precipitation") or [])

    fl_by_day: dict[str, list[float]] = {}
    for idx, ts in enumerate(h_times):
        day_str = str(ts)[:10]
        if idx < len(h_fl):
            fl_val = _to_finite_float(h_fl[idx])
            if fl_val is not None:
                fl_by_day.setdefault(day_str, []).append(fl_val)

    max_hourly_precip_mm = max(
        (pv for p in h_precip if (pv := _to_finite_float(p)) is not None),
        default=0.0,
    )

    pdd_14d = 0.0
    adj_tmax_vals: list[float] = []
    daily_fl_means: dict[str, float] = {}
    for idx, d_str in enumerate(f_dates):
        tx_val = _to_finite_float(tmax_raw[idx]) if idx < len(tmax_raw) else None
        tx = tx_val if tx_val is not None else 0.0
        tn_val = _to_finite_float(tmin_raw[idx]) if idx < len(tmin_raw) else None
        tn = tn_val if tn_val is not None else tx
        tx_adj = tx + delta_t
        tn_adj = tn + delta_t
        adj_tmax_vals.append(tx_adj)
        pdd_14d += max(0.0, (tx_adj + tn_adj) / 2.0)

        fl_vals = fl_by_day.get(str(d_str)) or []
        if fl_vals:
            daily_fl_means[str(d_str)] = sum(fl_vals) / len(fl_vals)

    pdd_14d = round(pdd_14d, 1)
    all_fl_vals = list(daily_fl_means.values())
    freezing_level_m = round(sum(all_fl_vals) / len(all_fl_vals), 1) if all_fl_vals else round(lake_elev_m - 200.0, 1)
    above_lake_elevation = freezing_level_m > lake_elev_m
    max_temp_c = round(max(adj_tmax_vals, default=0.0), 1)
    precip_7d_mm = round(
        sum(pv for p in precip_raw[-7:] if (pv := _to_finite_float(p)) is not None),
        1,
    )

    thermal_score = min(
        100.0,
        round(
            min(55.0, pdd_14d * 0.65)
            + (20.0 if above_lake_elevation else 5.0)
            + min(25.0, precip_7d_mm * 0.4 + max_hourly_precip_mm * 1.5),
            1,
        ),
    )

    # 2. Hydrological GloFAS Pillar
    daily_g = glofas_data.get("daily") if isinstance(glofas_data.get("daily"), dict) else {}
    g_dates = list(daily_g.get("time") or [])
    g_q = list(daily_g.get("river_discharge") or [])
    g_q_mean = list(daily_g.get("river_discharge_mean") or [])

    q_by_day: dict[str, float] = {}
    for idx, d_str in enumerate(g_dates):
        if idx < len(g_q):
            qv = _to_finite_float(g_q[idx])
            if qv is not None:
                q_by_day[str(d_str)] = qv

    valid_q = [qv for v in g_q if (qv := _to_finite_float(v)) is not None]
    valid_q_mean = [qv for v in g_q_mean if (qv := _to_finite_float(v)) is not None]

    discharge_m3s = round(max(valid_q, default=5.0), 2)
    mean_discharge_m3s = round(
        (sum(valid_q_mean) / len(valid_q_mean))
        if valid_q_mean
        else ((sum(valid_q) / len(valid_q)) if valid_q else 5.0),
        2,
    )
    anomaly_ratio = round(discharge_m3s / max(mean_discharge_m3s, 0.1), 2)
    hydro_score = min(
        100.0,
        round(max(5.0, (anomaly_ratio - 0.8) * 35.0) + min(25.0, discharge_m3s * 0.25), 1),
    )

    # 3. Seismic USGS Pillar
    seismic_events: list[dict[str, Any]] = []
    pga_values: list[float] = []
    raw_features = usgs_data.get("features") if isinstance(usgs_data.get("features"), list) else []
    for feat in raw_features:
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry")
        if not isinstance(geom, dict):
            continue
        coords = geom.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        eq_lon = _to_finite_float(coords[0])
        eq_lat = _to_finite_float(coords[1])
        if eq_lon is None or eq_lat is None:
            continue
        depth_val = _to_finite_float(coords[2]) if len(coords) > 2 else None
        depth_km = depth_val if depth_val is not None else 10.0
        dist_km = haversine_km(lat, lon, eq_lat, eq_lon)
        if dist_km > 150.0:
            continue
        props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
        mag_val = _to_finite_float(props.get("mag"))
        mag = mag_val if mag_val is not None else 0.0
        t_ms = _to_finite_float(props.get("time"))
        if t_ms is not None:
            try:
                time_utc = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (OverflowError, OSError, ValueError):
                time_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            time_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        r_hypo = math.sqrt(dist_km ** 2 + depth_km ** 2 + 49.0)
        pga_e = 0.015 * (10.0 ** (0.5 * (mag - 4.0))) * ((30.0 / max(10.0, r_hypo)) ** 1.35)
        pga_values.append(pga_e)

        seismic_events.append({
            "id": str(feat.get("id") or f"eq-{len(seismic_events) + 1}"),
            "magnitude": round(mag, 1),
            "place": str(props.get("place") or "Karakoram / Hindu Kush Region"),
            "distance_km": round(dist_km, 1),
            "time_utc": time_utc,
            "depth_km": round(depth_km, 1),
        })

    seismic_events.sort(key=lambda e: (e["distance_km"], -e["magnitude"]))
    event_count_30d = len(seismic_events)
    max_magnitude = round(max((e["magnitude"] for e in seismic_events), default=0.0), 1)
    nearest_km = seismic_events[0]["distance_km"] if seismic_events else None
    pga_proxy = round(min(1.0, max(pga_values, default=0.0)), 4)

    if event_count_30d == 0:
        seismic_score = 5.0
    else:
        seismic_score = min(
            100.0,
            round(
                min(40.0, max_magnitude * 6.5)
                + min(30.0, event_count_30d * 6.0)
                + min(30.0, pga_proxy * 300.0),
                1,
            ),
        )

    composite_trigger_score = round(
        min(100.0, 0.45 * thermal_score + 0.30 * hydro_score + 0.25 * seismic_score),
        1,
    )
    if composite_trigger_score >= 75.0:
        trigger_level = "Critical"
    elif composite_trigger_score >= 50.0:
        trigger_level = "Warning"
    elif composite_trigger_score >= 25.0:
        trigger_level = "Watch"
    else:
        trigger_level = "Normal"

    # Build 14-day daily_series aligned to forecast dates
    daily_series: list[dict[str, Any]] = []
    for idx, d_str in enumerate(f_dates[:14]):
        d_key = str(d_str)
        tx_val = _to_finite_float(tmax_raw[idx]) if idx < len(tmax_raw) else None
        pr_val = _to_finite_float(precip_raw[idx]) if idx < len(precip_raw) else None
        tx = tx_val if tx_val is not None else 0.0
        pr = pr_val if pr_val is not None else 0.0
        fl_day = daily_fl_means.get(d_key, freezing_level_m)
        q_day = q_by_day.get(d_key, discharge_m3s)
        daily_series.append({
            "date": d_key,
            "temp_max_c": round(tx + delta_t, 1),
            "precip_mm": round(pr, 1),
            "freezing_level_m": round(fl_day, 1),
            "river_discharge_m3s": round(q_day, 2),
        })

    return {
        "lake_id": lake.id,
        "lake_name": lake.name,
        "lake_elevation_m": round(lake_elev_m, 1),
        "composite_trigger_score": composite_trigger_score,
        "trigger_level": trigger_level,
        "pillars": {
            "thermal_melt": {
                "score": thermal_score,
                "pdd_14d": pdd_14d,
                "freezing_level_m": freezing_level_m,
                "above_lake_elevation": above_lake_elevation,
                "max_temp_c": max_temp_c,
            },
            "hydrological_glofas": {
                "score": hydro_score,
                "discharge_m3s": discharge_m3s,
                "mean_discharge_m3s": mean_discharge_m3s,
                "anomaly_ratio": anomaly_ratio,
            },
            "seismic_usgs": {
                "score": seismic_score,
                "event_count_30d": event_count_30d,
                "max_magnitude": max_magnitude,
                "nearest_km": nearest_km,
                "pga_proxy": pga_proxy,
            },
        },
        "daily_series": daily_series,
        "seismic_events": seismic_events,
        "provenance": {
            "open_meteo": om_source,
            "glofas": glofas_source,
            "usgs": usgs_source,
            "fallback_used": bool(om_fb or glofas_fb or usgs_fb or elev_fb),
        },
    }


def get_lake_triggers(lake: LakeModel, db: Optional[Session] = None) -> dict[str, Any]:
    if lake.latitude is None or lake.longitude is None:
        raise HTTPException(status_code=400, detail="Lake has no spatial coordinates")

    lat = float(lake.latitude)
    lon = float(lake.longitude)
    basin = assign_basin(lat, lon)

    if fetch_elevation_open_meteo is not _ORIG_FETCH_ELEV:
        elev_m = fetch_elevation_open_meteo(lat, lon)
        elev_fb = elev_m is None
    else:
        elevs, elev_source = fetch_elevations_batch([(lat, lon)])
        elev_m = elevs[0] if elevs else None
        elev_fb = (elev_m is None) or ("stale" in elev_source.lower()) or ("offline" in elev_source.lower())

    lake_elev_m = float(elev_m) if elev_m is not None else 3850.0

    forecast_data, om_source, om_fb = _fetch_forecast_14d(lat, lon, lake_elev_m)
    glofas_data, glofas_source, glofas_fb = _fetch_glofas_14d(lat, lon, basin["id"])
    usgs_data, usgs_source, usgs_fb = _fetch_usgs_events(lat, lon, force_regional=False)

    return _compute_triggers_from_raw(
        lake=lake,
        lake_elev_m=lake_elev_m,
        forecast_data=forecast_data,
        om_source=om_source,
        om_fb=om_fb,
        glofas_data=glofas_data,
        glofas_source=glofas_source,
        glofas_fb=glofas_fb,
        usgs_data=usgs_data,
        usgs_source=usgs_source,
        usgs_fb=usgs_fb,
        elev_fb=elev_fb,
    )


def get_regional_triggers(db: Session) -> dict[str, Any]:
    lakes = db.query(LakeModel).all()
    geo_lakes = [l for l in lakes if l.latitude is not None and l.longitude is not None]

    # Pre-warm USGS regional feed & batch-fetch all lake elevations in ONE HTTP call
    usgs_data, usgs_source, usgs_fb = _fetch_usgs_events(
        _REGIONAL_CENTER[0],
        _REGIONAL_CENTER[1],
        force_regional=True,
    )
    elevs, elev_source = fetch_elevations_batch(
        [(float(l.latitude), float(l.longitude)) for l in geo_lakes]
    )
    elev_stale_or_offline = ("stale" in elev_source.lower()) or ("offline" in elev_source.lower())

    items: list[dict[str, Any]] = []
    counts = {"Critical": 0, "Warning": 0, "Watch": 0, "Normal": 0}
    any_fb = usgs_fb or elev_stale_or_offline
    om_sources: set[str] = set()
    glofas_sources: set[str] = set()

    for idx, lake in enumerate(geo_lakes):
        lat = float(lake.latitude)
        lon = float(lake.longitude)
        basin = assign_basin(lat, lon)
        elev_m = elevs[idx] if idx < len(elevs) else None
        lake_elev_m = float(elev_m) if elev_m is not None else 3850.0
        elev_fb = (elev_m is None) or elev_stale_or_offline

        # Grid-coalesced 0.5° Forecast & GloFAS caches keep this to ~6-8 calls total
        forecast_data, om_source, om_fb = _fetch_forecast_14d(lat, lon, lake_elev_m)
        glofas_data, glofas_source, glofas_fb = _fetch_glofas_14d(lat, lon, basin["id"])
        om_sources.add(om_source)
        glofas_sources.add(glofas_source)
        any_fb = any_fb or elev_fb or om_fb or glofas_fb

        res = _compute_triggers_from_raw(
            lake=lake,
            lake_elev_m=lake_elev_m,
            forecast_data=forecast_data,
            om_source=om_source,
            om_fb=om_fb,
            glofas_data=glofas_data,
            glofas_source=glofas_source,
            glofas_fb=glofas_fb,
            usgs_data=usgs_data,
            usgs_source=usgs_source,
            usgs_fb=usgs_fb,
            elev_fb=elev_fb,
        )
        lvl = res["trigger_level"]
        if lvl in counts:
            counts[lvl] += 1

        items.append({
            "lake_id": res["lake_id"],
            "lake_name": res["lake_name"],
            "basin_id": basin["id"],
            "basin_name": basin["name"],
            "lake_elevation_m": res["lake_elevation_m"],
            "composite_trigger_score": res["composite_trigger_score"],
            "trigger_level": res["trigger_level"],
            "pillars": res["pillars"],
            "seismic_event_count_30d": res["pillars"]["seismic_usgs"]["event_count_30d"],
        })

    items.sort(key=lambda x: (-x["composite_trigger_score"], x["lake_name"]))

    return {
        "total_lakes": len(items),
        "summary_counts": counts,
        "top_alerts": items[:5],
        "lakes": items,
        "provenance": {
            "open_meteo": _summarize_sources(om_sources),
            "glofas": _summarize_sources(glofas_sources),
            "usgs": usgs_source,
            "fallback_used": any_fb,
        },
    }
