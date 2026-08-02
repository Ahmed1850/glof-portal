"""
GLOF Early Warning / Flood Monitoring API
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.lake import Lake as LakeModel
from app.services.early_warning import compute_early_warning, classify_level
from app.utils.gee_detection import GEE_READY, _init_ee, gee_status

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/early-warning", tags=["Early Warning / Flood Monitoring"])

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


def _http_get_json(url: str, timeout: float = 20.0) -> dict:
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
        data = _http_get_json(f"https://api.open-meteo.com/v1/elevation?{qs}")
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
        data = _http_get_json(f"https://api.open-elevation.com/api/v1/lookup?{qs}")
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
    """Percent growth per year from ~5-year Sentinel-2 NDWI series."""
    if not GEE_READY and not _init_ee():
        return None
    try:
        from app.utils.gee_detection import get_historical_areas
        hist = get_historical_areas(lat, lon)
        years = [y for y in (hist.get("years") or []) if y.get("area_ha") is not None]
        if len(years) < 2:
            return None
        years = sorted(years, key=lambda y: y["year"])
        first, last = years[0], years[-1]
        span = max(1, int(last["year"]) - int(first["year"]))
        a0, a1 = float(first["area_ha"]), float(last["area_ha"])
        if a0 <= 0:
            return 50.0 if a1 > 0 else 0.0
        total_pct = ((a1 - a0) / a0) * 100.0
        return round(total_pct / span, 2)
    except Exception:
        return None


def fetch_population_exposure(lat: float, lon: float, area_ha: Optional[float]) -> dict:
    """Use GEE WorldPop when available; otherwise 0 with note."""
    from app.utils.risk import calculate_risk
    risk = calculate_risk(area_ha or 0)
    if not GEE_READY and not _init_ee():
        return {
            "danger_population": 0,
            "warning_population": 0,
            "source": "unavailable",
            "risk_level": risk,
        }
    try:
        from app.utils.gee_detection import estimate_lake_exposure
        return estimate_lake_exposure(lat, lon, risk)
    except Exception as e:
        return {
            "danger_population": 0,
            "warning_population": 0,
            "source": f"error: {e}",
            "risk_level": risk,
        }


def assess_lake(
    *,
    lake_id: Optional[int],
    name: str,
    area_ha: Optional[float],
    lat: Optional[float],
    lon: Optional[float],
    use_gee: bool = True,
    include_population: bool = True,
) -> dict:
    if lat is None or lon is None:
        result = compute_early_warning(area_ha=area_ha, include_glacier=False)
        return {
            "lake_id": lake_id,
            "name": name,
            "latitude": lat,
            "longitude": lon,
            "early_warning": result,
            "data_sources": {"note": "Missing coordinates — area-only score"},
        }

    elevation_m, elevation_source = fetch_elevation_m(lat, lon)
    glacier_km = round(nearest_glacier_km(lat, lon), 2)

    growth = None
    pop = {"danger_population": 0, "warning_population": 0, "source": "skipped"}
    if use_gee:
        growth = fetch_growth_from_gee(lat, lon)
        if include_population:
            pop = fetch_population_exposure(lat, lon, area_ha)

    result = compute_early_warning(
        area_ha=area_ha,
        growth_pct_per_year=growth,
        elevation_m=elevation_m,
        glacier_distance_km=glacier_km,
        danger_population=pop.get("danger_population"),
        warning_population=pop.get("warning_population"),
        include_glacier=True,
    )

    return {
        "lake_id": lake_id,
        "name": name,
        "latitude": lat,
        "longitude": lon,
        "area_ha": area_ha,
        "early_warning": result,
        "data_sources": {
            "elevation": elevation_source,
            "growth": "GEE Sentinel-2 NDWI" if growth is not None else "unavailable",
            "population": pop.get("source"),
            "glacier": "reference glacier points (GB)",
            "gee_ready": bool(GEE_READY or _init_ee()),
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


@router.get("/monitor")
@limiter.limit("5/minute")
def monitor_all_lakes(
    request: Request,
    use_gee: bool = Query(
        False,
        description="If true, query GEE for growth + population (slow). Default fast hybrid score.",
    ),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Flood monitoring board: score all registered lakes.

    Fast mode (default): area + elevation (Open-Meteo) + glacier heuristic.
    GEE mode: also growth rate + WorldPop exposure (requires EE credentials).
    """
    lakes: List[LakeModel] = (
        db.query(LakeModel)
        .order_by(LakeModel.area_ha.desc())
        .limit(limit)
        .all()
    )

    results = []
    # Parallelize lightweight elevation fetches; GEE kept sequential-ish via workers=3
    workers = 6 if not use_gee else 3

    def _job(lake: LakeModel):
        return assess_lake(
            lake_id=lake.id,
            name=lake.name,
            area_ha=lake.area_ha,
            lat=lake.latitude,
            lon=lake.longitude,
            use_gee=use_gee,
            include_population=use_gee,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_job, lake): lake for lake in lakes}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                lake = futs[fut]
                results.append({
                    "lake_id": lake.id,
                    "name": lake.name,
                    "error": str(e),
                    "early_warning": classify_level(0),
                })

    # Sort Critical → Normal, then by score
    order = {"Critical": 0, "Warning": 1, "Watch": 2, "Normal": 3}
    results.sort(
        key=lambda r: (
            order.get((r.get("early_warning") or {}).get("level"), 9),
            -float((r.get("early_warning") or {}).get("score") or 0),
        )
    )

    counts = {"Critical": 0, "Warning": 0, "Watch": 0, "Normal": 0}
    for r in results:
        lvl = (r.get("early_warning") or {}).get("level")
        if lvl in counts:
            counts[lvl] += 1

    return {
        "total": len(results),
        "counts": counts,
        "use_gee": use_gee,
        "gee": gee_status(),
        "lakes": results,
    }
