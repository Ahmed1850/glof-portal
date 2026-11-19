"""Master CLI runner for GLOF Portal Opaque-Box E2E Suite (Tiers 1-4)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

TESTS_E2E_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_E2E_DIR.parents[1]
if str(TESTS_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_E2E_DIR))

from lib.server_harness import EphemeralBackend, VENV_PYTHON  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run GLOF Portal Opaque-Box E2E Test Suite (API + Headless UI/GIS)."
    )
    parser.add_argument(
        "--mode",
        choices=("full", "baseline"),
        default="full",
        help="full (default): strictly require all M1-M3 features; baseline: skip M1/M2/M3 showcase tests.",
    )
    parser.add_argument(
        "--track",
        choices=("api", "ui", "all"),
        default="all",
        help="Which test track to execute (default: all).",
    )
    parser.add_argument(
        "--tier",
        default="all",
        help="Which tier(s) to execute: 1, 2, 3, 4, comma-separated, or all (default: all).",
    )
    return parser.parse_args(argv)


def run_api_track(mode: str, tier: str) -> int:
    print(f"\n=== [E2E API TRACK] mode={mode} tier={tier} ===")
    t0 = time.monotonic()
    with EphemeralBackend() as backend:
        print(f"Ephemeral API booted at {backend.base_url} (db={backend.db_path})")
        static_res = backend.verify_backend_static(mode=mode)
        if static_res["present"]:
            print(f"backend/static/ verification: ok={static_res['ok']} checks={static_res['checks']}")
            if not static_res["ok"]:
                print(f"backend/static/ errors: {static_res['errors']}")
                if mode == "full":
                    return 1
        else:
            print("backend/static/ not present; skipping direct static mount verification.")

        env = os.environ.copy()
        env.update(
            {
                "GLOF_E2E_API_URL": backend.base_url,
                "GLOF_E2E_DB_PATH": str(backend.db_path),
                "GLOF_E2E_MODE": mode,
                "GLOF_E2E_TIER": tier,
                "PYTHONUNBUFFERED": "1",
            }
        )
        python_exe = str(VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable))
        proc = subprocess.run(
            [python_exe, "-u", "-m", "unittest", str(TESTS_E2E_DIR / "test_api_e2e.py"), "-v"],
            cwd=str(REPO_ROOT),
            env=env,
        )
        elapsed = time.monotonic() - t0
        print(f"=== [E2E API TRACK DONE] rc={proc.returncode} elapsed={elapsed:.1f}s ===")
        return proc.returncode


def run_ui_track(mode: str, tier: str) -> int:
    print(f"\n=== [E2E UI TRACK] mode={mode} tier={tier} ===")
    t0 = time.monotonic()
    with EphemeralBackend() as backend:
        print(f"Ephemeral API booted for UI track at {backend.base_url}")
        env = os.environ.copy()
        env.update(
            {
                "GLOF_E2E_API_URL": backend.base_url,
                "GLOF_E2E_MODE": mode,
                "GLOF_E2E_TIER": tier,
            }
        )
        proc = subprocess.run(
            ["node", "--test", str(TESTS_E2E_DIR / "test_ui_e2e.mjs")],
            cwd=str(REPO_ROOT),
            env=env,
        )
        elapsed = time.monotonic() - t0
        print(f"=== [E2E UI TRACK DONE] rc={proc.returncode} elapsed={elapsed:.1f}s ===")
        return proc.returncode


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results: dict[str, int] = {}

    if args.track in ("api", "all"):
        results["api"] = run_api_track(args.mode, args.tier)
    if args.track in ("ui", "all"):
        results["ui"] = run_ui_track(args.mode, args.tier)

    print("\n================ E2E RUN SUMMARY ================")
    print(f"Mode : {args.mode}")
    print(f"Tier : {args.tier}")
    for track_name, rc in results.items():
        status = "PASS" if rc == 0 else f"FAIL (rc={rc})"
        print(f"Track {track_name.upper():<4}: {status}")
    print("=================================================")

    return 0 if all(rc == 0 for rc in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
