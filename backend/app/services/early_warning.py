"""
GLOF Early Warning Score

Indicators (0–100 composite):
  - Current lake area            (25 pts)
  - Area growth rate (3–5 yr)    (25 pts)
  - Lake elevation               (15 pts)
  - Proximity to glacier         (15 pts, optional)
  - Downstream population        (20 pts)

Alert levels:
  Normal   0–24
  Watch    25–49
  Warning  50–74
  Critical 75–100
"""

from __future__ import annotations

from typing import Any, Optional


LEVELS = (
    ("Critical", 75, "#f0433a", "Immediate action — high GLOF potential"),
    ("Warning", 50, "#f5a524", "Elevated risk — intensify monitoring"),
    ("Watch", 25, "#38bdf8", "Developing signals — routine enhanced watch"),
    ("Normal", 0, "#2dd48e", "Baseline conditions — standard surveillance"),
)


def classify_level(score: float) -> dict:
    s = max(0.0, min(100.0, float(score or 0)))
    for name, threshold, color, advice in LEVELS:
        if s >= threshold:
            return {
                "level": name,
                "color": color,
                "advice": advice,
                "score": round(s, 1),
            }
    return {"level": "Normal", "color": "#2dd48e", "advice": LEVELS[-1][3], "score": round(s, 1)}


def score_area(area_ha: Optional[float]) -> tuple[float, str]:
    if area_ha is None:
        return 8.0, "Area unknown — partial score"
    a = float(area_ha)
    if a >= 80:
        return 25.0, f"Very large lake ({a:.1f} ha)"
    if a >= 40:
        return 22.0, f"Large lake ({a:.1f} ha)"
    if a >= 20:
        return 16.0, f"Moderate–large lake ({a:.1f} ha)"
    if a >= 10:
        return 10.0, f"Moderate lake ({a:.1f} ha)"
    if a >= 5:
        return 5.0, f"Small lake ({a:.1f} ha)"
    return 2.0, f"Very small lake ({a:.1f} ha)"


def score_growth(growth_pct_per_year: Optional[float]) -> tuple[float, str]:
    """growth_pct_per_year: percent change per year (positive = growing)."""
    if growth_pct_per_year is None:
        return 10.0, "Growth rate unknown — neutral score"
    g = float(growth_pct_per_year)
    if g >= 40:
        return 25.0, f"Rapid growth ({g:.1f}%/yr)"
    if g >= 20:
        return 20.0, f"Strong growth ({g:.1f}%/yr)"
    if g >= 10:
        return 15.0, f"Notable growth ({g:.1f}%/yr)"
    if g >= 3:
        return 10.0, f"Mild growth ({g:.1f}%/yr)"
    if g > -3:
        return 6.0, f"Stable ({g:.1f}%/yr)"
    if g > -15:
        return 3.0, f"Shrinking ({g:.1f}%/yr)"
    return 1.0, f"Strongly shrinking ({g:.1f}%/yr)"


def score_elevation(elevation_m: Optional[float]) -> tuple[float, str]:
    if elevation_m is None:
        return 6.0, "Elevation unknown — partial score"
    e = float(elevation_m)
    if e >= 5000:
        return 15.0, f"Very high altitude ({e:.0f} m)"
    if e >= 4200:
        return 13.0, f"High glacial belt ({e:.0f} m)"
    if e >= 3500:
        return 10.0, f"Upper valley ({e:.0f} m)"
    if e >= 2800:
        return 7.0, f"Mid elevation ({e:.0f} m)"
    return 3.0, f"Lower elevation ({e:.0f} m)"


def score_glacier_proximity(distance_km: Optional[float]) -> tuple[float, str]:
    if distance_km is None:
        return 6.0, "Glacier distance unknown — optional factor partial"
    d = float(distance_km)
    if d <= 1.0:
        return 15.0, f"Adjacent to glacier ({d:.1f} km)"
    if d <= 3.0:
        return 12.0, f"Very close to glacier ({d:.1f} km)"
    if d <= 6.0:
        return 8.0, f"Near glacier ({d:.1f} km)"
    if d <= 12.0:
        return 4.0, f"Moderate glacier distance ({d:.1f} km)"
    return 1.0, f"Far from mapped glaciers ({d:.1f} km)"


def score_population(danger_pop: Optional[float], warning_pop: Optional[float] = None) -> tuple[float, str]:
    d = float(danger_pop or 0)
    w = float(warning_pop or 0)
    # Weight danger zone more heavily
    exposure = d + 0.35 * max(0.0, w - d)
    if exposure >= 8000:
        return 20.0, f"Very high downstream exposure (~{int(exposure)} people)"
    if exposure >= 3000:
        return 16.0, f"High downstream exposure (~{int(exposure)} people)"
    if exposure >= 1000:
        return 12.0, f"Significant exposure (~{int(exposure)} people)"
    if exposure >= 300:
        return 8.0, f"Moderate exposure (~{int(exposure)} people)"
    if exposure >= 50:
        return 4.0, f"Low exposure (~{int(exposure)} people)"
    return 1.0, "Minimal mapped downstream population"


def compute_early_warning(
    *,
    area_ha: Optional[float] = None,
    growth_pct_per_year: Optional[float] = None,
    elevation_m: Optional[float] = None,
    glacier_distance_km: Optional[float] = None,
    danger_population: Optional[float] = None,
    warning_population: Optional[float] = None,
    include_glacier: bool = True,
) -> dict[str, Any]:
    a_pts, a_note = score_area(area_ha)
    g_pts, g_note = score_growth(growth_pct_per_year)
    e_pts, e_note = score_elevation(elevation_m)
    p_pts, p_note = score_population(danger_population, warning_population)

    indicators = [
        {"id": "area", "label": "Current lake area", "max": 25, "points": a_pts, "detail": a_note},
        {"id": "growth", "label": "Area change (3–5 yr growth rate)", "max": 25, "points": g_pts, "detail": g_note},
        {"id": "elevation", "label": "Lake elevation", "max": 15, "points": e_pts, "detail": e_note},
        {"id": "population", "label": "Downstream population", "max": 20, "points": p_pts, "detail": p_note},
    ]

    total_max = 25 + 25 + 15 + 20  # 85 without glacier
    total = a_pts + g_pts + e_pts + p_pts

    if include_glacier:
        gl_pts, gl_note = score_glacier_proximity(glacier_distance_km)
        indicators.append({
            "id": "glacier",
            "label": "Proximity to glacier",
            "max": 15,
            "points": gl_pts,
            "detail": gl_note,
        })
        total += gl_pts
        total_max += 15

    # Normalize to 0–100 if glacier omitted so levels stay consistent
    if total_max < 100:
        score_100 = (total / total_max) * 100.0
    else:
        score_100 = total

    level = classify_level(score_100)

    return {
        **level,
        "raw_points": round(total, 1),
        "max_points": total_max,
        "indicators": indicators,
        "inputs": {
            "area_ha": area_ha,
            "growth_pct_per_year": growth_pct_per_year,
            "elevation_m": elevation_m,
            "glacier_distance_km": glacier_distance_km,
            "danger_population": danger_population,
            "warning_population": warning_population,
        },
    }
