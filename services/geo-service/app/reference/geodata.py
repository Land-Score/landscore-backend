"""Compact embedded geographic reference dataset for the geo-service.

This module provides a small, realistic, fully in-process reference dataset so
the geo-service can compute useful location analytics without a populated
PostGIS database. All distances are computed with the haversine formula and all
zone checks use shapely point-in-polygon tests against the embedded polygons.

Coordinates use the (lat, lng) convention for the human-readable tables and the
GeoJSON (lng, lat) convention inside the ``geometry`` GeoJSON objects, matching
the GeoJSON specification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in kilometres between two WGS84 points."""

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def point_to_segment_km(lat: float, lng: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    """Approximate distance from a point to a lat/lng segment ``a``->``b``.

    Uses a local equirectangular projection around the query point, which is
    accurate enough at the scale of a single plot and avoids a heavyweight CRS
    transform. ``a`` and ``b`` are ``(lat, lng)`` tuples.
    """

    lat0 = math.radians(lat)
    km_per_deg_lat = 111.32
    km_per_deg_lng = 111.32 * math.cos(lat0)

    def to_xy(plat: float, plng: float) -> tuple[float, float]:
        return ((plng - lng) * km_per_deg_lng, (plat - lat) * km_per_deg_lat)

    px, py = 0.0, 0.0
    ax, ay = to_xy(a[0], a[1])
    bx, by = to_xy(b[0], b[1])
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


@dataclass(frozen=True)
class City:
    name: str
    lat: float
    lng: float
    region: str
    district: str
    population: int


@dataclass(frozen=True)
class PolyLine:
    """A reference linear feature (road / railway / river / power line)."""

    name: str
    feature_type: str  # "road" | "railway" | "river" | "power_line"
    # Ordered list of (lat, lng) vertices.
    vertices: list[tuple[float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class ProtectedZone:
    name: str
    zone_type: str  # "water_protection" | "nature_protection" | "sanitary_protection"
    restrictions: list[str]
    normative_basis: list[str]
    # GeoJSON Polygon geometry (lng, lat order).
    geometry: dict[str, Any]


# --- Cities (real coordinates) -------------------------------------------------

CITIES: list[City] = [
    City("Москва", 55.7558, 37.6173, "Москва", "Центральный округ", 13_010_000),
    City("Санкт-Петербург", 59.9343, 30.3351, "Санкт-Петербург", "Центральный район", 5_600_000),
    City("Казань", 55.7963, 49.1088, "Республика Татарстан", "Вахитовский район", 1_310_000),
    City("Краснодар", 45.0355, 38.9753, "Краснодарский край", "Западный округ", 1_100_000),
    City("Екатеринбург", 56.8389, 60.6057, "Свердловская область", "Ленинский район", 1_540_000),
    City("Новосибирск", 55.0084, 82.9357, "Новосибирская область", "Центральный район", 1_630_000),
    City("Нижний Новгород", 56.2965, 43.9361, "Нижегородская область", "Нижегородский район", 1_230_000),
    City("Ростов-на-Дону", 47.2225, 39.7188, "Ростовская область", "Ленинский район", 1_140_000),
    City("Самара", 53.1959, 50.1002, "Самарская область", "Ленинский район", 1_140_000),
    City("Воронеж", 51.6608, 39.2003, "Воронежская область", "Центральный район", 1_050_000),
    City("Тула", 54.1931, 37.6173, "Тульская область", "Центральный район", 467_000),
    City("Сочи", 43.5855, 39.7231, "Краснодарский край", "Центральный район", 466_000),
]


# --- Roads (federal highways, simplified polylines) ---------------------------

ROADS: list[PolyLine] = [
    PolyLine(
        "М-4 «Дон»",
        "road",
        [(55.7558, 37.6173), (54.1931, 37.6173), (51.6608, 39.2003), (47.2225, 39.7188), (45.0355, 38.9753), (43.5855, 39.7231)],
    ),
    PolyLine(
        "М-7 «Волга»",
        "road",
        [(55.7558, 37.6173), (56.2965, 43.9361), (55.7963, 49.1088)],
    ),
    PolyLine(
        "М-10 «Россия»",
        "road",
        [(55.7558, 37.6173), (57.6261, 39.8845), (59.9343, 30.3351)],
    ),
    PolyLine(
        "М-5 «Урал»",
        "road",
        [(55.7558, 37.6173), (53.1959, 50.1002), (54.7388, 55.9721), (56.8389, 60.6057)],
    ),
    PolyLine(
        "Р-256 «Чуйский тракт»",
        "road",
        [(55.0084, 82.9357), (53.3606, 83.7636)],
    ),
]


# --- Railways (simplified mainlines) ------------------------------------------

RAILWAYS: list[PolyLine] = [
    PolyLine(
        "Транссибирская магистраль",
        "railway",
        [(55.7558, 37.6173), (56.2965, 43.9361), (56.8389, 60.6057), (55.0084, 82.9357)],
    ),
    PolyLine(
        "Октябрьская железная дорога",
        "railway",
        [(55.7558, 37.6173), (57.6261, 39.8845), (59.9343, 30.3351)],
    ),
    PolyLine(
        "Северо-Кавказская железная дорога",
        "railway",
        [(51.6608, 39.2003), (47.2225, 39.7188), (45.0355, 38.9753), (43.5855, 39.7231)],
    ),
    PolyLine(
        "Куйбышевская железная дорога",
        "railway",
        [(55.7963, 49.1088), (53.1959, 50.1002), (53.5303, 49.3461)],
    ),
]


# --- Rivers (simplified) ------------------------------------------------------

RIVERS: list[PolyLine] = [
    PolyLine(
        "Волга",
        "river",
        [(56.2965, 43.9361), (55.7963, 49.1088), (53.1959, 50.1002), (48.7080, 44.5133), (46.3479, 48.0336)],
    ),
    PolyLine(
        "Москва-река",
        "river",
        [(55.7558, 37.6173), (55.7000, 37.9000), (55.5800, 38.7000)],
    ),
    PolyLine(
        "Дон",
        "river",
        [(51.6608, 39.2003), (49.5870, 41.0000), (47.2225, 39.7188)],
    ),
    PolyLine(
        "Нева",
        "river",
        [(59.9343, 30.3351), (59.8500, 30.7000), (59.8746, 31.0000)],
    ),
]


# --- Power lines (high-voltage corridors, simplified) -------------------------

POWER_LINES: list[PolyLine] = [
    PolyLine(
        "ВЛ 500 кВ Москва — Владимир",
        "power_line",
        [(55.7558, 37.6173), (56.1290, 40.4070)],
    ),
    PolyLine(
        "ВЛ 500 кВ Юг (Ростов — Краснодар)",
        "power_line",
        [(47.2225, 39.7188), (45.0355, 38.9753)],
    ),
    PolyLine(
        "ВЛ 220 кВ Казань — Самара",
        "power_line",
        [(55.7963, 49.1088), (53.1959, 50.1002)],
    ),
]


def _square_polygon(center_lat: float, center_lng: float, half_deg_lat: float, half_deg_lng: float) -> dict[str, Any]:
    """Build a GeoJSON Polygon (lng, lat) square around a center point."""

    min_lat = center_lat - half_deg_lat
    max_lat = center_lat + half_deg_lat
    min_lng = center_lng - half_deg_lng
    max_lng = center_lng + half_deg_lng
    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lng, min_lat],
            [max_lng, min_lat],
            [max_lng, max_lat],
            [min_lng, max_lat],
            [min_lng, min_lat],
        ]],
    }


# --- Protected zones (polygons around reference rivers / nature areas) ---------

PROTECTED_ZONES: list[ProtectedZone] = [
    ProtectedZone(
        name="Водоохранная зона реки Волги (Казань)",
        zone_type="water_protection",
        restrictions=[
            "Запрещено размещение кладбищ и скотомогильников",
            "Запрещено движение и стоянка транспортных средств вне дорог",
            "Ограничено применение пестицидов и агрохимикатов",
        ],
        normative_basis=["Водный кодекс РФ, ст. 65"],
        geometry=_square_polygon(55.7963, 49.1088, 0.05, 0.07),
    ),
    ProtectedZone(
        name="Водоохранная зона Москвы-реки",
        zone_type="water_protection",
        restrictions=[
            "Запрещено размещение АЗС и складов ГСМ",
            "Ограничена распашка земель в прибрежной полосе",
        ],
        normative_basis=["Водный кодекс РФ, ст. 65"],
        geometry=_square_polygon(55.7000, 37.9000, 0.04, 0.06),
    ),
    ProtectedZone(
        name="Природоохранная зона (Сочинский национальный парк)",
        zone_type="nature_protection",
        restrictions=[
            "Запрещена капитальная застройка",
            "Запрещена рубка леса",
            "Ограничена хозяйственная деятельность",
        ],
        normative_basis=["ФЗ «Об особо охраняемых природных территориях» № 33-ФЗ"],
        geometry=_square_polygon(43.6500, 39.9500, 0.12, 0.15),
    ),
    ProtectedZone(
        name="Санитарно-защитная зона (пригород Краснодара)",
        zone_type="sanitary_protection",
        restrictions=[
            "Запрещено жилищное строительство",
            "Ограничено размещение объектов пищевой промышленности",
        ],
        normative_basis=["СанПиН 2.2.1/2.1.1.1200-03"],
        geometry=_square_polygon(45.0355, 38.9753, 0.03, 0.04),
    ),
]
