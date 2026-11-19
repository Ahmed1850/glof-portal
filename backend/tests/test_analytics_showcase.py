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

import sys
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


if __name__ == "__main__":
    unittest.main()
