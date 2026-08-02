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


@_require_ee
def detect_glacial_lakes():
    """
    Improved detection of glacial lakes in high-elevation areas of Gilgit-Baltistan
    using Sentinel-2 NDWI + elevation filter.
    """
    roi = ee.Geometry.Rectangle([73.5, 35.2, 76.8, 37.2])
    elevation = ee.Image('USGS/SRTMGL1_003').select('elevation')

    s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
          .filterBounds(roi)
          .filterDate('2024-07-01', '2024-09-15')
          .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 15))
          .median()
          .clip(roi))

    green = s2.select('B3')
    nir = s2.select('B8')
    ndwi = green.subtract(nir).divide(green.add(nir)).rename('NDWI')

    water = ndwi.gt(0.25)
    high_elevation = elevation.gt(3200)
    glacial_water = water.And(high_elevation)

    vectors = glacial_water.selfMask().reduceToVectors(
        geometry=roi,
        scale=30,
        geometryType='polygon',
        eightConnected=False,
        labelProperty='water',
        maxPixels=1e10
    )

    def add_area(feature):
        area_ha = feature.geometry().area(maxError=1).divide(10000)
        return feature.set({'area_ha': area_ha})

    vectors = vectors.map(add_area)
    significant = vectors.filter(ee.Filter.gt('area_ha', 8))
    lakes_info = significant.limit(25).getInfo()

    results = []
    for i, feature in enumerate(lakes_info.get('features', []), 1):
        props = feature['properties']
        geom = feature['geometry']
        try:
            coords = geom['coordinates'][0]
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            centroid_lon = sum(lons) / len(lons)
            centroid_lat = sum(lats) / len(lats)
        except Exception:
            centroid_lon = None
            centroid_lat = None

        results.append({
            "name": f"GLOF Lake {i}",
            "area_ha": round(props.get('area_ha', 0), 2),
            "latitude": round(centroid_lat, 5) if centroid_lat else None,
            "longitude": round(centroid_lon, 5) if centroid_lon else None,
            "source": "GEE Sentinel-2 NDWI + Elevation Filter"
        })

    results = sorted(results, key=lambda x: x['area_ha'], reverse=True)
    for i, lake in enumerate(results, 1):
        lake['name'] = f"GLOF Lake {i}"
    return results


@_require_ee
def detect_glacial_lakes_sar():
    """
    Detect glacial lakes using Sentinel-1 SAR data (can see through clouds).
    """
    roi = ee.Geometry.Rectangle([73.5, 35.2, 76.8, 37.2])
    
    # Filter SAR collection
    s1 = (ee.ImageCollection('COPERNICUS/S1_GRD')
          .filterBounds(roi)
          .filterDate('2024-07-01', '2024-09-30')
          .filter(ee.Filter.eq('instrumentMode', 'IW'))
          .select('VV')
          .median()
          .clip(roi))
    
    # SAR water detection (low backscatter)
    water = s1.lt(-16) # Thresholding for water
    
    vectors = water.selfMask().reduceToVectors(
        geometry=roi,
        scale=30,
        geometryType='polygon',
        eightConnected=False,
        maxPixels=1e10
    )
    
    # Simple vector processing
    lakes_info = vectors.limit(25).getInfo()
    return {"message": "SAR detection functionality added", "features": lakes_info.get('features', [])}


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
    if risk_level == "High":
        danger_km, warning_km = 5.0, 10.0
    elif risk_level == "Medium":
        danger_km, warning_km = 2.0, 5.0
    else:
        danger_km, warning_km = 1.0, 2.0

    danger = estimate_population(lat, lon, danger_km)
    warning = estimate_population(lat, lon, warning_km)

    return {
        "danger_zone_km": danger_km,
        "warning_zone_km": warning_km,
        "danger_population": danger.get("population", 0),
        "warning_population": warning.get("population", 0),
        "source": "WorldPop via GEE",
        "risk_level": risk_level
    }


# ==================== HISTORICAL AREA (Sentinel-2 NDWI) ====================
@_require_ee
def estimate_area_for_year(lat: float, lon: float, year: int, buffer_m: float = 1500) -> dict:
    """
    Estimate water area (ha) around a lake point for one summer season.
    Uses Sentinel-2 NDWI median composite for July–September of that year.
    """
    try:
        point = ee.Geometry.Point([lon, lat])
        region = point.buffer(buffer_m)

        start = f"{year}-07-01"
        end = f"{year}-09-30"

        s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(region)
              .filterDate(start, end)
              .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
              .median())

        ndwi = s2.normalizedDifference(["B3", "B8"]).rename("NDWI")
        water = ndwi.gt(0.2).selfMask()

        # Pixel area in m², then convert to hectares
        pixel_area = ee.Image.pixelArea()
        water_area_m2 = water.multiply(pixel_area).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=20,
            maxPixels=1e8
        ).get("NDWI")

        area_m2 = ee.Number(water_area_m2).getInfo()
        if area_m2 is None:
            area_m2 = 0

        area_ha = round(float(area_m2) / 10000.0, 2)

        return {
            "year": year,
            "area_ha": area_ha,
            "latitude": lat,
            "longitude": lon,
            "source": "GEE Sentinel-2 NDWI"
        }
    except Exception as e:
        return {
            "year": year,
            "area_ha": None,
            "latitude": lat,
            "longitude": lon,
            "error": str(e)
        }

@_require_ee
def get_historical_areas(lat: float, lon: float, years=None) -> dict:
    """
    Get estimated area for consecutive years (2015–2025 = 11 years)
    """
    if years is None:
        years = list(range(2015, 2026))   # 2015, 2016, 2017, ..., 2025

    series = []
    for y in years:
        series.append(estimate_area_for_year(lat, lon, y))

    valid = [s for s in series if s.get("area_ha") is not None]
    trend = "unknown"
    if len(valid) >= 2:
        first = valid[0]["area_ha"]
        last = valid[-1]["area_ha"]
        if last > first * 1.1:
            trend = "growing"
        elif last < first * 0.9:
            trend = "shrinking"
        else:
            trend = "stable"

    return {
        "latitude": lat,
        "longitude": lon,
        "years": series,
        "trend": trend,
        "source": "GEE Sentinel-2 NDWI (summer composites)"
    }


# ==================== LIVE SATELLITE THUMBNAILS ====================
@_require_ee
def get_lake_thumbnail(lat: float, lon: float, buffer_m: float = 2000, mode: str = "ndwi") -> dict:
    """
    Generate a live thumbnail URL around the lake.
    mode:
      - 'rgb'  → true-color
      - 'ndwi' → water-highlighted (blue water)
    """
    try:
        point = ee.Geometry.Point([lon, lat])
        region = point.buffer(buffer_m)

        s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
              .filterBounds(region)
              .filterDate("2024-07-01", "2024-09-30")
              .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
              .median()
              .clip(region))

        if mode == "rgb":
            vis = {
                "bands": ["B4", "B3", "B2"],
                "min": 0,
                "max": 3000,
                "dimensions": 512,
                "region": region,
                "format": "png"
            }
            image = s2
        else:
            # NDWI false-color style (water bright)
            ndwi = s2.normalizedDifference(["B3", "B8"]).rename("NDWI")
            # Visualize NDWI: water = cyan/blue
            vis = {
                "min": -0.3,
                "max": 0.5,
                "palette": ["#0c1826", "#1e3a5f", "#38bdf8", "#5eead4", "#ffffff"],
                "dimensions": 512,
                "region": region,
                "format": "png"
            }
            image = ndwi

        url = image.getThumbURL(vis)

        return {
            "latitude": lat,
            "longitude": lon,
            "mode": mode,
            "url": url,
            "source": "GEE Sentinel-2"
        }
    except Exception as e:
        return {
            "latitude": lat,
            "longitude": lon,
            "mode": mode,
            "url": None,
            "error": str(e)
        }