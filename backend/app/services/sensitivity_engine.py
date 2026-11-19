"""
Vectorized NumPy Monte Carlo Sensitivity & SSP Climate Scenario Engine.
Compatible with NumPy 1.26.x and 2.x on Python 3.12+.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.models.lake import Lake as LakeModel
from app.services.elevation_service import get_cached_elevation
from app.services.geo_helpers import nearest_glacier_km, population_heuristic


def _safe_r2(x: np.ndarray, y: np.ndarray) -> float:
    x_std = float(np.std(x))
    y_std = float(np.std(y))
    if x_std <= 1e-12 or y_std <= 1e-12:
        return 0.0
    xc = x - float(np.mean(x))
    yc = y - float(np.mean(y))
    corr = float(np.mean(xc * yc)) / (x_std * y_std)
    return max(0.0, min(1.0, corr * corr))


def _pct_summary(arr: np.ndarray, *, decimals: int = 1) -> dict[str, float]:
    p10, p50, p90 = np.percentile(arr, [10.0, 50.0, 90.0])
    return {
        "p10": round(float(p10), decimals),
        "p50": round(float(p50), decimals),
        "p90": round(float(p90), decimals),
    }


def _simulate_pass(
    *,
    lake_id: int,
    base_area_ha: float,
    base_glacier_km: float,
    base_danger_pop: float,
    base_warning_pop: float,
    base_elev_m: float,
    base_growth_pct: float,
    delta_c: float,
    n_samples: int,
    seed_offset: int,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed=42 + int(lake_id) + int(seed_offset))

    area_ha_s = np.clip(rng.normal(base_area_ha, base_area_ha * 0.12, n_samples), 0.5, 5000.0)
    depth_m_s = np.clip(rng.normal(12.0, 12.0 * 0.25, n_samples), 4.0, 38.0)
    breach_frac_s = np.clip(rng.normal(0.75, 0.15, n_samples), 0.30, 1.0)
    temp_anom_s = rng.normal(float(delta_c), 0.75, n_samples)
    growth_s = np.clip(rng.normal(base_growth_pct + temp_anom_s * 3.5, 6.5, n_samples), -10.0, 65.0)
    elev_eff_s = np.clip(rng.normal(base_elev_m, 140.0, n_samples) + temp_anom_s * 150.0, 1500.0, 6500.0)
    glacier_km_s = np.clip(rng.normal(base_glacier_km, 0.65, n_samples), 0.1, 50.0)
    pop_mult_s = np.clip(rng.normal(1.0, 0.25, n_samples), 0.4, 2.2)

    danger_pop_s = base_danger_pop * pop_mult_s
    warning_pop_s = base_warning_pop * pop_mult_s

    vol_m3_s = area_ha_s * 10000.0 * depth_m_s * breach_frac_s
    qp_m3s_s = 0.0048 * np.power(np.maximum(vol_m3_s, 1.0), 0.89)

    pts_area = np.clip(2.0 + 23.0 * np.log1p(area_ha_s) / np.log1p(80.0), 2.0, 25.0)
    pts_growth = np.clip(6.0 + (growth_s / 40.0) * 19.0, 1.0, 25.0)
    pts_elev = np.clip(3.0 + ((elev_eff_s - 2500.0) / 2500.0) * 12.0, 3.0, 15.0)
    pts_glacier = np.clip(15.0 - glacier_km_s * 1.15, 1.0, 15.0)
    exposure_s = danger_pop_s + 0.35 * np.maximum(0.0, warning_pop_s - danger_pop_s)
    pts_pop = np.clip(1.0 + 19.0 * np.log1p(exposure_s) / np.log1p(8000.0), 1.0, 20.0)
    melt_shift = np.clip(temp_anom_s * 2.8, -4.0, 16.0)

    ew_raw_s = pts_area + pts_growth + pts_elev + pts_glacier + pts_pop + melt_shift
    ew_score_s = np.clip(ew_raw_s, 0.0, 100.0)

    return {
        "area_ha_s": area_ha_s,
        "depth_m_s": depth_m_s,
        "breach_frac_s": breach_frac_s,
        "growth_s": growth_s,
        "temp_anom_s": temp_anom_s,
        "pop_mult_s": pop_mult_s,
        "glacier_km_s": glacier_km_s,
        "vol_m3_s": vol_m3_s,
        "qp_m3s_s": qp_m3s_s,
        "ew_raw_s": ew_raw_s,
        "ew_score_s": ew_score_s,
    }


def run_monte_carlo_sensitivity(
    lake: LakeModel,
    *,
    temp_delta_c: float = 0.0,
    n_samples: int = 500,
) -> dict[str, Any]:
    base_area_ha = max(0.5, float(lake.area_ha or 10.0))
    has_coords = lake.latitude is not None and lake.longitude is not None
    base_glacier_km = nearest_glacier_km(lake.latitude, lake.longitude) if has_coords else 8.0
    pop = (
        population_heuristic(lake.latitude, lake.longitude, base_area_ha)
        if has_coords
        else {"danger_population": 50, "warning_population": 150}
    )
    cached_elev = get_cached_elevation(lake.latitude, lake.longitude) if has_coords else None
    base_elev_m = float(cached_elev) if cached_elev is not None else 3900.0
    base_growth_pct = max(2.0, min(32.0, 4.0 + base_area_ha * 0.18))

    main_pass = _simulate_pass(
        lake_id=int(lake.id or 0),
        base_area_ha=base_area_ha,
        base_glacier_km=base_glacier_km,
        base_danger_pop=float(pop["danger_population"]),
        base_warning_pop=float(pop["warning_population"]),
        base_elev_m=base_elev_m,
        base_growth_pct=base_growth_pct,
        delta_c=temp_delta_c,
        n_samples=n_samples,
        seed_offset=0,
    )

    ew_score_s = main_pass["ew_score_s"]
    qp_m3s_s = main_pass["qp_m3s_s"]

    # Composite breach-risk metric so hydraulic depth/breach parameters register meaningful sensitivity
    composite_metric = 0.7 * main_pass["ew_raw_s"] + 30.0 * (np.log1p(qp_m3s_s) / np.log1p(25000.0))

    raw_drivers = [
        ("Lake Area Uncertainty", _safe_r2(main_pass["area_ha_s"], composite_metric)),
        ("Dam Depth Uncertainty", _safe_r2(main_pass["depth_m_s"], composite_metric)),
        ("Breach Fraction", _safe_r2(main_pass["breach_frac_s"], composite_metric)),
        ("Expansion / Growth Rate", _safe_r2(main_pass["growth_s"], composite_metric)),
        ("Climate / Melt Anomaly", _safe_r2(main_pass["temp_anom_s"], composite_metric)),
        ("Downstream Exposure", _safe_r2(main_pass["pop_mult_s"], composite_metric)),
        ("Glacier Proximity", _safe_r2(main_pass["glacier_km_s"], composite_metric)),
    ]

    total_r2 = sum(v for _, v in raw_drivers)
    tornado_drivers: list[dict[str, Any]] = []
    if total_r2 <= 1e-12:
        even = round(100.0 / len(raw_drivers), 1)
        tornado_drivers = [{"factor": name, "impact_pct": even} for name, _ in raw_drivers]
    else:
        for name, val in raw_drivers:
            tornado_drivers.append({"factor": name, "impact_pct": round((val / total_r2) * 100.0, 1)})
    tornado_drivers.sort(key=lambda d: d["impact_pct"], reverse=True)
    # Adjust largest driver so weights sum cleanly to 100.0
    delta_sum = round(100.0 - sum(d["impact_pct"] for d in tornado_drivers), 1)
    if tornado_drivers and abs(delta_sum) > 0:
        tornado_drivers[0]["impact_pct"] = round(tornado_drivers[0]["impact_pct"] + delta_sum, 1)

    scenarios_spec = [
        ("Baseline (+0.0°C)", 0.0, 100),
        ("SSP1-2.6 (+1.5°C)", 1.5, 200),
        ("SSP3-7.0 (+3.0°C)", 3.0, 300),
    ]
    scenarios: list[dict[str, Any]] = []
    for label, d_c, offset in scenarios_spec:
        sp = _simulate_pass(
            lake_id=int(lake.id or 0),
            base_area_ha=base_area_ha,
            base_glacier_km=base_glacier_km,
            base_danger_pop=float(pop["danger_population"]),
            base_warning_pop=float(pop["warning_population"]),
            base_elev_m=base_elev_m,
            base_growth_pct=base_growth_pct,
            delta_c=d_c,
            n_samples=n_samples,
            seed_offset=offset,
        )
        sc_ew = sp["ew_score_s"]
        scenarios.append({
            "label": label,
            "temp_delta_c": d_c,
            "p50_score": round(float(np.percentile(sc_ew, 50.0)), 1),
            "critical_prob": round(float(np.mean(sc_ew >= 75.0)), 4),
        })

    return {
        "lake_id": lake.id,
        "lake_name": lake.name,
        "temp_delta_c": round(float(temp_delta_c), 2),
        "n_samples": int(n_samples),
        "volume_m3": _pct_summary(main_pass["vol_m3_s"], decimals=1),
        "peak_discharge_m3s": _pct_summary(qp_m3s_s, decimals=1),
        "ew_score": _pct_summary(ew_score_s, decimals=1),
        "exceedance_prob": {
            "warning_ge_50": round(float(np.mean(ew_score_s >= 50.0)), 4),
            "critical_ge_75": round(float(np.mean(ew_score_s >= 75.0)), 4),
        },
        "tornado_drivers": tornado_drivers,
        "scenarios": scenarios,
    }
