"""Pure-Python geo analytics computed from the embedded reference dataset.

No PostGIS, no network. All outputs are deterministic functions of the input
coordinates so the same plot always yields the same analysis. Distances use the
haversine formula; protected-zone membership uses shapely point-in-polygon.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from shapely.geometry import Point, shape

from app.reference.geodata import (
    CITIES,
    POWER_LINES,
    PROTECTED_ZONES,
    RAILWAYS,
    RIVERS,
    ROADS,
    City,
    PolyLine,
    ProtectedZone,
    haversine_km,
    point_to_segment_km,
)


# Thresholds (km) used to derive boolean availability heuristics.
ROAD_ACCESS_THRESHOLD_KM = 5.0
ELECTRICITY_THRESHOLD_KM = 40.0
GAS_THRESHOLD_KM = 35.0
WATER_THRESHOLD_KM = 30.0


def nearest_city(lat: float, lng: float) -> tuple[City, float]:
    best = min(CITIES, key=lambda c: haversine_km(lat, lng, c.lat, c.lng))
    return best, haversine_km(lat, lng, best.lat, best.lng)


def nearest_line_km(lat: float, lng: float, lines: list[PolyLine]) -> tuple[PolyLine | None, float]:
    best_line: PolyLine | None = None
    best_dist = math.inf
    for line in lines:
        verts = line.vertices
        if len(verts) == 1:
            dist = haversine_km(lat, lng, verts[0][0], verts[0][1])
        else:
            dist = min(
                point_to_segment_km(lat, lng, verts[i], verts[i + 1])
                for i in range(len(verts) - 1)
            )
        if dist < best_dist:
            best_dist = dist
            best_line = line
    return best_line, (best_dist if best_line is not None else 0.0)


def zones_containing_point(lat: float, lng: float) -> list[ProtectedZone]:
    point = Point(lng, lat)
    inside: list[ProtectedZone] = []
    for zone in PROTECTED_ZONES:
        polygon = shape(zone.geometry)
        if polygon.contains(point) or polygon.touches(point):
            inside.append(zone)
    return inside


def nearest_zone_km(lat: float, lng: float) -> tuple[ProtectedZone | None, float]:
    point = Point(lng, lat)
    best_zone: ProtectedZone | None = None
    best_dist = math.inf
    for zone in PROTECTED_ZONES:
        polygon = shape(zone.geometry)
        # Degree distance from shapely, converted to km at this latitude.
        deg = polygon.exterior.distance(point) if not polygon.contains(point) else 0.0
        km = deg * 111.32 * max(0.1, math.cos(math.radians(lat)))
        if km < best_dist:
            best_dist = km
            best_zone = zone
    return best_zone, (best_dist if best_zone is not None else math.inf)


def _accessibility_score(
    city_km: float,
    road_km: float,
    railway_km: float,
    has_road_access: bool,
    utilities: int,
    restricted: bool,
) -> int:
    """Score 0-100. Closer to infrastructure and utilities -> higher."""

    score = 100.0
    score -= min(40.0, city_km * 0.6)
    score -= min(25.0, road_km * 3.0)
    score -= min(10.0, railway_km * 0.4)
    if not has_road_access:
        score -= 15.0
    score -= (3 - utilities) * 5.0
    if restricted:
        score -= 12.0
    return int(max(0.0, min(100.0, round(score))))


def analyze_location(lat: float, lng: float, cadastral_number: str = "") -> dict[str, Any]:
    city, city_km = nearest_city(lat, lng)
    road, road_km = nearest_line_km(lat, lng, ROADS)
    railway, railway_km = nearest_line_km(lat, lng, RAILWAYS)
    river, river_km = nearest_line_km(lat, lng, RIVERS)
    power, power_km = nearest_line_km(lat, lng, POWER_LINES)

    has_road_access = road_km <= ROAD_ACCESS_THRESHOLD_KM
    # Utilities are more likely near larger / closer settlements.
    settlement_factor = 1.0 if city.population >= 1_000_000 else 1.4
    has_electricity = (city_km <= ELECTRICITY_THRESHOLD_KM * settlement_factor) or (power_km <= 3.0)
    has_gas = city_km <= GAS_THRESHOLD_KM * (0.9 if city.population >= 1_000_000 else 1.2)
    has_water = (city_km <= WATER_THRESHOLD_KM) or (river_km <= 2.0)

    inside_zones = zones_containing_point(lat, lng)
    restricted = bool(inside_zones)
    protected_zone_names = [z.name for z in inside_zones]

    utilities_count = int(has_electricity) + int(has_gas) + int(has_water)
    score = _accessibility_score(city_km, road_km, railway_km, has_road_access, utilities_count, restricted)

    # Locality: use the city name when very close, otherwise mark as a rural
    # locality oriented relative to the nearest city.
    if city_km <= 8.0:
        locality = city.name
    else:
        locality = f"Сельская местность (≈{round(city_km)} км от г. {city.name})"

    raw = {
        # Эти значения — эвристика на основе встроенного справочника (близость
        # к городам / рекам / ЛЭП), а не данные ЕГРН или сетевых организаций.
        # Маркеры ниже сообщают потребителям, что наличие сетей и расстояния до
        # них носят оценочный характер и требуют подтверждения у ресурсоснабжающих
        # организаций.
        "synthetic": True,
        "method": "reference_heuristic",
        "note": (
            "Наличие инженерных сетей (электричество, газ, вода) и связанные "
            "расстояния — эвристическая оценка по близости к справочным объектам, "
            "а не подтверждённые факты. Требуется проверка у ресурсоснабжающих "
            "организаций и в ЕГРН."
        ),
        "input": {"lat": lat, "lng": lng, "cadastral_number": cadastral_number},
        "nearest_city": {"name": city.name, "distance_km": round(city_km, 2), "population": city.population},
        "nearest_road": {"name": road.name if road else None, "distance_km": round(road_km, 2)},
        "nearest_railway": {"name": railway.name if railway else None, "distance_km": round(railway_km, 2)},
        "nearest_river": {"name": river.name if river else None, "distance_km": round(river_km, 2)},
        "nearest_power_line": {"name": power.name if power else None, "distance_km": round(power_km, 2)},
        "utilities": {
            "electricity": has_electricity,
            "gas": has_gas,
            "water": has_water,
            "available_count": utilities_count,
            # Логическое наличие сетей выведено эвристически из расстояний выше.
            "synthetic": True,
            "method": "reference_heuristic",
        },
        "protected_zones": [
            {"name": z.name, "zone_type": z.zone_type, "restrictions": z.restrictions}
            for z in inside_zones
        ],
        "accessibility_score": score,
        "thresholds_km": {
            "road_access": ROAD_ACCESS_THRESHOLD_KM,
            "electricity": ELECTRICITY_THRESHOLD_KM,
            "gas": GAS_THRESHOLD_KM,
            "water": WATER_THRESHOLD_KM,
        },
    }

    return {
        "distance_to_city_km": round(city_km, 2),
        "distance_to_road_km": round(road_km, 2),
        "distance_to_railway_km": round(railway_km, 2),
        "has_road_access": has_road_access,
        "has_electricity": has_electricity,
        "has_gas": has_gas,
        "has_water": has_water,
        "protected_zones": protected_zone_names,
        "region": city.region,
        "district": city.district,
        "locality": locality,
        "accessibility_score": score,
        "raw": raw,
    }


def compute_distances(lat: float, lng: float, poi_types: list[str]) -> dict[str, float]:
    """Distance (km) to each requested POI type backed by the reference dataset.

    Only POI types for which we have a genuine reference source are returned.
    Surface water bodies (``water``) are answered from the rivers dataset.
    POI types without any reference source (e.g. ``gas_pipe``) are **omitted**
    from the result map — they are never fabricated. Callers must treat a
    missing key as "unknown", not as zero distance.
    """

    line_sources: dict[str, list[PolyLine]] = {
        "road": ROADS,
        "railway": RAILWAYS,
        "river": RIVERS,
        "power_line": POWER_LINES,
    }
    # Common aliases (Russian + EN) mapped to canonical types we can answer.
    # ``water`` is treated as the nearest surface water body (river).
    aliases = {
        "highway": "road",
        "дорога": "road",
        "автодорога": "road",
        "ж/д": "railway",
        "железная_дорога": "railway",
        "река": "river",
        "water": "river",
        "вода": "river",
        "водоем": "river",
        "лэп": "power_line",
        "powerline": "power_line",
        "электросети": "power_line",
        "город": "city",
        "населенный_пункт": "city",
    }

    result: dict[str, float] = {}
    for raw_type in poi_types:
        key = (raw_type or "").strip()
        canonical = aliases.get(key.lower(), key.lower())
        if canonical == "city":
            _, dist = nearest_city(lat, lng)
        elif canonical in line_sources:
            _, dist = nearest_line_km(lat, lng, line_sources[canonical])
        else:
            # No reference source for this POI type (e.g. gas pipelines): omit
            # it rather than fabricate a value. Downstream sees a missing key.
            continue
        result[raw_type] = round(dist, 2)
    return result


def check_protected_zones(lat: float, lng: float) -> dict[str, Any]:
    inside = zones_containing_point(lat, lng)
    nearest_zone, nearest_km = nearest_zone_km(lat, lng)
    zone_types = sorted({z.zone_type for z in inside})
    details = {
        "is_restricted": bool(inside),
        "matched_zones": [
            {
                "name": z.name,
                "zone_type": z.zone_type,
                "restrictions": z.restrictions,
                "normative_basis": z.normative_basis,
            }
            for z in inside
        ],
        "nearest_zone": (
            {
                "name": nearest_zone.name,
                "zone_type": nearest_zone.zone_type,
                "distance_km": round(nearest_km, 2),
            }
            if nearest_zone is not None and math.isfinite(nearest_km)
            else None
        ),
    }
    return {
        "zone_types": zone_types,
        "is_restricted": bool(inside),
        "details": details,
    }


def synthesize_plots(
    center_lat: float,
    center_lng: float,
    radius_km: float,
    region: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Deterministically scatter candidate plots within ``radius_km``.

    Uses a hash seed derived from the center coordinates so repeated calls with
    the same inputs always produce identical candidates.
    """

    if limit <= 0:
        limit = 10
    limit = min(limit, 100)
    radius = radius_km if radius_km and radius_km > 0 else 10.0

    region_code = nearest_city(center_lat, center_lng)[0]
    # A pseudo-cadastral district code derived from the nearest city.
    base_code = (abs(hash(region_code.name)) % 90 + 10)

    km_per_deg_lat = 111.32
    km_per_deg_lng = 111.32 * max(0.1, math.cos(math.radians(center_lat)))

    plots: list[dict[str, Any]] = []
    for i in range(limit):
        seed = f"{round(center_lat, 5)}|{round(center_lng, 5)}|{round(radius, 3)}|{i}"
        digest = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
        # Uniform-ish scatter within the disk.
        angle = (digest % 36000) / 36000.0 * 2 * math.pi
        r_frac = math.sqrt(((digest >> 16) % 100000) / 100000.0)
        dist_km = r_frac * radius
        d_lat = (dist_km * math.cos(angle)) / km_per_deg_lat
        d_lng = (dist_km * math.sin(angle)) / km_per_deg_lng
        plat = round(center_lat + d_lat, 6)
        plng = round(center_lng + d_lng, 6)
        # Area between ~0.05 ha (500 m2) and ~5 ha (50000 m2), in sqm.
        area = round(500.0 + ((digest >> 32) % 49500), 1)
        block = (digest >> 48) % 100
        parcel = (digest >> 24) % 10000
        cadastral = f"{base_code}:{block:02d}:{(digest % 1000000):06d}:{parcel}"
        plots.append(
            {
                "cadastral_number": cadastral,
                "lat": plat,
                "lng": plng,
                "area": area,
            }
        )
    return plots
