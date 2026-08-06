"""
Microsoft Planetary Computer (STAC) helpers for Sentinel-2.

Used as cascade step 2 when Google Earth Engine fails or optical scenes are too cloudy.
Works with stdlib + httpx only (no rasterio required).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# Gilgit–Baltistan glacial lake ROI (lon_min, lat_min, lon_max, lat_max)
DEFAULT_BBOX = (73.5, 35.2, 76.8, 37.2)

PC_STAC_URL = os.getenv(
    "PC_STAC_URL",
    "https://planetarycomputer.microsoft.com/api/stac/v1",
).rstrip("/")
PC_SAS_URL = os.getenv(
    "PC_SAS_URL",
    "https://planetarycomputer.microsoft.com/api/sas/v1/token",
).rstrip("/")

# Scene-level cloud cover must be at or below this to accept optical MPC
MAX_CLOUD_PCT = float(os.getenv("PC_MAX_CLOUD_PCT", "25"))
# Minimum number of low-cloud intersecting scenes required to trust MPC optical
MIN_CLEAR_SCENES = int(os.getenv("PC_MIN_CLEAR_SCENES", "2"))


def _http_get_json(url: str, timeout: float = 45.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GLOF-Portal/1.0 (planetary-computer)",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url: str, body: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "GLOF-Portal/1.0 (planetary-computer)",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def mpc_status() -> dict:
    """Lightweight health probe for /gee/status cascade panel."""
    try:
        landing = _http_get_json(f"{PC_STAC_URL}/", timeout=12.0)
        title = landing.get("title") or landing.get("id") or "planetary-computer"
        return {
            "ready": True,
            "endpoint": PC_STAC_URL,
            "title": title,
            "error": None,
        }
    except Exception as e:
        return {
            "ready": False,
            "endpoint": PC_STAC_URL,
            "title": None,
            "error": str(e),
        }


def _date_window(days: int = 120) -> tuple[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def search_sentinel2_scenes(
    bbox: tuple[float, float, float, float] = DEFAULT_BBOX,
    max_cloud: float = MAX_CLOUD_PCT,
    limit: int = 50,
    days: int = 120,
) -> dict[str, Any]:
    """
    Search Planetary Computer STAC for Sentinel-2 L2A scenes over the ROI.

    Returns features, mean/min cloud stats, and whether the optical stack is usable.
    """
    start, end = _date_window(days)
    body = {
        "collections": ["sentinel-2-l2a"],
        "bbox": list(bbox),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": limit,
        "sortby": [{"field": "eo:cloud_cover", "direction": "asc"}],
    }

    try:
        result = _http_post_json(f"{PC_STAC_URL}/search", body, timeout=60.0)
    except Exception as e:
        return {
            "ok": False,
            "reason": f"MPC STAC search failed: {e}",
            "features": [],
            "scene_count": 0,
            "min_cloud_pct": None,
            "mean_cloud_pct": None,
            "date_start": start,
            "date_end": end,
        }

    features = result.get("features") or []
    clouds: list[float] = []
    for f in features:
        props = f.get("properties") or {}
        cc = props.get("eo:cloud_cover")
        if cc is not None:
            try:
                clouds.append(float(cc))
            except (TypeError, ValueError):
                pass

    min_cloud = min(clouds) if clouds else None
    mean_cloud = (sum(clouds) / len(clouds)) if clouds else None
    usable = len(features) >= MIN_CLEAR_SCENES and (
        min_cloud is not None and min_cloud <= max_cloud
    )

    return {
        "ok": usable,
        "reason": None if usable else (
            "No low-cloud Sentinel-2 scenes on Planetary Computer"
            if not features
            else f"Only {len(features)} clear scene(s); need ≥{MIN_CLEAR_SCENES}"
        ),
        "features": features,
        "scene_count": len(features),
        "min_cloud_pct": round(min_cloud, 2) if min_cloud is not None else None,
        "mean_cloud_pct": round(mean_cloud, 2) if mean_cloud is not None else None,
        "date_start": start,
        "date_end": end,
        "max_cloud_filter": max_cloud,
    }


def _point_in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    lon_min, lat_min, lon_max, lat_max = bbox
    return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max


def _scene_covers_point(feature: dict, lat: float, lon: float) -> bool:
    """True if STAC item bbox or geometry roughly covers the point."""
    bbox = feature.get("bbox")
    if bbox and len(bbox) >= 4:
        return _point_in_bbox(lat, lon, (bbox[0], bbox[1], bbox[2], bbox[3]))
    geom = feature.get("geometry") or {}
    # Fallback: accept if no bbox (caller already filtered by ROI search)
    return geom.get("type") in ("Polygon", "MultiPolygon", None) or True


def validate_lakes_under_clear_sky(
    lakes: list[dict],
    scenes: list[dict],
    min_scene_hits: int = 1,
) -> list[dict]:
    """
    Keep inventory lakes that fall under at least one low-cloud MPC scene footprint.
    Used when full NDWI vectorization is unavailable without GEE/rasterio.
    """
    if not lakes or not scenes:
        return []

    out = []
    for lake in lakes:
        lat = lake.get("latitude")
        lon = lake.get("longitude")
        if lat is None or lon is None:
            continue
        hits = 0
        best_cloud = None
        best_date = None
        for sc in scenes:
            if not _scene_covers_point(sc, float(lat), float(lon)):
                continue
            hits += 1
            props = sc.get("properties") or {}
            cc = props.get("eo:cloud_cover")
            dt = props.get("datetime")
            try:
                cc_f = float(cc) if cc is not None else None
            except (TypeError, ValueError):
                cc_f = None
            if cc_f is not None and (best_cloud is None or cc_f < best_cloud):
                best_cloud = cc_f
                best_date = dt
        if hits >= min_scene_hits:
            item = dict(lake)
            item["source"] = "MPC Sentinel-2 (clear-sky validated)"
            item["mpc_scene_hits"] = hits
            if best_cloud is not None:
                item["mpc_best_cloud_pct"] = round(best_cloud, 2)
            if best_date:
                item["mpc_best_date"] = best_date
            out.append(item)
    return out


def detect_lakes_mpc_sentinel2(
    inventory_lakes: Optional[list[dict]] = None,
    bbox: tuple[float, float, float, float] = DEFAULT_BBOX,
    max_cloud: float = MAX_CLOUD_PCT,
) -> dict[str, Any]:
    """
    Cascade step 2: Planetary Computer Sentinel-2.

    Strategy:
      1. STAC search for low-cloud S2-L2A over the GB ROI.
      2. If optical is still too cloudy → fail (caller tries SAR).
      3. If clear scenes exist, validate known-inventory lakes under those footprints
         (and return them as detections). Full NDWI polygon extraction remains on GEE.
    """
    search = search_sentinel2_scenes(bbox=bbox, max_cloud=max_cloud)
    meta = {
        "provider": "microsoft_planetary_computer",
        "sensor": "sentinel-2-l2a",
        "scene_count": search.get("scene_count"),
        "min_cloud_pct": search.get("min_cloud_pct"),
        "mean_cloud_pct": search.get("mean_cloud_pct"),
        "date_start": search.get("date_start"),
        "date_end": search.get("date_end"),
    }

    if not search.get("ok"):
        return {
            "ok": False,
            "lakes": [],
            "reason": search.get("reason") or "MPC optical unusable",
            "cloudy": True,
            "meta": meta,
        }

    inventory = inventory_lakes or []
    validated = validate_lakes_under_clear_sky(inventory, search.get("features") or [])

    if not validated:
        # Clear scenes exist but no inventory points — still report optical OK so
        # cascade can prefer this meta; return empty to allow SAR/DB to fill names.
        return {
            "ok": False,
            "lakes": [],
            "reason": "MPC found clear Sentinel-2 scenes but no inventory lakes under footprints",
            "cloudy": False,
            "meta": meta,
        }

    # Normalize names / sort
    validated = sorted(validated, key=lambda x: float(x.get("area_ha") or 0), reverse=True)
    return {
        "ok": True,
        "lakes": validated,
        "reason": None,
        "cloudy": False,
        "meta": meta,
    }
