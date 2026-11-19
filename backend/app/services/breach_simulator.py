"""
GLOF Dam-Breach & 1D Kinematic Hydraulic Routing Simulator.

Implements an ensemble of three empirical/semi-theoretical glacial dam breach
peak-discharge equations:
  - Froehlich (2008)
  - Costa (1985)
  - Walder & O'Connor (1997)

Generates a 0–240 min outflow hydrograph Q(t) and routes the flood wave across
basin downstream_storage nodes using cached Open-Meteo elevation profiles
(with deterministic offline basin gradient fallback), valley attenuation,
Manning stage rise, and kinematic wave celerity.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Optional

from fastapi import HTTPException

from app.services.elevation_service import fetch_elevations_batch
from app.services.geo_helpers import haversine_km
from app.services.glof_basins import assign_basin

_DAM_ERODIBILITY = {
    "moraine": 1.0,
    "ice": 0.85,
    "landslide": 1.15,
}

_ATTENUATION_BY_TYPE = {
    "gorge": 0.007,
    "lake_channel": 0.008,
    "main_stem": 0.010,
    "confluence": 0.011,
    "hub": 0.012,
    "valley_plain": 0.014,
    "reservoir_valley": 0.015,
    "urban_plain": 0.014,
}

_WIDTH_BY_TYPE = {
    "gorge": 35.0,
    "lake_channel": 45.0,
    "main_stem": 70.0,
    "confluence": 85.0,
    "valley_plain": 65.0,
    "urban_plain": 80.0,
    "reservoir_valley": 90.0,
    "hub": 60.0,
}

_HEADWATER_ALT_BY_BASIN = {
    "hunza": 3650.0,
    "nagar_hispar": 3950.0,
    "gilgit": 3700.0,
    "ghizer": 3850.0,
    "skardu_indus": 3900.0,
    "shigar_baltoro": 4250.0,
    "ghanche_hushe": 4100.0,
    "astore": 3800.0,
    "chitral": 3750.0,
    "shigar_unknown": 3950.0,
}


def simulate_breach(
    lake: Any,
    *,
    breach_fraction: float = 0.75,
    dam_type: Literal["moraine", "ice", "landslide"] = "moraine",
    breach_duration_min: int = 45,
    mean_depth_m: Optional[float] = None,
) -> dict[str, Any]:
    if lake.latitude is None or lake.longitude is None:
        raise HTTPException(status_code=400, detail="Lake has no spatial coordinates")
    if lake.area_ha is None or float(lake.area_ha) <= 0:
        raise HTTPException(status_code=400, detail="Lake area_ha must be positive for breach simulation")

    lat = float(lake.latitude)
    lon = float(lake.longitude)
    area_ha = float(lake.area_ha)
    area_m2 = area_ha * 10_000.0

    # Huggel et al. (2002) / Evans depth-area scaling unless overridden
    depth_m = float(mean_depth_m) if mean_depth_m is not None else max(4.0, min(85.0, 0.104 * (area_m2 ** 0.42)))
    total_volume_m3 = area_m2 * depth_m
    volume_m3 = round(total_volume_m3 * breach_fraction, 1)

    hw = max(2.0, depth_m * (0.45 + 0.65 * breach_fraction))
    k_dam = _DAM_ERODIBILITY.get(dam_type, 1.0)

    # 1. Froehlich (2008)
    froehlich_m3s = 0.607 * (volume_m3 ** 0.295) * (hw ** 1.24) * k_dam

    # 2. Costa (1985)
    pe_joules = 1000.0 * 9.81 * volume_m3 * hw
    costa_m3s = 0.0158 * (pe_joules ** 0.41) * k_dam

    # 3. Walder & O'Connor (1997)
    k_erosion = (hw / (float(breach_duration_min) * 60.0)) * k_dam
    eta = (k_erosion * volume_m3) / ((9.81 ** 0.5) * (hw ** 3.5))
    qp_star = (1.65 * eta) / (1.0 + 0.85 * eta)
    walder_oconnor_m3s = qp_star * (9.81 ** 0.5) * (hw ** 2.5)

    ensemble_m3s = round((froehlich_m3s + costa_m3s + walder_oconnor_m3s) / 3.0, 1)

    # Outflow hydrograph Q(t) over 0..240 min (49 points at 5-min intervals)
    t_peak = max(5, min(210, int(round((0.55 * breach_duration_min) / 5.0) * 5)))
    v_rise = 0.55 * ensemble_m3s * (t_peak * 60.0)
    v_fall = max(0.25 * volume_m3, volume_m3 - v_rise)
    tau_rec = max(10.0, v_fall / (max(ensemble_m3s, 1.0) * 60.0))

    hydrograph: list[dict[str, Any]] = []
    for t in range(0, 245, 5):
        if t == 0:
            q = 0.0
        elif t <= t_peak:
            q = ensemble_m3s * (math.sin((math.pi / 2.0) * (t / float(t_peak))) ** 1.5)
        else:
            q = ensemble_m3s * math.exp(-(t - t_peak) / tau_rec)
        hydrograph.append({"time_min": t, "discharge_m3s": round(q, 1)})

    # Route geometry & elevation profile
    basin = assign_basin(lat, lon)
    storage_nodes = list(basin.get("downstream_storage") or [])
    route_points: list[tuple[float, float, str, str]] = [(lat, lon, lake.name, "lake_origin")]
    for node in storage_nodes:
        route_points.append((float(node["lat"]), float(node["lon"]), node["name"], node.get("type", "valley_plain")))

    cum_dists_km: list[float] = [0.0]
    seg_dists_km: list[float] = [0.0]
    cum_km = 0.0
    for idx in range(1, len(route_points)):
        prev_lat, prev_lon = route_points[idx - 1][0], route_points[idx - 1][1]
        cur_lat, cur_lon = route_points[idx][0], route_points[idx][1]
        seg = haversine_km(prev_lat, prev_lon, cur_lat, cur_lon)
        cum_km += seg
        seg_dists_km.append(seg)
        cum_dists_km.append(cum_km)

    elevations_raw, elev_source = fetch_elevations_batch([(p[0], p[1]) for p in route_points])
    has_missing = any(v is None for v in elevations_raw)
    fallback_used = has_missing or ("stale" in elev_source.lower()) or ("offline" in elev_source.lower())

    resolved_elevs: list[float] = []
    if has_missing:
        head_alt = _HEADWATER_ALT_BY_BASIN.get(basin["id"], 3950.0)
        for idx, raw_e in enumerate(elevations_raw):
            if raw_e is not None:
                resolved_elevs.append(float(raw_e))
            else:
                resolved_elevs.append(max(1200.0, head_alt - cum_dists_km[idx] * 42.0))
        if all(v is None for v in elevations_raw):
            elev_source = "offline_basin_gradient"
        else:
            elev_source = f"{elev_source} + offline_basin_gradient"
    else:
        resolved_elevs = [float(v) for v in elevations_raw]  # type: ignore[arg-type]

    elevation_profile: list[dict[str, Any]] = []
    for idx, (_, _, label, _) in enumerate(route_points):
        elevation_profile.append({
            "distance_km": round(cum_dists_km[idx], 2),
            "elevation_m": round(resolved_elevs[idx], 1),
            "label": label,
        })

    # Hydraulic routing across downstream storage nodes
    downstream_nodes: list[dict[str, Any]] = []
    cum_travel_min = 0.0
    manning_n = 0.045

    for idx in range(1, len(route_points)):
        _, _, node_name, node_type = route_points[idx]
        seg_km = seg_dists_km[idx]
        node_cum_km = cum_dists_km[idx]
        prev_elev = resolved_elevs[idx - 1]
        curr_elev = resolved_elevs[idx]

        slope_i = max(0.008, min(0.22, (prev_elev - curr_elev) / max(seg_km * 1000.0, 500.0)))
        k_atten = _ATTENUATION_BY_TYPE.get(node_type, 0.011)
        width_i = _WIDTH_BY_TYPE.get(node_type, 55.0)

        attenuated_q = ensemble_m3s * math.exp(-k_atten * node_cum_km)
        stage_rise_m = ((max(attenuated_q, 0.1) * manning_n) / (width_i * (slope_i ** 0.5))) ** 0.6
        v_manning = (1.0 / manning_n) * (stage_rise_m ** (2.0 / 3.0)) * (slope_i ** 0.5)
        celerity_ms = max(2.0, min(12.0, (5.0 / 3.0) * v_manning))
        seg_travel_min = (seg_km * 1000.0) / (celerity_ms * 60.0)
        cum_travel_min += seg_travel_min

        eta_min = round(0.25 * breach_duration_min + cum_travel_min, 1)
        recession_min = round(eta_min + max(30.0, tau_rec * (1.4 + 0.012 * node_cum_km)), 1)

        if stage_rise_m >= 5.0 or attenuated_q >= 2500.0:
            severity = "Critical"
        elif stage_rise_m >= 2.5 or attenuated_q >= 900.0:
            severity = "Warning"
        elif stage_rise_m >= 1.0 or attenuated_q >= 250.0:
            severity = "Watch"
        else:
            severity = "Low"

        downstream_nodes.append({
            "name": node_name,
            "distance_km": round(node_cum_km, 2),
            "elevation_m": round(curr_elev, 1),
            "eta_min": eta_min,
            "recession_min": recession_min,
            "attenuated_q_m3s": round(attenuated_q, 1),
            "stage_rise_m": round(stage_rise_m, 2),
            "severity": severity,
        })

    return {
        "lake_id": lake.id,
        "lake_name": lake.name,
        "basin_id": basin["id"],
        "basin_name": basin["name"],
        "params": {
            "breach_fraction": round(float(breach_fraction), 3),
            "dam_type": dam_type,
            "breach_duration_min": int(breach_duration_min),
            "mean_depth_m": round(depth_m, 1),
        },
        "volume_m3": volume_m3,
        "peak_discharge": {
            "froehlich_m3s": round(froehlich_m3s, 1),
            "costa_m3s": round(costa_m3s, 1),
            "walder_oconnor_m3s": round(walder_oconnor_m3s, 1),
            "ensemble_m3s": ensemble_m3s,
        },
        "elevation_profile": elevation_profile,
        "hydrograph": hydrograph,
        "downstream_nodes": downstream_nodes,
        "provenance": {
            "elevation_source": elev_source,
            "fallback_used": fallback_used,
        },
    }
