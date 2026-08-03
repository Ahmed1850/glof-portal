"""
Flood impact footprint + GLOF flood likelihood prediction.

Uses early-warning inputs, temperature (Open-Meteo + fallbacks), historical growth,
and simplified downstream exposure (population + area).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from app.services.glof_basins import (
    assign_basin,
    estimate_flood_footprint,
    estimate_outburst_volume_m3,
)
from app.utils.risk import calculate_risk

# ---------------------------------------------------------------------------
# Weather cache — Render free dynos share public IPs and Open-Meteo free tier
# returns HTTP 429 under load. Cache + multi-source fallbacks keep UI working.
# ---------------------------------------------------------------------------
_TEMP_LOCK = threading.Lock()
_TEMP_CACHE: dict[str, tuple[float, dict]] = {}
_TEMP_TTL_SEC = float(os.getenv("WEATHER_CACHE_TTL_SEC", str(45 * 60)))  # 45 min
_TEMP_STALE_SEC = float(os.getenv("WEATHER_STALE_SEC", str(6 * 3600)))  # serve stale up to 6h on errors


def _temp_cache_key(lat: float, lon: float) -> str:
    # Coarse grid so all GB lakes share ~1 request (not 46 unique Open-Meteo calls).
    return f"{round(float(lat) * 2) / 2:.1f}:{round(float(lon) * 2) / 2:.1f}"


def _http_get_json(url: str, timeout: float = 12.0, headers: Optional[dict] = None) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GLOF-Portal/1.0 (https://github.com/Ahmed1850/glof-portal; weather)",
            "Accept": "application/json",
            **(headers or {}),
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_open_meteo(lat: float, lon: float) -> dict:
    """Primary provider. Supports optional OPEN_METEO_API_KEY (customer API)."""
    base = os.getenv("OPEN_METEO_BASE_URL", "").strip()
    api_key = os.getenv("OPEN_METEO_API_KEY", "").strip()
    if not base:
        base = (
            "https://customer-api.open-meteo.com/v1/forecast"
            if api_key
            else "https://api.open-meteo.com/v1/forecast"
        )
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m",
        "daily": "temperature_2m_max",
        "forecast_days": 3,
        "timezone": "auto",
    }
    if api_key:
        params["apikey"] = api_key
    qs = urllib.parse.urlencode(params)
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            data = _http_get_json(f"{base}?{qs}", timeout=10.0)
            current = (data.get("current") or {}).get("temperature_2m")
            daily_max = (data.get("daily") or {}).get("temperature_2m_max") or []
            peak = max([x for x in daily_max if x is not None], default=current)
            if current is None and peak is None:
                raise ValueError("Open-Meteo returned empty temperature")
            return {
                "temperature_c": float(current) if current is not None else None,
                "forecast_max_c": float(peak) if peak is not None else None,
                "source": "Open-Meteo",
            }
        except urllib.error.HTTPError as e:
            last_err = e
            # 429 / 5xx → brief backoff then retry
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise
    raise last_err or RuntimeError("Open-Meteo failed")


def _fetch_met_no(lat: float, lon: float) -> dict:
    """Fallback: Norwegian Met Institute (global coverage, free with UA)."""
    url = (
        "https://api.met.no/weatherapi/locationforecast/2.0/compact"
        f"?lat={lat:.4f}&lon={lon:.4f}"
    )
    data = _http_get_json(
        url,
        timeout=12.0,
        headers={"User-Agent": "GLOF-Portal/1.0 github.com/Ahmed1850/glof-portal"},
    )
    timeseries = ((data.get("properties") or {}).get("timeseries") or [])
    if not timeseries:
        raise ValueError("met.no returned no timeseries")
    first = timeseries[0]
    details = ((first.get("data") or {}).get("instant") or {}).get("details") or {}
    current = details.get("air_temperature")
    # Peak over next ~72h of available steps
    temps = []
    for step in timeseries[:72]:
        d = ((step.get("data") or {}).get("instant") or {}).get("details") or {}
        t = d.get("air_temperature")
        if t is not None:
            temps.append(float(t))
    peak = max(temps) if temps else current
    return {
        "temperature_c": float(current) if current is not None else None,
        "forecast_max_c": float(peak) if peak is not None else None,
        "source": "MET Norway",
    }


def _fetch_wttr(lat: float, lon: float) -> dict:
    """Last-resort fallback via wttr.in JSON."""
    url = f"https://wttr.in/{lat:.3f},{lon:.3f}?format=j1"
    data = _http_get_json(url, timeout=12.0)
    current = data.get("current_condition") or []
    cur_c = None
    if current:
        try:
            cur_c = float(current[0].get("temp_C"))
        except (TypeError, ValueError, IndexError):
            cur_c = None
    peak = cur_c
    weather = data.get("weather") or []
    for day in weather[:3]:
        try:
            mx = float(day.get("maxtempC"))
            if peak is None or mx > peak:
                peak = mx
        except (TypeError, ValueError):
            pass
    if cur_c is None and peak is None:
        raise ValueError("wttr.in returned no temperature")
    return {
        "temperature_c": cur_c,
        "forecast_max_c": peak,
        "source": "wttr.in",
    }


def fetch_temperature(lat: float, lon: float) -> dict:
    """
    Current + short-range max temperature for melt stress.

    Strategy (Render-safe):
      1. Fresh cache hit (45 min)
      2. Open-Meteo with retries
      3. MET Norway
      4. wttr.in
      5. Stale cache (up to 6 h) if all providers fail
    """
    if lat is None or lon is None:
        return {
            "temperature_c": None,
            "forecast_max_c": None,
            "source": "unavailable: missing coordinates",
        }

    key = _temp_cache_key(lat, lon)
    now = time.time()

    with _TEMP_LOCK:
        hit = _TEMP_CACHE.get(key)
        if hit and now - hit[0] <= _TEMP_TTL_SEC:
            out = dict(hit[1])
            out["cached"] = True
            return out
        stale = hit  # may reuse if providers fail

    errors: list[str] = []
    for provider in (_fetch_open_meteo, _fetch_met_no, _fetch_wttr):
        try:
            result = provider(float(lat), float(lon))
            if result.get("temperature_c") is None and result.get("forecast_max_c") is None:
                continue
            with _TEMP_LOCK:
                _TEMP_CACHE[key] = (time.time(), dict(result))
            result["cached"] = False
            return result
        except Exception as e:
            errors.append(f"{provider.__name__}: {e}")

    # Serve slightly stale cache rather than blank UI on 429 storms
    if stale and now - stale[0] <= _TEMP_STALE_SEC:
        out = dict(stale[1])
        out["cached"] = True
        out["source"] = f"{out.get('source', 'cache')} (stale cache; live fetch failed)"
        out["fetch_errors"] = errors[-3:]
        return out

    return {
        "temperature_c": None,
        "forecast_max_c": None,
        "source": "unavailable: " + ("; ".join(errors[-2:]) if errors else "no provider"),
        "cached": False,
    }


def zone_radii_km(area_ha: Optional[float]) -> tuple[float, float]:
    risk = calculate_risk(area_ha or 0)
    if risk == "High":
        return 5.0, 10.0
    if risk == "Medium":
        return 2.0, 5.0
    return 1.0, 2.0


def build_flood_impact(
    *,
    area_ha: Optional[float],
    lat: Optional[float],
    lon: Optional[float],
    danger_population: int = 0,
    warning_population: int = 0,
) -> dict[str, Any]:
    d_km, w_km = zone_radii_km(area_ha)
    footprint = estimate_flood_footprint(area_ha, d_km, w_km)
    volume = estimate_outburst_volume_m3(area_ha)
    basin = assign_basin(lat, lon) if lat is not None and lon is not None else None

    return {
        "danger_population": int(danger_population or 0),
        "warning_population": int(warning_population or 0),
        "people_at_risk_total": int(warning_population or danger_population or 0),
        "estimated_volume_m3": volume,
        "estimated_volume_million_m3": round(volume / 1e6, 3) if volume else None,
        "assumed_mean_depth_m": 12.0,
        "footprint": footprint,
        "basin_id": basin["id"] if basin else None,
        "basin_name": basin["name"] if basin else None,
        "downstream_storage": (basin or {}).get("downstream_storage", []),
        "at_risk_settlements": (basin or {}).get("at_risk_settlements", []),
        "notes": (
            "Impact uses planning-grade circular zones and WorldPop (if available). "
            "Not a 2D hydraulic inundation map."
        ),
    }


def predict_flood_likelihood(
    *,
    early_warning_score: float,
    early_warning_level: str,
    growth_pct_per_year: Optional[float],
    temperature_c: Optional[float],
    forecast_max_c: Optional[float],
    elevation_m: Optional[float],
    glacier_distance_km: Optional[float],
    area_ha: Optional[float],
) -> dict[str, Any]:
    """
    Predict relative GLOF likelihood from score + growth + heat + glacier context.
    Classes: Unlikely | Possible | Likely | High
    """
    pts = 0.0
    factors = []

    # Base from early warning score
    s = float(early_warning_score or 0)
    pts += min(40.0, s * 0.4)
    factors.append({"factor": "Early warning score", "value": s, "contribution": round(min(40.0, s * 0.4), 1)})

    # Historical growth
    g = growth_pct_per_year
    if g is None:
        g_pts = 8.0
        factors.append({"factor": "Area growth (3–5 yr)", "value": None, "contribution": g_pts, "note": "unknown — neutral"})
    else:
        if g >= 25:
            g_pts = 25.0
        elif g >= 12:
            g_pts = 18.0
        elif g >= 5:
            g_pts = 12.0
        elif g >= 0:
            g_pts = 6.0
        else:
            g_pts = 2.0
        factors.append({"factor": "Area growth (3–5 yr)", "value": g, "contribution": g_pts})
    pts += g_pts

    # Temperature / melt stress (high daytime heat at high elevation)
    t = forecast_max_c if forecast_max_c is not None else temperature_c
    if t is None:
        t_pts = 6.0
        factors.append({"factor": "Temperature / melt stress", "value": None, "contribution": t_pts, "note": "unknown"})
    else:
        # At high elevation, even moderate °C can mean strong melt
        elev = elevation_m or 3500
        heat_index = float(t) + max(0.0, (elev - 3000) / 500.0)  # elev boost
        if heat_index >= 28:
            t_pts = 20.0
        elif heat_index >= 22:
            t_pts = 14.0
        elif heat_index >= 16:
            t_pts = 9.0
        else:
            t_pts = 4.0
        factors.append({
            "factor": "Temperature / melt stress",
            "value": {"temperature_c": temperature_c, "forecast_max_c": forecast_max_c, "heat_index": round(heat_index, 1)},
            "contribution": t_pts,
        })
    pts += t_pts

    # Glacier proximity
    if glacier_distance_km is None:
        gl_pts = 5.0
    elif glacier_distance_km <= 2:
        gl_pts = 15.0
    elif glacier_distance_km <= 5:
        gl_pts = 10.0
    elif glacier_distance_km <= 10:
        gl_pts = 6.0
    else:
        gl_pts = 2.0
    pts += gl_pts
    factors.append({"factor": "Glacier proximity", "value": glacier_distance_km, "contribution": gl_pts})

    # Lake size
    a = area_ha or 0
    if a >= 40:
        a_pts = 10.0
    elif a >= 15:
        a_pts = 6.0
    else:
        a_pts = 3.0
    pts += a_pts
    factors.append({"factor": "Lake size", "value": area_ha, "contribution": a_pts})

    score = max(0.0, min(100.0, pts))
    if score >= 75 or early_warning_level == "Critical":
        level, color, advice = "High", "#f0433a", "Elevated flood likelihood — prioritize field/satellite watch"
    elif score >= 55 or early_warning_level == "Warning":
        level, color, advice = "Likely", "#f5a524", "Conditions favour GLOF development — increase monitoring frequency"
    elif score >= 35 or early_warning_level == "Watch":
        level, color, advice = "Possible", "#38bdf8", "Some favourable factors — maintain watch"
    else:
        level, color, advice = "Unlikely", "#2dd48e", "Low combined signal — standard surveillance"

    return {
        "likelihood": level,
        "score": round(score, 1),
        "color": color,
        "advice": advice,
        "factors": factors,
        "method": "Heuristic model: early-warning + historical growth + temperature + glacier + size",
    }
