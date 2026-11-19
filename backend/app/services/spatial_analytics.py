"""
Pure-Python Spatial Analytics Engine:
  - District Point-in-Polygon (ray-casting) aggregation over glof_districts.geojson
  - Basin Domino Cascade Risk scoring & chokepoints
  - Multi-lake 6-axis normalized comparison vectors
"""

from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.lake import Lake as LakeModel
from app.services.early_warning import compute_early_warning
from app.services.geo_helpers import nearest_glacier_km, population_heuristic
from app.services.glof_basins import BASINS, assign_basin, estimate_outburst_volume_m3
from app.utils.risk import calculate_risk

_DISTRICTS_LOCK = threading.Lock()
_DISTRICTS_CACHE: Optional[list[dict[str, Any]]] = None


def _resolve_districts_geojson() -> Path:
    env_path = (os.getenv("GLOF_DISTRICTS_GEOJSON") or "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p

    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "static" / "geo" / "glof_districts.geojson",
        here.parents[3] / "frontend" / "public" / "geo" / "glof_districts.geojson",
        here.parents[1] / "data" / "glof_districts.geojson",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    raise FileNotFoundError("glof_districts.geojson not found in static/geo, frontend/public/geo, or app/data")


def _load_districts() -> list[dict[str, Any]]:
    global _DISTRICTS_CACHE
    with _DISTRICTS_LOCK:
        if _DISTRICTS_CACHE is not None:
            return _DISTRICTS_CACHE

        path = _resolve_districts_geojson()
        data = json.loads(path.read_text(encoding="utf-8"))
        parsed: list[dict[str, Any]] = []

        for feat in data.get("features") or []:
            props = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            coords = geom.get("coordinates") or []
            if gtype == "Polygon":
                polygons = [coords]
            elif gtype == "MultiPolygon":
                polygons = coords
            else:
                continue

            min_lon = float("inf")
            min_lat = float("inf")
            max_lon = float("-inf")
            max_lat = float("-inf")
            for poly in polygons:
                if not poly or not poly[0]:
                    continue
                for pt in poly[0]:
                    px, py = float(pt[0]), float(pt[1])
                    if px < min_lon:
                        min_lon = px
                    if px > max_lon:
                        max_lon = px
                    if py < min_lat:
                        min_lat = py
                    if py > max_lat:
                        max_lat = py

            parsed.append({
                "pcode": str(props.get("adm2_pcode") or ""),
                "name": str(props.get("adm2_name") or "Unknown District"),
                "province": str(props.get("adm1_name") or ""),
                "area_sqkm": float(props.get("area_sqkm") or 0.0),
                "polygons": polygons,
                "bbox": (min_lon, min_lat, max_lon, max_lat),
            })

        _DISTRICTS_CACHE = parsed
        return _DISTRICTS_CACHE


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = float(ring[i][0]), float(ring[i][1])
        xj, yj = float(ring[j][0]), float(ring[j][1])
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lon: float, lat: float, rings: list[list[list[float]]]) -> bool:
    if not rings or not _point_in_ring(lon, lat, rings[0]):
        return False
    return not any(_point_in_ring(lon, lat, hole) for hole in rings[1:])


def classify_lake_district(lat: Optional[float], lon: Optional[float]) -> Optional[dict[str, Any]]:
    if lat is None or lon is None:
        return None
    lat_f = float(lat)
    lon_f = float(lon)
    for d in _load_districts():
        min_lon, min_lat, max_lon, max_lat = d["bbox"]
        if not (min_lon <= lon_f <= max_lon and min_lat <= lat_f <= max_lat):
            continue
        if any(_point_in_polygon(lon_f, lat_f, poly) for poly in d["polygons"]):
            return {
                "pcode": d["pcode"],
                "name": d["name"],
                "province": d["province"],
                "area_sqkm": round(d["area_sqkm"], 2),
            }
    return None


def _normalize_risk(raw_risk: Optional[str], area_ha: Optional[float]) -> str:
    val = (raw_risk or "").strip().capitalize()
    if val in ("High", "Medium", "Low"):
        return val
    return calculate_risk(area_ha or 0)


def get_district_analytics(db: Session) -> dict[str, Any]:
    districts_meta = _load_districts()
    buckets: dict[str, dict[str, Any]] = {}
    for d in districts_meta:
        key = d["pcode"] or d["name"]
        buckets[key] = {
            "pcode": d["pcode"],
            "name": d["name"],
            "province": d["province"],
            "area_sqkm": round(d["area_sqkm"], 2),
            "lake_count": 0,
            "total_area_ha": 0.0,
            "total_volume_m3": 0.0,
            "hazard_density_per_1000sqkm": 0.0,
            "risk_counts": {"High": 0, "Medium": 0, "Low": 0},
            "lakes": [],
        }

    lakes = db.query(LakeModel).all()
    for lake in lakes:
        matched = classify_lake_district(lake.latitude, lake.longitude)
        if not matched:
            continue
        key = matched["pcode"] or matched["name"]
        bucket = buckets.get(key)
        if bucket is None:
            continue

        area_ha = float(lake.area_ha or 0.0)
        risk = _normalize_risk(lake.risk_level, area_ha)
        vol_m3 = float(estimate_outburst_volume_m3(area_ha) or 0.0)

        bucket["lake_count"] += 1
        bucket["total_area_ha"] += area_ha
        bucket["total_volume_m3"] += vol_m3
        bucket["risk_counts"][risk] += 1
        bucket["lakes"].append({
            "id": lake.id,
            "name": lake.name,
            "area_ha": lake.area_ha,
            "risk_level": risk,
        })

    out_list: list[dict[str, Any]] = []
    for bucket in buckets.values():
        area_sqkm = float(bucket["area_sqkm"] or 0.0)
        count = bucket["lake_count"]
        bucket["total_area_ha"] = round(bucket["total_area_ha"], 2)
        bucket["total_volume_m3"] = round(bucket["total_volume_m3"], 1)
        bucket["hazard_density_per_1000sqkm"] = round((count / area_sqkm) * 1000.0, 3) if area_sqkm > 0 else 0.0
        bucket["lakes"].sort(key=lambda l: float(l.get("area_ha") or 0.0), reverse=True)
        out_list.append(bucket)

    out_list.sort(key=lambda d: (-d["lake_count"], -d["total_volume_m3"], d["name"]))
    return {
        "total_districts": len(out_list),
        "districts": out_list,
    }


def get_basin_cascade_analytics(db: Session) -> dict[str, Any]:
    lakes = db.query(LakeModel).all()
    by_basin: dict[str, list[LakeModel]] = {b["id"]: [] for b in BASINS}
    for lake in lakes:
        basin = assign_basin(lake.latitude, lake.longitude)
        by_basin.setdefault(basin["id"], []).append(lake)

    prio_pts_map = {"Critical": 15.0, "Warning": 10.0, "Watch": 5.0, "Normal": 2.0}
    out_basins: list[dict[str, Any]] = []

    for basin in BASINS:
        bid = basin["id"]
        b_lakes = by_basin.get(bid, [])
        if basin.get("is_fallback") and not b_lakes:
            continue

        high_count = 0
        med_count = 0
        total_vol = 0.0
        lake_entries: list[dict[str, Any]] = []

        for l in b_lakes:
            area_ha = float(l.area_ha or 0.0)
            risk = _normalize_risk(l.risk_level, area_ha)
            vol = float(estimate_outburst_volume_m3(area_ha) or 0.0)
            if risk == "High":
                high_count += 1
            elif risk == "Medium":
                med_count += 1
            total_vol += vol
            lake_entries.append({
                "id": l.id,
                "name": l.name,
                "area_ha": l.area_ha,
                "risk_level": risk,
                "volume_m3": round(vol, 1),
            })

        lake_count = len(b_lakes)
        if lake_count == 0:
            domino_risk_score = 0.0
            cascade_tier = "Low"
        else:
            cluster_pts = min(30.0, lake_count * 3.0)
            hazard_pts = min(30.0, high_count * 10.0 + med_count * 3.0)
            volume_pts = min(25.0, (total_vol / 1e6) * 0.45)
            prio_pts = prio_pts_map.get(str(basin.get("monitoring_priority")), 4.0)
            domino_risk_score = round(min(100.0, cluster_pts + hazard_pts + volume_pts + prio_pts), 1)
            if domino_risk_score >= 75.0:
                cascade_tier = "Extreme"
            elif domino_risk_score >= 50.0:
                cascade_tier = "High"
            elif domino_risk_score >= 25.0:
                cascade_tier = "Moderate"
            else:
                cascade_tier = "Low"

        chokepoints = [
            f"{n['name']} ({n.get('type', 'node')})"
            for n in (basin.get("downstream_storage") or [])
        ]
        lake_entries.sort(key=lambda x: float(x.get("area_ha") or 0.0), reverse=True)

        out_basins.append({
            "basin_id": bid,
            "basin_name": basin["name"],
            "lake_count": lake_count,
            "high_risk_count": high_count,
            "total_volume_m3": round(total_vol, 1),
            "domino_risk_score": domino_risk_score,
            "cascade_tier": cascade_tier,
            "chokepoints": chokepoints,
            "lakes": lake_entries,
        })

    out_basins.sort(key=lambda b: (-b["domino_risk_score"], -b["total_volume_m3"]))
    return {"basins": out_basins}


def compare_lakes(db: Session, lake_ids: Optional[str] = None) -> dict[str, Any]:
    selected: list[LakeModel] = []
    if lake_ids is None or not lake_ids.strip():
        selected = db.query(LakeModel).order_by(LakeModel.area_ha.desc()).limit(3).all()
    else:
        raw_parts = [p.strip() for p in lake_ids.split(",") if p.strip()]
        if not raw_parts:
            raise HTTPException(status_code=400, detail="Invalid lake_ids parameter")
        if len(raw_parts) > 6:
            raise HTTPException(status_code=400, detail="Maximum 6 lakes can be compared at once")
        parsed_ids: list[int] = []
        for p in raw_parts:
            try:
                parsed_ids.append(int(p))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid lake_id '{p}'")

        rows = db.query(LakeModel).filter(LakeModel.id.in_(parsed_ids)).all()
        by_id = {r.id: r for r in rows}
        missing = [lid for lid in parsed_ids if lid not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"Lake(s) not found: {missing}")
        selected = [by_id[lid] for lid in parsed_ids]

    compared: list[dict[str, Any]] = []
    for lake in selected:
        has_coords = lake.latitude is not None and lake.longitude is not None
        area_ha = float(lake.area_ha or 0.0)
        risk = _normalize_risk(lake.risk_level, area_ha)
        basin = assign_basin(lake.latitude, lake.longitude) if has_coords else {}
        vol_m3 = float(estimate_outburst_volume_m3(area_ha) or 0.0)
        qp_m3s = round(0.0048 * (vol_m3 ** 0.89), 1) if vol_m3 > 0 else 0.0
        glacier_km = round(nearest_glacier_km(lake.latitude, lake.longitude), 2) if has_coords else None
        pop = population_heuristic(lake.latitude, lake.longitude, area_ha) if has_coords else {"danger_population": 0, "warning_population": 0}

        ew = compute_early_warning(
            area_ha=area_ha,
            glacier_distance_km=glacier_km,
            danger_population=pop["danger_population"],
            warning_population=pop["warning_population"],
            include_glacier=has_coords,
        )
        ew_score = float(ew.get("score") or 0.0)
        ew_level = str(ew.get("level") or "Normal")

        trigger_score = min(100.0, round(ew_score * 0.85, 1))
        if has_coords:
            try:
                from app.services.trigger_engine import get_lake_triggers
                trig = get_lake_triggers(lake, db)
                trigger_score = float(trig.get("composite_trigger_score") or trigger_score)
            except Exception:
                pass

        danger_pop = int(pop.get("danger_population") or 0)
        normalized_radar = {
            "area": min(100.0, round(100.0 * math.log1p(max(0.0, area_ha)) / math.log1p(280.0), 1)),
            "volume": min(100.0, round(100.0 * math.log1p(vol_m3 / 1e6) / math.log1p(35.0), 1)),
            "discharge": min(100.0, round(100.0 * math.log1p(qp_m3s) / math.log1p(10000.0), 1)),
            "glacier_proximity": round(
                max(0.0, min(100.0, (1.0 - min(glacier_km if glacier_km is not None else 25.0, 25.0) / 25.0) * 100.0)),
                1,
            ),
            "exposure": min(100.0, round(100.0 * math.log1p(danger_pop) / math.log1p(5000.0), 1)),
            "trigger": round(max(0.0, min(100.0, trigger_score)), 1),
        }

        compared.append({
            "id": lake.id,
            "name": lake.name,
            "area_ha": lake.area_ha,
            "risk_level": risk,
            "basin_name": basin.get("name"),
            "outburst_volume_m3": round(vol_m3, 1),
            "peak_discharge_m3s": qp_m3s,
            "ew_score": ew_score,
            "ew_level": ew_level,
            "trigger_score": round(trigger_score, 1),
            "danger_population": danger_pop,
            "glacier_distance_km": glacier_km,
            "normalized_radar": normalized_radar,
        })

    return {"lakes": compared}
