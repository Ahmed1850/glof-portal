from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.lake import Lake as LakeModel
from app.services.early_warning import compute_early_warning
from app.services.geo_helpers import (
    haversine_km,
    nearest_glacier_km,
    population_heuristic,
    wkt_point,
    zone_radii_m,
)
from app.services.glof_basins import assign_basin, estimate_outburst_volume_m3
from app.services.spatial_analytics import classify_lake_district
from app.utils.risk import calculate_risk


def _minimal_pdf_bytes(lines: list[str]) -> bytes:
    """Fallback PDF generator using only Python stdlib if reportlab is unavailable."""
    escaped = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in lines
    ]
    text_ops = ["BT", "/F1 11 Tf", "50 750 Td", "15 TL"]
    for idx, line in enumerate(escaped):
        if idx == 0:
            text_ops.append(f"({line}) Tj")
        else:
            text_ops.append(f"T* ({line}) Tj")
    text_ops.append("ET")
    stream_data = "\n".join(text_ops).encode("latin-1", errors="replace")

    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("latin-1") + stream_data + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(out.tell())
        out.write(obj)
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )
    return out.getvalue()


def generate_sitrep(lake_data: dict) -> bytes:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "GLOF PORTAL - GEOSPATIAL SITUATION REPORT (SITREP)",
        f"Generated: {timestamp}",
        "------------------------------------------------------------",
        f"Lake Name: {lake_data.get('name')} (ID: {lake_data.get('id', 'N/A')})",
        f"Coordinates (WGS84): {lake_data.get('lat')} N, {lake_data.get('lon')} E",
        f"WKT Geometry: {lake_data.get('geom', 'N/A')}",
        f"District: {lake_data.get('district', 'N/A')}",
        f"Surface Area: {lake_data.get('area_ha')} ha",
        f"Risk Classification: {lake_data.get('risk')}",
        f"Drainage Basin: {lake_data.get('basin_name', 'N/A')} ({lake_data.get('river', 'N/A')})",
        f"Nearest Glacier Distance: {lake_data.get('nearest_glacier_km', 'N/A')} km",
        f"Estimated Outburst Volume: {lake_data.get('volume_million_m3', 'N/A')} million m3",
        f"Empirical Peak Discharge (Qp): {lake_data.get('peak_discharge_m3s', 'N/A')} m3/s",
        f"At-Risk Downstream Settlements: {', '.join(lake_data.get('settlements', [])) or 'N/A'}",
    ]
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        y = 750
        c.setFont("Helvetica-Bold", 13)
        c.drawString(50, y, lines[0])
        y -= 20
        c.setFont("Helvetica", 10)
        for line in lines[1:]:
            c.drawString(50, y, line)
            y -= 16
        c.save()
        return buffer.getvalue()
    except Exception:
        return _minimal_pdf_bytes(lines)


def _normalize_risk(raw_risk: Optional[str], area_ha: Optional[float]) -> str:
    val = (raw_risk or "").strip().capitalize()
    if val in ("High", "Medium", "Low"):
        return val
    return calculate_risk(area_ha or 0)


def build_export_geojson(
    lakes: list[LakeModel],
    *,
    risk_level: Optional[str] = None,
    basin_id: Optional[str] = None,
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for lake in lakes:
        if lake.latitude is None or lake.longitude is None:
            continue
        lat, lon = float(lake.latitude), float(lake.longitude)
        risk = _normalize_risk(lake.risk_level, lake.area_ha)
        if risk_level and risk_level != "All" and risk.lower() != risk_level.lower():
            continue

        basin = assign_basin(lat, lon)
        if basin_id and basin["id"] != basin_id:
            continue

        district = classify_lake_district(lat, lon) or {}
        vol_m3 = float(estimate_outburst_volume_m3(lake.area_ha) or 0.0)
        qp_m3s = round(0.0048 * (vol_m3 ** 0.89), 1) if vol_m3 > 0 else 0.0
        danger_m, warning_m = zone_radii_m(risk)
        glacier_km = round(nearest_glacier_km(lat, lon), 2)

        features.append({
            "type": "Feature",
            "id": lake.id,
            "geometry": {
                "type": "Point",
                "coordinates": [round(lon, 5), round(lat, 5)],
            },
            "properties": {
                "id": lake.id,
                "name": lake.name,
                "area_ha": lake.area_ha,
                "risk_level": risk,
                "wkt": lake.geom or wkt_point(lat, lon),
                "district": district.get("name"),
                "district_pcode": district.get("pcode"),
                "province": district.get("province"),
                "basin_id": basin["id"],
                "basin_name": basin["name"],
                "river": basin["river"],
                "estimated_volume_m3": vol_m3,
                "peak_discharge_m3s": qp_m3s,
                "warning_radius_m": warning_m,
                "danger_radius_m": danger_m,
                "nearest_glacier_km": glacier_km,
            },
        })

    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_features": len(features),
        "features": features,
    }


def build_export_csv(
    lakes: list[LakeModel],
    *,
    risk_level: Optional[str] = None,
    basin_id: Optional[str] = None,
) -> str:
    fieldnames = [
        "id",
        "name",
        "latitude",
        "longitude",
        "wkt",
        "area_ha",
        "risk_level",
        "district",
        "district_pcode",
        "province",
        "basin_id",
        "basin_name",
        "river",
        "nearest_glacier_km",
        "estimated_volume_m3",
        "peak_discharge_m3s",
        "ew_score",
        "ew_level",
        "danger_radius_m",
        "warning_radius_m",
        "danger_population",
        "warning_population",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()

    for lake in lakes:
        has_coords = lake.latitude is not None and lake.longitude is not None
        risk = _normalize_risk(lake.risk_level, lake.area_ha)
        if risk_level and risk_level != "All" and risk.lower() != risk_level.lower():
            continue

        basin = assign_basin(lake.latitude, lake.longitude) if has_coords else {}
        if basin_id and basin.get("id") != basin_id:
            continue

        district = classify_lake_district(lake.latitude, lake.longitude) if has_coords else {}
        district = district or {}
        vol_m3 = float(estimate_outburst_volume_m3(lake.area_ha) or 0.0)
        qp_m3s = round(0.0048 * (vol_m3 ** 0.89), 1) if vol_m3 > 0 else 0.0
        danger_m, warning_m = zone_radii_m(risk)
        glacier_km = round(nearest_glacier_km(lake.latitude, lake.longitude), 2) if has_coords else None
        pop = (
            population_heuristic(lake.latitude, lake.longitude, lake.area_ha)
            if has_coords
            else {"danger_population": 0, "warning_population": 0}
        )
        ew = compute_early_warning(
            area_ha=lake.area_ha,
            glacier_distance_km=glacier_km,
            danger_population=pop["danger_population"],
            warning_population=pop["warning_population"],
            include_glacier=has_coords,
        )

        writer.writerow({
            "id": lake.id,
            "name": lake.name,
            "latitude": lake.latitude,
            "longitude": lake.longitude,
            "wkt": lake.geom or wkt_point(lake.latitude, lake.longitude),
            "area_ha": lake.area_ha,
            "risk_level": risk,
            "district": district.get("name"),
            "district_pcode": district.get("pcode"),
            "province": district.get("province"),
            "basin_id": basin.get("id"),
            "basin_name": basin.get("name"),
            "river": basin.get("river"),
            "nearest_glacier_km": glacier_km,
            "estimated_volume_m3": vol_m3,
            "peak_discharge_m3s": qp_m3s,
            "ew_score": ew.get("score"),
            "ew_level": ew.get("level"),
            "danger_radius_m": danger_m,
            "warning_radius_m": warning_m,
            "danger_population": pop["danger_population"],
            "warning_population": pop["warning_population"],
        })

    return buf.getvalue()


def build_sitrep_briefing(lake: LakeModel, db: Session) -> dict[str, Any]:
    has_coords = lake.latitude is not None and lake.longitude is not None
    lat = float(lake.latitude) if has_coords else None
    lon = float(lake.longitude) if has_coords else None
    area_ha = float(lake.area_ha or 0.0)
    risk_level = _normalize_risk(lake.risk_level, area_ha)
    basin = assign_basin(lat, lon) if has_coords else {}
    district = (classify_lake_district(lat, lon) if has_coords else None) or {}
    glacier_km = round(nearest_glacier_km(lat, lon), 2) if has_coords else None
    vol_m3 = float(estimate_outburst_volume_m3(area_ha) or 0.0)
    qp_m3s = round(0.0048 * (vol_m3 ** 0.89), 1) if vol_m3 > 0 else 0.0
    danger_m, warning_m = zone_radii_m(risk_level)

    pop = (
        population_heuristic(lat, lon, area_ha)
        if has_coords
        else {"danger_population": 0, "warning_population": 0}
    )
    ew = compute_early_warning(
        area_ha=area_ha,
        glacier_distance_km=glacier_km,
        danger_population=pop["danger_population"],
        warning_population=pop["warning_population"],
        include_glacier=has_coords,
    )

    # Downstream corridor cumulative length & nodes
    nodes_summary: list[dict[str, Any]] = []
    cum_km = 0.0
    if has_coords and lat is not None and lon is not None:
        prev_lat, prev_lon = lat, lon
        for node in (basin.get("downstream_storage") or []):
            n_lat, n_lon = float(node["lat"]), float(node["lon"])
            seg = haversine_km(prev_lat, prev_lon, n_lat, n_lon)
            cum_km = round(cum_km + seg, 2)
            eta_min = round((cum_km / 16.2) * 60.0, 1)
            nodes_summary.append({
                "name": node["name"],
                "type": node.get("type", "valley_plain"),
                "distance_km": cum_km,
                "estimated_arrival_min": eta_min,
            })
            prev_lat, prev_lon = n_lat, n_lon

    triggers_payload: dict[str, Any] = {
        "composite_trigger_score": round(float(ew.get("score") or 0.0) * 0.8, 1),
        "trigger_level": ew.get("level", "Normal"),
        "provenance": {"fallback_used": True},
    }
    if has_coords:
        try:
            from app.services.trigger_engine import get_lake_triggers
            triggers_payload = get_lake_triggers(lake, db)
        except Exception:
            pass

    if risk_level == "High" or ew.get("level") in ("Critical", "Warning"):
        recommendation = (
            f"Priority telemetry watch recommended for {lake.name}: verify Sentinel-2/SAR shoreline extent, "
            f"alert downstream communities ({', '.join(basin.get('at_risk_settlements', [])[:4]) or 'basin corridor'}), "
            "and stage rapid-response telemetry."
        )
    elif risk_level == "Medium":
        recommendation = (
            f"Maintain enhanced seasonal NDWI/SAR monitoring for {lake.name} and track thermal melt anomalies."
        )
    else:
        recommendation = (
            f"Routine surveillance for {lake.name}; re-evaluate following peak summer melt or seismic events."
        )

    return {
        "lake": {
            "id": lake.id,
            "name": lake.name,
            "latitude": lake.latitude,
            "longitude": lake.longitude,
            "wkt": lake.geom or wkt_point(lake.latitude, lake.longitude),
            "area_ha": lake.area_ha,
            "district": district.get("name"),
            "district_pcode": district.get("pcode"),
            "province": district.get("province"),
            "basin_id": basin.get("id"),
            "basin_name": basin.get("name"),
            "river": basin.get("river"),
            "nearest_glacier_km": glacier_km,
        },
        "risk": {
            "risk_level": risk_level,
            "ew_score": ew.get("score"),
            "ew_level": ew.get("level"),
            "estimated_volume_m3": vol_m3,
            "estimated_volume_million_m3": round(vol_m3 / 1e6, 3) if vol_m3 else 0.0,
            "peak_discharge_m3s": qp_m3s,
            "danger_radius_m": danger_m,
            "warning_radius_m": warning_m,
            "danger_population": pop["danger_population"],
            "warning_population": pop["warning_population"],
        },
        "corridor_summary": {
            "total_corridor_km": round(cum_km, 2),
            "node_count": len(nodes_summary),
            "downstream_nodes": nodes_summary,
            "at_risk_settlements": basin.get("at_risk_settlements", []),
        },
        "triggers": {
            "composite_trigger_score": triggers_payload.get("composite_trigger_score"),
            "trigger_level": triggers_payload.get("trigger_level"),
            "pillars": triggers_payload.get("pillars"),
            "provenance": triggers_payload.get("provenance"),
        },
        "recommendation": recommendation,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
