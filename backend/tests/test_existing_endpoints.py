"""
Tests preserving all 35 existing backend operations, cold-start DB seeding,
SQLite :memory: StaticPool support, and shared elevation cache deduplication.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from tests._test_harness import (  # noqa: E402
    get_client,
    make_mock_httpx_get,
    reset_analytics_caches,
    restore_seed_db,
)
from app.db.session import SessionLocal  # noqa: E402
from app.models.lake import KnownLake, Lake as LakeModel  # noqa: E402
from app.models.user import Role, User  # noqa: E402
from app.services import elevation_service, flood_impact  # noqa: E402


class TestExistingEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = get_client()

    def setUp(self):
        reset_analytics_caches()

    def tearDown(self):
        restore_seed_db()

    def test_01_cold_start_seed_and_wkt_geometry(self):
        db = SessionLocal()
        try:
            lakes = db.query(LakeModel).all()
            known = db.query(KnownLake).all()
            self.assertEqual(len(lakes), 46)
            self.assertEqual(len(known), 46)
            for row in lakes:
                self.assertIsNotNone(row.geom)
                self.assertTrue(row.geom.startswith("POINT("))
            for row in known:
                self.assertIsNotNone(row.geom)
                self.assertTrue(row.geom.startswith("POINT("))
        finally:
            db.close()

    def test_02_sqlite_in_memory_static_pool_works_with_testclient(self):
        backend_dir = str(Path(__file__).resolve().parents[1])
        code = "\n".join([
            "import os, sys",
            "from concurrent.futures import ThreadPoolExecutor",
            "from unittest.mock import patch",
            "os.environ['DATABASE_URL'] = 'sqlite:///:memory:'",
            "os.environ['SEED_ON_START'] = '1'",
            "os.environ['WARM_WEATHER_ON_START'] = '0'",
            f"sys.path.insert(0, {backend_dir!r})",
            "try:",
            "    import ee",
            "    p = patch.object(ee, 'Initialize', side_effect=RuntimeError('disabled'))",
            "    p.start()",
            "except Exception:",
            "    p = None",
            "import app.main as m",
            "if p is not None: p.stop()",
            "from app.api.routers import lakes as lr, analytics as ar",
            "for mod in (m, lr, ar):",
            "    if hasattr(mod, 'limiter'): mod.limiter.enabled = False",
            "from fastapi.testclient import TestClient",
            "r = TestClient(m.app).get('/lakes/')",
            "assert r.status_code == 200 and len(r.json()) == 46, (r.status_code, len(r.json()))",
            "paths = ['/lakes/', '/analytics/providers', '/analytics/districts', '/analytics/basins/cascade'] * 6",
            "shared = TestClient(m.app)",
            "def _hit_shared(path):",
            "    resp = shared.get(path)",
            "    assert resp.status_code == 200, (path, resp.status_code, resp.text)",
            "    if path == '/lakes/': assert len(resp.json()) == 46",
            "def _hit_separate(path):",
            "    resp = TestClient(m.app).get(path)",
            "    assert resp.status_code == 200, (path, resp.status_code, resp.text)",
            "    if path == '/lakes/': assert len(resp.json()) == 46",
            "with ThreadPoolExecutor(max_workers=8) as ex:",
            "    list(ex.map(_hit_shared, paths))",
            "    list(ex.map(_hit_separate, paths))",
            "m.engine.dispose()",
        ])
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stderr={proc.stderr}\nstdout={proc.stdout}")

    def test_03_core_root_health_api_weather(self):
        r_root = self.client.get("/")
        self.assertEqual(r_root.status_code, 200)

        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.json(), {"status": "ok"})

        r_api = self.client.get("/api")
        self.assertEqual(r_api.status_code, 200)
        self.assertIn("message", r_api.json())

        mock_weather = {
            "current": {
                "temperature_2m": 6.4,
                "relative_humidity_2m": 52,
                "precipitation": 0.0,
                "weather_code": 2,
                "wind_speed_10m": 11.2,
            },
            "daily": {"temperature_2m_max": [9.1, 10.2, 8.8]},
        }
        with patch.object(flood_impact, "_http_get_json", return_value=mock_weather):
            r_w = self.client.get("/weather?lat=35.92&lon=74.31")
            self.assertEqual(r_w.status_code, 200)
            body = r_w.json()
            self.assertEqual(body["temperature_c"], 6.4)
            self.assertEqual(body["source"], "Open-Meteo")

            r_ew_w = self.client.get("/early-warning/weather?lat=35.92&lon=74.31")
            self.assertEqual(r_ew_w.status_code, 200)
            self.assertTrue(r_ew_w.json()["cached"])

    def test_04_lakes_read_and_gis_endpoints(self):
        r_all = self.client.get("/lakes/")
        self.assertEqual(r_all.status_code, 200)
        lakes = r_all.json()
        self.assertEqual(len(lakes), 46)
        first_id = lakes[0]["id"]

        r_one = self.client.get(f"/lakes/{first_id}")
        self.assertEqual(r_one.status_code, 200)
        self.assertEqual(r_one.json()["id"], first_id)
        self.assertEqual(self.client.get("/lakes/99999").status_code, 404)

        r_geo = self.client.get("/lakes/geojson")
        self.assertEqual(r_geo.status_code, 200)
        geo = r_geo.json()
        self.assertEqual(geo["type"], "FeatureCollection")
        self.assertEqual(geo["total_features"], 46)

        r_geo_filt = self.client.get("/lakes/geojson?risk_level=High&basin_id=hunza")
        self.assertEqual(r_geo_filt.status_code, 200)
        self.assertGreaterEqual(r_geo_filt.json()["total_features"], 1)

        r_search = self.client.get("/lakes/spatial-search?lat=36.318&lon=74.865&radius_km=35")
        self.assertEqual(r_search.status_code, 200)
        self.assertGreaterEqual(r_search.json()["count"], 1)

        r_corr = self.client.get(f"/lakes/{first_id}/flood-corridor")
        self.assertEqual(r_corr.status_code, 200)
        corr = r_corr.json()
        self.assertEqual(corr["type"], "FeatureCollection")
        self.assertGreater(corr["lake"]["peak_discharge_m3s"], 0)
        self.assertEqual(self.client.get("/lakes/99999/flood-corridor").status_code, 404)

    def test_05_lakes_crud_and_batch_operations(self):
        # Create
        r_create = self.client.post(
            "/lakes/",
            json={"name": "Test Glacial Lake", "area_ha": 18.5, "latitude": 36.11, "longitude": 74.41},
        )
        self.assertEqual(r_create.status_code, 200)
        created = r_create.json()
        new_id = created["id"]
        self.assertEqual(created["risk_level"], "Medium")

        # Rename
        r_ren = self.client.put(f"/lakes/{new_id}/rename?new_name=Renamed%20Test%20Lake")
        self.assertEqual(r_ren.status_code, 200)
        self.assertIn("Renamed Test Lake", r_ren.json()["message"])

        # Delete
        r_del = self.client.delete(f"/lakes/{new_id}")
        self.assertEqual(r_del.status_code, 200)
        self.assertEqual(self.client.get(f"/lakes/{new_id}").status_code, 404)

        # Bulk
        r_bulk = self.client.post(
            "/lakes/bulk",
            json=[
                {"name": "Unnamed Lake", "area_ha": 12.0, "latitude": 36.318, "longitude": 74.865},
                {"name": "Custom Alpine Pond", "area_ha": 6.0, "latitude": 35.51, "longitude": 74.91},
            ],
        )
        self.assertEqual(r_bulk.status_code, 200)
        self.assertEqual(r_bulk.json()["count"], 2)

        # Seed-known (already seeded -> skipped message)
        r_seed = self.client.post("/lakes/seed-known")
        self.assertEqual(r_seed.status_code, 200)
        self.assertEqual(r_seed.json()["count"], 46)

        # Match-names & recalculate-risk
        r_match = self.client.post("/lakes/match-names")
        self.assertEqual(r_match.status_code, 200)
        self.assertIn("matched", r_match.json())

        r_recalc = self.client.post("/lakes/recalculate-risk")
        self.assertEqual(r_recalc.status_code, 200)
        self.assertIn("Risk recalculated", r_recalc.json()["message"])

    def test_06_risk_and_early_warning_endpoints(self):
        r_risk = self.client.get("/risk/1")
        self.assertEqual(r_risk.status_code, 200)
        self.assertIn("spatial_metrics", r_risk.json())
        self.assertEqual(self.client.get("/risk/99999").status_code, 404)

        from app.api.routers import early_warning as ew_router
        mock_weather = {
            "current": {
                "temperature_2m": 7.0,
                "relative_humidity_2m": 48,
                "precipitation": 0.0,
                "weather_code": 1,
                "wind_speed_10m": 9.5,
            },
            "daily": {"temperature_2m_max": [10.0, 11.0, 9.5]},
        }
        with patch.object(ew_router, "gee_status", return_value={"ready": False, "mode": None}), \
             patch.object(flood_impact, "_http_get_json", return_value=mock_weather):
            r_ew_status = self.client.get("/early-warning/status")
            self.assertEqual(r_ew_status.status_code, 200)
            self.assertIn("indicators", r_ew_status.json())

            with patch("httpx.get", side_effect=make_mock_httpx_get()):
                r_score = self.client.get("/early-warning/score/1?use_gee=false")
                self.assertEqual(r_score.status_code, 200)
                self.assertIn("early_warning", r_score.json())

                reset_analytics_caches()
                elev_calls = {"n": 0}
                base_mock = make_mock_httpx_get()

                def _counting_elev_mock(url, params=None, headers=None, timeout=None, **kwargs):
                    if "api.open-meteo.com/v1/elevation" in url:
                        elev_calls["n"] += 1
                    return base_mock(url, params=params, headers=headers, timeout=timeout, **kwargs)

                with patch("httpx.get", side_effect=_counting_elev_mock):
                    r_mon = self.client.get("/early-warning/monitor?use_gee=false&limit=46")
                    self.assertEqual(r_mon.status_code, 200)
                    self.assertEqual(r_mon.json()["total"], 46)
                    self.assertLessEqual(elev_calls["n"], 1)

                r_job = self.client.post("/early-warning/monitor/jobs?use_gee=false&limit=3")
                self.assertEqual(r_job.status_code, 200)
                job_id = r_job.json()["job_id"]
                for _ in range(30):
                    r_poll = self.client.get(f"/early-warning/monitor/jobs/{job_id}")
                    self.assertEqual(r_poll.status_code, 200)
                    if r_poll.json()["status"] in ("done", "error"):
                        break
                    time.sleep(0.05)
                self.assertEqual(r_poll.json()["status"], "done")

        r_basins = self.client.get("/early-warning/basins")
        self.assertEqual(r_basins.status_code, 200)
        self.assertGreaterEqual(r_basins.json()["total_basins"], 9)

        r_basin = self.client.get("/early-warning/basins/hunza")
        self.assertEqual(r_basin.status_code, 200)
        self.assertEqual(r_basin.json()["id"], "hunza")
        self.assertEqual(self.client.get("/early-warning/basins/nonexistent").status_code, 404)

    def test_07_elevation_cache_dedup_and_stale_fallback(self):
        from app.api.routers import early_warning as ew_router

        calls = {"n": 0}

        def _counting_get(url, params=None, headers=None, timeout=None, **kwargs):
            calls["n"] += 1
            req = httpx.Request("GET", url, params=params)
            return httpx.Response(200, json={"elevation": [2366.0]}, request=req)

        with patch("httpx.get", side_effect=_counting_get):
            e1 = ew_router.fetch_elevation_open_meteo(36.318, 74.865)
            e2 = ew_router.fetch_elevation_open_meteo(36.318, 74.865)
            self.assertEqual(e1, 2366.0)
            self.assertEqual(e2, 2366.0)
            self.assertEqual(calls["n"], 1)

        # Force fresh TTL expiry (< 7d stale) and simulate ConnectTimeout -> stale fallback
        key = elevation_service._elev_key(36.318, 74.865)
        with elevation_service._ELEV_LOCK:
            elevation_service._ELEV_CACHE[key] = (time.time() - (25 * 3600), 2366.0)

        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            e_stale = ew_router.fetch_elevation_open_meteo(36.318, 74.865)
            self.assertEqual(e_stale, 2366.0)

            # Cold cache miss on network failure returns None
            e_cold = ew_router.fetch_elevation_open_meteo(35.123, 75.456)
            self.assertIsNone(e_cold)

    def test_08_gee_routes_with_mocked_upstream(self):
        from app.api.routers import gee as gee_router

        with patch.object(
            gee_router,
            "cascade_status",
            return_value={"gee": {"ready": False}, "cascade": [{"step": 4, "name": "DB"}]},
        ):
            r = self.client.get("/gee/status")
            self.assertEqual(r.status_code, 200)
            self.assertIn("cascade", r.json())

        with patch.object(
            gee_router,
            "detect_lakes_cascade",
            return_value={"source": "db_inventory", "total_detected": 46, "lakes": []},
        ):
            r = self.client.get("/gee/detect-lakes")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["total_detected"], 46)

        with patch.object(gee_router, "estimate_population", return_value={"population": 1200}):
            r = self.client.get("/gee/population?lat=36.318&lon=74.865")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["population"], 1200)

        with patch.object(
            gee_router,
            "estimate_lake_exposure",
            return_value={"danger_population": 350, "warning_population": 900},
        ):
            r = self.client.get("/gee/exposure?lat=36.318&lon=74.865&risk_level=High")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["danger_population"], 350)

        with patch.object(
            gee_router,
            "get_historical_areas",
            return_value={"years": [2020, 2021], "areas_ha": [275.0, 280.0]},
        ):
            r = self.client.get("/gee/historical?lat=36.318&lon=74.865")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(len(r.json()["years"]), 2)

        with patch.object(
            gee_router,
            "get_lake_thumbnail",
            return_value={"url": "https://example.com/thumb.png", "mode": "ndwi"},
        ):
            r = self.client.get("/gee/thumbnail?lat=36.318&lon=74.865&mode=ndwi")
            self.assertEqual(r.status_code, 200)
            self.assertIn("url", r.json())

        with patch.object(
            gee_router,
            "get_lake_thumbnails_pack",
            return_value={"ndwi": {"url": "https://example.com/ndwi.png"}},
        ):
            r = self.client.get("/gee/thumbnails?lat=36.318&lon=74.865")
            self.assertEqual(r.status_code, 200)
            self.assertIn("ndwi", r.json())

        with patch.object(
            gee_router,
            "_fetch_thumb_png",
            return_value=(b"\x89PNG\r\n\x1a\n", "image/png", "Sentinel-2"),
        ):
            r = self.client.get("/gee/thumbnail-image?lat=36.318&lon=74.865&mode=ndwi")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["content-type"], "image/png")

    def test_09_auth_login(self):
        from app.core.auth import get_password_hash

        db = SessionLocal()
        try:
            existing = db.query(User).filter(User.username == "tester_m1").first()
            if existing is None:
                try:
                    hashed = get_password_hash("glof-secret-123")
                except Exception:
                    # Fallback if bcrypt backend raises in isolated environment
                    import bcrypt
                    hashed = bcrypt.hashpw(b"glof-secret-123", bcrypt.gensalt()).decode("utf-8")
                db.add(User(username="tester_m1", hashed_password=hashed, role=Role.ANALYST))
                db.commit()
        finally:
            db.close()

        r_ok = self.client.post(
            "/auth/login",
            data={"username": "tester_m1", "password": "glof-secret-123"},
        )
        self.assertEqual(r_ok.status_code, 200)
        self.assertIn("access_token", r_ok.json())

        r_bad = self.client.post(
            "/auth/login",
            data={"username": "tester_m1", "password": "wrong-password"},
        )
        self.assertEqual(r_bad.status_code, 401)


if __name__ == "__main__":
    unittest.main()
