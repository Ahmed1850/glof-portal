import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.api.routers.lakes import router as lakes_router
from app.api.routers.risk import router as risk_router
from app.api.routers.gee import router as gee_router
from app.api.routers.auth import router as auth_router
from app.api.routers.early_warning import router as early_warning_router
from app.api.routers.reports import router as reports_router
from app.db.base import Base
from app.db.session import engine, SessionLocal

# IMPORTANT: import models so tables are registered
from app.models.lake import Lake, KnownLake  # noqa: F401
from app.models.audit import AuditLog

# ---------- Rate Limiter Setup ----------
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="GLOF Portal API",
    version="1.1",
    description="Glacial Lake Outburst Flood Monitoring & Geospatial Intelligence System"
)

# Attach limiter to the app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------- CORS ----------
_default_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]
_extra = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
allow_origins = list(dict.fromkeys(_default_origins + _extra)) or _default_origins
_allow_credentials = True
if "*" in allow_origins:
    allow_origins = ["*"]
    _allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_origin_regex=None if allow_origins == ["*"] else r"https://.*\.(vercel\.app|netlify\.app|onrender\.com|pages\.dev)",
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Middleware for audit logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    if request.method in ["POST", "PUT", "DELETE"]:
        db = SessionLocal()
        try:
            client_host = request.client.host if request.client else "unknown"
            audit_log = AuditLog(
                user_id="unknown",
                action=f"{request.method} {request.url.path}",
                ip_address=client_host,
            )
            db.add(audit_log)
            db.commit()
        finally:
            db.close()

    response = await call_next(request)
    return response


# Create tables (includes known_lakes)
Base.metadata.create_all(bind=engine)

# Include routers
app.include_router(lakes_router)
app.include_router(risk_router)
app.include_router(gee_router)
app.include_router(auth_router)
app.include_router(early_warning_router)
app.include_router(reports_router)


def _seed_and_backfill_geometries():
    """Populate lakes when DB is empty and backfill missing WKT POINT(lon lat) geometries."""
    from app.api.routers.lakes import KNOWN_LAKES_SEED
    from app.utils.risk import calculate_risk
    from app.models.lake import Lake as LakeModel, KnownLake as KnownLakeModel

    db = SessionLocal()
    try:
        should_seed = os.getenv("SEED_ON_START", "1") in ("1", "true", "True", "yes")
        if should_seed and db.query(LakeModel).count() == 0:
            for item in KNOWN_LAKES_SEED:
                risk = calculate_risk(item.get("area_ha") or 0)
                db.add(LakeModel(
                    name=item["name"],
                    area_ha=item.get("area_ha"),
                    risk_level=risk,
                    latitude=item["latitude"],
                    longitude=item["longitude"],
                    geom=f"POINT({item['longitude']} {item['latitude']})",
                ))
            db.commit()
        if should_seed and db.query(KnownLakeModel).count() == 0:
            for item in KNOWN_LAKES_SEED:
                db.add(KnownLakeModel(
                    name=item["name"],
                    latitude=item["latitude"],
                    longitude=item["longitude"],
                    area_ha=item.get("area_ha"),
                    district=item.get("district"),
                    source="manual",
                    geom=f"POINT({item['longitude']} {item['latitude']})",
                ))
            db.commit()

        # Backfill missing WKT geom strings on existing rows
        updated = 0
        for row in db.query(LakeModel).filter(LakeModel.geom.is_(None)).all():
            if row.latitude is not None and row.longitude is not None:
                row.geom = f"POINT({row.longitude} {row.latitude})"
                updated += 1
        for row in db.query(KnownLakeModel).filter(KnownLakeModel.geom.is_(None)).all():
            if row.latitude is not None and row.longitude is not None:
                row.geom = f"POINT({row.longitude} {row.latitude})"
                updated += 1
        if updated:
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Seed/backfill skipped: {e}")
    finally:
        db.close()


_seed_and_backfill_geometries()


def _warm_weather_cache():
    """Pre-fetch GB basin weather so first dashboard paint is not null on cold start."""
    try:
        from app.services.flood_impact import fetch_current_weather
        w = fetch_current_weather(35.92, 74.31)
        print(f"Weather cache warm: {w.get('temperature_c')}°C via {w.get('source')}")
    except Exception as e:
        print(f"Weather warm skipped: {e}")


_warm_weather_cache()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/health")
@limiter.limit("60/minute")
def health(request: Request):
    return {"status": "ok"}


@app.get("/weather")
@limiter.limit("120/minute")
def weather_root(
    request: Request,
    lat: float = 35.92,
    lon: float = 74.31,
):
    from app.services.flood_impact import fetch_current_weather
    w = fetch_current_weather(lat, lon)
    return {
        "latitude": lat,
        "longitude": lon,
        "temperature_c": w.get("temperature_c"),
        "temperature_2m": w.get("temperature_2m", w.get("temperature_c")),
        "forecast_max_c": w.get("forecast_max_c"),
        "relative_humidity_2m": w.get("relative_humidity_2m"),
        "precipitation": w.get("precipitation"),
        "wind_speed_10m": w.get("wind_speed_10m"),
        "weather_code": w.get("weather_code"),
        "source": w.get("source"),
        "cached": w.get("cached", False),
    }


@app.get("/api")
@limiter.limit("30/minute")
def api_root(request: Request):
    return {"message": "GLOF Portal Backend is running!"}


@app.get("/")
def root():
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"message": "GLOF Portal Backend is running!", "docs": "/docs"}


if STATIC_DIR.is_dir():
    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith(("lakes", "gee", "risk", "reports", "auth", "early-warning", "docs", "openapi", "redoc", "health", "api", "assets", "weather")):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
