from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.lake import Lake as LakeModel
from app.services.geo_helpers import nearest_glacier_km
from app.services.glof_basins import assign_basin, estimate_outburst_volume_m3
from app.services.report_service import (
    build_export_csv,
    build_export_geojson,
    build_sitrep_briefing,
    generate_sitrep,
)
from app.services.spatial_analytics import classify_lake_district
from app.utils.risk import calculate_risk

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/export/geojson")
def export_geojson(
    risk_level: Optional[str] = Query(None, description="Filter by High, Medium, or Low"),
    basin_id: Optional[str] = Query(None, description="Filter by basin ID"),
    db: Session = Depends(get_db),
):
    lakes = db.query(LakeModel).all()
    fc = build_export_geojson(lakes, risk_level=risk_level, basin_id=basin_id)
    return Response(
        content=json.dumps(fc, indent=2),
        media_type="application/geo+json",
        headers={"Content-Disposition": "attachment; filename=glof_lakes_export.geojson"},
    )


@router.get("/export/csv")
def export_csv(
    risk_level: Optional[str] = Query(None, description="Filter by High, Medium, or Low"),
    basin_id: Optional[str] = Query(None, description="Filter by basin ID"),
    db: Session = Depends(get_db),
):
    lakes = db.query(LakeModel).all()
    csv_text = build_export_csv(lakes, risk_level=risk_level, basin_id=basin_id)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=glof_lakes_inventory.csv"},
    )


@router.get("/sitrep/{lake_id}/briefing")
def get_sitrep_briefing(lake_id: int, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if not lake:
        raise HTTPException(status_code=404, detail="Lake not found")
    return build_sitrep_briefing(lake, db)


@router.get("/sitrep/{lake_id}")
def get_sitrep(lake_id: int, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if not lake:
        raise HTTPException(status_code=404, detail="Lake not found")

    has_coords = lake.latitude is not None and lake.longitude is not None
    basin = assign_basin(lake.latitude, lake.longitude) if has_coords else {}
    district = (classify_lake_district(lake.latitude, lake.longitude) if has_coords else None) or {}
    glacier_km = round(nearest_glacier_km(lake.latitude, lake.longitude), 2) if has_coords else None
    vol_m3 = estimate_outburst_volume_m3(lake.area_ha)
    qp_m3s = round(0.0048 * (float(vol_m3) ** 0.89), 1) if vol_m3 else None
    risk = lake.risk_level or calculate_risk(lake.area_ha or 0)

    lake_data = {
        "id": lake.id,
        "name": lake.name,
        "lat": lake.latitude,
        "lon": lake.longitude,
        "geom": lake.geom or (f"POINT({lake.longitude} {lake.latitude})" if has_coords else None),
        "district": district.get("name", "N/A"),
        "area_ha": lake.area_ha,
        "risk": risk,
        "basin_name": basin.get("name"),
        "river": basin.get("river"),
        "nearest_glacier_km": glacier_km,
        "volume_million_m3": round(vol_m3 / 1e6, 3) if vol_m3 else None,
        "peak_discharge_m3s": qp_m3s,
        "settlements": basin.get("at_risk_settlements", []),
    }

    pdf_bytes = generate_sitrep(lake_data)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=SITREP_Lake_{lake.id}.pdf"},
    )
