"""
Satellite lake-detection cascade for the GLOF Portal.

  1. Google Earth Engine  — Sentinel-2 NDWI
  2. Microsoft Planetary Computer — Sentinel-2 (if GEE fails or high clouds)
  3. Sentinel-1 SAR       — cloud-penetrating (via GEE when available)
  4. Database + Known Lakes Inventory — last-resort offline fallback
"""

from __future__ import annotations

import os
from typing import Any, Optional

from sqlalchemy.orm import Session

# Cloud threshold: GEE optical result with higher mean scene cloud is rejected
GEE_MAX_CLOUD_PCT = float(os.getenv("GEE_MAX_CLOUD_PCT", "30"))
CASCADE_MIN_LAKES = int(os.getenv("CASCADE_MIN_LAKES", "1"))


def _inventory_from_db(db: Optional[Session]) -> list[dict]:
    """Merge known_lakes + registered lakes into a single inventory list."""
    if db is None:
        return _static_inventory_fallback()

    from app.models.lake import Lake, KnownLake

    out: list[dict] = []
    seen: set[tuple[str, float, float]] = set()

    def _add(name, lat, lon, area_ha, source_label: str, district=None):
        if lat is None or lon is None:
            return
        key = (round(float(lat), 4), round(float(lon), 4))
        # de-dupe by coordinate grid
        for s in seen:
            if abs(s[1] - key[0]) < 0.01 and abs(s[2] - key[1]) < 0.01:
                return
        seen.add((name or "", key[0], key[1]))
        item = {
            "name": name or f"Lake ({float(lat):.3f}N, {float(lon):.3f}E)",
            "area_ha": round(float(area_ha), 2) if area_ha is not None else None,
            "latitude": round(float(lat), 5),
            "longitude": round(float(lon), 5),
            "source": source_label,
        }
        if district:
            item["district"] = district
        out.append(item)

    try:
        for k in db.query(KnownLake).all():
            _add(k.name, k.latitude, k.longitude, k.area_ha, "Known Lakes Inventory", k.district)
        for lake in db.query(Lake).all():
            _add(lake.name, lake.latitude, lake.longitude, lake.area_ha, "Database lakes")
    except Exception:
        return _static_inventory_fallback()

    if not out:
        return _static_inventory_fallback()
    return sorted(out, key=lambda x: float(x.get("area_ha") or 0), reverse=True)


def _static_inventory_fallback() -> list[dict]:
    """When DB is empty/unavailable, use the hard-coded seed list."""
    try:
        from app.api.routers.lakes import KNOWN_LAKES_SEED
    except Exception:
        return []
    lakes = []
    for item in KNOWN_LAKES_SEED:
        lakes.append({
            "name": item["name"],
            "area_ha": item.get("area_ha"),
            "latitude": item["latitude"],
            "longitude": item["longitude"],
            "district": item.get("district"),
            "source": "Known Lakes Inventory (seed)",
        })
    return sorted(lakes, key=lambda x: float(x.get("area_ha") or 0), reverse=True)


def _attempt(
    step: int,
    provider: str,
    sensor: str,
    status: str,
    lakes: Optional[list] = None,
    detail: Optional[str] = None,
    meta: Optional[dict] = None,
) -> dict:
    return {
        "step": step,
        "provider": provider,
        "sensor": sensor,
        "status": status,  # success | skipped | failed | cloudy
        "lake_count": len(lakes or []),
        "detail": detail,
        "meta": meta or {},
    }


def detect_lakes_cascade(db: Optional[Session] = None) -> dict[str, Any]:
    """
    Run the multi-source detection cascade and return a unified payload.

    Response shape:
      {
        "lakes": [...],
        "total_detected": N,
        "source_used": "gee_sentinel2" | "mpc_sentinel2" | "sentinel1_sar" | "database_inventory",
        "source_label": human readable,
        "cascade": [ attempt, ... ],
        "message": str,
      }
    """
    attempts: list[dict] = []
    inventory = _inventory_from_db(db)

    # ------------------------------------------------------------------
    # 1) Google Earth Engine — Sentinel-2
    # ------------------------------------------------------------------
    try:
        from app.utils.gee_detection import (
            GEE_READY,
            _init_ee,
            detect_glacial_lakes_s2_detailed,
        )

        if not GEE_READY and not _init_ee():
            attempts.append(_attempt(
                1, "google_earth_engine", "sentinel-2", "failed",
                detail="GEE not authenticated / unavailable",
            ))
        else:
            gee = detect_glacial_lakes_s2_detailed()
            cloud = gee.get("mean_cloud_pct")
            lakes = gee.get("lakes") or []
            cloudy = bool(gee.get("cloudy")) or (
                cloud is not None and float(cloud) > GEE_MAX_CLOUD_PCT
            )
            if gee.get("ok") and lakes and not cloudy:
                attempts.append(_attempt(
                    1, "google_earth_engine", "sentinel-2", "success",
                    lakes=lakes,
                    detail=gee.get("detail"),
                    meta={
                        "mean_cloud_pct": cloud,
                        "scene_count": gee.get("scene_count"),
                        "date_start": gee.get("date_start"),
                        "date_end": gee.get("date_end"),
                    },
                ))
                return _success_payload(
                    lakes=lakes,
                    source_used="gee_sentinel2",
                    source_label="Google Earth Engine · Sentinel-2 NDWI",
                    attempts=attempts,
                )
            status = "cloudy" if cloudy or not lakes else "failed"
            attempts.append(_attempt(
                1, "google_earth_engine", "sentinel-2", status,
                lakes=lakes,
                detail=gee.get("detail") or gee.get("error") or (
                    f"High clouds ({cloud}%)" if cloudy else "No lakes detected"
                ),
                meta={
                    "mean_cloud_pct": cloud,
                    "scene_count": gee.get("scene_count"),
                },
            ))
    except Exception as e:
        attempts.append(_attempt(
            1, "google_earth_engine", "sentinel-2", "failed",
            detail=str(e),
        ))

    # ------------------------------------------------------------------
    # 2) Microsoft Planetary Computer — Sentinel-2
    # ------------------------------------------------------------------
    try:
        from app.utils.planetary_computer import detect_lakes_mpc_sentinel2

        mpc = detect_lakes_mpc_sentinel2(inventory_lakes=inventory)
        lakes = mpc.get("lakes") or []
        if mpc.get("ok") and lakes:
            attempts.append(_attempt(
                2, "microsoft_planetary_computer", "sentinel-2", "success",
                lakes=lakes,
                detail="Clear-sky validated inventory under low-cloud S2 scenes",
                meta=mpc.get("meta"),
            ))
            return _success_payload(
                lakes=lakes,
                source_used="mpc_sentinel2",
                source_label="Microsoft Planetary Computer · Sentinel-2",
                attempts=attempts,
            )
        status = "cloudy" if mpc.get("cloudy") else "failed"
        attempts.append(_attempt(
            2, "microsoft_planetary_computer", "sentinel-2", status,
            lakes=lakes,
            detail=mpc.get("reason"),
            meta=mpc.get("meta"),
        ))
    except Exception as e:
        attempts.append(_attempt(
            2, "microsoft_planetary_computer", "sentinel-2", "failed",
            detail=str(e),
        ))

    # ------------------------------------------------------------------
    # 3) Sentinel-1 SAR (cloud-penetrating)
    # ------------------------------------------------------------------
    try:
        from app.utils.gee_detection import (
            GEE_READY,
            _init_ee,
            detect_glacial_lakes_sar,
        )

        if not GEE_READY and not _init_ee():
            attempts.append(_attempt(
                3, "google_earth_engine", "sentinel-1-sar", "failed",
                detail="GEE unavailable — cannot run Sentinel-1 SAR",
            ))
        else:
            sar = detect_glacial_lakes_sar()
            # Support both legacy and new return shapes
            if isinstance(sar, dict) and "lakes" in sar:
                lakes = sar.get("lakes") or []
                ok = bool(sar.get("ok", len(lakes) >= CASCADE_MIN_LAKES))
                detail = sar.get("detail") or sar.get("message")
                meta = {
                    "date_start": sar.get("date_start"),
                    "date_end": sar.get("date_end"),
                }
            elif isinstance(sar, list):
                lakes = sar
                ok = len(lakes) >= CASCADE_MIN_LAKES
                detail = None
                meta = {}
            else:
                features = (sar or {}).get("features") or []
                lakes = _sar_features_to_lakes(features)
                ok = len(lakes) >= CASCADE_MIN_LAKES
                detail = (sar or {}).get("message")
                meta = {}

            if ok and lakes:
                attempts.append(_attempt(
                    3, "google_earth_engine", "sentinel-1-sar", "success",
                    lakes=lakes,
                    detail=detail or "SAR water detection (VV backscatter)",
                    meta=meta,
                ))
                return _success_payload(
                    lakes=lakes,
                    source_used="sentinel1_sar",
                    source_label="Sentinel-1 SAR (cloud-penetrating)",
                    attempts=attempts,
                )
            attempts.append(_attempt(
                3, "google_earth_engine", "sentinel-1-sar", "failed",
                lakes=lakes,
                detail=detail or "SAR detection returned no significant water bodies",
                meta=meta,
            ))
    except Exception as e:
        attempts.append(_attempt(
            3, "google_earth_engine", "sentinel-1-sar", "failed",
            detail=str(e),
        ))

    # ------------------------------------------------------------------
    # 4) Database + Known Lakes Inventory
    # ------------------------------------------------------------------
    lakes = inventory
    for lake in lakes:
        if not lake.get("source") or "Inventory" not in str(lake.get("source")):
            lake["source"] = "Database + Known Lakes Inventory"

    attempts.append(_attempt(
        4, "local_database", "inventory", "success",
        lakes=lakes,
        detail="All remote sensors failed or were cloudy — using inventory",
        meta={"inventory_count": len(lakes)},
    ))
    return _success_payload(
        lakes=lakes,
        source_used="database_inventory",
        source_label="Database + Known Lakes Inventory",
        attempts=attempts,
        fallback=True,
    )


def _sar_features_to_lakes(features: list) -> list[dict]:
    """Convert GeoJSON-like SAR vectors to the standard lake dict list."""
    results = []
    for i, feature in enumerate(features, 1):
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        area_ha = props.get("area_ha")
        try:
            coords = geom.get("coordinates") or []
            # Polygon → outer ring
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

        if area_ha is None and ring:
            # rough geographic area not computed here
            area_ha = props.get("count")  # pixel count fallback if present

        results.append({
            "name": f"SAR Lake {i}",
            "area_ha": round(float(area_ha), 2) if area_ha is not None else None,
            "latitude": round(centroid_lat, 5) if centroid_lat is not None else None,
            "longitude": round(centroid_lon, 5) if centroid_lon is not None else None,
            "source": "GEE Sentinel-1 SAR",
        })

    results = [r for r in results if r.get("latitude") is not None]
    results = sorted(results, key=lambda x: float(x.get("area_ha") or 0), reverse=True)
    for i, lake in enumerate(results, 1):
        lake["name"] = f"SAR Lake {i}"
    return results


def _success_payload(
    *,
    lakes: list[dict],
    source_used: str,
    source_label: str,
    attempts: list[dict],
    fallback: bool = False,
) -> dict[str, Any]:
    # Ensure consistent naming + source stamp
    clean = []
    for i, lake in enumerate(lakes, 1):
        item = dict(lake)
        if not item.get("name"):
            item["name"] = f"GLOF Lake {i}"
        if not item.get("source"):
            item["source"] = source_label
        clean.append(item)

    msg = (
        f"Detection completed via {source_label}"
        + (" (offline fallback)" if fallback else "")
    )
    return {
        "message": msg,
        "total_detected": len(clean),
        "lakes": clean,
        "source_used": source_used,
        "source_label": source_label,
        "fallback": fallback,
        "cascade": attempts,
    }


def cascade_status() -> dict[str, Any]:
    """Provider health for the cascade (shown on /gee/status)."""
    from app.utils.gee_detection import gee_status
    from app.utils.planetary_computer import mpc_status

    gee = gee_status()
    mpc = mpc_status()
    # Top-level ready/mode/error kept for older clients that expect gee_status() shape
    return {
        "ready": bool(gee.get("ready")),
        "mode": gee.get("mode"),
        "project": gee.get("project"),
        "error": gee.get("error"),
        "hint": gee.get("hint"),
        "cascade": [
            {
                "step": 1,
                "name": "Google Earth Engine (Sentinel-2)",
                "ready": bool(gee.get("ready")),
                "detail": gee.get("error") or gee.get("mode"),
            },
            {
                "step": 2,
                "name": "Microsoft Planetary Computer (Sentinel-2)",
                "ready": bool(mpc.get("ready")),
                "detail": mpc.get("error") or mpc.get("endpoint"),
            },
            {
                "step": 3,
                "name": "Sentinel-1 SAR",
                "ready": bool(gee.get("ready")),
                "detail": "Uses GEE COPERNICUS/S1_GRD when GEE is ready",
            },
            {
                "step": 4,
                "name": "Database + Known Lakes Inventory",
                "ready": True,
                "detail": "Always available offline fallback",
            },
        ],
        "gee": gee,
        "planetary_computer": mpc,
    }
