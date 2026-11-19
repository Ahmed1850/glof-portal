"""
Passive, thread-safe external provider telemetry & live cache introspection.

Zero top-level imports of routers or other services to prevent circular imports.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

_LOCK = threading.Lock()

_CANONICAL_PROVIDERS: list[tuple[str, str]] = [
    ("Open-Meteo Weather", "MET Norway -> wttr.in -> stale cache"),
    ("Open-Meteo Elevation", "stale cache -> Open-Elevation -> alpine slope heuristic"),
    ("Open-Meteo GloFAS", "seasonal basin runoff climatology"),
    ("USGS ComCat", "zero recent seismicity (baseline M<3.5)"),
    ("Planetary Computer", "GEE Sentinel-1 SAR -> DB inventory"),
    ("GEE", "Planetary Computer S2 -> DB inventory / heuristic"),
]

_DEFAULT_FALLBACK_MAP: dict[str, str] = dict(_CANONICAL_PROVIDERS)

_TELEMETRY: dict[str, dict[str, Any]] = {
    name: {
        "name": name,
        "status": "standby",
        "cached_entries": 0,
        "last_latency_ms": None,
        "fallback_mode": fallback,
        "updated_at": None,
        "last_error": None,
    }
    for name, fallback in _CANONICAL_PROVIDERS
}

_CACHE_COUNTERS: dict[str, list[Callable[[], int]]] = {}


def register_cache_counter(name: str, counter_fn: Callable[[], int]) -> None:
    """Register a callable returning live cached entry count for a provider."""
    with _LOCK:
        lst = _CACHE_COUNTERS.setdefault(name, [])
        if counter_fn not in lst:
            lst.append(counter_fn)


def record_provider_event(
    name: str,
    *,
    status: str,
    latency_ms: Optional[float] = None,
    cached_entries: Optional[int] = None,
    fallback_mode: Optional[str] = None,
    cache_hit: bool = False,
    error: Optional[str] = None,
) -> None:
    """Record a provider probe, cache hit, or fallback transition."""
    with _LOCK:
        rec = _TELEMETRY.get(name)
        if rec is None:
            rec = {
                "name": name,
                "status": "standby",
                "cached_entries": 0,
                "last_latency_ms": None,
                "fallback_mode": fallback_mode or _DEFAULT_FALLBACK_MAP.get(name, "local fallback"),
                "updated_at": None,
                "last_error": None,
            }
            _TELEMETRY[name] = rec

        rec["status"] = status
        if latency_ms is not None:
            rec["last_latency_ms"] = round(float(latency_ms), 1)
        elif not cache_hit and status == "offline":
            rec["last_latency_ms"] = None

        if cached_entries is not None:
            rec["cached_entries"] = int(cached_entries)

        if fallback_mode is not None:
            rec["fallback_mode"] = fallback_mode
        elif not rec.get("fallback_mode"):
            rec["fallback_mode"] = _DEFAULT_FALLBACK_MAP.get(name, "local fallback")

        rec["updated_at"] = time.time()
        rec["last_error"] = error


def _sum_counters(counters: list[Callable[[], int]]) -> Optional[int]:
    if not counters:
        return None
    total = 0
    for fn in counters:
        try:
            total += int(fn())
        except Exception:
            pass
    return total


def get_providers_snapshot() -> list[dict[str, Any]]:
    """
    Return the 6 canonical provider telemetry records without performing
    any blocking network I/O.
    """
    gee_ready: Optional[bool] = None
    try:
        from app.utils.gee_detection import GEE_READY
        gee_ready = bool(GEE_READY)
    except Exception:
        gee_ready = None

    with _LOCK:
        names = [n for n, _ in _CANONICAL_PROVIDERS]
        for extra in _TELEMETRY:
            if extra not in names:
                names.append(extra)

        snapshot: list[dict[str, Any]] = []
        for name in names:
            rec = _TELEMETRY[name]
            counters = list(_CACHE_COUNTERS.get(name) or [])
            live_count = _sum_counters(counters)
            cached_entries = live_count if live_count is not None else int(rec.get("cached_entries") or 0)

            status = str(rec.get("status") or "standby")
            if name == "GEE" and gee_ready is not None:
                if status == "standby":
                    status = "healthy" if gee_ready else "offline"
                elif not gee_ready and status == "healthy":
                    status = "offline"

            snapshot.append({
                "name": rec["name"],
                "status": status,
                "cached_entries": cached_entries,
                "last_latency_ms": rec.get("last_latency_ms"),
                "fallback_mode": rec.get("fallback_mode") or _DEFAULT_FALLBACK_MAP.get(name, "local fallback"),
            })
        return snapshot
