"""
Tests for Milestone M1 Analytics Showcase endpoints:
  - GET /analytics/providers
  - GET /analytics/breach-simulation/{lake_id}
  - GET /analytics/triggers/regional & GET /analytics/triggers/{lake_id}
  - GET /analytics/districts
  - GET /analytics/basins/cascade
  - GET /analytics/compare
  - GET /analytics/sensitivity/{lake_id}
"""

from __future__ import annotations

import json
import math
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
)


class TestAnalyticsShowcase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = get_client()

    def setUp(self):
        reset_analytics_caches()

    def test_01_providers_telemetry(self):
        r = self.client.get("/analytics/providers")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("providers", body)
        providers = body["providers"]
        self.assertEqual(len(providers), 6)
        expected_names = {
            "Open-Meteo Weather",
            "Open-Meteo Elevation",
            "Open-Meteo GloFAS",
            "USGS ComCat",
            "Planetary Computer",
            "GEE",
        }
        self.assertEqual({p["name"] for p in providers}, expected_names)
        for p in providers:
            self.assertIn("status", p)
            self.assertIn("cached_entries", p)
            self.assertIn("last_latency_ms", p)
            self.assertIn("fallback_mode", p)

    def test_02_breach_simulation_online_physics_and_sensitivity(self):
        with patch("httpx.get", side_effect=make_mock_httpx_get()):
            r_moraine = self.client.get("/analytics/breach-simulation/1")
            self.assertEqual(r_moraine.status_code, 200)
            data = r_moraine.json()

            self.assertEqual(data["lake_id"], 1)
            self.assertEqual(data["lake_name"], "Attabad Lake")
            self.assertFalse(data["provenance"]["fallback_used"])
            self.assertEqual(len(data["hydrograph"]), 49)
            self.assertEqual(data["hydrograph"][0], {"time_min": 0, "discharge_m3s": 0.0})
            self.assertEqual(data["hydrograph"][-1]["time_min"], 240)

            max_q = max(pt["discharge_m3s"] for pt in data["hydrograph"])
            self.assertEqual(max_q, data["peak_discharge"]["ensemble_m3s"])
            self.assertGreater(data["peak_discharge"]["froehlich_m3s"], 0)
            self.assertGreater(data["peak_discharge"]["costa_m3s"], 0)
            self.assertGreater(data["peak_discharge"]["walder_oconnor_m3s"], 0)

            self.assertGreaterEqual(len(data["downstream_nodes"]), 1)
            for node in data["downstream_nodes"]:
                self.assertGreater(node["stage_rise_m"], 0)
                self.assertLess(node["eta_min"], node["recession_min"])

            r_landslide = self.client.get("/analytics/breach-simulation/1?dam_type=landslide")
            r_ice = self.client.get("/analytics/breach-simulation/1?dam_type=ice")
            self.assertGreater(
                r_landslide.json()["peak_discharge"]["ensemble_m3s"],
                data["peak_discharge"]["ensemble_m3s"],
            )
            self.assertGreater(
                data["peak_discharge"]["ensemble_m3s"],
                r_ice.json()["peak_discharge"]["ensemble_m3s"],
            )

            r_full = self.client.get("/analytics/breach-simulation/1?breach_fraction=1.0")
            r_quarter = self.client.get("/analytics/breach-simulation/1?breach_fraction=0.25")
            self.assertGreater(
                r_full.json()["peak_discharge"]["ensemble_m3s"],
                r_quarter.json()["peak_discharge"]["ensemble_m3s"],
            )

    def test_03_breach_simulation_offline_fallback_and_errors(self):
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            r = self.client.get("/analytics/breach-simulation/1")
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertTrue(data["provenance"]["fallback_used"])
            self.assertEqual(data["provenance"]["elevation_source"], "offline_basin_gradient")
            self.assertGreaterEqual(len(data["elevation_profile"]), 2)
            self.assertGreaterEqual(len(data["downstream_nodes"]), 1)

        self.assertEqual(self.client.get("/analytics/breach-simulation/99999").status_code, 404)

        # Coordinate-less and zero-area lakes should return 400
        r_no_coord = self.client.post(
            "/lakes/",
            json={"name": "NoCoord Lake", "area_ha": 10.0, "latitude": None, "longitude": None},
        )
        self.assertEqual(r_no_coord.status_code, 200)
        nc_id = r_no_coord.json()["id"]
        self.assertEqual(self.client.get(f"/analytics/breach-simulation/{nc_id}").status_code, 400)
        self.assertEqual(self.client.get(f"/analytics/triggers/{nc_id}").status_code, 400)
        self.client.delete(f"/lakes/{nc_id}")

        r_zero_area = self.client.post(
            "/lakes/",
            json={"name": "ZeroArea Lake", "area_ha": 0.0, "latitude": 36.1, "longitude": 74.5},
        )
        self.assertEqual(r_zero_area.status_code, 200)
        za_id = r_zero_area.json()["id"]
        self.assertEqual(self.client.get(f"/analytics/breach-simulation/{za_id}").status_code, 400)
        self.client.delete(f"/lakes/{za_id}")

    def test_04_triggers_regional_ordering_and_online_offline(self):
        with patch("httpx.get", side_effect=make_mock_httpx_get()):
            r_reg = self.client.get("/analytics/triggers/regional")
            self.assertEqual(r_reg.status_code, 200)
            reg = r_reg.json()
            self.assertEqual(reg["total_lakes"], 46)
            self.assertEqual(len(reg["lakes"]), 46)
            self.assertFalse(reg["provenance"]["fallback_used"])
            scores = [item["composite_trigger_score"] for item in reg["lakes"]]
            self.assertEqual(scores, sorted(scores, reverse=True))

            r_one = self.client.get("/analytics/triggers/1")
            self.assertEqual(r_one.status_code, 200)
            one = r_one.json()
            self.assertEqual(one["lake_id"], 1)
            self.assertFalse(one["provenance"]["fallback_used"])
            self.assertEqual(len(one["daily_series"]), 14)
            self.assertGreaterEqual(one["pillars"]["seismic_usgs"]["event_count_30d"], 1)
            self.assertGreaterEqual(one["composite_trigger_score"], 0.0)
            self.assertLessEqual(one["composite_trigger_score"], 100.0)

        # Offline fallback on cold caches
        reset_analytics_caches()
        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            r_one_off = self.client.get("/analytics/triggers/1")
            self.assertEqual(r_one_off.status_code, 200)
            one_off = r_one_off.json()
            self.assertTrue(one_off["provenance"]["fallback_used"])
            self.assertEqual(len(one_off["daily_series"]), 14)

            r_reg_off = self.client.get("/analytics/triggers/regional")
            self.assertEqual(r_reg_off.status_code, 200)
            self.assertTrue(r_reg_off.json()["provenance"]["fallback_used"])
            self.assertEqual(r_reg_off.json()["total_lakes"], 46)

        self.assertEqual(self.client.get("/analytics/triggers/99999").status_code, 404)

    def test_05_districts_pip_and_basin_cascade(self):
        r_dist = self.client.get("/analytics/districts")
        self.assertEqual(r_dist.status_code, 200)
        dist = r_dist.json()
        self.assertEqual(dist["total_districts"], 23)
        self.assertEqual(len(dist["districts"]), 23)
        self.assertEqual(sum(d["lake_count"] for d in dist["districts"]), 46)
        hunza = next(d for d in dist["districts"] if d["name"] == "Hunza")
        self.assertGreater(hunza["lake_count"], 0)
        self.assertGreater(hunza["hazard_density_per_1000sqkm"], 0)

        r_casc = self.client.get("/analytics/basins/cascade")
        self.assertEqual(r_casc.status_code, 200)
        basins = r_casc.json()["basins"]
        self.assertGreaterEqual(len(basins), 9)
        for b in basins:
            self.assertGreaterEqual(b["domino_risk_score"], 0.0)
            self.assertLessEqual(b["domino_risk_score"], 100.0)
            self.assertIn(b["cascade_tier"], ("Extreme", "High", "Moderate", "Low"))
            self.assertIsInstance(b["chokepoints"], list)
            for cp in b["chokepoints"]:
                self.assertIsInstance(cp, str)

    def test_06_compare_lakes(self):
        with patch("httpx.get", side_effect=make_mock_httpx_get()):
            r_default = self.client.get("/analytics/compare")
            self.assertEqual(r_default.status_code, 200)
            self.assertEqual(len(r_default.json()["lakes"]), 3)

            r_custom = self.client.get("/analytics/compare?lake_ids=3,1,2")
            self.assertEqual(r_custom.status_code, 200)
            lakes = r_custom.json()["lakes"]
            self.assertEqual([l["id"] for l in lakes], [3, 1, 2])
            for l in lakes:
                radar = l["normalized_radar"]
                self.assertEqual(
                    set(radar.keys()),
                    {"area", "volume", "discharge", "glacier_proximity", "exposure", "trigger"},
                )
                for val in radar.values():
                    self.assertGreaterEqual(val, 0.0)
                    self.assertLessEqual(val, 100.0)

        self.assertEqual(self.client.get("/analytics/compare?lake_ids=abc").status_code, 400)
        self.assertEqual(self.client.get("/analytics/compare?lake_ids=1,2,3,4,5,6,7").status_code, 400)
        self.assertEqual(self.client.get("/analytics/compare?lake_ids=99999").status_code, 404)

    def test_08_providers_reflect_live_cache_entries_after_queries(self):
        with patch("httpx.get", side_effect=make_mock_httpx_get()):
            self.assertEqual(self.client.get("/analytics/breach-simulation/1").status_code, 200)
            self.assertEqual(self.client.get("/analytics/triggers/1").status_code, 200)
        r = self.client.get("/analytics/providers")
        self.assertEqual(r.status_code, 200)
        by_name = {p["name"]: p for p in r.json()["providers"]}
        self.assertGreaterEqual(by_name["Open-Meteo Elevation"]["cached_entries"], 1)
        self.assertGreaterEqual(by_name["Open-Meteo GloFAS"]["cached_entries"], 1)
        self.assertGreaterEqual(by_name["USGS ComCat"]["cached_entries"], 1)

    def test_07_sensitivity_monte_carlo(self):
        r = self.client.get("/analytics/sensitivity/1?temp_delta_c=1.5&n_samples=500")
        self.assertEqual(r.status_code, 200)
        s = r.json()
        self.assertEqual(s["lake_id"], 1)
        self.assertEqual(s["n_samples"], 500)
        self.assertEqual(s["temp_delta_c"], 1.5)

        for metric in ("volume_m3", "peak_discharge_m3s", "ew_score"):
            self.assertLessEqual(s[metric]["p10"], s[metric]["p50"])
            self.assertLessEqual(s[metric]["p50"], s[metric]["p90"])

        self.assertGreaterEqual(s["exceedance_prob"]["warning_ge_50"], 0.0)
        self.assertLessEqual(s["exceedance_prob"]["warning_ge_50"], 1.0)
        self.assertGreaterEqual(s["exceedance_prob"]["critical_ge_75"], 0.0)
        self.assertLessEqual(s["exceedance_prob"]["critical_ge_75"], 1.0)

        total_pct = round(sum(d["impact_pct"] for d in s["tornado_drivers"]), 1)
        self.assertAlmostEqual(total_pct, 100.0, places=1)

        self.assertEqual(len(s["scenarios"]), 3)
        self.assertGreaterEqual(s["scenarios"][2]["p50_score"], s["scenarios"][0]["p50_score"])

        self.assertEqual(self.client.get("/analytics/sensitivity/99999").status_code, 404)

    def test_09_regional_triggers_single_elevation_call_and_provenance(self):
        from app.services import trigger_engine

        elev_calls = {"n": 0}
        usgs_radii: list[int] = []
        base_mock = make_mock_httpx_get()

        def _counting_mock(url, params=None, headers=None, timeout=None, **kwargs):
            if "api.open-meteo.com/v1/elevation" in url:
                elev_calls["n"] += 1
            if "earthquake.usgs.gov/fdsnws/event/1/query" in url and params:
                usgs_radii.append(int(params.get("maxradiuskm", 0)))
            return base_mock(url, params=params, headers=headers, timeout=timeout, **kwargs)

        with patch("httpx.get", side_effect=_counting_mock):
            r = self.client.get("/analytics/triggers/regional")
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(elev_calls["n"], 1)
        self.assertEqual(elev_calls["n"], 1)
        self.assertIn(420, usgs_radii)
        self.assertFalse(r.json()["provenance"]["fallback_used"])

        # Mixed forecast failure + success should report mixed (offline_fallback)
        reset_analytics_caches()
        forecast_seen = {"n": 0}

        def _mixed_mock(url, params=None, headers=None, timeout=None, **kwargs):
            if "api.open-meteo.com/v1/forecast" in url:
                forecast_seen["n"] += 1
                if forecast_seen["n"] == 1:
                    raise httpx.ConnectTimeout("partial forecast timeout")
            return base_mock(url, params=params, headers=headers, timeout=timeout, **kwargs)

        with patch("httpx.get", side_effect=_mixed_mock):
            r_mix = self.client.get("/analytics/triggers/regional")
        self.assertEqual(r_mix.status_code, 200)
        prov = r_mix.json()["provenance"]
        self.assertTrue(prov["fallback_used"])
        self.assertEqual(prov["open_meteo"], "mixed (offline_fallback)")

        # Elevation-only failure on single-lake trigger must set fallback_used=True
        reset_analytics_caches()

        def _elev_only_fail(url, params=None, headers=None, timeout=None, **kwargs):
            if "api.open-meteo.com/v1/elevation" in url:
                raise httpx.ConnectTimeout("elev timeout")
            return base_mock(url, params=params, headers=headers, timeout=timeout, **kwargs)

        with patch("httpx.get", side_effect=_elev_only_fail):
            r_el = self.client.get("/analytics/triggers/1")
        self.assertEqual(r_el.status_code, 200)
        self.assertTrue(r_el.json()["provenance"]["fallback_used"])
        self.assertEqual(r_el.json()["lake_elevation_m"], 3850.0)
        self.assertEqual(len(trigger_engine._FORECAST_CACHE), 1)

    def test_10_malformed_and_nan_http200_payloads_resilience_and_no_poisoning(self):
        from app.services import elevation_service, trigger_engine

        bad_scenarios = [
            # 1. Partial 2-day schema (USGS is valid empty collection)
            (
                {"daily": {"time": ["2026-11-19", "2026-11-20"]}},
                {"daily": {"time": ["2026-11-19", "2026-11-20"]}},
                {"type": "FeatureCollection", "features": []},
                {"elevation": [2366.0]},
                1,
                1,
            ),
            # 2. Non-numeric string values in arrays / coordinates
            (
                {
                    "daily": {
                        "time": [f"2026-11-{i+1:02d}" for i in range(14)],
                        "temperature_2m_max": ["bad"] * 14,
                        "temperature_2m_min": [2.0] * 14,
                        "precipitation_sum": [0.5] * 14,
                    }
                },
                {
                    "daily": {
                        "time": [f"2026-11-{i+1:02d}" for i in range(14)],
                        "river_discharge": ["bad"] * 14,
                    }
                },
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"mag": 4.2},
                            "geometry": {"type": "Point", "coordinates": ["bad", 36.2]},
                        }
                    ],
                },
                {"elevation": ["bad"]},
                0,
                0,
            ),
            # 3. Non-finite "NaN" strings
            (
                {
                    "daily": {
                        "time": [f"2026-11-{i+1:02d}" for i in range(14)],
                        "temperature_2m_max": ["NaN"] * 14,
                        "temperature_2m_min": [2.0] * 14,
                        "precipitation_sum": [0.5] * 14,
                    }
                },
                {
                    "daily": {
                        "time": [f"2026-11-{i+1:02d}" for i in range(14)],
                        "river_discharge": ["NaN"] * 14,
                    }
                },
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"mag": "NaN"},
                            "geometry": {"type": "Point", "coordinates": [74.5, 36.2]},
                        }
                    ],
                },
                {"elevation": ["NaN"]},
                0,
                0,
            ),
            # 4. Wrong container types
            (
                {"daily": {"time": "not-a-list"}},
                {"daily": {"time": "not-a-list"}},
                {"type": "FeatureCollection", "features": ["not-a-dict"]},
                {"elevation": ["Inf"]},
                0,
                0,
            ),
        ]

        for f_json, g_json, u_json, e_json, exp_usgs, exp_elev in bad_scenarios:
            reset_analytics_caches()

            def _malformed_get(url, params=None, headers=None, timeout=None, _f=f_json, _g=g_json, _u=u_json, _e=e_json, **kwargs):
                req = httpx.Request("GET", url, params=params)
                if "api.open-meteo.com/v1/elevation" in url:
                    return httpx.Response(200, json=_e, request=req)
                if "api.open-meteo.com/v1/forecast" in url:
                    return httpx.Response(200, json=_f, request=req)
                if "flood-api.open-meteo.com/v1/flood" in url:
                    return httpx.Response(200, json=_g, request=req)
                if "earthquake.usgs.gov/fdsnws/event/1/query" in url:
                    return httpx.Response(200, json=_u, request=req)
                return httpx.Response(404, json={}, request=req)

            with patch("httpx.get", side_effect=_malformed_get):
                r_tr = self.client.get("/analytics/triggers/1")
                self.assertEqual(r_tr.status_code, 200)
                body = r_tr.json()
                json.dumps(body, allow_nan=False)
                self.assertTrue(body["provenance"]["fallback_used"])
                self.assertEqual(len(body["daily_series"]), 14)
                self.assertGreaterEqual(body["composite_trigger_score"], 0.0)
                self.assertLessEqual(body["composite_trigger_score"], 100.0)

                r_br = self.client.get("/analytics/breach-simulation/1")
                self.assertEqual(r_br.status_code, 200)
                br_body = r_br.json()
                json.dumps(br_body, allow_nan=False)
                if exp_elev == 0:
                    self.assertTrue(br_body["provenance"]["fallback_used"])

            # Verify malformed responses did NOT poison any cache
            self.assertEqual(len(trigger_engine._FORECAST_CACHE), 0)
            self.assertEqual(len(trigger_engine._GLOFAS_CACHE), 0)
            self.assertEqual(len(trigger_engine._USGS_CACHE), exp_usgs)
            if exp_elev == 0:
                self.assertEqual(len(elevation_service._ELEV_CACHE), 0)

        # Follow up with healthy response to verify clean recovery
        with patch("httpx.get", side_effect=make_mock_httpx_get()):
            r_ok = self.client.get("/analytics/triggers/1")
            self.assertEqual(r_ok.status_code, 200)
            self.assertFalse(r_ok.json()["provenance"]["fallback_used"])

    def test_11_breach_stale_cache_provenance_and_compare_dedup(self):
        from app.services import elevation_service

        # Populate valid cache via online breach simulation, then age all entries to 25h (stale)
        with patch("httpx.get", side_effect=make_mock_httpx_get()):
            r_warm = self.client.get("/analytics/breach-simulation/1")
            self.assertEqual(r_warm.status_code, 200)
            self.assertFalse(r_warm.json()["provenance"]["fallback_used"])

        stale_ts = time.time() - (25 * 3600)
        with elevation_service._ELEV_LOCK:
            for k, (_, val) in list(elevation_service._ELEV_CACHE.items()):
                elevation_service._ELEV_CACHE[k] = (stale_ts, val)

        with patch("httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
            r_stale = self.client.get("/analytics/breach-simulation/1")
        self.assertEqual(r_stale.status_code, 200)
        prov = r_stale.json()["provenance"]
        self.assertTrue(prov["fallback_used"])
        self.assertIn("stale", prov["elevation_source"].lower())

        # Compare deduplication preserving order
        with patch("httpx.get", side_effect=make_mock_httpx_get()):
            r_cmp = self.client.get("/analytics/compare?lake_ids=2,2,1,2")
        self.assertEqual(r_cmp.status_code, 200)
        self.assertEqual([l["id"] for l in r_cmp.json()["lakes"]], [2, 1])


if __name__ == "__main__":
    unittest.main()
