"""Opaque-box E2E API test suite (Features 1-10, Tiers 1-4) for GLOF Portal."""

from __future__ import annotations

import csv
import io
import os
import re
import sys
import time
import unittest
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

import httpx

TESTS_E2E_DIR = Path(__file__).resolve().parent
if str(TESTS_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_E2E_DIR))

from lib.server_harness import (  # noqa: E402
    EphemeralBackend,
    find_free_port,
    seed_user_in_sqlite,
    verify_backend_static_serving,
)

GLOF_E2E_MODE = os.getenv("GLOF_E2E_MODE", "full").strip().lower()
if GLOF_E2E_MODE not in ("full", "baseline"):
    raise ValueError(f"Invalid GLOF_E2E_MODE={GLOF_E2E_MODE!r}; expected 'full' or 'baseline'")

IS_BASELINE = GLOF_E2E_MODE == "baseline"

_RAW_TIER = os.getenv("GLOF_E2E_TIER", "all").strip().lower()
ENABLED_TIERS: set[int] = (
    {1, 2, 3, 4}
    if _RAW_TIER in ("", "all")
    else {int(tok.strip()) for tok in _RAW_TIER.split(",") if tok.strip()}
)


def tier_enabled(tier: int) -> bool:
    return tier in ENABLED_TIERS


class RateLimitPacer:
    """Sliding-window pacer respecting SlowAPI per-route limits."""

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, bucket: str, limit: int, window_sec: float = 61.0) -> None:
        q = self._windows[bucket]
        now = time.monotonic()
        while q and (now - q[0]) >= window_sec:
            q.popleft()
        if len(q) >= limit:
            sleep_for = window_sec - (now - q[0]) + 0.25
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
            while q and (now - q[0]) >= window_sec:
                q.popleft()
        q.append(time.monotonic())


class ApiE2EContext:
    """Shared connection, optional owned EphemeralBackend, and pacer across test classes."""

    backend: Optional[EphemeralBackend] = None
    base_url: str = ""
    db_path: Optional[Path] = None
    client: Optional[httpx.Client] = None
    pacer: RateLimitPacer = RateLimitPacer()
    _refs: int = 0

    @classmethod
    def acquire(cls) -> None:
        cls._refs += 1
        if cls.client is not None:
            return
        env_url = os.getenv("GLOF_E2E_API_URL", "").strip()
        env_db = os.getenv("GLOF_E2E_DB_PATH", "").strip()
        if env_url:
            cls.base_url = env_url.rstrip("/")
            cls.db_path = Path(env_db) if env_db else None
        else:
            cls.backend = EphemeralBackend().start()
            cls.base_url = cls.backend.base_url
            cls.db_path = cls.backend.db_path
        cls.client = httpx.Client(base_url=cls.base_url, timeout=30.0)

    @classmethod
    def release(cls) -> None:
        cls._refs = max(0, cls._refs - 1)
        if cls._refs > 0:
            return
        if cls.client is not None:
            cls.client.close()
            cls.client = None
        if cls.backend is not None:
            cls.backend.stop()
            cls.backend = None


def setUpModule() -> None:
    ApiE2EContext.acquire()


def tearDownModule() -> None:
    while ApiE2EContext._refs > 0:
        ApiE2EContext.release()


class BaseApiTestCase(unittest.TestCase):
    client: httpx.Client
    pacer: RateLimitPacer

    @classmethod
    def setUpClass(cls) -> None:
        ApiE2EContext.acquire()
        assert ApiE2EContext.client is not None
        cls.client = ApiE2EContext.client
        cls.pacer = ApiE2EContext.pacer

    @classmethod
    def tearDownClass(cls) -> None:
        ApiE2EContext.release()

    def delete_probe_lake(self, lake_id: int) -> httpx.Response:
        self.pacer.acquire("delete_lake", 5)
        return self.client.delete(f"/lakes/{lake_id}")

    def create_probe_lake(self, payload: dict[str, Any]) -> httpx.Response:
        self.pacer.acquire("create_lake", 10)
        return self.client.post("/lakes/", json=payload)


# ==============================================================================
# TIER 1 — CORE FUNCTIONAL CONTRACT TESTS (BASELINE FEATURES 1–4)
# ==============================================================================
@unittest.skipUnless(tier_enabled(1), "Tier 1 disabled via GLOF_E2E_TIER")
class TestApiTier1Baseline(BaseApiTestCase):
    """Tier 1 happy-path API tests for Baseline Features 1–4."""

    # --- Feature 1: Lakes CRUD & Seed/Match ---
    def test_t1_f1_01_health_api_and_root(self) -> None:
        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.json(), {"status": "ok"})

        self.pacer.acquire("api_root", 30)
        r_api = self.client.get("/api")
        self.assertEqual(r_api.status_code, 200)
        self.assertIn("GLOF Portal Backend is running!", r_api.json().get("message", ""))

        r_root = self.client.get("/")
        self.assertEqual(r_root.status_code, 200)

    def test_t1_f1_02_list_lakes_and_get_attabad(self) -> None:
        r_list = self.client.get("/lakes/")
        self.assertEqual(r_list.status_code, 200)
        lakes = r_list.json()
        self.assertIsInstance(lakes, list)
        self.assertGreaterEqual(len(lakes), 46)

        wkt_re = re.compile(r"^POINT\(")
        for item in lakes[:10]:
            self.assertIsInstance(item["id"], int)
            self.assertIsInstance(item["name"], str)
            self.assertIsInstance(item["area_ha"], (int, float))
            self.assertIn(item["risk_level"], {"High", "Medium", "Low"})
            self.assertRegex(item.get("geom") or "", wkt_re)

        r_attabad = self.client.get("/lakes/1")
        self.assertEqual(r_attabad.status_code, 200)
        att = r_attabad.json()
        self.assertEqual(att["id"], 1)
        self.assertEqual(att["name"], "Attabad Lake")
        self.assertEqual(att["area_ha"], 280.0)
        self.assertEqual(att["risk_level"], "High")
        self.assertAlmostEqual(att["latitude"], 36.318, places=3)
        self.assertAlmostEqual(att["longitude"], 74.865, places=3)

    def test_t1_f1_03_crud_create_rename_delete_lifecycle(self) -> None:
        r_create = self.create_probe_lake(
            {
                "name": "E2E_PROBE_Alpha",
                "area_ha": 18.0,
                "risk_level": "Low",  # Server recomputes Medium for 18 ha
                "latitude": 34.50,
                "longitude": 72.10,
            }
        )
        self.assertEqual(r_create.status_code, 200, r_create.text)
        created = r_create.json()
        lake_id = created["id"]
        try:
            self.assertEqual(created["name"], "E2E_PROBE_Alpha")
            self.assertEqual(created["risk_level"], "Medium")
            self.assertEqual(created["geom"], "POINT(72.1 34.5)")

            r_rename = self.client.put(
                f"/lakes/{lake_id}/rename",
                params={"new_name": "E2E_PROBE_Renamed"},
            )
            self.assertEqual(r_rename.status_code, 200, r_rename.text)
            self.assertIn("E2E_PROBE_Renamed", r_rename.json()["message"])

            r_get = self.client.get(f"/lakes/{lake_id}")
            self.assertEqual(r_get.status_code, 200)
            self.assertEqual(r_get.json()["name"], "E2E_PROBE_Renamed")
        finally:
            r_del = self.delete_probe_lake(lake_id)
            self.assertEqual(r_del.status_code, 200, r_del.text)
            self.assertIn("deleted successfully", r_del.json()["message"])

        r_after = self.client.get(f"/lakes/{lake_id}")
        self.assertEqual(r_after.status_code, 404)

    def test_t1_f1_04_auto_name_within_2_5km(self) -> None:
        r_create = self.create_probe_lake(
            {
                "name": "Unknown 99",
                "area_ha": 10.0,
                "latitude": 36.319,
                "longitude": 74.866,
            }
        )
        self.assertEqual(r_create.status_code, 200, r_create.text)
        created = r_create.json()
        try:
            self.assertEqual(created["name"], "Attabad Lake")
            self.assertEqual(created["risk_level"], "Low")
        finally:
            self.delete_probe_lake(created["id"])

    def test_t1_f1_05_seed_known_match_recalc_and_bulk(self) -> None:
        self.pacer.acquire("seed_known", 2)
        r_seed = self.client.post("/lakes/seed-known")
        self.assertEqual(r_seed.status_code, 200)
        self.assertGreaterEqual(r_seed.json().get("count", 0), 46)

        self.pacer.acquire("match_names", 3)
        r_match = self.client.post("/lakes/match-names")
        self.assertEqual(r_match.status_code, 200)
        self.assertIn("matched", r_match.json())
        self.assertIn("fallback", r_match.json())

        self.pacer.acquire("recalc_risk", 3)
        r_recalc = self.client.post("/lakes/recalculate-risk")
        self.assertEqual(r_recalc.status_code, 200)
        self.assertIn("Risk recalculated", r_recalc.json().get("message", ""))

        self.pacer.acquire("bulk_lakes", 5)
        r_bulk = self.client.post(
            "/lakes/bulk",
            json=[
                {
                    "name": "E2E_PROBE_BulkOne",
                    "area_ha": 12.5,
                    "latitude": 34.40,
                    "longitude": 72.20,
                }
            ],
        )
        self.assertEqual(r_bulk.status_code, 200)
        self.assertEqual(r_bulk.json()["count"], 1)

        # Clean up synthetic bulk lake so seeded count returns to 46
        all_lakes = self.client.get("/lakes/").json()
        for lk in all_lakes:
            if lk["name"] == "E2E_PROBE_BulkOne":
                self.delete_probe_lake(lk["id"])

    # --- Feature 2: GIS Endpoints ---
    def test_t1_f2_01_geojson_all(self) -> None:
        resp = self.client.get("/lakes/geojson")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(data["crs"]["properties"]["name"], "urn:ogc:def:crs:OGC:1.3:CRS84")
        self.assertEqual(data["total_features"], len(data["features"]))
        self.assertGreaterEqual(data["total_features"], 46)

        feat = data["features"][0]
        self.assertEqual(feat["geometry"]["type"], "Point")
        lon, lat = feat["geometry"]["coordinates"]
        self.assertTrue(70.0 <= lon <= 78.5, f"Unexpected lon {lon}")
        self.assertTrue(34.0 <= lat <= 38.0, f"Unexpected lat {lat}")
        props = feat["properties"]
        for key in (
            "basin_id",
            "estimated_volume_m3",
            "danger_radius_m",
            "warning_radius_m",
        ):
            self.assertIn(key, props)

    def test_t1_f2_02_geojson_risk_and_basin_filters(self) -> None:
        r_high = self.client.get("/lakes/geojson", params={"risk_level": "High"})
        self.assertEqual(r_high.status_code, 200)
        high_data = r_high.json()
        self.assertGreaterEqual(high_data["total_features"], 3)
        for f in high_data["features"]:
            p = f["properties"]
            self.assertEqual(p["risk_level"], "High")
            self.assertEqual(p["danger_radius_m"], 5000)
            self.assertEqual(p["warning_radius_m"], 10000)

        r_lower = self.client.get("/lakes/geojson", params={"risk_level": "high"})
        self.assertEqual(r_lower.status_code, 200)
        self.assertEqual(r_lower.json()["total_features"], high_data["total_features"])

        r_hunza = self.client.get("/lakes/geojson", params={"basin_id": "hunza"})
        self.assertEqual(r_hunza.status_code, 200)
        hunza = r_hunza.json()
        self.assertGreaterEqual(hunza["total_features"], 5)
        for f in hunza["features"]:
            self.assertEqual(f["properties"]["basin_id"], "hunza")

    def test_t1_f2_03_spatial_search_radius(self) -> None:
        resp = self.client.get(
            "/lakes/spatial-search",
            params={"lat": 36.318, "lon": 74.865, "radius_km": 35},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["query"]["center"], {"lat": 36.318, "lon": 74.865})
        self.assertGreaterEqual(data["count"], 5)
        results = data["results"]
        self.assertEqual(results[0]["name"], "Attabad Lake")
        self.assertEqual(results[0]["distance_km"], 0.0)
        dists = [r["distance_km"] for r in results]
        self.assertTrue(all(d <= 35.0 for d in dists))
        self.assertEqual(dists, sorted(dists))

    def test_t1_f2_04_spatial_search_bbox(self) -> None:
        resp = self.client.get(
            "/lakes/spatial-search",
            params={
                "min_lon": 74.5,
                "min_lat": 36.1,
                "max_lon": 75.2,
                "max_lat": 36.6,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["query"]["bbox"], [74.5, 36.1, 75.2, 36.6])
        self.assertGreaterEqual(data["count"], 1)
        for r in data["results"]:
            self.assertTrue(74.5 <= r["longitude"] <= 75.2)
            self.assertTrue(36.1 <= r["latitude"] <= 36.6)

    def test_t1_f2_05_flood_corridor_shisper(self) -> None:
        resp = self.client.get("/lakes/2/flood-corridor")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(data["lake"]["id"], 2)
        self.assertEqual(data["lake"]["basin_id"], "hunza")
        self.assertGreater(data["lake"]["peak_discharge_m3s"], 0)

        layers = {f["properties"].get("layer"): f for f in data["features"]}
        for expected_layer in (
            "warning_zone",
            "danger_zone",
            "glacier_feeder",
            "downstream_corridor",
            "storage_node",
            "lake_origin",
        ):
            self.assertIn(expected_layer, layers, f"Missing layer {expected_layer}")

        nodes = [
            f["properties"]["estimated_arrival_min"]
            for f in data["features"]
            if f["properties"].get("layer") == "storage_node"
        ]
        self.assertGreaterEqual(len(nodes), 1)
        self.assertEqual(nodes, sorted(nodes))

    # --- Feature 3: Risk, Reports & Auth ---
    def test_t1_f3_01_risk_attabad(self) -> None:
        resp = self.client.get("/risk/1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["lake_id"], 1)
        self.assertIn(data["risk_level"], {"High", "Medium", "Low"})
        self.assertTrue(data["recommendation"])
        sm = data["spatial_metrics"]
        self.assertEqual(sm["basin_name"], "Hunza River Basin")
        for key in (
            "dist_to_glacier_km",
            "estimated_expansion_rate_pct",
            "terrain_slope_deg",
            "dist_to_fault_km",
            "estimated_outburst_volume_m3",
            "basin_name",
        ):
            self.assertIn(key, sm)
        self.assertEqual(sm["estimated_outburst_volume_m3"], 33600000)

    def test_t1_f3_02_risk_passu(self) -> None:
        resp = self.client.get("/risk/3")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["lake_id"], 3)
        self.assertEqual(data["area_ha"], 9.2)
        self.assertEqual(data["spatial_metrics"]["estimated_outburst_volume_m3"], 1104000)

    def test_t1_f3_03_sitrep_pdf_attabad(self) -> None:
        resp = self.client.get("/reports/sitrep/1")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/pdf"))
        self.assertIn("SITREP_Lake_1.pdf", resp.headers.get("content-disposition", ""))
        self.assertTrue(resp.content.startswith(b"%PDF-"))
        self.assertGreater(len(resp.content), 400)

    def test_t1_f3_04_sitrep_pdf_baltoro(self) -> None:
        resp = self.client.get("/reports/sitrep/10")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b"%PDF-"))
        self.assertIn("SITREP_Lake_10.pdf", resp.headers.get("content-disposition", ""))

    def test_t1_f3_05_auth_login_valid_jwt(self) -> None:
        if ApiE2EContext.db_path is None:
            self.skipTest("No isolated DB path provided for synthetic user fixture")
        seed_user_in_sqlite(
            ApiE2EContext.db_path,
            username="e2e_op",
            password="Secret123!",
            role="OPERATOR",
        )
        resp = self.client.post(
            "/auth/login",
            data={"username": "e2e_op", "password": "Secret123!"},
        )
        self.assertEqual(
            resp.status_code,
            200,
            f"POST /auth/login failed with {resp.status_code}: {resp.text}",
        )
        body = resp.json()
        self.assertEqual(body.get("token_type"), "bearer")
        token = body.get("access_token", "")
        self.assertEqual(len(token.split(".")), 3, f"Malformed JWT: {token!r}")

    # --- Feature 4: Early Warning, Weather & GEE ---
    def test_t1_f4_01_root_weather(self) -> None:
        resp = self.client.get("/weather", params={"lat": 35.92, "lon": 74.31})
        self.assertEqual(resp.status_code, 200)
        w = resp.json()
        self.assertAlmostEqual(w["latitude"], 35.92, places=2)
        self.assertAlmostEqual(w["longitude"], 74.31, places=2)
        for key in (
            "temperature_c",
            "temperature_2m",
            "forecast_max_c",
            "relative_humidity_2m",
            "precipitation",
            "wind_speed_10m",
            "source",
            "cached",
        ):
            self.assertIn(key, w)

    def test_t1_f4_02_ew_status_and_weather(self) -> None:
        r_status = self.client.get("/early-warning/status")
        self.assertEqual(r_status.status_code, 200)
        st = r_status.json()
        self.assertEqual(len(st.get("levels", [])), 4)
        indicators = st.get("indicators", [])
        self.assertEqual(len(indicators), 5)
        self.assertEqual(sum(ind["weight"] for ind in indicators), 100)
        self.assertIn("sensors", st)
        self.assertIn("gee", st)

        r_weather = self.client.get(
            "/early-warning/weather",
            params={"lat": 36.318, "lon": 74.865},
        )
        self.assertEqual(r_weather.status_code, 200)
        self.assertIn("source", r_weather.json())

    def test_t1_f4_03_ew_score_fast(self) -> None:
        self.pacer.acquire("ew_score", 20)
        resp = self.client.get("/early-warning/score/2", params={"use_gee": "false"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["lake_id"], 2)
        ew = data["early_warning"]
        self.assertTrue(0 <= ew["score"] <= 100)
        self.assertIn(ew["level"], {"Normal", "Watch", "Warning", "Critical"})
        self.assertGreater(data["flood_impact"]["estimated_volume_m3"], 0)
        self.assertIn(
            data["flood_prediction"]["likelihood"],
            {"Unlikely", "Possible", "Likely", "High"},
        )
        self.assertEqual(data["data_sources"]["mode"], "fast")

    def test_t1_f4_04_ew_monitor_fast(self) -> None:
        resp = self.client.get(
            "/early-warning/monitor",
            params={"use_gee": "false", "limit": 10, "offset": 0},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 10)
        self.assertGreaterEqual(data["inventory_total"], 46)
        self.assertTrue(data["has_more"])
        self.assertEqual(data["next_offset"], 10)
        self.assertEqual(sum(data["counts"].values()), 10)
        self.assertGreaterEqual(len(data["basins"]), 1)

    def test_t1_f4_05_ew_basins_and_detail(self) -> None:
        self.pacer.acquire("ew_basins", 30)
        r_list = self.client.get("/early-warning/basins")
        self.assertEqual(r_list.status_code, 200)
        blist = r_list.json()
        self.assertGreaterEqual(blist["total_basins"], 9)

        self.pacer.acquire("ew_basins", 30)
        r_hunza = self.client.get("/early-warning/basins/hunza")
        self.assertEqual(r_hunza.status_code, 200)
        hunza = r_hunza.json()
        self.assertEqual(hunza["id"], "hunza")
        self.assertGreaterEqual(hunza["lake_count"], 5)
        self.assertEqual(hunza["cascade_risk"], "High")

    def test_t1_f4_06_gee_status_and_detect_lakes(self) -> None:
        r_status = self.client.get("/gee/status")
        self.assertEqual(r_status.status_code, 200)
        st = r_status.json()
        self.assertEqual(len(st["cascade"]), 4)
        self.assertTrue(st["cascade"][3]["ready"])

        self.pacer.acquire("detect_lakes", 3)
        r_detect = self.client.get("/gee/detect-lakes", timeout=90.0)
        self.assertEqual(r_detect.status_code, 200)
        det = r_detect.json()
        self.assertIn(
            det["source_used"],
            {"gee_sentinel2", "mpc_sentinel2", "sentinel1_sar", "database_inventory"},
        )
        self.assertEqual(det["total_detected"], len(det["lakes"]))
        if det["source_used"] == "database_inventory":
            self.assertGreaterEqual(det["total_detected"], 46)
        else:
            self.assertGreaterEqual(det["total_detected"], 1)
        self.assertGreaterEqual(len(det["cascade"]), 1)


# ==============================================================================
# TIER 1 — M1 SHOWCASE ENDPOINTS (FEATURES 5–10)
# ==============================================================================
@unittest.skipUnless(tier_enabled(1), "Tier 1 disabled via GLOF_E2E_TIER")
@unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
class TestApiTier1Showcase(BaseApiTestCase):
    """Tier 1 happy-path API tests for M1 Showcase Features 5–10."""

    # --- Feature 5: Elevation Caching & Provider Telemetry ---
    def test_t1_f5_01_providers_schema(self) -> None:
        resp = self.client.get("/analytics/providers")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        providers = data.get("providers")
        self.assertIsInstance(providers, list)
        self.assertGreaterEqual(len(providers), 1)
        for p in providers:
            for key in ("name", "status", "cached_entries", "last_latency_ms", "fallback_mode"):
                self.assertIn(key, p)
            self.assertIsInstance(p["cached_entries"], int)
            self.assertGreaterEqual(p["cached_entries"], 0)

    def test_t1_f5_02_providers_cover_hydromet_and_seismic(self) -> None:
        resp = self.client.get("/analytics/providers")
        self.assertEqual(resp.status_code, 200)
        names = " ".join(p["name"].lower() for p in resp.json()["providers"])
        self.assertTrue("open-meteo" in names or "meteo" in names, f"Missing Open-Meteo in {names}")
        self.assertIn("usgs", names, f"Missing USGS in {names}")

    def test_t1_f5_03_elevation_cache_populated_after_breach(self) -> None:
        r_breach = self.client.get("/analytics/breach-simulation/1")
        self.assertEqual(r_breach.status_code, 200, r_breach.text)
        r_prov = self.client.get("/analytics/providers")
        self.assertEqual(r_prov.status_code, 200)
        total_cached = sum(int(p["cached_entries"]) for p in r_prov.json()["providers"])
        self.assertGreaterEqual(total_cached, 1)

    def test_t1_f5_04_repeat_monitor_all_lakes_cached(self) -> None:
        r1 = self.client.get("/early-warning/monitor", params={"use_gee": "false", "limit": 46})
        self.assertEqual(r1.status_code, 200)
        t0 = time.monotonic()
        r2 = self.client.get("/early-warning/monitor", params={"use_gee": "false", "limit": 46})
        elapsed = time.monotonic() - t0
        self.assertEqual(r2.status_code, 200)
        d2 = r2.json()
        self.assertEqual(d2["total"], d2["inventory_total"])
        self.assertLess(elapsed, 15.0)

    def test_t1_f5_05_telemetry_status_values_after_triggers(self) -> None:
        self.client.get("/analytics/triggers/1")
        resp = self.client.get("/analytics/providers")
        self.assertEqual(resp.status_code, 200)
        for p in resp.json()["providers"]:
            self.assertTrue(isinstance(p["status"], str) and len(p["status"]) > 0)
            self.assertIsInstance(p["fallback_mode"], bool)

    # --- Feature 6: Dam-Breach & Hydraulic Routing Simulator ---
    def test_t1_f6_01_breach_default_attabad(self) -> None:
        resp = self.client.get("/analytics/breach-simulation/1")
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertEqual(d["lake_id"], 1)
        self.assertGreater(d["volume_m3"], 0)
        pq = d["peak_discharge"]
        for k in ("froehlich_m3s", "costa_m3s", "walder_oconnor_m3s", "ensemble_m3s"):
            self.assertGreater(pq[k], 0)
        self.assertGreaterEqual(len(d["hydrograph"]), 10)
        self.assertGreaterEqual(len(d["elevation_profile"]), 2)
        self.assertGreaterEqual(len(d["downstream_nodes"]), 1)
        self.assertIn("elevation_source", d["provenance"])
        self.assertIsInstance(d["provenance"]["fallback_used"], bool)

    def test_t1_f6_02_breach_dam_types(self) -> None:
        for dam_type in ("ice", "landslide"):
            resp = self.client.get(
                "/analytics/breach-simulation/2",
                params={"dam_type": dam_type},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            d = resp.json()
            self.assertEqual(d["params"]["dam_type"], dam_type)
            self.assertGreater(d["peak_discharge"]["ensemble_m3s"], 0)

    def test_t1_f6_03_breach_fraction_monotonicity(self) -> None:
        r_low = self.client.get("/analytics/breach-simulation/1", params={"breach_fraction": 0.25})
        r_high = self.client.get("/analytics/breach-simulation/1", params={"breach_fraction": 1.0})
        self.assertEqual(r_low.status_code, 200)
        self.assertEqual(r_high.status_code, 200)
        d_low, d_high = r_low.json(), r_high.json()
        self.assertGreater(d_high["volume_m3"], d_low["volume_m3"])
        self.assertGreater(
            d_high["peak_discharge"]["ensemble_m3s"],
            d_low["peak_discharge"]["ensemble_m3s"],
        )

    def test_t1_f6_04_breach_duration_response(self) -> None:
        r_fast = self.client.get("/analytics/breach-simulation/1", params={"breach_duration_min": 15})
        r_slow = self.client.get("/analytics/breach-simulation/1", params={"breach_duration_min": 180})
        self.assertEqual(r_fast.status_code, 200)
        self.assertEqual(r_slow.status_code, 200)
        h_fast = r_fast.json()["hydrograph"]
        h_slow = r_slow.json()["hydrograph"]
        self.assertGreaterEqual(h_fast[-1]["time_min"], 120)
        self.assertGreaterEqual(h_slow[-1]["time_min"], 180)
        self.assertGreaterEqual(
            max(p["discharge_m3s"] for p in h_fast),
            max(p["discharge_m3s"] for p in h_slow),
        )

    def test_t1_f6_05_breach_downstream_nodes_eta_ordering(self) -> None:
        resp = self.client.get("/analytics/breach-simulation/1")
        self.assertEqual(resp.status_code, 200)
        nodes = resp.json()["downstream_nodes"]
        dists = [n["distance_km"] for n in nodes]
        etas = [n["eta_min"] for n in nodes]
        self.assertEqual(dists, sorted(dists))
        self.assertEqual(etas, sorted(etas))
        for n in nodes:
            self.assertGreaterEqual(n["recession_min"], n["eta_min"])
            self.assertGreater(n["attenuated_q_m3s"], 0)
            self.assertGreater(n["stage_rise_m"], 0)
            self.assertTrue(n["severity"])

    # --- Feature 7: Multi-Hazard Trigger Engine ---
    def test_t1_f7_01_triggers_shisper_contract(self) -> None:
        resp = self.client.get("/analytics/triggers/2")
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertEqual(d["lake_id"], 2)
        self.assertTrue(0 <= d["composite_trigger_score"] <= 100)
        self.assertTrue(d["trigger_level"])
        self.assertGreaterEqual(len(d["daily_series"]), 7)
        self.assertIsInstance(d["seismic_events"], list)
        prov = d["provenance"]
        for key in ("open_meteo", "glofas", "usgs", "fallback_used"):
            self.assertIn(key, prov)
        self.assertIsInstance(prov["fallback_used"], bool)

    def test_t1_f7_02_triggers_pillar_fields(self) -> None:
        resp = self.client.get("/analytics/triggers/1")
        self.assertEqual(resp.status_code, 200)
        pillars = resp.json()["pillars"]
        for k in ("score", "pdd_14d", "freezing_level_m", "above_lake_elevation", "max_temp_c"):
            self.assertIn(k, pillars["thermal_melt"])
        for k in ("score", "discharge_m3s", "mean_discharge_m3s", "anomaly_ratio"):
            self.assertIn(k, pillars["hydrological_glofas"])
        for k in ("score", "event_count_30d", "max_magnitude", "nearest_km", "pga_proxy"):
            self.assertIn(k, pillars["seismic_usgs"])

    def test_t1_f7_03_triggers_daily_series_schema(self) -> None:
        resp = self.client.get("/analytics/triggers/5")
        self.assertEqual(resp.status_code, 200)
        series = resp.json()["daily_series"]
        self.assertGreaterEqual(len(series), 7)
        for row in series:
            for k in ("date", "temp_max_c", "precip_mm", "freezing_level_m", "river_discharge_m3s"):
                self.assertIn(k, row)

    def test_t1_f7_04_triggers_regional_route(self) -> None:
        resp = self.client.get("/analytics/triggers/regional")
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertIsInstance(d, dict)
        self.assertTrue(len(d) >= 1)

    def test_t1_f7_05_triggers_baltoro(self) -> None:
        resp = self.client.get("/analytics/triggers/10")
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["lake_id"], 10)
        self.assertEqual(d["lake_name"], "Baltoro Lake")
        self.assertTrue(0 <= d["composite_trigger_score"] <= 100)

    # --- Feature 8: Districts, Basin Cascade & Compare ---
    def test_t1_f8_01_districts_23_and_hunza(self) -> None:
        resp = self.client.get("/analytics/districts")
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertEqual(d["total_districts"], 23)
        self.assertEqual(len(d["districts"]), 23)
        hunza = next((x for x in d["districts"] if x["pcode"] == "PK306"), None)
        self.assertIsNotNone(hunza, "PK306 Hunza district missing")
        assert hunza is not None
        self.assertGreaterEqual(hunza["lake_count"], 10)
        self.assertGreater(hunza["total_area_ha"], 300)
        self.assertGreater(hunza["hazard_density_per_1000sqkm"], 0)
        self.assertEqual(set(hunza["risk_counts"].keys()), {"High", "Medium", "Low"})
        self.assertGreater(len(hunza["lakes"]), 0)

    def test_t1_f8_02_districts_lake_count_consistency(self) -> None:
        d = self.client.get("/analytics/districts").json()
        total_assigned = sum(int(x["lake_count"]) for x in d["districts"])
        self.assertGreaterEqual(total_assigned, 40)
        self.assertLessEqual(total_assigned, len(self.client.get("/lakes/").json()))

    def test_t1_f8_03_basins_cascade(self) -> None:
        resp = self.client.get("/analytics/basins/cascade")
        self.assertEqual(resp.status_code, 200, resp.text)
        basins = resp.json()["basins"]
        self.assertGreaterEqual(len(basins), 1)
        for b in basins:
            for k in (
                "basin_id",
                "basin_name",
                "lake_count",
                "high_risk_count",
                "total_volume_m3",
                "domino_risk_score",
                "cascade_tier",
                "chokepoints",
                "lakes",
            ):
                self.assertIn(k, b)
        hunza = next((b for b in basins if b["basin_id"] == "hunza"), None)
        self.assertIsNotNone(hunza)
        assert hunza is not None
        self.assertGreaterEqual(hunza["high_risk_count"], 3)

    def test_t1_f8_04_compare_three_lakes_radar(self) -> None:
        resp = self.client.get("/analytics/compare", params={"lake_ids": "1,2,10"})
        self.assertEqual(resp.status_code, 200, resp.text)
        lakes = resp.json()["lakes"]
        self.assertEqual(len(lakes), 3)
        expected_axes = {"area", "volume", "discharge", "glacier_proximity", "exposure", "trigger"}
        for lk in lakes:
            for k in (
                "id",
                "name",
                "area_ha",
                "risk_level",
                "basin_name",
                "outburst_volume_m3",
                "peak_discharge_m3s",
                "ew_score",
                "ew_level",
                "trigger_score",
                "danger_population",
                "glacier_distance_km",
                "normalized_radar",
            ):
                self.assertIn(k, lk)
            self.assertEqual(set(lk["normalized_radar"].keys()), expected_axes)
            for val in lk["normalized_radar"].values():
                self.assertTrue(0 <= val <= 100)

    def test_t1_f8_05_compare_two_and_four_lakes(self) -> None:
        r2 = self.client.get("/analytics/compare", params={"lake_ids": "1,5"})
        r4 = self.client.get("/analytics/compare", params={"lake_ids": "1,2,5,10"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r4.status_code, 200)
        self.assertEqual(len(r2.json()["lakes"]), 2)
        self.assertEqual(len(r4.json()["lakes"]), 4)

    # --- Feature 9: Monte Carlo Sensitivity & SSP Scenarios ---
    def test_t1_f9_01_sensitivity_default_percentiles(self) -> None:
        resp = self.client.get("/analytics/sensitivity/1")
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertEqual(d["lake_id"], 1)
        self.assertEqual(d["n_samples"], 500)
        for metric in ("volume_m3", "peak_discharge_m3s", "ew_score"):
            m = d[metric]
            self.assertLessEqual(m["p10"], m["p50"])
            self.assertLessEqual(m["p50"], m["p90"])
        self.assertIn("warning_ge_50", d["exceedance_prob"])
        self.assertIn("critical_ge_75", d["exceedance_prob"])
        self.assertGreaterEqual(len(d["tornado_drivers"]), 1)
        self.assertGreaterEqual(len(d["scenarios"]), 3)

    def test_t1_f9_02_sensitivity_warming_delta(self) -> None:
        r0 = self.client.get("/analytics/sensitivity/2", params={"temp_delta_c": 0.0, "n_samples": 500})
        r3 = self.client.get("/analytics/sensitivity/2", params={"temp_delta_c": 3.0, "n_samples": 500})
        self.assertEqual(r0.status_code, 200)
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()["temp_delta_c"], 3.0)
        self.assertGreaterEqual(r3.json()["ew_score"]["p50"], r0.json()["ew_score"]["p50"])

    def test_t1_f9_03_sensitivity_cooling_min_samples(self) -> None:
        resp = self.client.get(
            "/analytics/sensitivity/2",
            params={"temp_delta_c": -1.0, "n_samples": 100},
        )
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["temp_delta_c"], -1.0)
        self.assertEqual(d["n_samples"], 100)
        self.assertLessEqual(d["ew_score"]["p10"], d["ew_score"]["p50"])
        self.assertLessEqual(d["ew_score"]["p50"], d["ew_score"]["p90"])

    def test_t1_f9_04_sensitivity_max_samples_2000(self) -> None:
        resp = self.client.get("/analytics/sensitivity/1", params={"n_samples": 2000})
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertEqual(d["n_samples"], 2000)
        self.assertLessEqual(d["volume_m3"]["p10"], d["volume_m3"]["p90"])

    def test_t1_f9_05_sensitivity_scenarios_list(self) -> None:
        resp = self.client.get("/analytics/sensitivity/13")
        self.assertEqual(resp.status_code, 200)
        scenarios = resp.json()["scenarios"]
        deltas = [s["temp_delta_c"] for s in scenarios]
        self.assertIn(0.0, deltas)
        self.assertIn(1.5, deltas)
        self.assertIn(3.0, deltas)
        for s in scenarios:
            for k in ("label", "temp_delta_c", "p50_score", "critical_prob"):
                self.assertIn(k, s)

    # --- Feature 10: GIS/CSV Exports, JSON Briefing & spa_fallback ---
    def test_t1_f10_01_export_geojson(self) -> None:
        resp = self.client.get("/reports/export/geojson")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("attachment", resp.headers.get("content-disposition", "").lower())
        data = resp.json()
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertGreaterEqual(len(data["features"]), 46)
        props = data["features"][0]["properties"]
        self.assertIn("district", props)
        self.assertTrue("basin_id" in props or "basin_name" in props)

    def test_t1_f10_02_export_csv(self) -> None:
        resp = self.client.get("/reports/export/csv")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.headers.get("content-type", "").startswith("text/csv"))
        self.assertIn("attachment", resp.headers.get("content-disposition", "").lower())
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        self.assertGreaterEqual(len(rows), 46)
        cols = set(rows[0].keys())
        for expected in ("id", "name", "area_ha", "risk_level", "latitude", "longitude"):
            self.assertIn(expected, cols)

    def test_t1_f10_03_sitrep_briefing_attabad(self) -> None:
        resp = self.client.get("/reports/sitrep/1/briefing")
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        for key in ("lake", "risk", "corridor_summary", "triggers", "recommendation", "generated_at_utc"):
            self.assertIn(key, d)
        self.assertEqual(d["lake"]["id"], 1)

    def test_t1_f10_04_sitrep_briefing_pdf_parity_shisper(self) -> None:
        r_b = self.client.get("/reports/sitrep/2/briefing")
        r_p = self.client.get("/reports/sitrep/2")
        self.assertEqual(r_b.status_code, 200)
        self.assertEqual(r_p.status_code, 200)
        self.assertEqual(r_b.json()["lake"]["name"], "Shisper Lake")
        self.assertTrue(r_p.content.startswith(b"%PDF-"))

    def test_t1_f10_05_spa_fallback_analytics_404(self) -> None:
        resp = self.client.get("/analytics/__e2e_nonexistent_route__")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("application/json", resp.headers.get("content-type", ""))
        self.assertEqual(resp.json().get("detail"), "Not found")

        static_check = verify_backend_static_serving(ApiE2EContext.base_url, mode="full")
        self.assertTrue(static_check["ok"], f"backend/static check failed: {static_check}")


# ==============================================================================
# TIER 2 — BOUNDARY, NEGATIVE, AUTH & OFFLINE RESILIENCE TESTS
# ==============================================================================
@unittest.skipUnless(tier_enabled(2), "Tier 2 disabled via GLOF_E2E_TIER")
class TestApiTier2NegativeAndBoundary(BaseApiTestCase):
    """Tier 2 negative, boundary, auth, and offline resilience tests."""

    def test_t2_01_lake_404(self) -> None:
        resp = self.client.get("/lakes/999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json().get("detail"), "Lake not found")

    def test_t2_02_create_lake_422_missing_area(self) -> None:
        resp = self.client.post("/lakes/", json={"name": "E2E_BadLake"})
        self.assertEqual(resp.status_code, 422)

    def test_t2_03_rename_missing_query_param_422(self) -> None:
        resp = self.client.put("/lakes/1/rename", json={"new_name": "ShouldFail"})
        self.assertEqual(resp.status_code, 422)

    def test_t2_04_delete_and_rename_404(self) -> None:
        r_ren = self.client.put("/lakes/999999/rename", params={"new_name": "Nope"})
        self.assertEqual(r_ren.status_code, 404)
        r_del = self.delete_probe_lake(999999)
        self.assertEqual(r_del.status_code, 404)

    def test_t2_05_corridor_no_coordinates_400(self) -> None:
        r_create = self.create_probe_lake(
            {"name": "E2E_PROBE_NoCoords", "area_ha": 12.0}
        )
        self.assertEqual(r_create.status_code, 200, r_create.text)
        lid = r_create.json()["id"]
        try:
            r_corr = self.client.get(f"/lakes/{lid}/flood-corridor")
            self.assertEqual(r_corr.status_code, 400)
            self.assertIn("coordinates", r_corr.json().get("detail", "").lower())
        finally:
            self.delete_probe_lake(lid)

    def test_t2_06_spatial_search_out_of_bounds_422(self) -> None:
        for params in (
            {"lat": 95, "lon": 74},
            {"lat": 36.3, "lon": 74.8, "radius_km": 0.1},
            {"lat": 36.3, "lon": 74.8, "radius_km": 600},
        ):
            resp = self.client.get("/lakes/spatial-search", params=params)
            self.assertEqual(resp.status_code, 422, f"Expected 422 for {params}")

    def test_t2_07_auth_401_wrong_credentials(self) -> None:
        resp = self.client.post(
            "/auth/login",
            data={"username": "nonexistent_user", "password": "wrong_password"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json().get("detail"), "Incorrect username or password")

    def test_t2_08_auth_422_json_body(self) -> None:
        resp = self.client.post(
            "/auth/login",
            json={"username": "a", "password": "b"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_t2_09_risk_and_sitrep_404(self) -> None:
        self.assertEqual(self.client.get("/risk/999999").status_code, 404)
        self.assertEqual(self.client.get("/reports/sitrep/999999").status_code, 404)

    def test_t2_10_early_warning_bounds_and_404(self) -> None:
        self.pacer.acquire("ew_score", 20)
        self.assertEqual(self.client.get("/early-warning/score/999999").status_code, 404)
        self.pacer.acquire("ew_basins", 30)
        self.assertEqual(self.client.get("/early-warning/basins/nonexistent").status_code, 404)
        self.assertEqual(self.client.get("/early-warning/monitor/jobs/badjobid").status_code, 404)
        self.assertEqual(self.client.get("/early-warning/monitor", params={"limit": 0}).status_code, 422)
        self.assertEqual(self.client.get("/early-warning/weather").status_code, 422)

    def test_t2_11_gee_bounds_and_offline_safety(self) -> None:
        gee_ready = bool(self.client.get("/gee/status").json().get("gee", {}).get("ready"))
        r_hist = self.client.get("/gee/historical", params={"lat": 36.3, "lon": 74.8})
        if gee_ready:
            self.assertIn(r_hist.status_code, (200, 503))
        else:
            self.assertEqual(r_hist.status_code, 503)

        r_bad_mode = self.client.get(
            "/gee/thumbnail",
            params={"lat": 36.3, "lon": 74.8, "mode": "invalid"},
        )
        self.assertEqual(r_bad_mode.status_code, 422)

        r_bad_rad = self.client.get(
            "/gee/population",
            params={"lat": 36.3, "lon": 74.8, "radius_km": 100},
        )
        self.assertEqual(r_bad_rad.status_code, 422)

    @unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
    def test_t2_12_breach_simulation_bounds_and_404(self) -> None:
        self.assertEqual(self.client.get("/analytics/breach-simulation/999999").status_code, 404)
        for params in (
            {"breach_fraction": 0.05},
            {"breach_fraction": 1.5},
            {"dam_type": "concrete"},
            {"breach_duration_min": 5},
            {"breach_duration_min": 300},
        ):
            r = self.client.get("/analytics/breach-simulation/1", params=params)
            self.assertEqual(r.status_code, 422, f"Expected 422 for {params}, got {r.status_code}")

    @unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
    def test_t2_13_triggers_404_and_offline_resilience(self) -> None:
        self.assertEqual(self.client.get("/analytics/triggers/999999").status_code, 404)

        dead_port = find_free_port()
        dead_proxy = f"http://127.0.0.1:{dead_port}"
        with EphemeralBackend(
            extra_env={
                "HTTP_PROXY": dead_proxy,
                "HTTPS_PROXY": dead_proxy,
                "NO_PROXY": "127.0.0.1,localhost",
            },
            startup_timeout=90.0,
        ) as offline_srv:
            with httpx.Client(base_url=offline_srv.base_url, timeout=15.0) as c:
                t0 = time.monotonic()
                r_trig = c.get("/analytics/triggers/1")
                elapsed = time.monotonic() - t0
                self.assertEqual(r_trig.status_code, 200)
                self.assertLessEqual(elapsed, 12.0)
                trig = r_trig.json()
                self.assertIsInstance(trig["composite_trigger_score"], (int, float))
                self.assertIsInstance(trig["provenance"]["fallback_used"], bool)

                r_breach = c.get("/analytics/breach-simulation/1")
                self.assertEqual(r_breach.status_code, 200)
                self.assertGreater(r_breach.json()["peak_discharge"]["ensemble_m3s"], 0)

    @unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
    def test_t2_14_compare_edge_inputs(self) -> None:
        for bad_ids in ("abc", "", "1,2,3,4,5"):
            r = self.client.get("/analytics/compare", params={"lake_ids": bad_ids})
            self.assertIn(r.status_code, (400, 422), f"Unexpected {r.status_code} for lake_ids={bad_ids!r}")

        r_miss = self.client.get("/analytics/compare", params={"lake_ids": "999999"})
        self.assertNotEqual(r_miss.status_code, 500)

        r_dup = self.client.get("/analytics/compare", params={"lake_ids": "1,1"})
        self.assertNotEqual(r_dup.status_code, 500)

    @unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
    def test_t2_15_sensitivity_bounds_and_404(self) -> None:
        self.assertEqual(self.client.get("/analytics/sensitivity/999999").status_code, 404)
        for params in (
            {"temp_delta_c": -2.0},
            {"temp_delta_c": 6.0},
            {"n_samples": 50},
            {"n_samples": 5000},
        ):
            r = self.client.get("/analytics/sensitivity/1", params=params)
            self.assertEqual(r.status_code, 422, f"Expected 422 for {params}, got {r.status_code}")

    @unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
    def test_t2_16_briefing_404_and_invalid_lake_id_path(self) -> None:
        self.assertEqual(self.client.get("/reports/sitrep/999999/briefing").status_code, 404)
        self.assertEqual(self.client.get("/lakes/nonexistent_str").status_code, 422)
        r_bad_analytics = self.client.get("/analytics/__e2e_bad__")
        self.assertEqual(r_bad_analytics.status_code, 404)
        self.assertIn("application/json", r_bad_analytics.headers.get("content-type", ""))


# ==============================================================================
# TIER 3 — CROSS-FEATURE API WORKFLOWS
# ==============================================================================
@unittest.skipUnless(tier_enabled(3), "Tier 3 disabled via GLOF_E2E_TIER")
class TestApiTier3CrossFeature(BaseApiTestCase):
    """Tier 3 cross-endpoint consistency and multi-step chains."""

    def test_t3_01_geojson_corridor_breach_sitrep_chain(self) -> None:
        geo = self.client.get("/lakes/geojson", params={"risk_level": "High"}).json()
        shisper = next((f for f in geo["features"] if f["properties"]["id"] == 2), None)
        self.assertIsNotNone(shisper)
        assert shisper is not None
        basin_id = shisper["properties"]["basin_id"]

        corr = self.client.get("/lakes/2/flood-corridor").json()
        self.assertEqual(corr["lake"]["basin_id"], basin_id)

        pdf_resp = self.client.get("/reports/sitrep/2")
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertTrue(pdf_resp.content.startswith(b"%PDF-"))

        if not IS_BASELINE:
            breach = self.client.get("/analytics/breach-simulation/2", params={"breach_fraction": 1.0}).json()
            self.assertEqual(breach["basin_id"], basin_id)
            briefing = self.client.get("/reports/sitrep/2/briefing").json()
            self.assertEqual(briefing["lake"]["id"], 2)

    @unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
    def test_t3_02_spatial_search_compare_csv_parity(self) -> None:
        sr = self.client.get(
            "/lakes/spatial-search",
            params={"lat": 36.318, "lon": 74.865, "radius_km": 35},
        ).json()
        top_ids = [str(r["id"]) for r in sr["results"][:3]]
        self.assertEqual(len(top_ids), 3)

        cmp_data = self.client.get("/analytics/compare", params={"lake_ids": ",".join(top_ids)}).json()
        cmp_map = {str(lk["id"]): lk for lk in cmp_data["lakes"]}

        csv_text = self.client.get("/reports/export/csv").text
        csv_map = {row["id"]: row for row in csv.DictReader(io.StringIO(csv_text))}

        for lid in top_ids:
            self.assertIn(lid, cmp_map)
            self.assertIn(lid, csv_map)
            self.assertAlmostEqual(float(cmp_map[lid]["area_ha"]), float(csv_map[lid]["area_ha"]), places=1)
            self.assertEqual(cmp_map[lid]["risk_level"], csv_map[lid]["risk_level"])

    @unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
    def test_t3_03_triggers_sensitivity_ew_dossier_coherence(self) -> None:
        self.pacer.acquire("ew_score", 20)
        ew = self.client.get("/early-warning/score/1", params={"use_gee": "false"}).json()
        trig = self.client.get("/analytics/triggers/1").json()
        sens = self.client.get("/analytics/sensitivity/1", params={"temp_delta_c": 1.5}).json()

        self.assertEqual(ew["lake_id"], 1)
        self.assertEqual(trig["lake_id"], 1)
        self.assertEqual(sens["lake_id"], 1)
        self.assertEqual(trig["lake_name"], "Attabad Lake")
        self.assertEqual(sens["lake_name"], "Attabad Lake")
        self.assertTrue(0 <= sens["ew_score"]["p50"] <= 100)

    @unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase not active")
    def test_t3_04_districts_and_export_geojson_parity(self) -> None:
        districts_resp = self.client.get("/analytics/districts").json()
        district_names = {d["name"] for d in districts_resp["districts"]}
        geo_resp = self.client.get("/reports/export/geojson").json()

        for feat in geo_resp["features"]:
            dist = feat["properties"].get("district")
            if dist:
                self.assertIn(dist, district_names)
        sum_counts = sum(int(d["lake_count"]) for d in districts_resp["districts"])
        self.assertGreaterEqual(len(geo_resp["features"]), sum_counts)

    def test_t3_05_async_monitor_job_and_telemetry(self) -> None:
        r_start = self.client.post(
            "/early-warning/monitor/jobs",
            params={"use_gee": "false", "limit": 5},
        )
        self.assertEqual(r_start.status_code, 200, r_start.text)
        job_id = r_start.json()["job_id"]

        deadline = time.monotonic() + 25.0
        snap = None
        while time.monotonic() < deadline:
            snap = self.client.get(f"/early-warning/monitor/jobs/{job_id}").json()
            if snap["status"] in ("done", "error"):
                break
            time.sleep(0.3)

        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap["status"], "done", f"Job failed: {snap}")
        self.assertEqual(snap["done"], 5)
        self.assertFalse(snap["result"]["partial"])
        self.assertEqual(snap["result"]["total"], 5)

        if not IS_BASELINE:
            prov = self.client.get("/analytics/providers").json()
            self.assertGreaterEqual(sum(int(p["cached_entries"]) for p in prov["providers"]), 1)

    def test_t3_06_synthetic_lake_lifecycle_and_cleanup(self) -> None:
        r_create = self.create_probe_lake(
            {
                "name": "E2E_PROBE_Temp_Hunza",
                "area_ha": 45.0,
                "latitude": 36.42,
                "longitude": 74.75,
            }
        )
        self.assertEqual(r_create.status_code, 200, r_create.text)
        lid = r_create.json()["id"]
        try:
            self.assertEqual(self.client.get(f"/lakes/{lid}").status_code, 200)
            r_risk = self.client.get(f"/risk/{lid}")
            self.assertEqual(r_risk.status_code, 200)
            self.assertIn("Hunza", r_risk.json()["spatial_metrics"]["basin_name"])

            if not IS_BASELINE:
                r_br = self.client.get(f"/analytics/breach-simulation/{lid}")
                self.assertEqual(r_br.status_code, 200)
                self.assertEqual(r_br.json()["basin_id"], "hunza")
                self.assertEqual(self.client.get(f"/analytics/sensitivity/{lid}").status_code, 200)
                self.assertEqual(self.client.get(f"/reports/sitrep/{lid}/briefing").status_code, 200)
        finally:
            self.assertEqual(self.delete_probe_lake(lid).status_code, 200)

        self.assertEqual(self.client.get(f"/lakes/{lid}").status_code, 404)
        if not IS_BASELINE:
            self.assertEqual(self.client.get(f"/analytics/breach-simulation/{lid}").status_code, 404)


# ==============================================================================
# TIER 4 — REALISTIC STAKEHOLDER API SCENARIOS
# ==============================================================================
@unittest.skipUnless(tier_enabled(4), "Tier 4 disabled via GLOF_E2E_TIER")
@unittest.skipIf(IS_BASELINE, "Baseline mode: M1 showcase scenarios require M1 APIs")
class TestApiTier4StakeholderScenarios(BaseApiTestCase):
    """Tier 4 domain-realistic stakeholder scenarios."""

    def test_t4_01_shisper_surge_ice_dam_breach(self) -> None:
        corr = self.client.get("/lakes/2/flood-corridor").json()
        self.assertEqual(corr["lake"]["basin_id"], "hunza")

        breach = self.client.get(
            "/analytics/breach-simulation/2",
            params={"dam_type": "ice", "breach_fraction": 0.85, "breach_duration_min": 30},
        ).json()
        self.assertGreater(breach["peak_discharge"]["ensemble_m3s"], 0)
        peak_pt = max(breach["hydrograph"], key=lambda p: p["discharge_m3s"])
        self.assertLessEqual(peak_pt["time_min"], 60)
        self.assertGreaterEqual(len(breach["downstream_nodes"]), 2)
        for node in breach["downstream_nodes"]:
            self.assertGreater(node["eta_min"], 0)
            self.assertGreater(node["stage_rise_m"], 0)

        trig = self.client.get("/analytics/triggers/2").json()
        self.assertIn("thermal_melt", trig["pillars"])
        self.assertIn("hydrological_glofas", trig["pillars"])
        self.assertIn("seismic_usgs", trig["pillars"])

        sens = self.client.get("/analytics/sensitivity/2", params={"temp_delta_c": 1.5}).json()
        self.assertLessEqual(sens["ew_score"]["p10"], sens["ew_score"]["p90"])

        pdf = self.client.get("/reports/sitrep/2")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF-"))

    def test_t4_02_attabad_mega_landslide_dam_breach(self) -> None:
        br_land = self.client.get(
            "/analytics/breach-simulation/1",
            params={"dam_type": "landslide", "breach_fraction": 0.6, "breach_duration_min": 90},
        ).json()
        br_mor = self.client.get(
            "/analytics/breach-simulation/1",
            params={"dam_type": "moraine", "breach_fraction": 1.0, "breach_duration_min": 45},
        ).json()
        br_shisper = self.client.get(
            "/analytics/breach-simulation/2",
            params={"dam_type": "moraine", "breach_fraction": 1.0, "breach_duration_min": 45},
        ).json()

        self.assertGreater(br_mor["volume_m3"], br_land["volume_m3"])
        self.assertGreater(
            br_mor["peak_discharge"]["ensemble_m3s"],
            br_shisper["peak_discharge"]["ensemble_m3s"],
        )

        briefing = self.client.get("/reports/sitrep/1/briefing").json()
        self.assertEqual(briefing["lake"]["risk_level"], "High")

    def test_t4_03_hunza_cascade_and_district_pk306(self) -> None:
        cascade = self.client.get("/analytics/basins/cascade").json()
        hunza_b = next((b for b in cascade["basins"] if b["basin_id"] == "hunza"), None)
        self.assertIsNotNone(hunza_b)
        assert hunza_b is not None
        self.assertGreaterEqual(hunza_b["high_risk_count"], 3)

        districts = self.client.get("/analytics/districts").json()["districts"]
        pk306 = next(d for d in districts if d["pcode"] == "PK306")
        self.assertEqual(pk306["total_area_ha"], max(d["total_area_ha"] for d in districts))

        reg = self.client.get("/analytics/triggers/regional")
        self.assertEqual(reg.status_code, 200)

        geo = self.client.get("/lakes/geojson", params={"basin_id": "hunza"}).json()
        self.assertGreaterEqual(geo["total_features"], 5)

    def test_t4_04_portfolio_comparison_across_basins(self) -> None:
        cmp_data = self.client.get("/analytics/compare", params={"lake_ids": "1,2,5,10"}).json()
        lakes = cmp_data["lakes"]
        self.assertEqual(len(lakes), 4)
        basins = {lk["basin_name"] for lk in lakes}
        self.assertGreaterEqual(len(basins), 3)

        att = next(lk for lk in lakes if lk["id"] == 1)
        self.assertEqual(att["outburst_volume_m3"], max(lk["outburst_volume_m3"] for lk in lakes))

        geo_ids = {f["properties"]["id"] for f in self.client.get("/reports/export/geojson").json()["features"]}
        csv_ids = {
            int(r["id"])
            for r in csv.DictReader(io.StringIO(self.client.get("/reports/export/csv").text))
        }
        for target_id in (1, 2, 5, 10):
            self.assertIn(target_id, geo_ids)
            self.assertIn(target_id, csv_ids)

        prov = self.client.get("/analytics/providers")
        self.assertEqual(prov.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
