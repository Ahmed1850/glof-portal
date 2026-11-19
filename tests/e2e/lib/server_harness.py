"""Isolated ephemeral uvicorn server harness for GLOF Portal E2E tests."""

from __future__ import annotations

import atexit
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import bcrypt
import httpx


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def find_free_port() -> int:
    """Allocate an ephemeral loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _kill_process_tree(pid: int) -> None:
    """Terminate a process and all its children on Windows (or POSIX fallback)."""
    if not pid:
        return
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            check=False,
        )
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


class EphemeralBackend:
    """Manages an isolated uvicorn app.main:app instance backed by a temp SQLite DB."""

    def __init__(
        self,
        temp_dir: Optional[Path] = None,
        port: Optional[int] = None,
        extra_env: Optional[dict[str, str]] = None,
        startup_timeout: float = 90.0,
    ) -> None:
        self._owns_temp_dir = temp_dir is None
        self.temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.mkdtemp(prefix="glof_e2e_api_"))
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.port = port or find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.db_path = self.temp_dir / "e2e_glof.db"
        self.log_path = self.temp_dir / "uvicorn.log"
        self.extra_env = dict(extra_env or {})
        self.startup_timeout = startup_timeout
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._log_fh = None
        self._atexit_registered = False

    def start(self) -> "EphemeralBackend":
        """Start uvicorn and wait until /health returns HTTP 200."""
        if self._proc is not None and self._proc.poll() is None:
            return self

        python_exe = VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)
        env = os.environ.copy()
        env.update(
            {
                "DATABASE_URL": f"sqlite:///{self.db_path.as_posix()}",
                "SEED_ON_START": "1",
                "CORS_ORIGINS": "*",
                "PYTHONUNBUFFERED": "1",
            }
        )
        env.update(self.extra_env)

        self._log_fh = open(self.log_path, "wb")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._proc = subprocess.Popen(
            [
                str(python_exe),
                "-u",
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True

        deadline = time.monotonic() + self.startup_timeout
        last_err: Optional[str] = None
        with httpx.Client(timeout=2.5) as client:
            while time.monotonic() < deadline:
                rc = self._proc.poll()
                if rc is not None:
                    logs = self.read_logs(tail_lines=80)
                    self.stop()
                    raise RuntimeError(
                        f"Ephemeral uvicorn exited prematurely with code {rc}.\n--- LOGS ---\n{logs}"
                    )
                try:
                    resp = client.get(f"{self.base_url}/health")
                    if resp.status_code == 200:
                        return self
                    last_err = f"HTTP {resp.status_code}"
                except Exception as exc:
                    last_err = str(exc)
                time.sleep(0.25)

        logs = self.read_logs(tail_lines=80)
        self.stop()
        raise TimeoutError(
            f"Timed out after {self.startup_timeout}s waiting for {self.base_url}/health "
            f"(last error: {last_err}).\n--- LOGS ---\n{logs}"
        )

    def read_logs(self, tail_lines: int = 80) -> str:
        """Read tail of captured uvicorn stdout/stderr log."""
        if not self.log_path.is_file():
            return ""
        text = self.log_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-tail_lines:])

    def seed_test_user(self, username: str, password: str, role: str = "OPERATOR") -> None:
        """Seed a synthetic user directly into the temp SQLite DB without touching backend/glof.db."""
        seed_user_in_sqlite(self.db_path, username=username, password=password, role=role)

    def verify_backend_static(self, mode: str = "full") -> dict[str, Any]:
        """Verify backend/static/ serving and spa_fallback rules when backend/static/index.html exists."""
        return verify_backend_static_serving(self.base_url, mode=mode)

    def stop(self) -> None:
        """Guarantee process-tree teardown and temp directory cleanup."""
        if self._proc is not None:
            pid = self._proc.pid
            if self._proc.poll() is None:
                _kill_process_tree(pid)
                try:
                    self._proc.wait(timeout=5)
                except Exception:
                    pass
            self._proc = None

        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

        if self._owns_temp_dir and self.temp_dir.exists():
            for _ in range(5):
                try:
                    shutil.rmtree(self.temp_dir)
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                shutil.rmtree(self.temp_dir, ignore_errors=True)

    def __enter__(self) -> "EphemeralBackend":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def seed_user_in_sqlite(db_path: Path, username: str, password: str, role: str = "OPERATOR") -> None:
    """Insert or update a synthetic user in an isolated SQLite DB using bcrypt."""
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE users SET hashed_password = ?, role = ? WHERE id = ?",
                (hashed, role, row[0]),
            )
        else:
            cur.execute(
                "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
                (username, hashed, role),
            )
        conn.commit()
    finally:
        conn.close()


def verify_backend_static_serving(base_url: str, mode: str = "full") -> dict[str, Any]:
    """Check backend/static/index.html serving, /assets/*, SPA fallback, and JSON 404 exclusions."""
    static_dir = BACKEND_DIR / "static"
    index_file = static_dir / "index.html"
    if not index_file.is_file():
        return {"present": False, "ok": True, "skipped": True, "checks": [], "errors": []}

    checks: list[str] = []
    errors: list[str] = []

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        # 1. Root HTML
        r_root = client.get("/")
        if r_root.status_code != 200 or "text/html" not in r_root.headers.get("content-type", ""):
            errors.append(f"GET / expected 200 text/html, got {r_root.status_code} {r_root.headers.get('content-type')}")
        else:
            checks.append("GET / -> 200 text/html")

        html = r_root.text

        # 2. Assets referenced in index.html + no baked 127.0.0.1:8000
        asset_paths = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
        if not asset_paths:
            errors.append("No /assets/* references found in GET / HTML")
        for ap in asset_paths:
            ra = client.get(ap)
            if ra.status_code != 200:
                errors.append(f"GET {ap} returned {ra.status_code}")
            else:
                checks.append(f"GET {ap} -> 200")

        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            for js_file in assets_dir.glob("*.js"):
                content = js_file.read_text(encoding="utf-8", errors="replace")
                if "127.0.0.1:8000" in content:
                    errors.append(f"Baked localhost origin '127.0.0.1:8000' found in {js_file.name}")
            checks.append("Verified backend/static/assets/*.js free of 127.0.0.1:8000")

        # 3. SPA client route fallback
        r_spa = client.get("/__e2e_client_route__/preview")
        if r_spa.status_code != 200 or "text/html" not in r_spa.headers.get("content-type", ""):
            errors.append(f"SPA fallback failed: {r_spa.status_code} {r_spa.headers.get('content-type')}")
        else:
            checks.append("SPA fallback -> 200 text/html")

        # 4. API prefix exclusion (baseline prefix)
        r_api_404 = client.get("/lakes/__e2e_nonexistent__")
        if r_api_404.status_code not in (404, 422) or "application/json" not in r_api_404.headers.get("content-type", ""):
            errors.append(f"/lakes/__e2e_nonexistent__ expected JSON 404/422, got {r_api_404.status_code}")
        else:
            checks.append(f"/lakes/__e2e_nonexistent__ -> JSON {r_api_404.status_code}")

        # 5. M1 /analytics/ exclusion
        if mode == "full":
            r_analytics_404 = client.get("/analytics/__e2e_nonexistent__")
            if r_analytics_404.status_code != 404 or "application/json" not in r_analytics_404.headers.get("content-type", ""):
                errors.append(
                    f"/analytics/__e2e_nonexistent__ expected JSON 404, got "
                    f"{r_analytics_404.status_code} ({r_analytics_404.headers.get('content-type')})"
                )
            else:
                checks.append("/analytics/__e2e_nonexistent__ -> JSON 404")

    return {
        "present": True,
        "ok": len(errors) == 0,
        "skipped": False,
        "checks": checks,
        "errors": errors,
    }


if __name__ == "__main__":
    with EphemeralBackend() as srv:
        print(f"Ephemeral backend ready at {srv.base_url} (db={srv.db_path})")
        static_res = srv.verify_backend_static(mode="full")
        print(f"Static check: {static_res}")
