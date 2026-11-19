"""
Shared pure-Python geospatial & planning-grade exposure helpers for analytics services.
Matches existing constants/formulas in routers/early_warning.py and routers/lakes.py
without creating service -> router import dependencies.
"""

from __future__ import annotations

import math
from typing import Optional

from app.utils.risk import calculate_risk

GLACIER_REFS: list[tuple[float, float]] = [
    (36.45, 74.60),   # Batura / Passu region
    (36.40, 74.70),   # Ultar / Hunza
    (36.20, 74.80),   # Hispar / Hopar
    (35.80, 76.50),   # Baltoro / Concordia
    (35.85, 76.40),   # K2 approach
    (36.10, 75.20),   # Shimshal / Khurdopin
    (36.55, 74.90),   # Ghulkin / Batura
    (35.60, 76.30),   # Masherbrum / Hushe
    (36.25, 71.85),   # Tirich Mir
    (35.35, 74.80),   # Rama / Astore
]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points in kilometers."""
    lat1_r, lon1_r, lat2_r, lon2_r = map(math.radians, (float(lat1), float(lon1), float(lat2), float(lon2)))
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return 6371.0088 * c


def nearest_glacier_km(lat: float, lon: float) -> float:
    return min(haversine_km(lat, lon, glat, glon) for glat, glon in GLACIER_REFS)


def zone_radii_m(risk: str) -> tuple[int, int]:
    if risk == "High":
        return 5000, 10000
    if risk == "Medium":
        return 2000, 5000
    return 1000, 2000


def wkt_point(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    if lat is None or lon is None:
        return None
    return f"POINT({lon} {lat})"


def population_heuristic(lat: float, lon: float, area_ha: Optional[float]) -> dict:
    """Planning-grade population heuristic matching routers/early_warning.py."""
    risk = calculate_risk(area_ha or 0)
    d_km, w_km = (5.0, 10.0) if risk == "High" else ((2.0, 5.0) if risk == "Medium" else (1.0, 2.0))
    dens = 95.0
    danger_pop = int(math.pi * (d_km ** 2) * dens * 0.35)
    warning_pop = int(math.pi * (w_km ** 2) * dens * 0.35)
    return {
        "danger_population": danger_pop,
        "warning_population": max(warning_pop, danger_pop),
        "source": "heuristic valley density (GEE offline)",
        "risk_level": risk,
    }
