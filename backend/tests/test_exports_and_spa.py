"""
Tests for Reports, GeoJSON/CSV Exports, JSON SITREP Briefing, and Render SPA Fallback.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from tests._test_harness import get_client, make_mock_httpx_get, reset_analytics_caches  # noqa: E402


class TestExportsAndSpa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = get_client()

    def setUp(self):
        reset_analytics_caches()

    def test_01_sitrep_pdf_preserved(self):
        r = self.client.get("/reports/sitrep/1")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("application/pdf"))
        self.assertIn("SITREP_Lake_1.pdf", r.headers.get("content-disposition", ""))
        self.assertTrue(r.content.startswith(b"%PDF-"))
        self.assertEqual(self.client.get("/reports/sitrep/99999").status_code, 404)

    def test_02_sitrep_briefing_json(self):
        with patch("httpx.get", side_effect=make_mock_httpx_get()):
            r = self.client.get("/reports/sitrep/1/briefing")
        self.assertEqual(r.status_code, 200)
        b = r.json()
        for key in ("lake", "risk", "corridor_summary", "triggers", "recommendation", "generated_at_utc"):
            self.assertIn(key, b)
        self.assertEqual(b["lake"]["id"], 1)
        self.assertIn(b["lake"]["district"], ("Hunza", "Nagar"))
        self.assertGreater(b["risk"]["peak_discharge_m3s"], 0)
        self.assertEqual(self.client.get("/reports/sitrep/99999/briefing").status_code, 404)

    def test_03_export_geojson(self):
        r = self.client.get("/reports/export/geojson")
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/geo+json", r.headers["content-type"])
        self.assertIn("glof_lakes_export.geojson", r.headers.get("content-disposition", ""))
        fc = r.json()
        self.assertEqual(fc["type"], "FeatureCollection")
        self.assertEqual(fc["total_features"], 46)
        self.assertEqual(len(fc["features"]), 46)

        sample_props = fc["features"][0]["properties"]
        for prop in (
            "district",
            "basin_id",
            "estimated_volume_m3",
            "peak_discharge_m3s",
            "warning_radius_m",
            "danger_radius_m",
        ):
            self.assertIn(prop, sample_props)

        r_filt = self.client.get("/reports/export/geojson?risk_level=High&basin_id=hunza")
        self.assertEqual(r_filt.status_code, 200)
        filt_fc = r_filt.json()
        self.assertGreaterEqual(filt_fc["total_features"], 1)
        self.assertLess(filt_fc["total_features"], 46)

    def test_04_export_csv(self):
        r = self.client.get("/reports/export/csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("glof_lakes_inventory.csv", r.headers.get("content-disposition", ""))

        rows = list(csv.DictReader(io.StringIO(r.text)))
        self.assertEqual(len(rows), 46)
        expected_cols = {
            "id",
            "name",
            "latitude",
            "longitude",
            "wkt",
            "area_ha",
            "risk_level",
            "district",
            "basin_id",
            "estimated_volume_m3",
            "peak_discharge_m3s",
            "ew_score",
            "ew_level",
            "danger_radius_m",
            "warning_radius_m",
        }
        self.assertTrue(expected_cols.issubset(set(rows[0].keys())))

    def test_05_render_spa_fallback_excludes_analytics_and_reports(self):
        backend_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="glof_spa_") as tmp:
            tmp_path = Path(tmp)
            db_file = tmp_path / "spa_test.db"
            static_dir = tmp_path / "static"
            static_dir.mkdir()
            (static_dir / "index.html").write_text("<html><body>GLOF SPA</body></html>", encoding="utf-8")
            assets_dir = static_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "app.js").write_text("console.log('glof');", encoding="utf-8")

            # Verify real app.main spa_fallback route when GLOF_STATIC_DIR points to a built static directory
            script = "\n".join([
                "import os, sys",
                "from pathlib import Path",
                "from unittest.mock import patch",
                f"os.environ['DATABASE_URL'] = 'sqlite:///' + {db_file.as_posix()!r}",
                "os.environ['SEED_ON_START'] = '1'",
                "os.environ['WARM_WEATHER_ON_START'] = '0'",
                f"os.environ['GLOF_STATIC_DIR'] = {str(static_dir)!r}",
                f"sys.path.insert(0, {str(backend_dir)!r})",
                "try:",
                "    import ee",
                "    p = patch.object(ee, 'Initialize', side_effect=RuntimeError('disabled'))",
                "    p.start()",
                "except Exception:",
                "    p = None",
                "import app.main as m",
                "if p is not None: p.stop()",
                "from fastapi.testclient import TestClient",
                "assert any(getattr(r, 'name', None) == 'spa_fallback' for r in m.app.routes), 'real spa_fallback not registered'",
                "c = TestClient(m.app)",
                "assert c.get('/analytics/providers').status_code == 200",
                "r_an = c.get('/analytics/nonexistent')",
                "assert r_an.status_code == 404 and r_an.json() == {'detail': 'Not found'}, r_an.text",
                "r_rp = c.get('/reports/nonexistent')",
                "assert r_rp.status_code == 404 and r_rp.json() == {'detail': 'Not found'}, r_rp.text",
                "r_asset = c.get('/assets/app.js')",
                "assert r_asset.status_code == 200 and 'glof' in r_asset.text, r_asset.text",
                "r_spa = c.get('/dashboard')",
                "assert r_spa.status_code == 200 and 'GLOF SPA' in r_spa.text, r_spa.text",
                "m.engine.dispose()",
            ])
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=90,
            )
            self.assertEqual(proc.returncode, 0, msg=f"stderr={proc.stderr}\nstdout={proc.stdout}")


if __name__ == "__main__":
    unittest.main()
