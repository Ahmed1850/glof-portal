# app/api/routers/gee.py

import threading
import time
import urllib.request
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.db.session import get_db
from app.utils.gee_detection import (
    estimate_population,
    estimate_lake_exposure,
    get_historical_areas,
    get_lake_thumbnail,
    get_lake_thumbnails_pack,
    gee_status,
)
from app.services.satellite_cascade import detect_lakes_cascade, cascade_status

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(
    prefix="/gee",
    tags=["Google Earth Engine"]
)

# PNG proxy cache — basin UI requests 3 modes × many switches; cache stops 429 storms
_PNG_CACHE: dict[str, tuple[float, bytes, str]] = {}
_PNG_LOCK = threading.Lock()
_PNG_TTL_SEC = 20 * 60  # 20 minutes
_PNG_MAX_ENTRIES = 120


def _gee_http_error(e: Exception) -> HTTPException:
    msg = str(e)
    code = 503 if "unavailable" in msg.lower() or "not authenticated" in msg.lower() else 500
    return HTTPException(status_code=code, detail=msg)


def _png_cache_key(lat: float, lon: float, mode: str, buffer_m: float) -> str:
    return f"{round(float(lat), 4)}:{round(float(lon), 4)}:{(mode or 'ndwi').lower()}:{int(buffer_m)}"


def _png_cache_get(key: str) -> Optional[tuple[bytes, str]]:
    with _PNG_LOCK:
        item = _PNG_CACHE.get(key)
        if not item:
            return None
        ts, data, ctype = item
        if time.time() - ts > _PNG_TTL_SEC:
            _PNG_CACHE.pop(key, None)
            return None
        return data, ctype


def _png_cache_set(key: str, data: bytes, content_type: str) -> None:
    with _PNG_LOCK:
        if len(_PNG_CACHE) >= _PNG_MAX_ENTRIES:
            oldest = sorted(_PNG_CACHE.items(), key=lambda kv: kv[1][0])[:30]
            for k, _ in oldest:
                _PNG_CACHE.pop(k, None)
        _PNG_CACHE[key] = (time.time(), data, content_type)


def _fetch_thumb_png(lat: float, lon: float, mode: str, buffer_m: float) -> tuple[bytes, str, str]:
    """Return (bytes, content_type, source_label). Uses URL + PNG caches."""
    key = _png_cache_key(lat, lon, mode, buffer_m)
    hit = _png_cache_get(key)
    if hit:
        return hit[0], hit[1], "cache"

    result = get_lake_thumbnail(lat, lon, buffer_m=buffer_m, mode=mode)
    if result.get("error") or not result.get("url"):
        raise HTTPException(
            status_code=503,
            detail=result.get("error") or "No thumbnail URL from Earth Engine",
        )
    req = urllib.request.Request(
        result["url"],
        headers={"User-Agent": "GLOF-Portal/1.0"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "image/png")
    _png_cache_set(key, data, content_type)
    sensor = "Sentinel-1-SAR" if (mode or "").lower() == "sar" else "Sentinel-2"
    return data, content_type, sensor


@router.get("/status")
@limiter.limit("60/minute")
def status(request: Request):
    """
    GEE auth + multi-source cascade health:
      1) GEE Sentinel-2
      2) Planetary Computer Sentinel-2
      3) Sentinel-1 SAR
      4) Database inventory
    """
    try:
        return cascade_status()
    except Exception:
        # Never break status endpoint
        return {"gee": gee_status(), "cascade": [], "error": "cascade status partial"}


@router.get("/detect-lakes")
@limiter.limit("3/minute")
def detect_lakes(request: Request, db: Session = Depends(get_db)):
    """
    Multi-source glacial lake detection cascade:

      1. Google Earth Engine (Sentinel-2 NDWI)
      2. Microsoft Planetary Computer (Sentinel-2) — if GEE fails / high clouds
      3. Sentinel-1 SAR — if optical still cloudy
      4. Database + Known Lakes Inventory — offline fallback
    """
    try:
        result = detect_lakes_cascade(db=db)
        return result
    except Exception as e:
        raise _gee_http_error(e)


@router.get("/population")
@limiter.limit("10/minute")
def get_population(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    radius_km: float = Query(5.0, ge=0.5, le=30)
):
    try:
        result = estimate_population(lat, lon, radius_km)
        if result.get("error") and result.get("population", 0) == 0:
            raise HTTPException(status_code=500, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise _gee_http_error(e)


@router.get("/exposure")
@limiter.limit("10/minute")
def get_exposure(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    risk_level: str = Query("High")
):
    try:
        if risk_level not in ["High", "Medium", "Low"]:
            risk_level = "High"
        return estimate_lake_exposure(lat, lon, risk_level)
    except Exception as e:
        raise _gee_http_error(e)


@router.get("/historical")
@limiter.limit("5/minute")
def historical_area(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...)
):
    """
    Hybrid historical lake area (2015–2025):
      - Sentinel-2 NDWI summer composites (optical)
      - Sentinel-1 SAR VV (cloud-penetrating fallback / dual series)
    Example: /gee/historical?lat=36.318&lon=74.865
    """
    try:
        return get_historical_areas(lat, lon)
    except Exception as e:
        raise _gee_http_error(e)


@router.get("/thumbnail")
@limiter.limit("90/minute")
def lake_thumbnail(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    mode: str = Query("ndwi", pattern="^(rgb|ndwi|sar)$"),
    buffer_m: float = Query(2000, ge=500, le=50000),
):
    """
    Live satellite thumbnail around the lake.
    mode=rgb  → Sentinel-2 true color
    mode=ndwi → Sentinel-2 water highlight
    mode=sar  → Sentinel-1 VV backscatter (all-weather)
    Example: /gee/thumbnail?lat=36.318&lon=74.865&mode=sar
    """
    try:
        result = get_lake_thumbnail(lat, lon, buffer_m=buffer_m, mode=mode)
        if result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise _gee_http_error(e)


@router.get("/thumbnails")
@limiter.limit("40/minute")
def lake_thumbnails_pack(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    buffer_m: float = Query(2000, ge=500, le=50000),
    modes: str = Query("ndwi,rgb,sar"),
):
    """
    Batch thumbnail URLs in one request (basin / Find Lake UI).
    modes=ndwi,rgb,sar
    """
    try:
        mode_list = [m.strip() for m in (modes or "").split(",") if m.strip()]
        if not mode_list:
            mode_list = ["ndwi", "rgb", "sar"]
        return get_lake_thumbnails_pack(lat, lon, buffer_m=buffer_m, modes=mode_list)
    except Exception as e:
        raise _gee_http_error(e)


@router.get("/thumbnail-image")
@limiter.limit("120/minute")
def lake_thumbnail_image(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    mode: str = Query("ndwi", pattern="^(rgb|ndwi|sar)$"),
    buffer_m: float = Query(8000, ge=500, le=50000),
):
    """
    Proxy GEE thumbnail as a real PNG so browsers can display it in <img src>.
    Cached ~20 min to avoid rate-limit storms when switching basins.
    """
    try:
        data, content_type, source = _fetch_thumb_png(lat, lon, mode, buffer_m)
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=600",
                "X-GEE-Source": source,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _gee_http_error(e)
