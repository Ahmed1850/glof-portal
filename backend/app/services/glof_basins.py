"""
GLOF Basin intelligence for Gilgit-Baltistan / Northern Pakistan.

Each basin describes:
  - approximate geographic extent (bbox)
  - main river / drainage
  - downstream valleys and settlements that can store / receive outburst water
  - typical cascade / storage notes
"""

from __future__ import annotations

from typing import Any, Optional


# Bounding boxes: (min_lon, min_lat, max_lon, max_lat)
BASINS: list[dict[str, Any]] = [
    {
        "id": "hunza",
        "name": "Hunza River Basin",
        "river": "Hunza River → Gilgit River → Indus",
        "bbox": (74.40, 36.15, 75.50, 36.75),
        "center": {"lat": 36.40, "lon": 74.75},
        "area_km2_approx": 13700,
        "downstream_storage": [
            {"name": "Hunza valley floor (Aliabad–Karimabad corridor)", "type": "valley_plain", "lat": 36.32, "lon": 74.65},
            {"name": "Attabad / Gojal channel storage", "type": "lake_channel", "lat": 36.33, "lon": 74.87},
            {"name": "Gilgit confluence floodplain", "type": "confluence", "lat": 35.92, "lon": 74.31},
        ],
        "at_risk_settlements": ["Gulmit", "Passu", "Hunza", "Aliabad", "Karimabad", "Gilgit (downstream)"],
        "cascade_notes": "Multiple glacier-dammed lakes (Shisper, Passu, Batura systems). Outburst waves route through Hunza gorge; temporary ponding at constrictions (e.g. Attabad reach).",
        "monitoring_priority": "Critical",
        "color": "#f0433a",
    },
    {
        "id": "nagar_hispar",
        "name": "Nagar–Hispar Basin",
        "river": "Hispar / Hopar streams → Hunza",
        "bbox": (74.55, 36.05, 75.35, 36.35),
        "center": {"lat": 36.20, "lon": 74.85},
        "area_km2_approx": 4200,
        "downstream_storage": [
            {"name": "Hopar–Hispar valley terraces", "type": "valley_plain", "lat": 36.20, "lon": 74.78},
            {"name": "Nagar valley floor", "type": "valley_plain", "lat": 36.27, "lon": 74.72},
            {"name": "Hunza main stem (after confluence)", "type": "main_stem", "lat": 36.30, "lon": 74.68},
        ],
        "at_risk_settlements": ["Hopar", "Hispar", "Nagar", "Sumayar"],
        "cascade_notes": "Ice-dammed lakes near Hispar/Hopar glaciers; flood peaks join Hunza corridor quickly.",
        "monitoring_priority": "Warning",
        "color": "#f5a524",
    },
    {
        "id": "gilgit",
        "name": "Gilgit River Basin",
        "river": "Gilgit River → Indus",
        "bbox": (73.80, 35.70, 74.55, 36.40),
        "center": {"lat": 36.05, "lon": 74.20},
        "area_km2_approx": 9800,
        "downstream_storage": [
            {"name": "Naltar valley floors", "type": "valley_plain", "lat": 36.16, "lon": 74.20},
            {"name": "Gilgit town floodplain", "type": "urban_plain", "lat": 35.92, "lon": 74.31},
            {"name": "Indus junction (Bunji approach)", "type": "confluence", "lat": 35.65, "lon": 74.63},
        ],
        "at_risk_settlements": ["Naltar", "Gilgit", "Danyore", "Juglot"],
        "cascade_notes": "Urban exposure at Gilgit; storage capacity limited in narrow valley reaches.",
        "monitoring_priority": "Watch",
        "color": "#38bdf8",
    },
    {
        "id": "ghizer",
        "name": "Ghizer / Ghizer–Yasin Basin",
        "river": "Ghizer / Gilgit headwaters",
        "bbox": (72.40, 36.00, 74.00, 36.85),
        "center": {"lat": 36.30, "lon": 73.40},
        "area_km2_approx": 12500,
        "downstream_storage": [
            {"name": "Phander–Gupis valley lakes/plains", "type": "valley_plain", "lat": 36.18, "lon": 72.95},
            {"name": "Yasin–Ishkoman corridors", "type": "valley_plain", "lat": 36.50, "lon": 73.50},
            {"name": "Gahkuch basin node", "type": "hub", "lat": 36.17, "lon": 73.77},
        ],
        "at_risk_settlements": ["Phander", "Gupis", "Yasin", "Ishkoman", "Gahkuch"],
        "cascade_notes": "Multiple mid-elevation lakes; flood waves can temporarily pond in wide valley sections before entering Gilgit main stem.",
        "monitoring_priority": "Watch",
        "color": "#5eead4",
    },
    {
        "id": "skardu_indus",
        "name": "Upper Indus–Skardu Basin",
        "river": "Indus / Shigar / Satpara systems",
        "bbox": (74.90, 34.90, 76.20, 35.70),
        "center": {"lat": 35.30, "lon": 75.55},
        "area_km2_approx": 15000,
        "downstream_storage": [
            {"name": "Satpara–Skardu valley storage", "type": "reservoir_valley", "lat": 35.24, "lon": 75.62},
            {"name": "Skardu plain", "type": "urban_plain", "lat": 35.30, "lon": 75.63},
            {"name": "Indus main stem below Skardu", "type": "main_stem", "lat": 35.40, "lon": 75.80},
        ],
        "at_risk_settlements": ["Skardu", "Satpara", "Shigar (edge)", "Kachura"],
        "cascade_notes": "Satpara and plateau lakes; Skardu plain can temporarily store sheet flow; dam/reservoir interactions matter.",
        "monitoring_priority": "Warning",
        "color": "#f5a524",
    },
    {
        "id": "shigar_baltoro",
        "name": "Shigar–Baltoro Basin",
        "river": "Braldu / Shigar → Indus",
        "bbox": (75.80, 35.40, 76.80, 36.00),
        "center": {"lat": 35.70, "lon": 76.30},
        "area_km2_approx": 7200,
        "downstream_storage": [
            {"name": "Braldu gorge temporary ponding", "type": "gorge", "lat": 35.70, "lon": 76.20},
            {"name": "Shigar valley floor", "type": "valley_plain", "lat": 35.45, "lon": 75.75},
            {"name": "Indus at Skardu approach", "type": "confluence", "lat": 35.35, "lon": 75.70},
        ],
        "at_risk_settlements": ["Askole (upstream)", "Shigar", "Skardu (downstream)"],
        "cascade_notes": "High glacier density (Baltoro, Concordia). Extreme peak discharge possible; storage limited in gorges.",
        "monitoring_priority": "Critical",
        "color": "#f0433a",
    },
    {
        "id": "ghanche_hushe",
        "name": "Ghanche–Hushe Basin",
        "river": "Hushe / Saltoro systems → Shyok / Indus",
        "bbox": (76.00, 35.20, 76.70, 35.75),
        "center": {"lat": 35.50, "lon": 76.30},
        "area_km2_approx": 5100,
        "downstream_storage": [
            {"name": "Hushe valley terraces", "type": "valley_plain", "lat": 35.45, "lon": 76.35},
            {"name": "Khaplu approach floodplain", "type": "valley_plain", "lat": 35.16, "lon": 76.33},
        ],
        "at_risk_settlements": ["Hushe", "Machulu", "Khaplu"],
        "cascade_notes": "Masherbrum / Thalle glacier lakes; floods route through narrow Hushe corridor.",
        "monitoring_priority": "Warning",
        "color": "#f5a524",
    },
    {
        "id": "astore",
        "name": "Astore Basin",
        "river": "Astore River → Indus",
        "bbox": (74.50, 35.00, 75.20, 35.55),
        "center": {"lat": 35.30, "lon": 74.85},
        "area_km2_approx": 4500,
        "downstream_storage": [
            {"name": "Rama–Astore valley floors", "type": "valley_plain", "lat": 35.35, "lon": 74.80},
            {"name": "Astore town corridor", "type": "urban_plain", "lat": 35.37, "lon": 74.86},
            {"name": "Indus confluence (near Bunji)", "type": "confluence", "lat": 35.65, "lon": 74.65},
        ],
        "at_risk_settlements": ["Rama", "Astore", "Gorikot"],
        "cascade_notes": "Alpine lakes (Rama, Mirror); moderate storage on terraces before Indus.",
        "monitoring_priority": "Watch",
        "color": "#38bdf8",
    },
    {
        "id": "chitral",
        "name": "Chitral / Mastuj Basin",
        "river": "Chitral / Kunar headwaters",
        "bbox": (71.50, 35.60, 73.20, 36.70),
        "center": {"lat": 36.10, "lon": 72.20},
        "area_km2_approx": 11000,
        "downstream_storage": [
            {"name": "Mastuj–Chitral valley floor", "type": "valley_plain", "lat": 36.00, "lon": 72.20},
            {"name": "Chitral town floodplain", "type": "urban_plain", "lat": 35.85, "lon": 71.79},
        ],
        "at_risk_settlements": ["Mastuj", "Chitral", "Booni"],
        "cascade_notes": "Tirich / Buni Zom glacial systems; floods can attenuate slightly in wider Chitral valley reaches.",
        "monitoring_priority": "Watch",
        "color": "#5eead4",
    },
    {
        "id": "shigar_unknown",
        "name": "Transboundary / Unassigned High Karakoram",
        "river": "Mixed Indus headwaters",
        "bbox": (73.00, 34.80, 77.00, 37.20),
        "center": {"lat": 35.90, "lon": 75.00},
        "area_km2_approx": None,
        "downstream_storage": [
            {"name": "Nearest Indus main-stem reach", "type": "main_stem", "lat": 35.70, "lon": 74.80},
        ],
        "at_risk_settlements": ["Varies by sub-catchment"],
        "cascade_notes": "Fallback basin when lake falls outside named catchments.",
        "monitoring_priority": "Normal",
        "color": "#8ea3ba",
        "is_fallback": True,
    },
]


def point_in_bbox(lat: float, lon: float, bbox: tuple) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def assign_basin(lat: Optional[float], lon: Optional[float]) -> dict:
    if lat is None or lon is None:
        return next(b for b in BASINS if b.get("is_fallback"))
    # Prefer non-fallback basins first
    for basin in BASINS:
        if basin.get("is_fallback"):
            continue
        if point_in_bbox(lat, lon, basin["bbox"]):
            return basin
    return next(b for b in BASINS if b.get("is_fallback"))


def estimate_outburst_volume_m3(area_ha: Optional[float], mean_depth_m: float = 12.0) -> Optional[float]:
    """Heuristic volume = area × assumed mean depth (not a bathymetric survey)."""
    if area_ha is None or area_ha <= 0:
        return None
    area_m2 = float(area_ha) * 10000.0
    return round(area_m2 * mean_depth_m, 0)


def estimate_flood_footprint(
    area_ha: Optional[float],
    danger_km: float,
    warning_km: float,
) -> dict:
    """
    Simplified circular footprint for communication (not 2D hydraulic inundation).
    Affected area ≈ π r² for danger / warning radii scaled by lake size.
    """
    import math

    size_factor = 1.0
    if area_ha is not None:
        if area_ha >= 80:
            size_factor = 1.35
        elif area_ha >= 40:
            size_factor = 1.2
        elif area_ha >= 15:
            size_factor = 1.05

    d_r = danger_km * size_factor
    w_r = warning_km * size_factor
    danger_area_km2 = round(math.pi * (d_r ** 2), 2)
    warning_area_km2 = round(math.pi * (w_r ** 2), 2)
    return {
        "danger_radius_km": round(d_r, 2),
        "warning_radius_km": round(w_r, 2),
        "danger_area_km2": danger_area_km2,
        "warning_area_km2": warning_area_km2,
        "model": "simplified circular footprint (planning-grade, not hydraulic)",
    }


def basin_summary_for_lakes(lakes: list[dict]) -> list[dict]:
    """Aggregate lake early-warning results by basin."""
    buckets: dict[str, dict] = {}
    for lake in lakes:
        lat, lon = lake.get("latitude"), lake.get("longitude")
        basin = assign_basin(lat, lon)
        bid = basin["id"]
        if bid not in buckets:
            buckets[bid] = {
                "basin": {k: basin[k] for k in basin if k != "bbox"},
                "bbox": basin["bbox"],
                "lake_count": 0,
                "lakes": [],
                "level_counts": {"Critical": 0, "Warning": 0, "Watch": 0, "Normal": 0},
                "total_area_ha": 0.0,
                "total_estimated_volume_m3": 0.0,
                "max_score": 0.0,
                "people_danger": 0,
                "people_warning": 0,
            }
        b = buckets[bid]
        b["lake_count"] += 1
        b["lakes"].append({
            "lake_id": lake.get("lake_id"),
            "name": lake.get("name"),
            "level": (lake.get("early_warning") or {}).get("level"),
            "score": (lake.get("early_warning") or {}).get("score"),
            "area_ha": lake.get("area_ha"),
        })
        lvl = (lake.get("early_warning") or {}).get("level") or "Normal"
        if lvl in b["level_counts"]:
            b["level_counts"][lvl] += 1
        area = lake.get("area_ha") or 0
        b["total_area_ha"] += float(area)
        vol = estimate_outburst_volume_m3(area)
        if vol:
            b["total_estimated_volume_m3"] += vol
        score = float((lake.get("early_warning") or {}).get("score") or 0)
        b["max_score"] = max(b["max_score"], score)
        impact = lake.get("flood_impact") or {}
        b["people_danger"] += int(impact.get("danger_population") or 0)
        b["people_warning"] += int(impact.get("warning_population") or 0)

    out = list(buckets.values())
    order = {"Critical": 0, "Warning": 1, "Watch": 2, "Normal": 3}

    def basin_rank(item):
        # worst lake level in basin
        for name in ("Critical", "Warning", "Watch", "Normal"):
            if item["level_counts"].get(name, 0) > 0:
                return (order[name], -item["max_score"])
        return (9, 0)

    out.sort(key=basin_rank)
    return out
