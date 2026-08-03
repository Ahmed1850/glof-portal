"""
GLOF Early Warning / Flood Monitoring API
"""

from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
import traceback
import uuid
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from typing import Any, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.models.lake import Lake as LakeModel
from app.services.early_warning import compute_early_warning, classify_level
from app.services.flood_impact import build_flood_impact, predict_flood_likelihood, fetch_temperature
from app.services.glof_basins import BASINS, assign_basin, basin_summary_for_lakes, estimate_outburst_volume_m3
from app.utils.gee_detection import GEE_READY, _init_ee, gee_status

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/early-warning", tags=["Early Warning / Flood Monitoring"])

# ---------------------------------------------------------------------------
# In-process cache — critical on Render free tier where every GEE getInfo is
# expensive and HTTP requests are hard-capped (~30s free / ~100s paid).
# ---------------------------------------------------------------------------
_CACHE_LOCK = threading.Lock()
_GROWTH_CACHE: dict[str, tuple[float, Optional[float]]] = {}
_POP_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SEC = float(os.getenv("EW_GEE_CACHE_TTL_SEC", str(6 * 3600)))  # 6h

# Background monitor jobs (bypass Render HTTP proxy timeout on long GEE scans)
_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_JOB_TTL_SEC = 2 * 3600
_ACTIVE_JOB_ID: Optional[str] = None


def _cache_key(lat: float, lon: float, *parts) -> str:
    return f"{round(float(lat), 3)}:{round(float(lon), 3)}:" + ":".join(str(p) for p in parts)


def _cache_get(store: dict, key: str):
    with _CACHE_LOCK:
        item = store.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > _CACHE_TTL_SEC:
            store.pop(key, None)
            return None
        return value


def _cache_set(store: dict, key: str, value) -> None:
    with _CACHE_LOCK:
        store[key] = (time.time(), value)


def _is_render() -> bool:
    return bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") or os.getenv("RENDER_EXTERNAL_URL"))

# Approximate glacier tongue / icefield reference points across Gilgit-Baltistan
# Used when GEE glacier distance is unavailable (optional indicator).
GLACIER_REFS = [
    (36.45, 74.60),   # Batura / Passu region
    (36.40, 74.70),   # Ultar / Hunza
    (36.20, 74.80),   # Hispar / Hopar
    (35.80, 76.50),   # Baltoro / Concordia
    (35.85, 76.40),   # K2 approach
    (36.10, 75.20),   # Shimshal / Khurdopin
    (36.55, 74.90),   # Ghulkin / Batura
    (35.60, 76.30),   # Masherbrum / Hushe
    (36.25, 71.85),   # Tirich Mir
    (35.35, 74.80),   # Rama / Astore
]


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_glacier_km(lat: float, lon: float) -> float:
    return min(_haversine_km(lat, lon, glat, glon) for glat, glon in GLACIER_REFS)


def _http_get_json(url: str, timeout: float = 8.0) -> dict:
    """Stdlib GET — more reliable on free hosts than httpx in some environments."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GLOF-Portal/1.0 (early-warning; educational)"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_elevation_open_meteo(lat: float, lon: float) -> Optional[float]:
    """Open-Meteo elevation API (no key)."""
    try:
        qs = urllib.parse.urlencode({"latitude": lat, "longitude": lon})
        data = _http_get_json(f"https://api.open-meteo.com/v1/elevation?{qs}", timeout=6.0)
        elev = data.get("elevation")
        if isinstance(elev, list) and elev:
            return float(elev[0])
        if elev is not None:
            return float(elev)
    except Exception as e:
        print(f"Open-Meteo elevation failed for {lat},{lon}: {e}")
    return None


def fetch_elevation_open_elevation(lat: float, lon: float) -> Optional[float]:
    """Public Open-Elevation fallback."""
    try:
        qs = urllib.parse.urlencode({"locations": f"{lat},{lon}"})
        data = _http_get_json(f"https://api.open-elevation.com/api/v1/lookup?{qs}", timeout=6.0)
        results = data.get("results") or []
        if results and results[0].get("elevation") is not None:
            return float(results[0]["elevation"])
    except Exception as e:
        print(f"Open-Elevation failed for {lat},{lon}: {e}")
    return None


def fetch_elevation_gee_srtm(lat: float, lon: float) -> Optional[float]:
    """SRTM elevation via Google Earth Engine (works when EE credentials are set)."""
    if not GEE_READY and not _init_ee():
        return None
    try:
        from app.utils.gee_detection import ee
        if ee is None:
            return None
        point = ee.Geometry.Point([lon, lat])
        elev_img = ee.Image("USGS/SRTMGL1_003").select("elevation")
        sample = elev_img.reduceRegion(
            reducer=ee.Reducer.first(),
            geometry=point,
            scale=30,
            maxPixels=1e6,
        ).getInfo()
        if sample and sample.get("elevation") is not None:
            return float(sample["elevation"])
    except Exception as e:
        print(f"GEE SRTM elevation failed for {lat},{lon}: {e}")
    return None


def fetch_elevation_m(lat: float, lon: float) -> Tuple[Optional[float], Optional[str]]:
    """
    Multi-source elevation (metres).
    Order: Open-Meteo → Open-Elevation → GEE SRTM.
    Returns (elevation_m, source_label).
    """
    elev = fetch_elevation_open_meteo(lat, lon)
    if elev is not None:
        return elev, "Open-Meteo"

    elev = fetch_elevation_open_elevation(lat, lon)
    if elev is not None:
        return elev, "Open-Elevation"

    elev = fetch_elevation_gee_srtm(lat, lon)
    if elev is not None:
        return elev, "GEE SRTM"

    return None, None


def fetch_growth_from_gee(lat: float, lon: float) -> Optional[float]:
    """
    Percent growth per year from a 2-year Sentinel-2 NDWI pair (one GEE getInfo).

    Previously pulled an 11-year series (~11 getInfo calls) which always timed out
    on Render free tier when scoring many lakes.
    """
    key = _cache_key(lat, lon, "growth", 2019, 2024)
    with _CACHE_LOCK:
        item = _GROWTH_CACHE.get(key)
        if item and time.time() - item[0] <= _CACHE_TTL_SEC:
            return item[1]  # may be None (failed / no water) — still a cache hit

    if not GEE_READY and not _init_ee():
        return None
    try:
        from app.utils.gee_detection import estimate_growth_pct_per_year
        res = estimate_growth_pct_per_year(lat, lon, year_start=2019, year_end=2024)
        growth = res.get("growth_pct_per_year")
        if growth is not None:
            growth = float(growth)
        _cache_set(_GROWTH_CACHE, key, growth)
        return growth
    except Exception:
        return None


def _population_heuristic(lat: float, lon: float, area_ha: Optional[float]) -> dict:
    """Planning-grade population if GEE WorldPop is offline."""
    from app.utils.risk import calculate_risk
    risk = calculate_risk(area_ha or 0)
    d_km, w_km = (5.0, 10.0) if risk == "High" else ((2.0, 5.0) if risk == "Medium" else (1.0, 2.0))
    # Valley density heuristic (people/km²) for GB corridor settlements
    dens = 95.0
    danger_pop = int(math.pi * (d_km ** 2) * dens * 0.35)  # 35% habitable fraction
    warning_pop = int(math.pi * (w_km ** 2) * dens * 0.35)
    return {
        "danger_population": danger_pop,
        "warning_population": max(warning_pop, danger_pop),
        "source": "heuristic valley density (GEE offline)",
        "risk_level": risk,
    }


def fetch_population_exposure(lat: float, lon: float, area_ha: Optional[float]) -> dict:
    """Use GEE WorldPop when available; heuristic fallback so impact is never blank."""
    from app.utils.risk import calculate_risk
    risk = calculate_risk(area_ha or 0)
    key = _cache_key(lat, lon, "pop", risk)
    cached = _cache_get(_POP_CACHE, key)
    if cached is not None:
        return dict(cached)

    if not GEE_READY and not _init_ee():
        return _population_heuristic(lat, lon, area_ha)
    try:
        from app.utils.gee_detection import estimate_lake_exposure
        res = estimate_lake_exposure(lat, lon, risk)
        # If GEE returns zeros/errors, still provide heuristic floor for UX
        if res.get("error") or (
            int(res.get("danger_population") or 0) == 0
            and int(res.get("warning_population") or 0) == 0
        ):
            h = _population_heuristic(lat, lon, area_ha)
            h["source"] = f"heuristic (GEE empty/error: {res.get('error') or 'zero pop'})"
            return h
        _cache_set(_POP_CACHE, key, res)
        return res
    except Exception as e:
        h = _population_heuristic(lat, lon, area_ha)
        h["source"] = f"heuristic (GEE error: {e})"
        return h


def assess_lake(
    *,
    lake_id: Optional[int],
    name: str,
    area_ha: Optional[float],
    lat: Optional[float],
    lon: Optional[float],
    use_gee: bool = True,
    include_population: bool = True,
    shared_temp: Optional[dict] = None,
) -> dict:
    """
    Score one lake.

    Fast path (use_gee=False): no GEE calls — Open-Meteo elev/temp + heuristic pop.
    Slow path (use_gee=True): growth + WorldPop via GEE (can take minutes for many lakes).
    """
    if lat is None or lon is None:
        result = compute_early_warning(area_ha=area_ha, include_glacier=False)
        impact = build_flood_impact(area_ha=area_ha, lat=None, lon=None)
        prediction = predict_flood_likelihood(
            early_warning_score=result.get("score") or 0,
            early_warning_level=result.get("level") or "Normal",
            growth_pct_per_year=None,
            temperature_c=None,
            forecast_max_c=None,
            elevation_m=None,
            glacier_distance_km=None,
            area_ha=area_ha,
        )
        return {
            "lake_id": lake_id,
            "name": name,
            "latitude": lat,
            "longitude": lon,
            "early_warning": result,
            "flood_impact": impact,
            "flood_prediction": prediction,
            "data_sources": {"note": "Missing coordinates — area-only score"},
        }

    glacier_km = round(nearest_glacier_km(lat, lon), 2)
    temp = shared_temp if shared_temp is not None else fetch_temperature(lat, lon)

    growth = None
    growth_source = None
    if use_gee:
        # Elevation: Open-Meteo only (never fall through to GEE SRTM — that adds
        # another slow getInfo on the same critical path as growth/pop).
        elevation_m = fetch_elevation_open_meteo(lat, lon)
        elevation_source = "Open-Meteo" if elevation_m is not None else None
        if elevation_m is None:
            elevation_m = fetch_elevation_open_elevation(lat, lon)
            elevation_source = "Open-Elevation" if elevation_m is not None else None
        try:
            growth = fetch_growth_from_gee(lat, lon)
            growth_source = "GEE Sentinel-2 NDWI (2-year fast)" if growth is not None else "GEE growth unavailable"
        except Exception as e:
            growth = None
            growth_source = f"GEE growth error: {e}"
        try:
            pop = (
                fetch_population_exposure(lat, lon, area_ha)
                if include_population
                else _population_heuristic(lat, lon, area_ha)
            )
        except Exception as e:
            pop = _population_heuristic(lat, lon, area_ha)
            pop["source"] = f"heuristic (GEE pop error: {e})"
    else:
        # FAST: never call GEE (avoids timeout / hang on Render free tier)
        elevation_m = fetch_elevation_open_meteo(lat, lon)
        elevation_source = "Open-Meteo" if elevation_m is not None else None
        if elevation_m is None:
            elevation_m = fetch_elevation_open_elevation(lat, lon)
            elevation_source = "Open-Elevation" if elevation_m is not None else None
        pop = _population_heuristic(lat, lon, area_ha) if include_population else {
            "danger_population": 0,
            "warning_population": 0,
            "source": "skipped",
        }
        growth_source = "skipped (fast mode)"

    result = compute_early_warning(
        area_ha=area_ha,
        growth_pct_per_year=growth,
        elevation_m=elevation_m,
        glacier_distance_km=glacier_km,
        danger_population=pop.get("danger_population"),
        warning_population=pop.get("warning_population"),
        include_glacier=True,
    )

    impact = build_flood_impact(
        area_ha=area_ha,
        lat=lat,
        lon=lon,
        danger_population=int(pop.get("danger_population") or 0),
        warning_population=int(pop.get("warning_population") or 0),
    )
    prediction = predict_flood_likelihood(
        early_warning_score=result.get("score") or 0,
        early_warning_level=result.get("level") or "Normal",
        growth_pct_per_year=growth,
        temperature_c=temp.get("temperature_c"),
        forecast_max_c=temp.get("forecast_max_c"),
        elevation_m=elevation_m,
        glacier_distance_km=glacier_km,
        area_ha=area_ha,
    )
    basin = assign_basin(lat, lon)

    return {
        "lake_id": lake_id,
        "name": name,
        "latitude": lat,
        "longitude": lon,
        "area_ha": area_ha,
        "early_warning": result,
        "flood_impact": impact,
        "flood_prediction": prediction,
        "temperature": temp,
        "basin": {
            "id": basin["id"],
            "name": basin["name"],
            "river": basin["river"],
            "monitoring_priority": basin.get("monitoring_priority"),
        },
        "data_sources": {
            "elevation": elevation_source,
            "growth": growth_source or (
                "GEE Sentinel-2 NDWI" if growth is not None else "unavailable (enable GEE scan)"
            ),
            "population": pop.get("source"),
            "glacier": "reference glacier points (GB)",
            "temperature": temp.get("source"),
            "gee_ready": bool(GEE_READY),
            "mode": "gee" if use_gee else "fast",
        },
    }


@router.get("/status")
@limiter.limit("60/minute")
def early_warning_status(request: Request):
    return {
        "service": "GLOF Early Warning Score",
        "levels": [
            {"level": "Normal", "min_score": 0, "max_score": 24, "color": "#2dd48e"},
            {"level": "Watch", "min_score": 25, "max_score": 49, "color": "#38bdf8"},
            {"level": "Warning", "min_score": 50, "max_score": 74, "color": "#f5a524"},
            {"level": "Critical", "min_score": 75, "max_score": 100, "color": "#f0433a"},
        ],
        "indicators": [
            {"id": "area", "weight": 25, "label": "Current lake area"},
            {"id": "growth", "weight": 25, "label": "Area change last 3–5 years"},
            {"id": "elevation", "weight": 15, "label": "Elevation of the lake"},
            {"id": "glacier", "weight": 15, "label": "Proximity to glacier (optional)"},
            {"id": "population", "weight": 20, "label": "Downstream population"},
        ],
        "gee": gee_status(),
    }


@router.get("/weather")
@limiter.limit("60/minute")
def weather_at_point(
    request: Request,
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
):
    """
    Cached temperature for a point (Open-Meteo → MET Norway → wttr.in).
    Used by the dashboard and Flood Monitoring so Render does not 429 Open-Meteo.
    """
    t = fetch_temperature(lat, lon)
    return {
        "latitude": lat,
        "longitude": lon,
        "temperature_c": t.get("temperature_c"),
        "temperature_2m": t.get("temperature_c"),  # dashboard field name
        "forecast_max_c": t.get("forecast_max_c"),
        "source": t.get("source"),
        "cached": t.get("cached", False),
    }


@router.get("/score/{lake_id}")
@limiter.limit("20/minute")
def score_one_lake(
    request: Request,
    lake_id: int,
    use_gee: bool = Query(True),
    db: Session = Depends(get_db),
):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if not lake:
        raise HTTPException(status_code=404, detail="Lake not found")
    return assess_lake(
        lake_id=lake.id,
        name=lake.name,
        area_ha=lake.area_ha,
        lat=lake.latitude,
        lon=lake.longitude,
        use_gee=use_gee,
        include_population=use_gee,
    )


def _failed_lake_row(lake: LakeModel, err: str) -> dict:
    return {
        "lake_id": lake.id,
        "name": lake.name,
        "latitude": lake.latitude,
        "longitude": lake.longitude,
        "area_ha": lake.area_ha,
        "error": err,
        "early_warning": classify_level(0),
        "flood_impact": build_flood_impact(
            area_ha=lake.area_ha, lat=lake.latitude, lon=lake.longitude
        ),
        "flood_prediction": predict_flood_likelihood(
            early_warning_score=0,
            early_warning_level="Normal",
            growth_pct_per_year=None,
            temperature_c=None,
            forecast_max_c=None,
            elevation_m=None,
            glacier_distance_km=None,
            area_ha=lake.area_ha,
        ),
        "data_sources": {"mode": "error", "error": err},
    }


def _summarize_monitor(results: list, use_gee: bool, *, offset: int = 0, limit: int = 0, has_more: bool = False) -> dict:
    order = {"Critical": 0, "Warning": 1, "Watch": 2, "Normal": 3}
    results = sorted(
        results,
        key=lambda r: (
            order.get((r.get("early_warning") or {}).get("level"), 9),
            -float((r.get("early_warning") or {}).get("score") or 0),
        ),
    )
    counts = {"Critical": 0, "Warning": 0, "Watch": 0, "Normal": 0}
    for r in results:
        lvl = (r.get("early_warning") or {}).get("level")
        if lvl in counts:
            counts[lvl] += 1

    pred_counts = {"High": 0, "Likely": 0, "Possible": 0, "Unlikely": 0}
    for r in results:
        pl = (r.get("flood_prediction") or {}).get("likelihood")
        if pl in pred_counts:
            pred_counts[pl] += 1

    return {
        "total": len(results),
        "counts": counts,
        "prediction_counts": pred_counts,
        "use_gee": use_gee,
        "gee": gee_status(),
        "lakes": results,
        "basins": basin_summary_for_lakes(results),
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "chunked": offset > 0 or has_more,
    }


@router.get("/monitor")
@limiter.limit("60/minute")
def monitor_all_lakes(
    request: Request,
    use_gee: bool = Query(
        False,
        description="If true, query GEE for growth + population (slow). Default fast hybrid score.",
    ),
    limit: int = Query(
        46,
        ge=1,
        le=100,
        description="Max lakes in this response. For GEE on Render, client should request small chunks (1–3).",
    ),
    offset: int = Query(
        0,
        ge=0,
        le=500,
        description="Skip first N lakes (largest first). Use with small limit for Render-safe GEE scans.",
    ),
    db: Session = Depends(get_db),
):
    """
    Flood monitoring board: score registered lakes.

    Fast mode (default): area + Open-Meteo elev/temp + glacier heuristic + pop heuristic.
    GEE mode: 2-year growth + WorldPop (needs EE credentials).

    Render free tier kills HTTP requests ~30s — clients must call this endpoint in
    small chunks when use_gee=true (offset/limit), not one giant request.
    """
    # Cap GEE chunk size server-side so a misconfigured client cannot time out the dyno.
    if use_gee:
        max_gee_chunk = int(os.getenv("EW_GEE_CHUNK_MAX", "3" if _is_render() else "8"))
        if limit > max_gee_chunk:
            limit = max_gee_chunk

    q = db.query(LakeModel).order_by(LakeModel.area_ha.desc())
    total_inventory = q.count()
    lakes: List[LakeModel] = q.offset(offset).limit(limit).all()
    has_more = (offset + len(lakes)) < total_inventory

    # One regional temperature for all lakes (avoids 50 Open-Meteo calls)
    shared_temp = fetch_temperature(35.92, 74.31)

    results = []
    # GEE: sequential or tiny pool — parallel getInfo often worsens latency/rate-limits on free hosts.
    workers = 6 if not use_gee else 1

    # Hard wall-clock budget so Render proxy does not 502 the whole request.
    # Leave headroom under ~30s free / ~100s paid.
    default_budget = 22.0 if (_is_render() and use_gee) else (85.0 if use_gee else 60.0)
    budget_sec = float(os.getenv("EW_GEE_REQUEST_BUDGET_SEC", str(default_budget)))
    deadline = time.monotonic() + budget_sec

    def _job(lake: LakeModel, force_fast: bool = False):
        return assess_lake(
            lake_id=lake.id,
            name=lake.name,
            area_ha=lake.area_ha,
            lat=lake.latitude,
            lon=lake.longitude,
            use_gee=use_gee and not force_fast,
            include_population=True,
            shared_temp=shared_temp,
        )

    if not use_gee:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_job, lake): lake for lake in lakes}
            for fut in as_completed(futs):
                lake = futs[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    results.append(_failed_lake_row(lake, str(e)))
    else:
        # Sequential GEE with deadline: remaining lakes scored in fast mode so the
        # chunk always returns before the platform proxy timeout.
        for lake in lakes:
            remaining = deadline - time.monotonic()
            force_fast = remaining < 6.0  # need ~few seconds for one GEE lake
            try:
                if force_fast:
                    row = _job(lake, force_fast=True)
                    ds = row.setdefault("data_sources", {})
                    ds["mode"] = "fast_fallback"
                    ds["note"] = "Scored without GEE to beat host request timeout; retry chunk or wait for cache."
                    results.append(row)
                else:
                    # Per-lake timeout so one stuck getInfo cannot kill the chunk
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        fut = pool.submit(_job, lake, False)
                        try:
                            results.append(fut.result(timeout=max(5.0, min(remaining - 2.0, 28.0))))
                        except FuturesTimeout:
                            row = _job(lake, force_fast=True)
                            ds = row.setdefault("data_sources", {})
                            ds["mode"] = "fast_fallback"
                            ds["note"] = "GEE lake timed out; used fast fallback."
                            results.append(row)
            except Exception as e:
                results.append(_failed_lake_row(lake, str(e)))

    payload = _summarize_monitor(
        results,
        use_gee,
        offset=offset,
        limit=limit,
        has_more=has_more,
    )
    payload["inventory_total"] = total_inventory
    payload["next_offset"] = offset + len(lakes) if has_more else None
    payload["request_budget_sec"] = budget_sec
    return payload


def _prune_jobs() -> None:
    now = time.time()
    with _JOBS_LOCK:
        dead = [jid for jid, j in _JOBS.items() if now - float(j.get("created_at") or 0) > _JOB_TTL_SEC]
        for jid in dead:
            _JOBS.pop(jid, None)


def _snapshot_job(job: dict) -> dict:
    """Thread-safe shallow copy for API responses (result cloned)."""
    with _JOBS_LOCK:
        snap = {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "use_gee": job.get("use_gee"),
            "done": int(job.get("done") or 0),
            "total": int(job.get("total") or 0),
            "current_lake": job.get("current_lake"),
            "error": job.get("error"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "finished_at": job.get("finished_at"),
            "partial": job.get("status") == "running",
            "lakes": list(job.get("lakes") or []),
            "result": copy.deepcopy(job.get("result")) if job.get("result") is not None else None,
        }
    snap["gee"] = gee_status()
    return snap


def _run_monitor_job(job_id: str, lake_rows: list[dict], use_gee: bool) -> None:
    """Score lakes in a background thread — not bound by Render HTTP proxy timeout."""
    global _ACTIVE_JOB_ID
    shared_temp = fetch_temperature(35.92, 74.31)
    results: list = []
    try:
        for i, row in enumerate(lake_rows):
            with _JOBS_LOCK:
                job = _JOBS.get(job_id)
                if not job or job.get("cancel"):
                    return
                job["current_lake"] = row.get("name")
                job["updated_at"] = time.time()

            try:
                assessed = assess_lake(
                    lake_id=row.get("id"),
                    name=row.get("name") or "Lake",
                    area_ha=row.get("area_ha"),
                    lat=row.get("latitude"),
                    lon=row.get("longitude"),
                    use_gee=use_gee,
                    include_population=True,
                    shared_temp=shared_temp,
                )
            except Exception as e:
                # Build a minimal failed row without ORM
                assessed = {
                    "lake_id": row.get("id"),
                    "name": row.get("name"),
                    "latitude": row.get("latitude"),
                    "longitude": row.get("longitude"),
                    "area_ha": row.get("area_ha"),
                    "error": str(e),
                    "early_warning": classify_level(0),
                    "flood_impact": build_flood_impact(
                        area_ha=row.get("area_ha"),
                        lat=row.get("latitude"),
                        lon=row.get("longitude"),
                    ),
                    "flood_prediction": predict_flood_likelihood(
                        early_warning_score=0,
                        early_warning_level="Normal",
                        growth_pct_per_year=None,
                        temperature_c=None,
                        forecast_max_c=None,
                        elevation_m=None,
                        glacier_distance_km=None,
                        area_ha=row.get("area_ha"),
                    ),
                    "data_sources": {"mode": "error", "error": str(e)},
                }

            results.append(assessed)
            partial = _summarize_monitor(results, use_gee, offset=0, limit=len(results), has_more=True)
            partial["inventory_total"] = len(lake_rows)
            partial["job_id"] = job_id
            partial["partial"] = True

            with _JOBS_LOCK:
                job = _JOBS.get(job_id)
                if not job:
                    return
                job["done"] = i + 1
                job["lakes"] = list(results)
                job["result"] = partial
                job["updated_at"] = time.time()

        final = _summarize_monitor(results, use_gee, offset=0, limit=len(results), has_more=False)
        final["inventory_total"] = len(lake_rows)
        final["job_id"] = job_id
        final["partial"] = False
        final["next_offset"] = None

        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job["status"] = "done"
                job["done"] = len(results)
                job["lakes"] = list(results)
                job["result"] = final
                job["current_lake"] = None
                job["finished_at"] = time.time()
                job["updated_at"] = time.time()
    except Exception as e:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = f"{e}\n{traceback.format_exc()[-500:]}"
                job["finished_at"] = time.time()
                job["updated_at"] = time.time()
                if results:
                    partial = _summarize_monitor(results, use_gee, offset=0, limit=len(results), has_more=False)
                    partial["partial"] = True
                    partial["error"] = str(e)
                    job["result"] = partial
                    job["lakes"] = list(results)
    finally:
        with _JOBS_LOCK:
            if _ACTIVE_JOB_ID == job_id:
                _ACTIVE_JOB_ID = None


@router.post("/monitor/jobs")
@limiter.limit("20/minute")
def start_monitor_job(
    request: Request,
    use_gee: bool = Query(True, description="GEE growth + WorldPop (background job for Render)."),
    limit: int = Query(46, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Start a background Flood Monitoring scan.

    Use this on Render instead of a single long /monitor call. Poll
    GET /early-warning/monitor/jobs/{job_id} until status is done|error.
    Short polls keep free dynos awake without hitting the ~30s HTTP timeout.
    """
    global _ACTIVE_JOB_ID
    _prune_jobs()

    # Reuse an in-flight job with the same mode instead of stacking GEE work.
    reuse_id = None
    with _JOBS_LOCK:
        if _ACTIVE_JOB_ID and _ACTIVE_JOB_ID in _JOBS:
            active = _JOBS[_ACTIVE_JOB_ID]
            if active.get("status") == "running" and bool(active.get("use_gee")) == bool(use_gee):
                reuse_id = _ACTIVE_JOB_ID
    if reuse_id:
        with _JOBS_LOCK:
            active = _JOBS.get(reuse_id)
        if active:
            return _snapshot_job(active)

    lakes: List[LakeModel] = (
        db.query(LakeModel)
        .order_by(LakeModel.area_ha.desc())
        .limit(limit)
        .all()
    )
    lake_rows = [
        {
            "id": lake.id,
            "name": lake.name,
            "area_ha": lake.area_ha,
            "latitude": lake.latitude,
            "longitude": lake.longitude,
        }
        for lake in lakes
    ]

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "status": "running",
        "use_gee": bool(use_gee),
        "done": 0,
        "total": len(lake_rows),
        "current_lake": None,
        "lakes": [],
        "result": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
        "finished_at": None,
        "cancel": False,
    }

    with _JOBS_LOCK:
        _JOBS[job_id] = job
        _ACTIVE_JOB_ID = job_id

    t = threading.Thread(
        target=_run_monitor_job,
        args=(job_id, lake_rows, bool(use_gee)),
        name=f"ew-monitor-{job_id}",
        daemon=True,
    )
    t.start()
    return _snapshot_job(job)


@router.get("/monitor/jobs/{job_id}")
@limiter.limit("120/minute")
def get_monitor_job(request: Request, job_id: str):
    """Poll background monitor job (cheap; safe under Render proxy limits)."""
    _prune_jobs()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return _snapshot_job(job)


@router.get("/basins")
@limiter.limit("30/minute")
def list_basins(request: Request, db: Session = Depends(get_db)):
    """
    GLOF basin catalogue: drainage corridors, storage nodes, and lakes assigned
    to each basin from the inventory.
    """
    lakes = db.query(LakeModel).all()
    by_basin: dict[str, list] = {b["id"]: [] for b in BASINS}
    for lake in lakes:
        basin = assign_basin(lake.latitude, lake.longitude)
        by_basin.setdefault(basin["id"], []).append({
            "id": lake.id,
            "name": lake.name,
            "area_ha": lake.area_ha,
            "latitude": lake.latitude,
            "longitude": lake.longitude,
            "estimated_volume_m3": estimate_outburst_volume_m3(lake.area_ha),
        })

    payload = []
    for basin in BASINS:
        if basin.get("is_fallback") and not by_basin.get(basin["id"]):
            continue
        lake_list = by_basin.get(basin["id"], [])
        total_area = sum(float(l.get("area_ha") or 0) for l in lake_list)
        total_vol = sum(float(l.get("estimated_volume_m3") or 0) for l in lake_list)
        payload.append({
            **{k: v for k, v in basin.items() if k != "bbox"},
            "bbox": basin["bbox"],
            "lake_count": len(lake_list),
            "lakes": sorted(lake_list, key=lambda x: float(x.get("area_ha") or 0), reverse=True),
            "total_lake_area_ha": round(total_area, 1),
            "total_estimated_outburst_volume_m3": round(total_vol, 0),
            "total_estimated_outburst_volume_million_m3": round(total_vol / 1e6, 3) if total_vol else 0,
        })

    # Priority order
    prio = {"Critical": 0, "Warning": 1, "Watch": 2, "Normal": 3}
    payload.sort(key=lambda b: (prio.get(b.get("monitoring_priority"), 9), -b.get("lake_count", 0)))
    return {
        "total_basins": len(payload),
        "basins": payload,
        "notes": [
            "Basins are planning polygons for GB / Northern Pakistan catchments.",
            "Downstream storage nodes are valleys, gorges, and plains where outburst water can temporarily pond or route.",
            "Volumes assume mean depth ~12 m (heuristic — not bathymetry).",
            "Use with Flood Monitoring scores for operational prioritisation.",
        ],
    }


@router.get("/basins/{basin_id}")
@limiter.limit("30/minute")
def get_basin(request: Request, basin_id: str, db: Session = Depends(get_db)):
    basin = next((b for b in BASINS if b["id"] == basin_id), None)
    if not basin:
        raise HTTPException(status_code=404, detail="Basin not found")
    lakes = db.query(LakeModel).all()
    assigned = []
    for lake in lakes:
        if assign_basin(lake.latitude, lake.longitude)["id"] == basin_id:
            assigned.append({
                "id": lake.id,
                "name": lake.name,
                "area_ha": lake.area_ha,
                "latitude": lake.latitude,
                "longitude": lake.longitude,
                "estimated_volume_m3": estimate_outburst_volume_m3(lake.area_ha),
            })
    total_vol = sum(float(l.get("estimated_volume_m3") or 0) for l in assigned)
    return {
        **{k: v for k, v in basin.items()},
        "lakes": assigned,
        "lake_count": len(assigned),
        "total_estimated_outburst_volume_m3": total_vol,
        "cascade_risk": (
            "High" if len(assigned) >= 5 or any((l.get("area_ha") or 0) >= 40 for l in assigned)
            else "Moderate" if assigned else "Low"
        ),
    }
