"""
Deterministic mock data generation for ROSREESTR_MODE=mock.

All output is keyed by a stable hash of the cadastral number (or address), so
repeated calls are reproducible for demos and tests. No randomness is used.

Risk profile is driven by the last digit of the cadastral number per CLAUDE.md:
  * even digit            -> clean plot (no encumbrances, no stop-factors)
  * odd digit             -> 1-2 encumbrances, no critical stop-factor
  * digit 0 or 5          -> a critical stop-factor (overlap with protected zone
                             or missing access), regardless of even/odd above

The mock parcel geometry is a small WGS84 polygon around the plot center so
downstream geometry/area logic has a real ``Polygon`` to work with.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

# Region anchors keep generated coordinates realistic per cadastral district.
# Keyed by the first cadastral block (region code).
_REGION_ANCHORS: dict[str, tuple[str, float, float]] = {
    "50": ("Московская область", 55.75, 37.40),
    "77": ("город Москва", 55.75, 37.62),
    "26": ("Ставропольский край", 45.04, 41.97),
    "23": ("Краснодарский край", 45.04, 38.98),
    "61": ("Ростовская область", 47.22, 39.72),
    "47": ("Ленинградская область", 59.94, 30.31),
}
_DEFAULT_ANCHOR = ("Московская область", 55.75, 37.40)

_CATEGORIES = [
    "Земли сельскохозяйственного назначения",
    "Земли населённых пунктов",
    "Земли промышленности",
]

_ALLOWED_USE_BY_CATEGORY: dict[str, list[str]] = {
    "Земли сельскохозяйственного назначения": [
        "Для ведения крестьянского (фермерского) хозяйства",
        "Для сельскохозяйственного производства",
        "Растениеводство",
    ],
    "Земли населённых пунктов": [
        "Для индивидуального жилищного строительства",
        "Для ведения личного подсобного хозяйства",
        "Малоэтажная жилая застройка",
    ],
    "Земли промышленности": [
        "Под производственную базу",
        "Склады",
        "Для размещения объектов промышленности",
    ],
}

_DISTRICT_NAMES = [
    "Раменский район",
    "Шпаковский район",
    "Истринский район",
    "Одинцовский район",
    "Азовский район",
    "Всеволожский район",
]


@dataclass(frozen=True)
class RiskProfile:
    """Deterministic risk classification of a mock plot."""

    level: str  # clean | encumbered | critical
    last_digit: int
    has_critical_stop_factor: bool
    critical_reason: str  # russian, empty when no stop-factor
    encumbrance_count: int


def stable_hash(value: str) -> int:
    """Stable, process-independent 32-bit hash (Python's hash() is salted)."""

    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _last_digit(cadastral_number: str) -> int:
    for char in reversed(cadastral_number.strip()):
        if char.isdigit():
            return int(char)
    return 0


def risk_profile_for(cadastral_number: str) -> RiskProfile:
    """Classify the plot's risk profile from its cadastral number."""

    last = _last_digit(cadastral_number)
    if last in (0, 5):
        # Critical stop-factor: alternate between two realistic blockers.
        if stable_hash(cadastral_number) % 2 == 0:
            reason = (
                "Участок пересекается с охранной зоной с особым режимом использования; "
                "до уточнения границ ЗОУИТ строительство и часть работ под запретом."
            )
        else:
            reason = (
                "Нет подтверждённого подъезда к участку: ближайшая дорога общего "
                "пользования не граничит с участком, требуется проверка сервитута."
            )
        return RiskProfile(
            level="critical",
            last_digit=last,
            has_critical_stop_factor=True,
            critical_reason=reason,
            encumbrance_count=2,
        )
    if last % 2 == 1:
        count = 1 + (stable_hash(cadastral_number) % 2)  # 1 or 2
        return RiskProfile(
            level="encumbered",
            last_digit=last,
            has_critical_stop_factor=False,
            critical_reason="",
            encumbrance_count=count,
        )
    return RiskProfile(
        level="clean",
        last_digit=last,
        has_critical_stop_factor=False,
        critical_reason="",
        encumbrance_count=0,
    )


def _region_block(cadastral_number: str) -> str:
    head = cadastral_number.strip().split(":", 1)[0]
    return head if head.isdigit() else ""


def anchor_for(cadastral_number: str) -> tuple[str, float, float]:
    return _REGION_ANCHORS.get(_region_block(cadastral_number), _DEFAULT_ANCHOR)


def _jitter(seed: int, span: float) -> float:
    """Deterministic value in [-span, span] from an integer seed."""

    # Map seed to [0, 1) then to [-span, span].
    fraction = (seed % 100_000) / 100_000.0
    return (fraction * 2.0 - 1.0) * span


def center_for(cadastral_number: str) -> tuple[float, float]:
    """Deterministic WGS84 plot center near the region anchor."""

    _region, base_lat, base_lng = anchor_for(cadastral_number)
    seed = stable_hash(cadastral_number)
    lat = base_lat + _jitter(seed, 0.45)
    lng = base_lng + _jitter(seed // 7 + 13, 0.85)
    return round(lat, 6), round(lng, 6)


def area_hectares_for(cadastral_number: str) -> float:
    seed = stable_hash(cadastral_number + ":area")
    # 0.06 .. 50.0 ha, deterministic.
    return round(0.06 + (seed % 4994) / 100.0, 2)


def _polygon_ring(lat: float, lng: float, area_sqm: float) -> list[list[float]]:
    """Build a square WGS84 ring of roughly ``area_sqm`` centred on lat/lng."""

    side_m = max(20.0, math.sqrt(max(area_sqm, 400.0)))
    half = side_m / 2.0
    # Degrees per metre (approx) at this latitude.
    d_lat = half / 111_320.0
    d_lng = half / (111_320.0 * max(0.1, math.cos(math.radians(lat))))
    ring = [
        [round(lng - d_lng, 7), round(lat - d_lat, 7)],
        [round(lng + d_lng, 7), round(lat - d_lat, 7)],
        [round(lng + d_lng, 7), round(lat + d_lat, 7)],
        [round(lng - d_lng, 7), round(lat + d_lat, 7)],
        [round(lng - d_lng, 7), round(lat - d_lat, 7)],
    ]
    return ring


def parcel_polygon(cadastral_number: str, lat: float, lng: float, area_ha: float) -> dict[str, Any]:
    """WGS84 GeoJSON Polygon geometry for the parcel."""

    ring = _polygon_ring(lat, lng, area_ha * 10_000.0)
    return {
        "type": "Polygon",
        "coordinates": [ring],
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    }


def _offset_polygon(ring: list[list[float]], d_lng: float, d_lat: float) -> list[list[float]]:
    return [[round(x + d_lng, 7), round(y + d_lat, 7)] for x, y in ring]


def category_for(cadastral_number: str) -> str:
    seed = stable_hash(cadastral_number + ":cat")
    return _CATEGORIES[seed % len(_CATEGORIES)]


def allowed_use_for(cadastral_number: str, category: str) -> str:
    options = _ALLOWED_USE_BY_CATEGORY.get(category, _ALLOWED_USE_BY_CATEGORY[_CATEGORIES[0]])
    seed = stable_hash(cadastral_number + ":vri")
    return options[seed % len(options)]


def district_for(cadastral_number: str) -> str:
    seed = stable_hash(cadastral_number + ":district")
    return _DISTRICT_NAMES[seed % len(_DISTRICT_NAMES)]


def address_for(cadastral_number: str) -> str:
    region, _lat, _lng = anchor_for(cadastral_number)
    district = district_for(cadastral_number)
    seed = stable_hash(cadastral_number + ":addr")
    plot_no = seed % 900 + 1
    return f"{region}, {district}, сельское поселение, уч. {plot_no}"


def cadastral_value_for(cadastral_number: str, area_ha: float, category: str) -> float:
    # Rough per-ha cadastral value by category, deterministic spread.
    base_per_ha = {
        "Земли сельскохозяйственного назначения": 350_000.0,
        "Земли населённых пунктов": 4_500_000.0,
        "Земли промышленности": 2_200_000.0,
    }.get(category, 500_000.0)
    seed = stable_hash(cadastral_number + ":price")
    factor = 0.7 + (seed % 60) / 100.0  # 0.7 .. 1.29
    return round(area_ha * base_per_ha * factor, 0)


def build_plot_payload(cadastral_number: str) -> dict[str, Any]:
    """Assemble the deterministic plot attributes for a cadastral number."""

    cadastral_number = cadastral_number.strip()
    region, _base_lat, _base_lng = anchor_for(cadastral_number)
    lat, lng = center_for(cadastral_number)
    area_ha = area_hectares_for(cadastral_number)
    area_sqm = round(area_ha * 10_000.0, 1)
    category = category_for(cadastral_number)
    allowed_use = allowed_use_for(cadastral_number, category)
    price = cadastral_value_for(cadastral_number, area_ha, category)
    geometry = parcel_polygon(cadastral_number, lat, lng, area_ha)
    profile = risk_profile_for(cadastral_number)

    feature = {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "category": 36368,
            "categoryName": "Земельные участки из ЕГРН",
            "options": {
                "cad_num": cadastral_number,
                "readable_address": address_for(cadastral_number),
                "specified_area": area_sqm,
                "land_record_category_type": category,
                "permitted_use_established_by_document": allowed_use,
                "cost_value": price,
                "status": "Учтенный",
                "ownership_type": "Частная собственность" if profile.level != "clean" else "",
                "quarter_cad_number": ":".join(cadastral_number.split(":")[:3]),
            },
        },
    }

    raw_json = {
        "source": "mock",
        "data": {"features": [feature]},
        "risk_profile": {
            "level": profile.level,
            "last_digit": profile.last_digit,
            "has_critical_stop_factor": profile.has_critical_stop_factor,
        },
    }

    return {
        "cadastral_number": cadastral_number,
        "address": address_for(cadastral_number),
        "area": area_sqm,
        "category": category,
        "allowed_use": allowed_use,
        "owner_type": "Частная собственность" if profile.level != "clean" else "",
        "lat": lat,
        "lng": lng,
        "price": price,
        "status": "Учтенный",
        "raw_json": raw_json,
        "_geometry": geometry,
        "_area_ha": area_ha,
        "_region": region,
        "_profile": profile,
    }


def build_egrn_payload(cadastral_number: str) -> dict[str, Any]:
    """Deterministic owner + encumbrances keyed by risk profile."""

    cadastral_number = cadastral_number.strip()
    profile = risk_profile_for(cadastral_number)
    seed = stable_hash(cadastral_number + ":egrn")

    owners_individual = [
        "Иванов Иван Иванович",
        "Петрова Анна Сергеевна",
        "Смирнов Дмитрий Олегович",
        "Кузнецова Мария Викторовна",
    ]
    owners_legal = [
        "ООО «АгроИнвест»",
        "ООО «ЗемРесурс»",
        "АО «Девелопмент Групп»",
    ]
    if seed % 3 == 0:
        owner = owners_legal[seed % len(owners_legal)]
    else:
        owner = owners_individual[seed % len(owners_individual)]

    # Registration date, deterministic, between 2008 and 2023.
    year = 2008 + (seed % 16)
    month = 1 + (seed // 16 % 12)
    day = 1 + (seed // 7 % 28)
    registration_date = f"{year:04d}-{month:02d}-{day:02d}"

    encumbrance_pool = [
        "Ипотека в силу закона (залог в пользу банка)",
        "Аренда сроком на 11 месяцев",
        "Сервитут для прохода и проезда к смежному участку",
        "Арест на основании определения суда",
    ]
    encumbrances: list[str] = []
    for index in range(profile.encumbrance_count):
        encumbrances.append(encumbrance_pool[(seed + index) % len(encumbrance_pool)])

    if profile.has_critical_stop_factor:
        # Surface the stop-factor as an encumbrance/limitation too.
        if "охранной зоной" in profile.critical_reason:
            encumbrances.append("Ограничение прав в связи с зоной с особыми условиями использования (ЗОУИТ)")
        else:
            encumbrances.append("Отсутствует обеспеченный доступ (подъезд) к земельному участку")

    return {
        "cadastral_number": cadastral_number,
        "owner": owner,
        "encumbrances": encumbrances,
        "registration_date": registration_date,
        "raw_json": {
            "source": "mock",
            "note": (
                "Синтетические данные ЕГРН для демонстрации (ROSREESTR_MODE=mock). "
                "Не являются официальной выпиской из ЕГРН."
            ),
            "risk_level": profile.level,
            "rights": [
                {
                    "type": "Собственность",
                    "owner": owner,
                    "registration_date": registration_date,
                }
            ],
            "encumbrances": encumbrances,
        },
    }


def build_raw_features_by_layer(cadastral_number: str) -> dict[str, list[dict[str, Any]]]:
    """Deterministic spatial layer raw features intersecting the parcel.

    The output matches the shape that ``SpatialLayerCollector.collect_from_features``
    expects: a mapping of source layer key -> list of GeoJSON-ish features that the
    normalizer turns into ``CollectedSpatialLayer`` objects.
    """

    cadastral_number = cadastral_number.strip()
    lat, lng = center_for(cadastral_number)
    area_ha = area_hectares_for(cadastral_number)
    category = category_for(cadastral_number)
    profile = risk_profile_for(cadastral_number)
    seed = stable_hash(cadastral_number + ":layers")

    parcel_ring = _polygon_ring(lat, lng, area_ha * 10_000.0)

    def _polygon(ring: list[list[float]]) -> dict[str, Any]:
        return {
            "type": "Polygon",
            "coordinates": [ring],
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        }

    layers: dict[str, list[dict[str, Any]]] = {}

    # 1. Land-use layer (agricultural categories -> arable/pasture; others -> unknown).
    if category == "Земли сельскохозяйственного назначения":
        land_use_key = "land_use_arable" if seed % 2 == 0 else "land_use_pasture"
    else:
        land_use_key = "land_use_unknown"
    layers[land_use_key] = [
        {
            "type": "Feature",
            "geometry": _polygon(parcel_ring),
            "properties": {
                "name": "Сельскохозяйственное угодье" if "land_use" in land_use_key else "Угодье",
                "cad_num": cadastral_number,
                "options": {"area": round(area_ha * 10_000.0, 1)},
            },
        }
    ]

    # 2. Restriction layers for risky plots.
    if profile.has_critical_stop_factor and "охранной зоной" in profile.critical_reason:
        # A protective zone (ЛЭП / power line) overlapping part of the parcel.
        offset = _offset_polygon(parcel_ring, d_lng=0.0003, d_lat=0.0002)
        layers["energy_transport_zouit"] = [
            {
                "type": "Feature",
                "geometry": _polygon(offset),
                "properties": {
                    "name": "Охранная зона ВЛ 110 кВ",
                    "cad_num": ":".join(cadastral_number.split(":")[:3]) + ":ЗОУИТ",
                    "options": {"type_zone": "Охранная зона объекта электросетевого хозяйства"},
                },
            }
        ]
    elif profile.level == "encumbered":
        # Non-critical restriction: water protection zone touching a corner,
        # or a territorial zone, chosen deterministically.
        if seed % 2 == 0:
            offset = _offset_polygon(parcel_ring, d_lng=-0.0004, d_lat=0.0003)
            layers["water_protection_zone"] = [
                {
                    "type": "Feature",
                    "geometry": _polygon(offset),
                    "properties": {
                        "name": "Водоохранная зона реки",
                        "options": {"distance_m": 100},
                    },
                }
            ]
        else:
            layers["territorial_zones"] = [
                {
                    "type": "Feature",
                    "geometry": _polygon(parcel_ring),
                    "properties": {
                        "name": "Территориальная зона СХ-1",
                        "options": {"zone_index": "СХ-1"},
                    },
                }
            ]

    # 3. A real-estate object (building) on some plots (deterministic, not on every plot).
    if seed % 3 == 0 and category != "Земли сельскохозяйственного назначения":
        # Small building footprint near the parcel center.
        b_lat, b_lng = lat + 0.00005, lng + 0.00005
        b_ring = _polygon_ring(b_lat, b_lng, 200.0)
        building_child = ":".join(cadastral_number.split(":")[:3]) + f":{1000 + seed % 9000}"
        layers["buildings"] = [
            {
                "type": "Feature",
                "geometry": _polygon(b_ring),
                "properties": {
                    "name": "Нежилое здание",
                    "cad_num": building_child,
                    "options": {"area": 200.0, "purpose": "Нежилое здание"},
                },
            }
        ]

    return layers


def build_child_objects_and_composition(
    cadastral_number: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic child real-estate objects, land parts and land composition.

    Returns raw feature lists keyed for the normalizer plus a land_composition
    list of plain dicts. Land parts reuse the parcel geometry (a part = sub-area).
    """

    cadastral_number = cadastral_number.strip()
    lat, lng = center_for(cadastral_number)
    area_ha = area_hectares_for(cadastral_number)
    category = category_for(cadastral_number)
    profile = risk_profile_for(cadastral_number)
    seed = stable_hash(cadastral_number + ":children")

    parcel_ring = _polygon_ring(lat, lng, area_ha * 10_000.0)

    def _polygon(ring: list[list[float]]) -> dict[str, Any]:
        return {
            "type": "Polygon",
            "coordinates": [ring],
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        }

    child_objects: list[dict[str, Any]] = []
    land_parts: list[dict[str, Any]] = []
    land_composition: list[dict[str, Any]] = []

    # Child real estate object (building) on built-up, non-agricultural plots.
    if seed % 3 == 0 and category != "Земли сельскохозяйственного назначения":
        b_ring = _polygon_ring(lat + 0.00006, lng - 0.00004, 180.0)
        child_cad = ":".join(cadastral_number.split(":")[:3]) + f":{2000 + seed % 8000}"
        child_objects.append(
            {
                "type": "Feature",
                "geometry": _polygon(b_ring),
                "properties": {
                    "name": "Нежилое здание (ОКС на участке)",
                    "cad_num": child_cad,
                    "options": {"area": 180.0, "purpose": "Нежилое здание"},
                },
            }
        )

    # Land part for a restriction (encumbered/critical plots have a part of the
    # parcel burdened by ЗОУИТ / сервитут).
    if profile.level != "clean":
        part_ring = _offset_polygon(parcel_ring, d_lng=0.0001, d_lat=0.0001)
        part_cad = cadastral_number + "/чзу1"
        land_parts.append(
            {
                "type": "Feature",
                "geometry": _polygon(part_ring),
                "properties": {
                    "name": "Часть земельного участка (обременение)",
                    "cad_num": part_cad,
                    "options": {"part_type": "Зона действия ограничения"},
                },
            }
        )

    # Land composition (агросостав) for agricultural plots.
    if category == "Земли сельскохозяйственного назначения":
        arable_share = 0.6 + (seed % 30) / 100.0  # 0.60 .. 0.89
        arable_ha = round(area_ha * arable_share, 2)
        other_ha = round(area_ha - arable_ha, 2)
        land_composition.append(
            {
                "type": "Пашня",
                "area_ha": arable_ha,
                "share": round(arable_share, 2),
            }
        )
        if other_ha > 0:
            land_composition.append(
                {
                    "type": "Прочие угодья",
                    "area_ha": other_ha,
                    "share": round(1.0 - arable_share, 2),
                }
            )

    return child_objects, land_parts, land_composition


def mock_dataset(plot_data: dict[str, Any]) -> dict[str, Any]:
    """Build a rich, deterministic dataset (soil / infra / market) for mock mode.

    ``plot_data`` is the dict produced by ``plot_to_dict`` (PlotData -> dict).
    """

    cadastral_number = plot_data.get("cadastral_number", "")
    seed = stable_hash(cadastral_number + ":dataset")
    profile = risk_profile_for(cadastral_number)
    lat = float(plot_data.get("lat") or 0.0)
    lng = float(plot_data.get("lng") or 0.0)
    area_ha = round(float(plot_data.get("area") or 0.0) / 10_000.0, 2)
    category = str(plot_data.get("category") or "")

    bonitet = 45 + (seed % 50)  # 45..94 балл бонитета
    humus = round(2.0 + (seed % 50) / 10.0, 1)  # 2.0..6.9 %
    soil = {
        "success": True,
        "source": "mock",
        "soil": {
            "dominant_type": "Чернозём обыкновенный" if seed % 2 == 0 else "Серая лесная",
            "bonitet_score": bonitet,
            "humus_percent": humus,
            "ph": round(5.5 + (seed % 25) / 10.0, 1),
        },
        "limitations": ["mock_mode_synthetic_soil"],
    }

    distance_to_road_m = 50 + (seed % 4000)
    distance_to_power_m = 100 + (seed // 3 % 5000)
    has_access = not (profile.has_critical_stop_factor and "подъезд" in profile.critical_reason)
    infrastructure = {
        "success": True,
        "source": "mock",
        "counts": {
            "roads_500m": 0 if not has_access else 1 + (seed % 4),
            "power_lines_1km": 1 + (seed // 5 % 3),
            "settlements_5km": 1 + (seed // 9 % 6),
        },
        "distances_m": {
            "nearest_road": distance_to_road_m if has_access else None,
            "power_line": distance_to_power_m,
            "gas_pipeline": 500 + (seed // 11 % 6000),
        },
        "has_legal_access": has_access,
        "limitations": ["mock_mode_synthetic_infrastructure"],
    }

    base_per_ha = {
        "Земли сельскохозяйственного назначения": 280_000.0,
        "Земли населённых пунктов": 5_200_000.0,
        "Земли промышленности": 2_600_000.0,
    }.get(category, 600_000.0)
    market_factor = 0.8 + (seed % 50) / 100.0
    market_price_per_ha = round(base_per_ha * market_factor, 0)
    comparables = []
    for index in range(3):
        comp_factor = 0.85 + ((seed + index * 17) % 35) / 100.0
        comparables.append(
            {
                "cadastral_number": f"{cadastral_number}~comp{index + 1}",
                "area_ha": round(max(0.1, area_ha * (0.7 + index * 0.3)), 2),
                "price_per_ha": round(base_per_ha * comp_factor, 0),
                "distance_km": round(2.0 + index * 3.5, 1),
            }
        )
    market = {
        "success": True,
        "source": "mock",
        "region": str(plot_data.get("address", "")).split(",", 1)[0],
        "estimated_market_price_per_ha": market_price_per_ha,
        "estimated_market_price_total": round(market_price_per_ha * max(area_ha, 0.01), 0),
        "liquidity": ["low", "medium", "high"][seed % 3],
        "comparables_count": len(comparables),
        "comparables": comparables,
        "limitations": ["mock_mode_synthetic_market"],
    }

    return {
        "success": True,
        "source": "data_collector_dataset_pipeline_mock",
        "cadastralNumber": cadastral_number,
        "nspd": plot_data,
        "soil": soil,
        "infrastructure": infrastructure,
        "marketLiquidity": market,
        "warnings": [
            "ROSREESTR_MODE=mock: внешние парсеры не вызывались, данные синтетические.",
        ],
        "coordinates": {"lat": lat, "lng": lng},
    }
