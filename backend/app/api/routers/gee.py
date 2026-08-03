# app/api/routers/gee.py

from fastapi import APIRouter, HTTPException, Request, Query
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.utils.gee_detection import (
    detect_glacial_lakes,
    estimate_population,
    estimate_lake_exposure,
    get_historical_areas,
    get_lake_thumbnail,
    gee_status,
)

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(
    prefix="/gee",
    tags=["Google Earth Engine"]
)


def _gee_http_error(e: Exception) -> HTTPException:
    msg = str(e)
    code = 503 if "unavailable" in msg.lower() or "not authenticated" in msg.lower() else 500
    return HTTPException(status_code=code, detail=msg)


@router.get("/status")
@limiter.limit("60/minute")
def status(request: Request):
    """Check whether GEE is authenticated on this server."""
    return gee_status()


@router.get("/detect-lakes")
@limiter.limit("3/minute")
def detect_lakes(request: Request):
    try:
        lakes = detect_glacial_lakes()
        return {
            "message": "Detection completed successfully",
            "total_detected": len(lakes),
            "lakes": lakes
        }
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
    Estimate lake area for years 2015, 2017, 2019, 2021, 2023, 2025
    using Sentinel-2 NDWI summer composites.
    Example: /gee/historical?lat=36.318&lon=74.865
    """
    try:
        return get_historical_areas(lat, lon)
    except Exception as e:
        raise _gee_http_error(e)


@router.get("/thumbnail")
@limiter.limit("15/minute")
def lake_thumbnail(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    mode: str = Query("ndwi", pattern="^(rgb|ndwi)$"),
    buffer_m: float = Query(2000, ge=500, le=50000),
):
    """
    Live satellite thumbnail around the lake.
    mode=rgb  → true color
    mode=ndwi → water-highlighted
    Example: /gee/thumbnail?lat=36.318&lon=74.865&mode=ndwi
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


@router.get("/thumbnail-image")
@limiter.limit("20/minute")
def lake_thumbnail_image(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
    mode: str = Query("ndwi", pattern="^(rgb|ndwi)$"),
    buffer_m: float = Query(8000, ge=500, le=50000),
):
    """
    Proxy GEE thumbnail as a real PNG so browsers can display it in <img src>.
    Earth Engine v1 thumb URLs sometimes fail as direct image sources in the browser.
    """
    import io
    import urllib.request
    from fastapi.responses import Response, StreamingResponse

    try:
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
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=600",
                "X-GEE-Source": "Sentinel-2",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _gee_http_error(e)
