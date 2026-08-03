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
from app.db.base import Base
from app.db.session import engine

# IMPORTANT: import models so tables are registered
from app.models.lake import Lake, KnownLake  # noqa: F401

# ---------- Rate Limiter Setup ----------
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="GLOF Portal API",
    version="1.0",
    description="Glacial Lake Outburst Flood Monitoring System"
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
# Star cannot be used with credentials
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

from app.models.audit import AuditLog
from app.db.session import SessionLocal

# Middleware for audit logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Only log mutating requests (POST, PUT, DELETE)
    if request.method in ["POST", "PUT", "DELETE"]:
        db = SessionLocal()
        try:
            audit_log = AuditLog(
                user_id="unknown",  # Need to extract from token later
                action=f"{request.method} {request.url.path}",
                ip_address=request.client.host
            )
            db.add(audit_log)
            db.commit()
        finally:
            db.close()

    response = await call_next(request)
    return response

import socketio

# Setup for real-time alerting
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
sio_app = socketio.ASGIApp(sio, app)

# Create tables (now includes known_lakes)
Base.metadata.create_all(bind=engine)

# Include routers
app.include_router(lakes_router)
app.include_router(risk_router)
app.include_router(gee_router)
app.include_router(auth_router)
app.include_router(early_warning_router)


def _seed_if_empty():
    """Populate lakes on free hosting restarts when DB is empty."""
    if os.getenv("SEED_ON_START", "0") not in ("1", "true", "True", "yes"):
        return
    from app.api.routers.lakes import KNOWN_LAKES_SEED
    from app.utils.risk import calculate_risk
    from app.models.lake import Lake as LakeModel, KnownLake as KnownLakeModel

    db = SessionLocal()
    try:
        if db.query(LakeModel).count() == 0:
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
        if db.query(KnownLakeModel).count() == 0:
            for item in KNOWN_LAKES_SEED:
                db.add(KnownLakeModel(
                    name=item["name"],
                    latitude=item["latitude"],
                    longitude=item["longitude"],
                    area_ha=item.get("area_ha"),
                    district=item.get("district"),
                    source="manual",
                ))
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Seed skipped: {e}")
    finally:
        db.close()


_seed_if_empty()

# Optional SPA bundle (built frontend copied to backend/static for single-service host)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/health")
@limiter.limit("60/minute")
def health(request: Request):
    return {"status": "ok"}


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
        """Serve built React app for client-side routes (API routes take priority)."""
        if full_path.startswith(("lakes", "gee", "risk", "auth", "early-warning", "docs", "openapi", "redoc", "health", "api", "assets")):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
