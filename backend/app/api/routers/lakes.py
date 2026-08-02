from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List
from slowapi import Limiter
from slowapi.util import get_remote_address
from math import radians, cos, sin, asin, sqrt

from app.db.session import get_db
from app.models.lake import Lake as LakeModel, KnownLake
from app.schemas.lake import Lake, LakeCreate
from app.utils.risk import calculate_risk

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(
    prefix="/lakes",
    tags=["Lakes"]
)


def haversine(lat1, lon1, lat2, lon2):
    """Distance between two points in kilometers"""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return 6371 * c


# ==================== KNOWN LAKES SEED DATA ====================
KNOWN_LAKES_SEED = [
    {"name": "Attabad Lake", "latitude": 36.318, "longitude": 74.865, "area_ha": 280, "district": "Hunza"},
    {"name": "Shisper Lake", "latitude": 36.412, "longitude": 74.623, "area_ha": 48.5, "district": "Hunza"},
    {"name": "Passu Lake", "latitude": 36.468, "longitude": 74.895, "area_ha": 9.2, "district": "Hunza"},
    {"name": "Rush Lake", "latitude": 36.174, "longitude": 74.885, "area_ha": 13.8, "district": "Nagar"},
    {"name": "Satpara Lake", "latitude": 35.225, "longitude": 75.632, "area_ha": 28, "district": "Skardu"},
    {"name": "Borit Lake", "latitude": 36.432, "longitude": 74.862, "area_ha": 7.5, "district": "Hunza"},
    {"name": "Khurdopin Lake", "latitude": 36.385, "longitude": 75.112, "area_ha": 22, "district": "Hunza"},
    {"name": "Sokha Lake", "latitude": 35.918, "longitude": 75.421, "area_ha": 11.4, "district": "Skardu"},
    {"name": "Ghamu Bar Lake", "latitude": 36.291, "longitude": 74.978, "area_ha": 16.7, "district": "Nagar"},
    {"name": "Baltoro Lake", "latitude": 35.752, "longitude": 76.435, "area_ha": 35, "district": "Shigar"},
    {"name": "Shimshal Lake", "latitude": 36.485, "longitude": 75.325, "area_ha": 19.5, "district": "Hunza"},
    {"name": "Hispar Lake", "latitude": 36.178, "longitude": 75.187, "area_ha": 14.2, "district": "Nagar"},
    {"name": "Hassanabad Lake", "latitude": 36.405, "longitude": 74.615, "area_ha": 42, "district": "Hunza"},
    {"name": "Sheosar Lake", "latitude": 35.033, "longitude": 75.250, "area_ha": 25, "district": "Skardu"},
    {"name": "Upper Kachura Lake", "latitude": 35.445, "longitude": 75.445, "area_ha": 12, "district": "Skardu"},
    {"name": "Lower Kachura Lake", "latitude": 35.430, "longitude": 75.455, "area_ha": 8, "district": "Skardu"},
    {"name": "Batura Glacier Lake", "latitude": 36.550, "longitude": 74.850, "area_ha": 18, "district": "Hunza"},
    {"name": "Ghulkin Lake", "latitude": 36.430, "longitude": 74.870, "area_ha": 9.5, "district": "Hunza"},
    {"name": "Gulmit Lake", "latitude": 36.390, "longitude": 74.870, "area_ha": 6.8, "district": "Hunza"},
    {"name": "Hopar Lake", "latitude": 36.220, "longitude": 74.770, "area_ha": 11, "district": "Nagar"},
    {"name": "Naltar Lake", "latitude": 36.160, "longitude": 74.200, "area_ha": 7.2, "district": "Gilgit"},
    {"name": "Rakaposhi Base Lake", "latitude": 36.150, "longitude": 74.500, "area_ha": 8.5, "district": "Nagar"},
    {"name": "Ultar Glacier Lake", "latitude": 36.390, "longitude": 74.700, "area_ha": 15, "district": "Hunza"},
    {"name": "Barpu Glacier Lake", "latitude": 36.200, "longitude": 74.850, "area_ha": 10, "district": "Nagar"},
    {"name": "Pisan Glacier Lake", "latitude": 36.180, "longitude": 74.820, "area_ha": 12.5, "district": "Nagar"},
    {"name": "Minapin Glacier Lake", "latitude": 36.170, "longitude": 74.650, "area_ha": 9, "district": "Nagar"},
    {"name": "Chillinji Lake", "latitude": 36.720, "longitude": 74.120, "area_ha": 14, "district": "Ghizer"},
    {"name": "Darkot Lake", "latitude": 36.650, "longitude": 73.450, "area_ha": 11, "district": "Ghizer"},
    {"name": "Yasin Valley Lake", "latitude": 36.400, "longitude": 73.300, "area_ha": 8, "district": "Ghizer"},
    {"name": "Ishkoman Lake", "latitude": 36.550, "longitude": 73.850, "area_ha": 7.5, "district": "Ghizer"},
    {"name": "Gupis Lake", "latitude": 36.230, "longitude": 73.450, "area_ha": 6, "district": "Ghizer"},
    {"name": "Phander Lake", "latitude": 36.150, "longitude": 72.950, "area_ha": 18, "district": "Ghizer"},
    {"name": "Shandur Lake", "latitude": 36.090, "longitude": 72.550, "area_ha": 22, "district": "Ghizer"},
    {"name": "Chitral Gol Lake", "latitude": 35.850, "longitude": 71.780, "area_ha": 9, "district": "Chitral"},
    {"name": "Tirich Mir Base Lake", "latitude": 36.250, "longitude": 71.850, "area_ha": 12, "district": "Chitral"},
    {"name": "Buni Zom Lake", "latitude": 36.000, "longitude": 72.200, "area_ha": 8.5, "district": "Chitral"},
    {"name": "Ghamot Lake", "latitude": 35.200, "longitude": 74.000, "area_ha": 7, "district": "Astore"},
    {"name": "Rama Lake", "latitude": 35.350, "longitude": 74.800, "area_ha": 15, "district": "Astore"},
    {"name": "Mirror Lake (Astore)", "latitude": 35.280, "longitude": 74.750, "area_ha": 6.5, "district": "Astore"},
    {"name": "Deosai Plateau Lake", "latitude": 35.000, "longitude": 75.400, "area_ha": 20, "district": "Skardu"},
    {"name": "Sadpara Upstream Lake", "latitude": 35.240, "longitude": 75.610, "area_ha": 10, "district": "Skardu"},
    {"name": "K2 Base Camp Lake", "latitude": 35.850, "longitude": 76.500, "area_ha": 8, "district": "Shigar"},
    {"name": "Concordia Lake", "latitude": 35.750, "longitude": 76.520, "area_ha": 11, "district": "Shigar"},
    {"name": "Hushe Valley Lake", "latitude": 35.450, "longitude": 76.350, "area_ha": 9, "district": "Ghanche"},
    {"name": "Thalle Glacier Lake", "latitude": 35.500, "longitude": 76.200, "area_ha": 13, "district": "Ghanche"},
    {"name": "Masherbrum Lake", "latitude": 35.600, "longitude": 76.300, "area_ha": 10.5, "district": "Ghanche"},
]


def is_generic_name(name: str) -> bool:
    if not name:
        return True
    n = name.lower().strip()
    return (
        n.startswith("glof lake")
        or n.startswith("unknown")
        or n == "unnamed"
        or n.startswith("lake ")
        or n.startswith("unnamed lake")
    )


def suggest_name(lat: float, lon: float, db: Session, original_name: str = None) -> str:
    """
    Auto-naming logic:
    1. Match nearest known lake within 2.5 km
    2. Otherwise keep original name if it is not generic
    3. Otherwise create a coordinate-based temporary name
    """
    if lat is None or lon is None:
        return original_name or "Unnamed Lake"

    known = db.query(KnownLake).all()

    best_match = None
    best_distance = 999

    for k in known:
        dist = haversine(lat, lon, k.latitude, k.longitude)
        if dist < best_distance:
            best_distance = dist
            best_match = k

    # Strong match → use known name
    if best_match and best_distance <= 2.5:
        return best_match.name

    # If original name is already good, keep it
    if original_name and not is_generic_name(original_name):
        return original_name

    # Fallback → temporary coordinate name
    return f"Unnamed Lake ({lat:.2f}N, {lon:.2f}E)"


# ==================== SEED KNOWN LAKES ====================
@router.post("/seed-known")
@limiter.limit("2/minute")
def seed_known_lakes(request: Request, db: Session = Depends(get_db)):
    """One-time: fill known_lakes table"""
    existing = db.query(KnownLake).count()
    if existing > 0:
        return {"message": f"known_lakes already has {existing} rows. Skipped.", "count": existing}

    for item in KNOWN_LAKES_SEED:
        db.add(KnownLake(
            name=item["name"],
            latitude=item["latitude"],
            longitude=item["longitude"],
            area_ha=item.get("area_ha"),
            district=item.get("district"),
            source="manual"
        ))
    db.commit()
    return {"message": f"Seeded {len(KNOWN_LAKES_SEED)} known lakes", "count": len(KNOWN_LAKES_SEED)}


# ==================== NORMAL ENDPOINTS ====================
@router.get("/", response_model=List[Lake])
@limiter.limit("60/minute")
def get_all_lakes(request: Request, db: Session = Depends(get_db)):
    return db.query(LakeModel).all()


@router.get("/{lake_id}", response_model=Lake)
@limiter.limit("60/minute")
def get_lake(request: Request, lake_id: int, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if lake is None:
        raise HTTPException(status_code=404, detail="Lake not found")
    return lake


@router.post("/", response_model=Lake)
@limiter.limit("10/minute")
def create_lake(request: Request, lake: LakeCreate, db: Session = Depends(get_db)):
    risk = calculate_risk(lake.area_ha)

    # Auto-name
    final_name = suggest_name(lake.latitude, lake.longitude, db, lake.name)

    new_lake = LakeModel(
        name=final_name,
        area_ha=lake.area_ha,
        risk_level=risk,
        latitude=lake.latitude,
        longitude=lake.longitude
    )
    db.add(new_lake)
    db.commit()
    db.refresh(new_lake)
    return new_lake


@router.post("/bulk")
@limiter.limit("5/minute")
def create_lakes_bulk(request: Request, lakes: List[LakeCreate], db: Session = Depends(get_db)):
    created = []
    for lake_data in lakes:
        risk = calculate_risk(lake_data.area_ha)

        # Auto-name each lake
        final_name = suggest_name(
            lake_data.latitude,
            lake_data.longitude,
            db,
            lake_data.name
        )

        new_lake = LakeModel(
            name=final_name,
            area_ha=lake_data.area_ha,
            risk_level=risk,
            latitude=lake_data.latitude,
            longitude=lake_data.longitude
        )
        db.add(new_lake)
        created.append(new_lake)

    db.commit()
    for lake in created:
        db.refresh(lake)

    return {
        "message": f"Successfully saved {len(created)} lakes (auto-named)",
        "count": len(created)
    }


@router.post("/match-names")
@limiter.limit("3/minute")
def match_lake_names(request: Request, db: Session = Depends(get_db)):
    """
    Re-run naming on all existing lakes:
    1. Match with known lakes (within 2.5 km)
    2. If still generic name → coordinate-based name
    """
    lakes = db.query(LakeModel).all()
    known = db.query(KnownLake).all()
    matched = 0
    fallback = 0

    if not known:
        raise HTTPException(
            status_code=400,
            detail="No known lakes found. Call POST /lakes/seed-known first."
        )

    for lake in lakes:
        if not lake.latitude or not lake.longitude:
            continue

        # 1. Try match with known lakes
        best_match = None
        best_distance = 999

        for k in known:
            dist = haversine(lake.latitude, lake.longitude, k.latitude, k.longitude)
            if dist < best_distance:
                best_distance = dist
                best_match = k

        if best_match and best_distance <= 2.5:
            if lake.name != best_match.name:
                lake.name = best_match.name
                matched += 1
            continue

        # 2. Fallback for generic names
        if is_generic_name(lake.name):
            new_name = f"Unnamed Lake ({lake.latitude:.2f}N, {lake.longitude:.2f}E)"
            if lake.name != new_name:
                lake.name = new_name
                fallback += 1

    db.commit()
    return {
        "message": f"Matched {matched} lakes with known names, renamed {fallback} generic lakes",
        "matched": matched,
        "fallback": fallback
    }


@router.put("/{lake_id}/rename")
@limiter.limit("20/minute")
def rename_lake(request: Request, lake_id: int, new_name: str, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if not lake:
        raise HTTPException(status_code=404, detail="Lake not found")

    lake.name = new_name.strip()
    db.commit()
    db.refresh(lake)
    return {"message": f"Lake renamed to '{lake.name}'"}


@router.delete("/{lake_id}")
@limiter.limit("5/minute")
def delete_lake(request: Request, lake_id: int, db: Session = Depends(get_db)):
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if lake is None:
        raise HTTPException(status_code=404, detail="Lake not found")

    db.delete(lake)
    db.commit()
    return {"message": f"Lake '{lake.name}' deleted successfully"}


@router.post("/recalculate-risk")
@limiter.limit("3/minute")
def recalculate_all_risks(request: Request, db: Session = Depends(get_db)):
    lakes = db.query(LakeModel).all()
    updated = 0
    for lake in lakes:
        new_risk = calculate_risk(lake.area_ha)
        if lake.risk_level != new_risk:
            lake.risk_level = new_risk
            updated += 1
    db.commit()
    return {"message": f"Risk recalculated for {updated} lakes"}