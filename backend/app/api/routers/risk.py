from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.db.session import get_db
from app.models.lake import Lake as LakeModel
from app.services.ml_service import calculate_ml_risk_score

# Same limiter instance
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(
    prefix="/risk",
    tags=["Risk"]
)


@router.get("/{lake_id}")
@limiter.limit("60/minute")          # Reading risk info – normal limit
def get_risk(request: Request, lake_id: int, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()

    if lake is None:
        raise HTTPException(status_code=404, detail="Lake not found")

    # ML-based Risk Scoring (Placeholder values for demo)
    final_risk = calculate_ml_risk_score(
        expansion_rate=15.5, 
        slope=25.0, 
        dist_to_glacier=5.2, 
        dist_to_fault=12.0
    )

    return {
        "lake_id": lake.id,
        "lake_name": lake.name,
        "area_ha": lake.area_ha,
        "risk_level": final_risk,
        "recommendation": "Immediate monitoring required" if final_risk == "High" else "Regular monitoring advised"
    }