"""
Advanced GLOF Analytics Router:
  - /analytics/providers
  - /analytics/breach-simulation/{lake_id}
  - /analytics/triggers/regional  (registered before /triggers/{lake_id})
  - /analytics/triggers/{lake_id}
  - /analytics/districts
  - /analytics/basins/cascade
  - /analytics/compare
  - /analytics/sensitivity/{lake_id}
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.lake import Lake as LakeModel
from app.services.breach_simulator import simulate_breach
from app.services.provider_telemetry import get_providers_snapshot
from app.services.sensitivity_engine import run_monte_carlo_sensitivity
from app.services.spatial_analytics import (
    compare_lakes,
    get_basin_cascade_analytics,
    get_district_analytics,
)
from app.services.trigger_engine import get_lake_triggers, get_regional_triggers

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/providers")
@limiter.limit("120/minute")
def get_providers(request: Request):
    return {"providers": get_providers_snapshot()}


@router.get("/breach-simulation/{lake_id}")
@limiter.limit("60/minute")
def get_breach_simulation(
    request: Request,
    lake_id: int,
    breach_fraction: float = Query(0.75, ge=0.1, le=1.0),
    dam_type: Literal["moraine", "ice", "landslide"] = Query("moraine"),
    breach_duration_min: int = Query(45, ge=10, le=240),
    mean_depth_m: Optional[float] = Query(None, ge=1.0, le=250.0),
    db: Session = Depends(get_db),
):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if lake is None:
        raise HTTPException(status_code=404, detail="Lake not found")
    return simulate_breach(
        lake,
        breach_fraction=breach_fraction,
        dam_type=dam_type,
        breach_duration_min=breach_duration_min,
        mean_depth_m=mean_depth_m,
    )


@router.get("/triggers/regional")
@limiter.limit("60/minute")
def regional_triggers(request: Request, db: Session = Depends(get_db)):
    return get_regional_triggers(db)


@router.get("/triggers/{lake_id}")
@limiter.limit("60/minute")
def lake_triggers(request: Request, lake_id: int, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if lake is None:
        raise HTTPException(status_code=404, detail="Lake not found")
    return get_lake_triggers(lake, db)


@router.get("/districts")
@limiter.limit("60/minute")
def analytics_districts(request: Request, db: Session = Depends(get_db)):
    return get_district_analytics(db)


@router.get("/basins/cascade")
@limiter.limit("60/minute")
def analytics_basins_cascade(request: Request, db: Session = Depends(get_db)):
    return get_basin_cascade_analytics(db)


@router.get("/compare")
@limiter.limit("60/minute")
def analytics_compare(
    request: Request,
    lake_ids: Optional[str] = Query(None, description="Comma-separated lake IDs (e.g. 1,2,3)"),
    db: Session = Depends(get_db),
):
    return compare_lakes(db, lake_ids=lake_ids)


@router.get("/sensitivity/{lake_id}")
@limiter.limit("60/minute")
def analytics_sensitivity(
    request: Request,
    lake_id: int,
    temp_delta_c: float = Query(0.0, ge=-1.0, le=5.0),
    n_samples: int = Query(500, ge=100, le=2000),
    db: Session = Depends(get_db),
):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if lake is None:
        raise HTTPException(status_code=404, detail="Lake not found")
    return run_monte_carlo_sensitivity(
        lake,
        temp_delta_c=temp_delta_c,
        n_samples=n_samples,
    )
