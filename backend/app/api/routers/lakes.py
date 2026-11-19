from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Any, List, Optional
from slowapi import Limiter
from slowapi.util import get_remote_address
from math import radians, degrees, cos, sin, asin, atan2, sqrt, pi

from app.db.session import get_db
from app.models.lake import Lake as LakeModel, KnownLake
from app.schemas.lake import Lake, LakeCreate
from app.utils.risk import calculate_risk
from app.services.glof_basins import assign_basin, estimate_outburst_volume_m3
from app.api.routers.early_warning import GLACIER_REFS, nearest_glacier_km

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(
    prefix="/lakes",
    tags=["Lakes"]
)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points in kilometers."""
    lat1_r, lon1_r, lat2_r, lon2_r = map(radians, [lat1, lon1, lat2, lon2])
    dlon = lon2_r - lon1_r
    dlat = lat2_r - lat1_r
    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    c = 2 * asin(min(1.0, sqrt(a)))
    return 6371.0088 * c


def compass_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing in degrees (0-360) from point 1 to point 2."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    x = sin(dlon) * cos(phi2)
    y = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dlon)
    return round((degrees(atan2(x, y)) + 360) % 360, 1)


def geodesic_circle_ring(lat: float, lon: float, radius_m: float, segments: int = 48) -> list[list[float]]:
    """
    Generate an RFC 7946 GeoJSON Polygon exterior ring ([lon, lat]) representing
    a true metric buffer circle on the WGS84 sphere (avoids EPSG:4326 degree distortion).
    """
    earth_r = 6371008.8
    lat_r = radians(lat)
    lon_r = radians(lon)
    ang_dist = radius_m / earth_r
    ring: list[list[float]] = []
    for i in range(segments):
        brng = 2.0 * pi * i / segments
        lat2 = asin(
            sin(lat_r) * cos(ang_dist) + cos(lat_r) * sin(ang_dist) * cos(brng)
        )
        lon2 = lon_r + atan2(
            sin(brng) * sin(ang_dist) * cos(lat_r),
            cos(ang_dist) - sin(lat_r) * sin(lat2),
        )
        ring.append([round(degrees(lon2), 5), round(degrees(lat2), 5)])
    if ring:
        ring.append(ring[0])
    return ring


def _wkt_point(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    if lat is None or lon is None:
        return None
    # Standard WKT axis order: POINT(X Y) = POINT(longitude latitude)
    return f"POINT({lon} {lat})"


def _zone_radii_m(risk: str) -> tuple[int, int]:
    if risk == "High":
        return 5000, 10000
    if risk == "Medium":
        return 2000, 5000
    return 1000, 2000


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
    best_distance = 999.0

    for k in known:
        dist = haversine(lat, lon, k.latitude, k.longitude)
        if dist < best_distance:
            best_distance = dist
            best_match = k

    if best_match and best_distance <= 2.5:
        return best_match.name

    if original_name and not is_generic_name(original_name):
        return original_name

    return f"Unnamed Lake ({lat:.2f}N, {lon:.2f}E)"


# ==================== SEED KNOWN LAKES ====================
@router.post("/seed-known")
@limiter.limit("2/minute")
def seed_known_lakes(request: Request, db: Session = Depends(get_db)):
    """One-time: fill known_lakes table with WKT geometries."""
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
            source="manual",
            geom=_wkt_point(item["latitude"], item["longitude"]),
        ))
    db.commit()
    return {"message": f"Seeded {len(KNOWN_LAKES_SEED)} known lakes", "count": len(KNOWN_LAKES_SEED)}


# ==================== ADVANCED GIS ENDPOINTS ====================
@router.get("/geojson")
@limiter.limit("60/minute")
def get_lakes_geojson(
    request: Request,
    risk_level: Optional[str] = Query(None, description="Filter by High, Medium, or Low"),
    basin_id: Optional[str] = Query(None, description="Filter by GLOF drainage basin id"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    RFC 7946 GeoJSON FeatureCollection of monitored glacial lakes.
    Coordinates follow [longitude, latitude] order (CRS: EPSG:4326).
    """
    lakes = db.query(LakeModel).all()
    features = []

    for lake in lakes:
        if lake.latitude is None or lake.longitude is None:
            continue
        risk = lake.risk_level or calculate_risk(lake.area_ha or 0)
        if risk_level and risk_level != "All" and risk.lower() != risk_level.lower():
            continue

        basin = assign_basin(lake.latitude, lake.longitude)
        if basin_id and basin["id"] != basin_id:
            continue

        vol_m3 = estimate_outburst_volume_m3(lake.area_ha)
        glacier_km = round(nearest_glacier_km(lake.latitude, lake.longitude), 2)
        danger_m, warning_m = _zone_radii_m(risk)
        wkt = lake.geom or _wkt_point(lake.latitude, lake.longitude)

        features.append({
            "type": "Feature",
            "id": lake.id,
            "geometry": {
                "type": "Point",
                "coordinates": [round(float(lake.longitude), 5), round(float(lake.latitude), 5)],
            },
            "properties": {
                "id": lake.id,
                "name": lake.name,
                "area_ha": lake.area_ha,
                "risk_level": risk,
                "wkt": wkt,
                "basin_id": basin["id"],
                "basin_name": basin["name"],
                "river": basin["river"],
                "monitoring_priority": basin.get("monitoring_priority"),
                "nearest_glacier_km": glacier_km,
                "estimated_volume_m3": vol_m3,
                "estimated_volume_million_m3": round(vol_m3 / 1e6, 3) if vol_m3 else None,
                "danger_radius_m": danger_m,
                "warning_radius_m": warning_m,
                "at_risk_settlements": basin.get("at_risk_settlements", []),
            },
        })

    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "total_features": len(features),
        "features": features,
    }


@router.get("/spatial-search")
@limiter.limit("60/minute")
def spatial_search_lakes(
    request: Request,
    lat: Optional[float] = Query(None, ge=-90, le=90, description="Center latitude for proximity search"),
    lon: Optional[float] = Query(None, ge=-180, le=180, description="Center longitude for proximity search"),
    radius_km: float = Query(35.0, ge=0.5, le=500.0, description="Search radius in kilometers"),
    min_lon: Optional[float] = Query(None, description="Bounding box min longitude"),
    min_lat: Optional[float] = Query(None, description="Bounding box min latitude"),
    max_lon: Optional[float] = Query(None, description="Bounding box max longitude"),
    max_lat: Optional[float] = Query(None, description="Bounding box max latitude"),
    risk_level: Optional[str] = Query(None, description="Optional risk filter: High | Medium | Low"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Spatial proximity (K-Nearest Neighbor / radius buffer) and bounding-box query over monitored lakes.
    """
    lakes = db.query(LakeModel).all()
    has_center = lat is not None and lon is not None
    has_bbox = all(v is not None for v in (min_lon, min_lat, max_lon, max_lat))

    matches = []
    for lake in lakes:
        if lake.latitude is None or lake.longitude is None:
            continue
        risk = lake.risk_level or calculate_risk(lake.area_ha or 0)
        if risk_level and risk_level != "All" and risk.lower() != risk_level.lower():
            continue

        if has_bbox and not (min_lon <= lake.longitude <= max_lon and min_lat <= lake.latitude <= max_lat):
            continue

        dist_km = None
        bearing = None
        if has_center:
            dist_km = round(haversine(lat, lon, lake.latitude, lake.longitude), 2)
            if dist_km > radius_km:
                continue
            bearing = compass_bearing(lat, lon, lake.latitude, lake.longitude)

        basin = assign_basin(lake.latitude, lake.longitude)
        matches.append({
            "id": lake.id,
            "name": lake.name,
            "area_ha": lake.area_ha,
            "risk_level": risk,
            "latitude": lake.latitude,
            "longitude": lake.longitude,
            "wkt": lake.geom or _wkt_point(lake.latitude, lake.longitude),
            "distance_km": dist_km,
            "bearing_deg": bearing,
            "basin_id": basin["id"],
            "basin_name": basin["name"],
            "estimated_volume_m3": estimate_outburst_volume_m3(lake.area_ha),
        })

    if has_center:
        matches.sort(key=lambda item: (item["distance_km"] if item["distance_km"] is not None else 99999.0))
    else:
        matches.sort(key=lambda item: float(item["area_ha"] or 0), reverse=True)

    return {
        "query": {
            "center": {"lat": lat, "lon": lon} if has_center else None,
            "radius_km": radius_km if has_center else None,
            "bbox": [min_lon, min_lat, max_lon, max_lat] if has_bbox else None,
            "risk_level": risk_level,
        },
        "count": len(matches),
        "results": matches,
    }


@router.get("/{lake_id}/flood-corridor")
@limiter.limit("60/minute")
def get_lake_flood_corridor(
    request: Request,
    lake_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Model a lake's spatial GLOF hazard footprint & downstream flood routing corridor
    as a multi-layer RFC 7946 GeoJSON FeatureCollection:
      1. Geodesic Warning & Danger buffer polygons (metric spheroidal rings)
      2. Nearest glacier feeder vector (LineString)
      3. Downstream outburst routing path through valley storage nodes (LineString + Point nodes)
      4. Empirical peak discharge Q_p (m3/s) and flood-wave travel time (minutes)
    """
    lake = db.query(LakeModel).filter(LakeModel.id == lake_id).first()
    if not lake:
        raise HTTPException(status_code=404, detail="Lake not found")
    if lake.latitude is None or lake.longitude is None:
        raise HTTPException(status_code=400, detail="Lake has no spatial coordinates")

    lat, lon = float(lake.latitude), float(lake.longitude)
    risk = lake.risk_level or calculate_risk(lake.area_ha or 0)
    danger_m, warning_m = _zone_radii_m(risk)
    basin = assign_basin(lat, lon)
    vol_m3 = float(estimate_outburst_volume_m3(lake.area_ha) or 0.0)

    # Empirical glacial dam breach peak discharge: Q_p = 0.0048 * V^0.89 (m3/s)
    peak_discharge_m3s = round(0.0048 * (vol_m3 ** 0.89), 1) if vol_m3 > 0 else 0.0

    # Nearest glacier reference vector
    nearest_g = min(
        GLACIER_REFS,
        key=lambda g: haversine(lat, lon, g[0], g[1]),
    )
    glacier_dist_km = round(haversine(lat, lon, nearest_g[0], nearest_g[1]), 2)

    # Downstream routing nodes from basin
    storage_nodes = list(basin.get("downstream_storage") or [])
    route_coords = [[round(lon, 5), round(lat, 5)]]
    cumulative_km = 0.0
    prev_lat, prev_lon = lat, lon
    node_features = []

    # Average alpine GLOF flood-wave velocity ~ 4.5 m/s (16.2 km/h)
    wave_speed_kmh = 16.2

    for idx, node in enumerate(storage_nodes, start=1):
        n_lat, n_lon = float(node["lat"]), float(node["lon"])
        seg_km = haversine(prev_lat, prev_lon, n_lat, n_lon)
        cumulative_km = round(cumulative_km + seg_km, 2)
        eta_min = round((cumulative_km / wave_speed_kmh) * 60.0, 1)
        route_coords.append([round(n_lon, 5), round(n_lat, 5)])
        prev_lat, prev_lon = n_lat, n_lon

        node_features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(n_lon, 5), round(n_lat, 5)],
            },
            "properties": {
                "layer": "storage_node",
                "sequence": idx,
                "name": node["name"],
                "node_type": node.get("type", "valley_plain"),
                "distance_from_lake_km": cumulative_km,
                "estimated_arrival_min": eta_min,
            },
        })

    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [geodesic_circle_ring(lat, lon, warning_m)],
            },
            "properties": {
                "layer": "warning_zone",
                "radius_m": warning_m,
                "risk_level": risk,
                "color": "#f5a524",
            },
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [geodesic_circle_ring(lat, lon, danger_m)],
            },
            "properties": {
                "layer": "danger_zone",
                "radius_m": danger_m,
                "risk_level": risk,
                "color": "#f0433a",
            },
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(nearest_g[1], 5), round(nearest_g[0], 5)],
                    [round(lon, 5), round(lat, 5)],
                ],
            },
            "properties": {
                "layer": "glacier_feeder",
                "distance_km": glacier_dist_km,
                "label": f"Nearest Glacier Tongue ({glacier_dist_km} km)",
                "color": "#38bdf8",
            },
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": route_coords,
            },
            "properties": {
                "layer": "downstream_corridor",
                "basin_id": basin["id"],
                "basin_name": basin["name"],
                "river": basin["river"],
                "total_route_km": cumulative_km,
                "peak_discharge_m3s": peak_discharge_m3s,
                "estimated_volume_m3": vol_m3,
                "at_risk_settlements": basin.get("at_risk_settlements", []),
                "color": "#ec4899",
            },
        },
        *node_features,
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(lon, 5), round(lat, 5)],
            },
            "properties": {
                "layer": "lake_origin",
                "id": lake.id,
                "name": lake.name,
                "area_ha": lake.area_ha,
                "risk_level": risk,
                "wkt": lake.geom or _wkt_point(lat, lon),
                "peak_discharge_m3s": peak_discharge_m3s,
            },
        },
    ]

    return {
        "type": "FeatureCollection",
        "lake": {
            "id": lake.id,
            "name": lake.name,
            "area_ha": lake.area_ha,
            "risk_level": risk,
            "latitude": lat,
            "longitude": lon,
            "wkt": lake.geom or _wkt_point(lat, lon),
            "basin_id": basin["id"],
            "basin_name": basin["name"],
            "river": basin["river"],
            "nearest_glacier_km": glacier_dist_km,
            "estimated_volume_m3": vol_m3,
            "estimated_volume_million_m3": round(vol_m3 / 1e6, 3) if vol_m3 else 0,
            "peak_discharge_m3s": peak_discharge_m3s,
            "total_corridor_km": cumulative_km,
            "at_risk_settlements": basin.get("at_risk_settlements", []),
        },
        "features": features,
    }


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
    final_name = suggest_name(lake.latitude, lake.longitude, db, lake.name)

    new_lake = LakeModel(
        name=final_name,
        area_ha=lake.area_ha,
        risk_level=risk,
        latitude=lake.latitude,
        longitude=lake.longitude,
        geom=_wkt_point(lake.latitude, lake.longitude),
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
        final_name = suggest_name(
            lake_data.latitude,
            lake_data.longitude,
            db,
            lake_data.name,
        )

        new_lake = LakeModel(
            name=final_name,
            area_ha=lake_data.area_ha,
            risk_level=risk,
            latitude=lake_data.latitude,
            longitude=lake_data.longitude,
            geom=_wkt_point(lake_data.latitude, lake_data.longitude),
        )
        db.add(new_lake)
        created.append(new_lake)

    db.commit()
    for lake in created:
        db.refresh(lake)

    return {
        "message": f"Successfully saved {len(created)} lakes (auto-named)",
        "count": len(created),
    }


@router.post("/match-names")
@limiter.limit("3/minute")
def match_lake_names(request: Request, db: Session = Depends(get_db)):
    lakes = db.query(LakeModel).all()
    known = db.query(KnownLake).all()
    matched = 0
    fallback = 0

    if not known:
        raise HTTPException(
            status_code=400,
            detail="No known lakes found. Call POST /lakes/seed-known first.",
        )

    for lake in lakes:
        if not lake.latitude or not lake.longitude:
            continue

        best_match = None
        best_distance = 999.0

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

        if is_generic_name(lake.name):
            new_name = f"Unnamed Lake ({lake.latitude:.2f}N, {lake.longitude:.2f}E)"
            if lake.name != new_name:
                lake.name = new_name
                fallback += 1

    db.commit()
    return {
        "message": f"Matched {matched} lakes with known names, renamed {fallback} generic lakes",
        "matched": matched,
        "fallback": fallback,
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
        if not lake.geom and lake.latitude is not None and lake.longitude is not None:
            lake.geom = _wkt_point(lake.latitude, lake.longitude)
    db.commit()
    return {"message": f"Risk recalculated for {updated} lakes"}
