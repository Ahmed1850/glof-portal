"""
Shared isolated test harness for GLOF Portal backend tests.

Ensures:
  1. Works when executed from either repo root or backend/.
  2. Uses an isolated temporary SQLite file with SEED_ON_START=1 (never mutates backend/glof.db).
  3. Disables startup network calls (WARM_WEATHER_ON_START=0, patched ee.Initialize).
  4. Disables SlowAPI rate limiters so TestClient suites never hit false 429s.
  5. Disposes SQLAlchemy engine on exit before deleting temporary directory (Windows lock safety).
"""

from __future__ import annotations

import atexit
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_TMP_DIR = tempfile.TemporaryDirectory(prefix="glof_test_")
_TEST_DB_PATH = Path(_TMP_DIR.name) / "test_glof.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"
os.environ["SEED_ON_START"] = "1"
os.environ["WARM_WEATHER_ON_START"] = "0"


def _stub_ee_init(*args, **kwargs):
    raise RuntimeError("GEE disabled in offline test harness")


# Patch ee.Initialize during initial import of app.main / gee_detection so startup is instant
try:
    import ee as _ee_mod  # type: ignore
    _ee_patch = patch.object(_ee_mod, "Initialize", side_effect=_stub_ee_init)
    _ee_patch.start()
except Exception:
    _ee_patch = None

import app.main as app_main  # noqa: E402
from app.api.routers import (  # noqa: E402
    analytics as analytics_router,
    early_warning as ew_router,
    gee as gee_router,
    lakes as lakes_router,
    risk as risk_router,
)
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.lake import KnownLake, Lake as LakeModel  # noqa: E402
from app.utils.risk import calculate_risk  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

if _ee_patch is not None:
    _ee_patch.stop()

# Disable all SlowAPI rate limiters during automated testing
for _mod in (app_main, lakes_router, risk_router, gee_router, ew_router, analytics_router):
    if hasattr(_mod, "limiter"):
        _mod.limiter.enabled = False


def _cleanup_harness() -> None:
    try:
        engine.dispose()
    except Exception:
        pass
    try:
        _TMP_DIR.cleanup()
    except Exception:
        pass


atexit.register(_cleanup_harness)


def get_client() -> TestClient:
    return TestClient(app_main.app)


def restore_seed_db() -> None:
    """Reset lakes and known_lakes tables back to the exact 46-lake baseline."""
    db = SessionLocal()
    try:
        db.query(LakeModel).delete()
        db.query(KnownLake).delete()
        db.commit()
        for item in lakes_router.KNOWN_LAKES_SEED:
            risk = calculate_risk(item.get("area_ha") or 0)
            wkt = f"POINT({item['longitude']} {item['latitude']})"
            db.add(LakeModel(
                name=item["name"],
                area_ha=item.get("area_ha"),
                risk_level=risk,
                latitude=item["latitude"],
                longitude=item["longitude"],
                geom=wkt,
            ))
            db.add(KnownLake(
                name=item["name"],
                latitude=item["latitude"],
                longitude=item["longitude"],
                area_ha=item.get("area_ha"),
                district=item.get("district"),
                source="manual",
                geom=wkt,
            ))
        db.commit()
    finally:
        db.close()


def reset_analytics_caches() -> None:
    """Clear in-memory caches across elevation, trigger, and weather services."""
    from app.services import elevation_service, flood_impact, trigger_engine

    with elevation_service._ELEV_LOCK:
        elevation_service._ELEV_CACHE.clear()
    with trigger_engine._TRIGGER_LOCK:
        trigger_engine._FORECAST_CACHE.clear()
        trigger_engine._GLOFAS_CACHE.clear()
        trigger_engine._USGS_CACHE.clear()
    with flood_impact._TEMP_LOCK:
        flood_impact._TEMP_CACHE.clear()


def build_mock_forecast_json(elev: float = 2366.0) -> dict:
    now = datetime.now(timezone.utc)
    dates = [(now + timedelta(days=i - 7)).strftime("%Y-%m-%d") for i in range(14)]
    return {
        "elevation": elev,
        "daily": {
            "time": dates,
            "temperature_2m_max": [14.5 + (i % 4) for i in range(14)],
            "temperature_2m_min": [3.5 + (i % 3) for i in range(14)],
            "precipitation_sum": [2.2 if i % 3 == 0 else 0.4 for i in range(14)],
        },
        "hourly": {
            "time": [f"{d}T12:00" for d in dates],
            "freezing_level_height": [4150.0 for _ in dates],
            "precipitation": [1.2 if i % 5 == 0 else 0.1 for i in range(14)],
        },
    }


def build_mock_glofas_json() -> dict:
    now = datetime.now(timezone.utc)
    dates = [(now + timedelta(days=i - 7)).strftime("%Y-%m-%d") for i in range(14)]
    return {
        "daily": {
            "time": dates,
            "river_discharge": [28.4 if i >= 10 else 16.5 for i in range(14)],
            "river_discharge_mean": [15.0 for _ in range(14)],
            "river_discharge_max": [32.0 for _ in range(14)],
        }
    }


def build_mock_usgs_json() -> dict:
    now_ms = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp() * 1000)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "us7000mock1",
                "properties": {
                    "mag": 4.4,
                    "place": "42 km N of Gilgit, Pakistan",
                    "time": now_ms,
                },
                "geometry": {"type": "Point", "coordinates": [74.55, 36.25, 14.0]},
            },
            {
                "type": "Feature",
                "id": "us7000mock2",
                "properties": {
                    "mag": 3.8,
                    "place": "18 km SE of Skardu, Pakistan",
                    "time": now_ms - 86400 * 1000,
                },
                "geometry": {"type": "Point", "coordinates": [75.70, 35.20, 12.0]},
            },
        ],
    }


def make_mock_httpx_get(elevations: list[float] | None = None):
    """Return a side_effect function for unittest.mock.patch('httpx.get', ...)."""
    elev_vals = elevations or [2366.0, 2313.0, 2400.0, 1520.0]

    def _mock_get(url: str, params=None, headers=None, timeout=None, **kwargs):
        req = httpx.Request("GET", url, params=params)
        if "api.open-meteo.com/v1/elevation" in url:
            lat_str = str((params or {}).get("latitude", ""))
            n_pts = len(lat_str.split(",")) if "," in lat_str else 1
            payload_elev = [elev_vals[i % len(elev_vals)] for i in range(n_pts)]
            return httpx.Response(200, json={"elevation": payload_elev}, request=req)
        if "api.open-meteo.com/v1/forecast" in url:
            return httpx.Response(200, json=build_mock_forecast_json(), request=req)
        if "flood-api.open-meteo.com/v1/flood" in url:
            return httpx.Response(200, json=build_mock_glofas_json(), request=req)
        if "earthquake.usgs.gov/fdsnws/event/1/query" in url:
            return httpx.Response(200, json=build_mock_usgs_json(), request=req)
        return httpx.Response(404, json={"error": "unmocked url"}, request=req)

    return _mock_get
