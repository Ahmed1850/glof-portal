from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.db.session import get_db
from app.models.lake import Lake as LakeModel
from app.services.ml_service import calculate_ml_risk_score
from app.api.routers.early_warning import nearest_glacier_km
from app.services.glof_basins import assign_basin, estimate_outburst_volume_m3

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(
    prefix="/risk",
    tags=["Risk"]
)


@router.get("/{lake_id}")
@limiter.limit("60/minute")
def get_risk(request: Request, lake_id: int, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()

    if lake is None:
        raise HTTPException(status_code=404, detail="Lake not found")

    area_ha = float(lake.area_ha or 0.0)
    has_coords = lake.latitude is not None and lake.longitude is not None
    dist_to_glacier = round(nearest_glacier_km(lake.latitude, lake.longitude), 2) if has_coords else 8.0
    basin = assign_basin(lake.latitude, lake.longitude) if has_coords else None

    # Derive realistic spatial inputs from lake area, glacier proximity, and basin priority
    expansion_rate = round(min(45.0, max(3.5, area_ha * 0.42)), 2)
    slope = 28.0 if (basin and basin.get("monitoring_priority") == "Critical") else 21.0
    dist_to_fault = 9.5 if (basin and basin.get("id") in ("hunza", "shigar_baltoro", "nagar_hispar")) else 16.0

    final_risk = calculate_ml_risk_score(
        expansion_rate=expansion_rate,
        slope=slope,
        dist_to_glacier=dist_to_glacier,
        dist_to_fault=dist_to_fault,
    )

    volume_m3 = estimate_outburst_volume_m3(area_ha)

    return {
        "lake_id": lake.id,
        "lake_name": lake.name,
        "area_ha": lake.area_ha,
        "risk_level": final_risk,
        "spatial_metrics": {
            "dist_to_glacier_km": dist_to_glacier,
            "estimated_expansion_rate_pct": expansion_rate,
            "terrain_slope_deg": slope,
            "dist_to_fault_km": dist_to_fault,
            "estimated_outburst_volume_m3": volume_m3,
            "basin_name": basin["name"] if basin else None,
        },
        "recommendation": (
            "Immediate satellite + field telemetry monitoring required"
            if final_risk == "High"
            else "Regular seasonal NDWI/SAR monitoring advised"
        ),
    }
