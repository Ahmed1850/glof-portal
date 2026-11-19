from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.lake import Lake as LakeModel
from app.services.report_service import generate_sitrep
from app.services.glof_basins import assign_basin, estimate_outburst_volume_m3
from app.api.routers.early_warning import nearest_glacier_km
from app.utils.risk import calculate_risk

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/sitrep/{lake_id}")
def get_sitrep(lake_id: int, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if not lake:
        raise HTTPException(status_code=404, detail="Lake not found")

    has_coords = lake.latitude is not None and lake.longitude is not None
    basin = assign_basin(lake.latitude, lake.longitude) if has_coords else {}
    glacier_km = round(nearest_glacier_km(lake.latitude, lake.longitude), 2) if has_coords else None
    vol_m3 = estimate_outburst_volume_m3(lake.area_ha)
    risk = lake.risk_level or calculate_risk(lake.area_ha or 0)

    lake_data = {
        "id": lake.id,
        "name": lake.name,
        "lat": lake.latitude,
        "lon": lake.longitude,
        "geom": lake.geom or (f"POINT({lake.longitude} {lake.latitude})" if has_coords else None),
        "area_ha": lake.area_ha,
        "risk": risk,
        "basin_name": basin.get("name"),
        "river": basin.get("river"),
        "nearest_glacier_km": glacier_km,
        "volume_million_m3": round(vol_m3 / 1e6, 3) if vol_m3 else None,
        "settlements": basin.get("at_risk_settlements", []),
    }

    pdf_bytes = generate_sitrep(lake_data)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=SITREP_Lake_{lake.id}.pdf"},
    )
