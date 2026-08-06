# app/utils/gee_detection.py

import json
import os
import tempfile
from functools import wraps

ee = None
GEE_READY = False
GEE_ERROR = None
GEE_MODE = None  # "service_account" | "default" | None


def _credentials_from_env():
    """
    Resolve Earth Engine credentials for local + Render.

    Supported env vars (first match wins):
      1. EE_CREDENTIALS_JSON  — full service-account JSON string (best for Render secrets)
      2. EE_SERVICE_ACCOUNT_JSON / GOOGLE_APPLICATION_CREDENTIALS — path to JSON file
         OR a raw JSON string starting with '{'
    """
    from google.oauth2 import service_account

    scopes = [
        "https://www.googleapis.com/auth/earthengine",
        "https://www.googleapis.com/auth/cloud-platform",
    ]

    raw = os.getenv("EE_CREDENTIALS_JSON", "").strip()
    if raw:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=scopes), "service_account"

    for key in ("GOOGLE_APPLICATION_CREDENTIALS", "EE_SERVICE_ACCOUNT_JSON"):
        val = (os.getenv(key) or "").strip()
        if not val:
            continue
        if val.startswith("{"):
            info = json.loads(val)
            # Persist so other Google libs can find it if needed
            path = os.path.join(tempfile.gettempdir(), "ee-service-account.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(val)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
            return service_account.Credentials.from_service_account_info(info, scopes=scopes), "service_account"
        if os.path.isfile(val):
            return service_account.Credentials.from_service_account_file(val, scopes=scopes), "service_account"

    return None, None


def _init_ee():
    """Initialize Earth Engine if credentials exist; never crash the whole API."""
    global ee, GEE_READY, GEE_ERROR, GEE_MODE
    if GEE_READY:
        return True
    try:
        import ee as _ee
        ee = _ee
        project = os.getenv("EE_PROJECT", "glof-portal-502521")

        credentials, mode = _credentials_from_env()
        if credentials is not None:
            ee.Initialize(credentials=credentials, project=project)
            GEE_MODE = mode
        else:
            # Local: earthengine authenticate / application-default credentials
            ee.Initialize(project=project)
            GEE_MODE = "default"

        GEE_READY = True
        GEE_ERROR = None
        print(f"GEE initialized (mode={GEE_MODE}, project={project})")
        return True
    except Exception as e:
        GEE_READY = False
        GEE_MODE = None
        GEE_ERROR = str(e)
        print(f"GEE not initialized (API will still run): {e}")
        return False


def gee_status() -> dict:
    """Public diagnostic payload for /gee/status."""
    if not GEE_READY:
        _init_ee()
    return {
        "ready": GEE_READY,
        "mode": GEE_MODE,
        "project": os.getenv("EE_PROJECT", "glof-portal-502521"),
        "error": None if GEE_READY else GEE_ERROR,
        "hint": None if GEE_READY else (
            "Add a Google service account on Render: set secret EE_CREDENTIALS_JSON "
            "to the full service-account JSON, EE_PROJECT to your GCP project id, "
            "and register the service account email at https://signup.earthengine.google.com/#!/service_accounts"
        ),
    }


# Attempt once at import — failures are non-fatal
_init_ee()


def _require_ee(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not GEE_READY and not _init_ee():
            raise RuntimeError(
                f"Google Earth Engine unavailable: {GEE_ERROR or 'not authenticated'}. "
                "On Render, set secret env EE_CREDENTIALS_JSON (service account JSON) "
                "and EE_PROJECT, then register the SA email with Earth Engine."
            )
        return fn(*args, **kwargs)
    return wrapper


def _s2_date_window(summer_only: bool = True) -> tuple[str, str]:
    """Rolling window for optical composites (prefer recent summer if possible)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    year = now.year
    # Prefer current-year summer; if before July, use previous year
    if summer_only:
        if now.month < 7:
            year = year - 1
        return f"{year}-07-01", f"{year}-09-30"
    # Last ~120 days
    from datetime import timedelta
    end = now
    start = end - timedelta(days=120)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _features_to_lake_list(features: list, source: str, name_prefix: str = "GLOF Lake") -> list:
    results = []
    for i, feature in enumerate(features, 1):
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        try:
            coords = geom.get("coordinates") or []
            ring = coords[0] if geom.get("type") == "Polygon" and coords else None
            if ring:
                lons = [c[0] for c in ring]
                lats = [c[1] for c in ring]
                centroid_lon = sum(lons) / len(lons)
                centroid_lat = sum(lats) / len(lats)
            else:
                centroid_lon = centroid_lat = None
        except Exception:
            centroid_lon = centroid_lat = None

        area_ha = props.get("area_ha", 0) or 0
        results.append({
            "name": f"{name_prefix} {i}",
            "area_ha": round(float(area_ha), 2),
            "latitude": round(centroid_lat, 5) if centroid_lat is not None else None,
            "longitude": round(centroid_lon, 5) if centroid_lon is not None else None,
            "source": source,
        })

    results = [r for r in results if r.get("latitude") is not None]
    results = sorted(results, key=lambda x: x["area_ha"], reverse=True)
    for i, lake in enumerate(results, 1):
        lake["name"] = f"{name_prefix} {i}"
    return results


@_require_ee
def detect_glacial_lakes_s2_detailed(
    max_cloud_pct: float = 30.0,
    cloud_filter: float = 40.0,
    min_area_ha: float = 8.0,
    limit: int = 25,
) -> dict:
    """
    Sentinel-2 NDWI detection with cloud diagnostics for the cascade.

    Returns:
      ok, lakes, cloudy, mean_cloud_pct, scene_count, detail, ...
    """
    date_start, date_end = _s2_date_window(summer_only=True)
    roi = ee.Geometry.Rectangle([73.5, 35.2, 76.8, 37.2])
    elevation = ee.Image("USGS/SRTMGL1_003").select("elevation")

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(roi)
        .filterDate(date_start, date_end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_filter))
    )

    # Cloud + count diagnostics in one server round-trip where possible
    scene_count = collection.size()
    mean_cloud = collection.aggregate_mean("CLOUDY_PIXEL_PERCENTAGE")
    stats = ee.Dictionary({
        "scene_count": scene_count,
        "mean_cloud": mean_cloud,
    }).getInfo()

    n_scenes = int(stats.get("scene_count") or 0)
    mean_cloud_val = stats.get("mean_cloud")
    try:
        mean_cloud_val = float(mean_cloud_val) if mean_cloud_val is not None else None
    except (TypeError, ValueError):
        mean_cloud_val = None

    if n_scenes == 0:
        return {
            "ok": False,
            "lakes": [],
            "cloudy": True,
            "mean_cloud_pct": None,
            "scene_count": 0,
            "date_start": date_start,
            "date_end": date_end,
            "detail": f"No Sentinel-2 scenes under {cloud_filter}% cloud for {date_start}→{date_end}",
        }

    cloudy = mean_cloud_val is not None and mean_cloud_val > max_cloud_pct

    s2 = collection.median().clip(roi)
    green = s2.select("B3")
    nir = s2.select("B8")
    ndwi = green.subtract(nir).divide(green.add(nir)).rename("NDWI")

    water = ndwi.gt(0.25)
    high_elevation = elevation.gt(3200)
    glacial_water = water.And(high_elevation)

    vectors = glacial_water.selfMask().reduceToVectors(
        geometry=roi,
        scale=30,
        geometryType="polygon",
        eightConnected=False,
        labelProperty="water",
        maxPixels=1e10,
    )

    def add_area(feature):
        area_ha = feature.geometry().area(maxError=1).divide(10000)
        return feature.set({"area_ha": area_ha})

    vectors = vectors.map(add_area)
    significant = vectors.filter(ee.Filter.gt("area_ha", min_area_ha))
    lakes_info = significant.limit(limit).getInfo()
    lakes = _features_to_lake_list(
        lakes_info.get("features", []),
        source="GEE Sentinel-2 NDWI + Elevation Filter",
        name_prefix="GLOF Lake",
    )

    if cloudy:
        return {
            "ok": False,
            "lakes": lakes,
            "cloudy": True,
            "mean_cloud_pct": round(mean_cloud_val, 2) if mean_cloud_val is not None else None,
            "scene_count": n_scenes,
            "date_start": date_start,
            "date_end": date_end,
            "detail": (
                f"High cloud cover on GEE Sentinel-2 "
                f"(mean {mean_cloud_val:.1f}% > {max_cloud_pct}%)"
            ),
        }

    if not lakes:
        return {
            "ok": False,
            "lakes": [],
            "cloudy": False,
            "mean_cloud_pct": round(mean_cloud_val, 2) if mean_cloud_val is not None else None,
            "scene_count": n_scenes,
            "date_start": date_start,
            "date_end": date_end,
            "detail": "GEE Sentinel-2 composite OK but no lakes above area threshold",
        }

    return {
        "ok": True,
        "lakes": lakes,
        "cloudy": False,
        "mean_cloud_pct": round(mean_cloud_val, 2) if mean_cloud_val is not None else None,
        "scene_count": n_scenes,
        "date_start": date_start,
        "date_end": date_end,
        "detail": f"Detected {len(lakes)} lakes from {n_scenes} S2 scenes",
    }


@_require_ee
def detect_glacial_lakes():
    """
    Backward-compatible lake list from GEE Sentinel-2.
    Prefer detect_lakes_cascade() for production multi-source flow.
    """
    result = detect_glacial_lakes_s2_detailed()
    return result.get("lakes") or []


@_require_ee
def detect_glacial_lakes_sar(
    min_area_ha: float = 8.0,
    limit: int = 25,
    vv_threshold_db: float = -16.0,
) -> dict:
    """
    Detect glacial lakes using Sentinel-1 SAR (sees through clouds).

    Returns the same lake dict shape as optical detection, plus ok/detail meta.
    """
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=90)
    date_start = start.strftime("%Y-%m-%d")
    date_end = end.strftime("%Y-%m-%d")

    roi = ee.Geometry.Rectangle([73.5, 35.2, 76.8, 37.2])
    elevation = ee.Image("USGS/SRTMGL1_003").select("elevation")

    s1 = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(roi)
        .filterDate(date_start, date_end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
        .median()
        .clip(roi)
    )

    # SAR water detection (low VV backscatter) + high elevation glacial filter
    water = s1.lt(vv_threshold_db)
    glacial_water = water.And(elevation.gt(3200))

    vectors = glacial_water.selfMask().reduceToVectors(
        geometry=roi,
        scale=40,
        geometryType="polygon",
        eightConnected=False,
        maxPixels=1e10,
    )

    def add_area(feature):
        area_ha = feature.geometry().area(maxError=1).divide(10000)
        return feature.set({"area_ha": area_ha})

    vectors = vectors.map(add_area)
    significant = vectors.filter(ee.Filter.gt("area_ha", min_area_ha))
    lakes_info = significant.limit(limit).getInfo()
    features = lakes_info.get("features", [])
    lakes = _features_to_lake_list(
        features,
        source="GEE Sentinel-1 SAR",
        name_prefix="SAR Lake",
    )

    return {
        "ok": len(lakes) > 0,
        "lakes": lakes,
        "features": features,
        "message": f"SAR detection completed ({len(lakes)} water bodies)",
        "detail": f"Sentinel-1 IW VV median {date_start}→{date_end}",
        "date_start": date_start,
        "date_end": date_end,
        "source": "GEE Sentinel-1 SAR",
    }


@_require_ee
def estimate_population(lat: float, lon: float, radius_km: float = 5.0) -> dict:
    if lat is None or lon is None:
        return {"population": 0, "radius_km": radius_km, "error": "Missing coordinates"}

    try:
        point = ee.Geometry.Point([lon, lat])
        buffer = point.buffer(radius_km * 1000)

        pop = (ee.ImageCollection("WorldPop/GP/100m/pop")
               .filter(ee.Filter.eq("country", "PAK"))
               .sort("year", False)
               .first())

        if pop is None:
            pop = ee.ImageCollection("WorldPop/GP/100m/pop").mosaic()

        stats = pop.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=buffer,
            scale=100,
            maxPixels=1e9
        ).getInfo()

        population = 0
        if stats:
            population = stats.get("population") or stats.get(list(stats.keys())[0], 0) or 0

        return {
            "population": int(round(float(population))),
            "radius_km": radius_km,
            "latitude": lat,
            "longitude": lon,
            "source": "WorldPop via Google Earth Engine"
        }
    except Exception as e:
        return {"population": 0, "radius_km": radius_km, "error": str(e)}


@_require_ee
def estimate_lake_exposure(lat: float, lon: float, risk_level: str = "High") -> dict:
    """
    Danger + warning population in a single GEE getInfo() call.
    (Previously 2 sequential WorldPop reduceRegions — too slow for Render.)
    """
    if risk_level == "High":
        danger_km, warning_km = 5.0, 10.0
    elif risk_level == "Medium":
        danger_km, warning_km = 2.0, 5.0
    else:
        danger_km, warning_km = 1.0, 2.0

    if lat is None or lon is None:
        return {
            "danger_zone_km": danger_km,
            "warning_zone_km": warning_km,
            "danger_population": 0,
            "warning_population": 0,
            "source": "WorldPop via GEE",
            "risk_level": risk_level,
            "error": "Missing coordinates",
        }

    try:
        point = ee.Geometry.Point([lon, lat])
        danger_geom = point.buffer(danger_km * 1000)
        warning_geom = point.buffer(warning_km * 1000)

        pop = (ee.ImageCollection("WorldPop/GP/100m/pop")
               .filter(ee.Filter.eq("country", "PAK"))
               .sort("year", False)
               .first())

        band = pop.bandNames().get(0)
        pop_img = pop.select([band], ["population"])

        danger_sum = pop_img.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=danger_geom,
            scale=100,
            maxPixels=1e8,
            bestEffort=True,
        ).get("population")
        warning_sum = pop_img.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=warning_geom,
            scale=100,
            maxPixels=1e8,
            bestEffort=True,
        ).get("population")

        stats = ee.Dictionary({
            "danger": danger_sum,
            "warning": warning_sum,
        }).getInfo()

        def _as_int(v):
            if v is None:
                return 0
            try:
                return int(round(float(v)))
            except (TypeError, ValueError):
                return 0

        danger_pop = _as_int(stats.get("danger") if stats else None)
        warning_pop = _as_int(stats.get("warning") if stats else None)

        return {
            "danger_zone_km": danger_km,
            "warning_zone_km": warning_km,
            "danger_population": danger_pop,
            "warning_population": max(warning_pop, danger_pop),
            "source": "WorldPop via GEE",
            "risk_level": risk_level,
        }
    except Exception as e:
        return {
            "danger_zone_km": danger_km,
            "warning_zone_km": warning_km,
            "danger_population": 0,
            "warning_population": 0,
            "source": "WorldPop via GEE",
            "risk_level": risk_level,
            "error": str(e),
        }


def _growth_from_areas(a0_m2, a1_m2, year_start: int, year_end: int) -> dict:
    """Shared growth math from two water areas (m²)."""
    if a0_m2 is None and a1_m2 is None:
        return {
            "growth_pct_per_year": None,
            "year_start": year_start,
            "year_end": year_end,
            "error": "No water area for either year",
        }
    a0 = float(a0_m2 or 0.0) / 10000.0
    a1 = float(a1_m2 or 0.0) / 10000.0
    span = max(1, int(year_end) - int(year_start))
    if a0 <= 0:
        growth = 50.0 if a1 > 0 else 0.0
    else:
        growth = ((a1 - a0) / a0) * 100.0 / span
    return {
        "growth_pct_per_year": round(float(growth), 2),
        "area_start_ha": round(a0, 2),
        "area_end_ha": round(a1, 2),
        "year_start": year_start,
        "year_end": year_end,
    }


def _s2_summer_water_area_m2(region, year: int):
    """EE expression: summer Sentinel-2 NDWI water area (m²) for one year."""
    start = f"{year}-07-01"
    end = f"{year}-09-30"
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        .median()
    )
    ndwi = s2.normalizedDifference(["B3", "B8"]).rename("NDWI")
    water = ndwi.gt(0.2).selfMask()
    return water.multiply(ee.Image.pixelArea()).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=30,
        maxPixels=1e7,
        bestEffort=True,
    ).get("NDWI")


def _sar_summer_water_area_m2(region, year: int, vv_threshold_db: float = -16.0):
    """EE expression: summer Sentinel-1 VV water area (m²) for one year (cloud-free)."""
    start = f"{year}-07-01"
    end = f"{year}-09-30"
    s1 = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
        .median()
    )
    water = s1.lt(vv_threshold_db).selfMask()
    return water.multiply(ee.Image.pixelArea()).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=region,
        scale=40,
        maxPixels=1e7,
        bestEffort=True,
    ).get("VV")


@_require_ee
def estimate_growth_pct_per_year(
    lat: float,
    lon: float,
    year_start: int = 2019,
    year_end: int = 2024,
    buffer_m: float = 1200,
) -> dict:
    """
    Fast growth rate for early-warning / flood monitoring.

    Cascade (one or two getInfo calls):
      1) Sentinel-2 NDWI summer pair
      2) Sentinel-1 SAR VV pair if optical fails / no water (cloudy monsoons)
    """
    if lat is None or lon is None:
        return {"growth_pct_per_year": None, "error": "Missing coordinates"}

    try:
        point = ee.Geometry.Point([lon, lat])
        region = point.buffer(buffer_m)

        # Optical first
        s2_stats = ee.Dictionary({
            "a0": _s2_summer_water_area_m2(region, year_start),
            "a1": _s2_summer_water_area_m2(region, year_end),
        }).getInfo()
        s2_result = _growth_from_areas(
            s2_stats.get("a0"), s2_stats.get("a1"), year_start, year_end
        )
        if s2_result.get("growth_pct_per_year") is not None and not s2_result.get("error"):
            # Prefer S2 when at least one year has measurable water
            a0 = s2_result.get("area_start_ha") or 0
            a1 = s2_result.get("area_end_ha") or 0
            if a0 > 0 or a1 > 0:
                s2_result["source"] = "GEE Sentinel-2 NDWI (2-year fast growth)"
                s2_result["sensor"] = "sentinel-2"
                return s2_result

        # SAR fallback (penetrates clouds)
        s1_stats = ee.Dictionary({
            "a0": _sar_summer_water_area_m2(region, year_start),
            "a1": _sar_summer_water_area_m2(region, year_end),
        }).getInfo()
        s1_result = _growth_from_areas(
            s1_stats.get("a0"), s1_stats.get("a1"), year_start, year_end
        )
        if s1_result.get("growth_pct_per_year") is not None and not s1_result.get("error"):
            s1_result["source"] = "GEE Sentinel-1 SAR (2-year fast growth, cloud-free)"
            s1_result["sensor"] = "sentinel-1-sar"
            s1_result["optical_fallback"] = True
            return s1_result

        # Both failed — return optical error if any
        out = s2_result if s2_result.get("error") else s1_result
        out["source"] = "GEE optical+SAR growth unavailable"
        out["sensor"] = None
        return out
    except Exception as e:
        return {
            "growth_pct_per_year": None,
            "year_start": year_start,
            "year_end": year_end,
            "error": str(e),
            "sensor": None,
        }


# ==================== HISTORICAL AREA (S2 NDWI + S1 SAR) ====================
@_require_ee
def estimate_area_for_year(lat: float, lon: float, year: int, buffer_m: float = 1500) -> dict:
    """
    Estimate water area (ha) for one summer season.
    Prefer Sentinel-2 NDWI; fall back to Sentinel-1 SAR if optical is empty/fails.
    """
    try:
        point = ee.Geometry.Point([lon, lat])
        region = point.buffer(buffer_m)

        stats = ee.Dictionary({
            "s2": _s2_summer_water_area_m2(region, year),
            "s1": _sar_summer_water_area_m2(region, year),
        }).getInfo()

        s2_m2 = stats.get("s2")
        s1_m2 = stats.get("s1")
        s2_ha = round(float(s2_m2) / 10000.0, 2) if s2_m2 is not None else None
        s1_ha = round(float(s1_m2) / 10000.0, 2) if s1_m2 is not None else None

        if s2_ha is not None and s2_ha > 0:
            return {
                "year": year,
                "area_ha": s2_ha,
                "optical_area_ha": s2_ha,
                "sar_area_ha": s1_ha,
                "latitude": lat,
                "longitude": lon,
                "sensor": "sentinel-2",
                "source": "GEE Sentinel-2 NDWI",
            }
        if s1_ha is not None:
            return {
                "year": year,
                "area_ha": s1_ha,
                "optical_area_ha": s2_ha,
                "sar_area_ha": s1_ha,
                "latitude": lat,
                "longitude": lon,
                "sensor": "sentinel-1-sar",
                "source": "GEE Sentinel-1 SAR (optical cloudy/empty)",
            }
        return {
            "year": year,
            "area_ha": s2_ha if s2_ha is not None else 0,
            "optical_area_ha": s2_ha,
            "sar_area_ha": s1_ha,
            "latitude": lat,
            "longitude": lon,
            "sensor": "sentinel-2",
            "source": "GEE Sentinel-2 NDWI",
        }
    except Exception as e:
        return {
            "year": year,
            "area_ha": None,
            "latitude": lat,
            "longitude": lon,
            "error": str(e),
        }


def _series_trend(series: list) -> str:
    valid = [s for s in series if s.get("area_ha") is not None]
    if len(valid) < 2:
        return "unknown"
    first = valid[0]["area_ha"]
    last = valid[-1]["area_ha"]
    if last > first * 1.1:
        return "growing"
    if last < first * 0.9:
        return "shrinking"
    return "stable"


@_require_ee
def get_historical_areas(lat: float, lon: float, years=None) -> dict:
    """
    Multi-year lake area series with dual sensors (batched getInfo for Render).

    - optical_years: Sentinel-2 NDWI summer composites
    - sar_years: Sentinel-1 SAR VV water (cloud-penetrating)
    - years: hybrid preferred series (S2 when water detected, else SAR)
    """
    if years is None:
        years = list(range(2015, 2026))

    point = ee.Geometry.Point([lon, lat])
    region = point.buffer(1500)

    # Two batched round-trips instead of 11×2 sequential getInfo calls
    optical_expr = {f"y{y}": _s2_summer_water_area_m2(region, y) for y in years}
    sar_expr = {f"y{y}": _sar_summer_water_area_m2(region, y) for y in years}

    try:
        optical_raw = ee.Dictionary(optical_expr).getInfo() or {}
    except Exception as e:
        optical_raw = {"_error": str(e)}
    try:
        sar_raw = ee.Dictionary(sar_expr).getInfo() or {}
    except Exception as e:
        sar_raw = {"_error": str(e)}

    optical_years = []
    sar_years = []
    hybrid = []

    for y in years:
        key = f"y{y}"
        o_m2 = optical_raw.get(key) if "_error" not in optical_raw else None
        s_m2 = sar_raw.get(key) if "_error" not in sar_raw else None

        o_ha = round(float(o_m2) / 10000.0, 2) if o_m2 is not None else None
        s_ha = round(float(s_m2) / 10000.0, 2) if s_m2 is not None else None

        optical_years.append({
            "year": y,
            "area_ha": o_ha,
            "latitude": lat,
            "longitude": lon,
            "sensor": "sentinel-2",
            "source": "GEE Sentinel-2 NDWI",
            **({"error": optical_raw["_error"]} if "_error" in optical_raw else {}),
        })
        sar_years.append({
            "year": y,
            "area_ha": s_ha,
            "latitude": lat,
            "longitude": lon,
            "sensor": "sentinel-1-sar",
            "source": "GEE Sentinel-1 SAR",
            **({"error": sar_raw["_error"]} if "_error" in sar_raw else {}),
        })

        if o_ha is not None and o_ha > 0:
            hybrid.append({
                "year": y,
                "area_ha": o_ha,
                "optical_area_ha": o_ha,
                "sar_area_ha": s_ha,
                "latitude": lat,
                "longitude": lon,
                "sensor": "sentinel-2",
                "source": "GEE Sentinel-2 NDWI",
            })
        elif s_ha is not None:
            hybrid.append({
                "year": y,
                "area_ha": s_ha,
                "optical_area_ha": o_ha,
                "sar_area_ha": s_ha,
                "latitude": lat,
                "longitude": lon,
                "sensor": "sentinel-1-sar",
                "source": "GEE Sentinel-1 SAR (optical cloudy/empty)",
            })
        else:
            hybrid.append({
                "year": y,
                "area_ha": o_ha if o_ha is not None else s_ha,
                "optical_area_ha": o_ha,
                "sar_area_ha": s_ha,
                "latitude": lat,
                "longitude": lon,
                "sensor": "sentinel-2" if o_ha is not None else ("sentinel-1-sar" if s_ha is not None else None),
                "source": "GEE hybrid (no water detected)",
            })

    return {
        "latitude": lat,
        "longitude": lon,
        "years": hybrid,
        "optical_years": optical_years,
        "sar_years": sar_years,
        "trend": _series_trend(hybrid),
        "optical_trend": _series_trend(optical_years),
        "sar_trend": _series_trend(sar_years),
        "source": "GEE hybrid Sentinel-2 NDWI + Sentinel-1 SAR",
        "sensors": ["sentinel-2", "sentinel-1-sar"],
        "notes": [
            "Primary series prefers Sentinel-2 NDWI when water is detected.",
            "Sentinel-1 SAR fills cloudy monsoon summers (all-weather).",
            "SAR VV threshold ≈ −16 dB; areas are planning-grade, not survey-grade.",
        ],
    }


# ==================== LIVE SATELLITE THUMBNAILS ====================
@_require_ee
def get_lake_thumbnail(lat: float, lon: float, buffer_m: float = 2000, mode: str = "ndwi") -> dict:
    """
    Generate a live thumbnail URL around the lake.
    mode:
      - 'rgb'  → true-color (Sentinel-2)
      - 'ndwi' → water-highlighted (Sentinel-2)
      - 'sar'  → Sentinel-1 VV backscatter (cloud-penetrating)
    """
    try:
        point = ee.Geometry.Point([lon, lat])
        region = point.buffer(buffer_m)
        mode_l = (mode or "ndwi").lower()

        if mode_l == "sar":
            from datetime import datetime, timedelta, timezone
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=90)
            s1 = (
                ee.ImageCollection("COPERNICUS/S1_GRD")
                .filterBounds(region)
                .filterDate(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
                .filter(ee.Filter.eq("instrumentMode", "IW"))
                .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
                .select("VV")
                .median()
                .clip(region)
            )
            vis = {
                "min": -25,
                "max": 0,
                "palette": ["#0a1628", "#1e3a5f", "#38bdf8", "#f8fafc", "#fbbf24"],
                "dimensions": 512,
                "region": region,
                "format": "png",
            }
            url = s1.getThumbURL(vis)
            return {
                "latitude": lat,
                "longitude": lon,
                "mode": "sar",
                "url": url,
                "source": "GEE Sentinel-1 SAR VV",
            }

        date_start, date_end = _s2_date_window(summer_only=True)
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(date_start, date_end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .median()
            .clip(region)
        )

        if mode_l == "rgb":
            vis = {
                "bands": ["B4", "B3", "B2"],
                "min": 0,
                "max": 3000,
                "dimensions": 512,
                "region": region,
                "format": "png",
            }
            image = s2
            source = "GEE Sentinel-2 RGB"
        else:
            ndwi = s2.normalizedDifference(["B3", "B8"]).rename("NDWI")
            vis = {
                "min": -0.3,
                "max": 0.5,
                "palette": ["#0c1826", "#1e3a5f", "#38bdf8", "#5eead4", "#ffffff"],
                "dimensions": 512,
                "region": region,
                "format": "png",
            }
            image = ndwi
            source = "GEE Sentinel-2 NDWI"

        url = image.getThumbURL(vis)

        return {
            "latitude": lat,
            "longitude": lon,
            "mode": mode_l,
            "url": url,
            "source": source,
        }
    except Exception as e:
        return {
            "latitude": lat,
            "longitude": lon,
            "mode": mode,
            "url": None,
            "error": str(e),
        }