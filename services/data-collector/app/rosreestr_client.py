"""
Rosreestr / NSPD integration.

Public NSPD data is reference cadastral data, not an official EGRN extract.
Do not use this client to bypass official EGRN ordering, authentication, payment,
CAPTCHA, or access controls.
"""
from __future__ import annotations

import json
import math
import random
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.config import settings

LAND_PLOTS_CATEGORY_ID = 36368
CADASTRAL_NUMBER_RE = re.compile(r"\b\d{1,2}:\d{1,2}:\d{1,10}:\d{1,10}(?:/\d+)?\b")


@dataclass
class PlotData:
    cadastral_number: str
    address: str
    area: float
    category: str
    allowed_use: str
    owner_type: str
    lat: float
    lng: float
    price: float
    status: str
    raw_json: dict[str, Any]


@dataclass
class EGRNData:
    cadastral_number: str
    owner: str
    encumbrances: list[str]
    registration_date: str
    raw_json: dict[str, Any]


def _web_mercator(lng: float, lat: float) -> tuple[float, float]:
    if not -180 <= lng <= 180:
        raise ValueError("lng must be between -180 and 180")
    if not -85.05112878 <= lat <= 85.05112878:
        raise ValueError("lat must be between -85.05112878 and 85.05112878")

    radius = 6_378_137.0
    x = radius * math.radians(lng)
    y = radius * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def _wgs84_from_web_mercator(x: float, y: float) -> tuple[float, float]:
    radius = 6_378_137.0
    lng = math.degrees(x / radius)
    lat = math.degrees(2 * math.atan(math.exp(y / radius)) - math.pi / 2)
    return lat, lng


def _point_buffer_feature_collection(lat: float, lng: float, radius_m: float) -> dict[str, Any]:
    x, y = _web_mercator(lng, lat)
    radius_m = max(0.5, float(radius_m))
    ring = [
        [x - radius_m, y - radius_m],
        [x + radius_m, y - radius_m],
        [x + radius_m, y + radius_m],
        [x - radius_m, y + radius_m],
        [x - radius_m, y - radius_m],
    ]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "crs": {"properties": {"name": "EPSG:3857"}, "type": "name"},
                    "type": "Polygon",
                    "coordinates": [ring],
                },
                "properties": {},
            }
        ],
    }


def _first_present(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any:
    normalized = {str(key).lower(): value for key, value in attrs.items()}
    for key in keys:
        if key in attrs and attrs[key] not in (None, ""):
            return attrs[key]
        value = normalized.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    cleaned = re.sub(r"[^\d,.\-]", "", str(value)).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _attrs_from_feature(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties") or {}
    options = properties.get("options") or {}

    attrs: dict[str, Any] = {}
    if isinstance(options, dict):
        attrs.update(options)
    if isinstance(properties, dict):
        attrs.update(properties)
    return attrs


def _find_cadastral_number(attrs: dict[str, Any]) -> str:
    direct = _first_present(
        attrs,
        (
            "cadastral_number",
            "cadastralNumber",
            "cad_num",
            "cadNum",
            "cn",
            "objectCn",
            "number",
            "label",
        ),
    )
    if direct:
        match = CADASTRAL_NUMBER_RE.search(str(direct))
        if match:
            return match.group(0)

    dumped = json.dumps(attrs, ensure_ascii=False)
    match = CADASTRAL_NUMBER_RE.search(dumped)
    return match.group(0) if match else ""


def _geometry_center(feature: dict[str, Any]) -> tuple[float, float]:
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if not coordinates:
        return 0.0, 0.0

    points: list[tuple[float, float]] = []

    def collect(value: Any) -> None:
        if (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], int | float)
            and isinstance(value[1], int | float)
        ):
            points.append((float(value[1]), float(value[0])))
            return
        if isinstance(value, list):
            for item in value:
                collect(item)

    collect(coordinates)
    if not points:
        return 0.0, 0.0

    lat = sum(point[0] for point in points) / len(points)
    lng = sum(point[1] for point in points) / len(points)
    crs_name = (
        ((geometry.get("crs") or {}).get("properties") or {}).get("name")
        or ((feature.get("crs") or {}).get("properties") or {}).get("name")
        or ""
    )
    if "3857" in str(crs_name):
        return _wgs84_from_web_mercator(lng, lat)
    return lat, lng


def _plot_from_feature(
    feature: dict[str, Any],
    *,
    fallback_lat: float = 0.0,
    fallback_lng: float = 0.0,
    raw: dict[str, Any] | None = None,
) -> PlotData:
    attrs = _attrs_from_feature(feature)
    center_lat, center_lng = _geometry_center(feature)

    return PlotData(
        cadastral_number=_find_cadastral_number(attrs),
        address=str(
            _first_present(
                attrs,
                ("address", "readableAddress", "readable_address", "location", "addr", "Адрес"),
            )
            or ""
        ),
        area=_to_float(
            _first_present(
                attrs,
                (
                    "area",
                    "specifiedArea",
                    "specified_area",
                    "areaValue",
                    "landRecordArea",
                    "land_record_area",
                    "declared_area",
                    "Площадь",
                ),
            )
        ),
        category=str(
            _first_present(
                attrs,
                (
                    "land_record_category_type",
                    "landRecordCategoryType",
                    "landCategory",
                    "categoryName",
                    "category",
                    "Категория земель",
                ),
            )
            or ""
        ),
        allowed_use=str(
            _first_present(
                attrs,
                (
                    "allowed_use",
                    "permittedUse",
                    "permitted_use",
                    "by_document",
                    "typePermittedUse",
                    "landRecordRegPermittedUse",
                    "permitted_use_established_by_document",
                    "Вид разрешенного использования",
                ),
            )
            or ""
        ),
        owner_type=str(
            _first_present(attrs, ("ownerType", "ownership", "ownership_type", "right_type", "Форма собственности"))
            or ""
        ),
        lat=center_lat or fallback_lat,
        lng=center_lng or fallback_lng,
        price=_to_float(
            _first_present(
                attrs,
                (
                    "price",
                    "cadastralCost",
                    "cadastralValue",
                    "cost",
                    "cost_value",
                    "cadCost",
                    "Кадастровая стоимость",
                ),
            )
        ),
        status=str(_first_present(attrs, ("status", "previously_posted", "objectStatus", "regStatus")) or ""),
        raw_json=raw or feature,
    )


class MockRosreestrClient:
    """Returns realistic fake data for demo/local development."""

    async def get_plot(self, cadastral_number: str) -> PlotData:
        return PlotData(
            cadastral_number=cadastral_number,
            address=f"Московская область, тест, уч. {cadastral_number[-4:]}",
            area=round(random.uniform(0.5, 50.0), 2),
            category="Земли сельскохозяйственного назначения",
            allowed_use="Для ведения крестьянского (фермерского) хозяйства",
            owner_type="",
            lat=55.7 + random.uniform(-0.5, 0.5),
            lng=37.6 + random.uniform(-1.0, 1.0),
            price=round(random.uniform(500_000, 15_000_000), 0),
            status="Учтенный",
            raw_json={"source": "mock", "cadastral_number": cadastral_number},
        )

    async def get_plot_by_coordinates(self, lat: float, lng: float, radius_m: float = 2.0) -> PlotData:
        cadastral_number = f"50:21:{abs(hash((round(lat, 5), round(lng, 5)))) % 10_000_000:07d}:1"
        plot = await self.get_plot(cadastral_number)
        plot.lat = lat
        plot.lng = lng
        plot.raw_json = {
            "source": "mock",
            "lat": lat,
            "lng": lng,
            "radius_m": radius_m,
            "note": "Mock coordinate lookup. Set ROSREESTR_MODE=real for NSPD lookup.",
        }
        return plot

    async def get_egrn(self, cadastral_number: str) -> EGRNData:
        return EGRNData(
            cadastral_number=cadastral_number,
            owner="",
            encumbrances=[],
            registration_date="",
            raw_json={
                "source": "mock",
                "note": "Official EGRN extracts require a separate authorized Rosreestr/Gosuslugi request.",
            },
        )


class RealRosreestrClient:
    """Public NSPD integration for reference cadastral data."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        verify_ssl: bool | None = None,
    ) -> None:
        self.base_url = (base_url or settings.rosreestr_api_url).rstrip("/")
        self.timeout = timeout or settings.rosreestr_timeout
        self.verify_ssl = settings.rosreestr_verify_ssl if verify_ssl is None else verify_ssl
        if settings.nspd_insecure_tls:
            self.verify_ssl = False
        self.original_host = urlsplit(self.base_url).netloc
        self.request_base_url = _base_url_with_resolved_ip(self.base_url, settings.nspd_resolve_ip)
        self.headers = {
            "Accept": "application/json,*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/json",
            "Referer": "https://nspd.gov.ru/map?thematic=PKK&baseLayerId=235&theme_id=1",
            "User-Agent": settings.rosreestr_user_agent,
            "X-Public-User": "true",
        }
        if settings.nspd_resolve_ip:
            self.headers["Host"] = self.original_host

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.request_base_url,
            headers=self.headers,
            timeout=self.timeout,
            verify=self.verify_ssl,
            follow_redirects=True,
        )

    async def get_plot(self, cadastral_number: str) -> PlotData:
        query = quote(cadastral_number.strip(), safe="")
        async with await self._client() as client:
            payload = await _first_successful_json(
                client,
                (
                    f"/geoportal/v2/search/geoportal?thematicSearchId=1&query={query}&CRS=EPSG:3857",
                    f"/geoportal/v2/search/geoportal?query={query}&CRS=EPSG:3857",
                    f"/geoportal/v1/search/geoportal?thematicSearchId=1&query={query}&CRS=EPSG:3857",
                ),
            )

        features = payload.get("data", {}).get("features", [])
        if not features:
            raise LookupError(f"Plot {cadastral_number} was not found in NSPD public data")

        return _plot_from_feature(features[0], raw=payload)

    async def get_plot_by_coordinates(self, lat: float, lng: float, radius_m: float = 2.0) -> PlotData:
        body = {
            "geom": _point_buffer_feature_collection(lat, lng, radius_m),
            "categories": [{"id": LAND_PLOTS_CATEGORY_ID}],
        }

        async with await self._client() as client:
            response = await client.post("/geoportal/v1/intersects?typeIntersect=fullObject", json=body)
            response.raise_for_status()
            payload = response.json()

        features = _extract_features(payload)
        if not features:
            raise LookupError(f"No public land plot found near lat={lat}, lng={lng}, radius_m={radius_m}")

        return _plot_from_feature(features[0], fallback_lat=lat, fallback_lng=lng, raw=payload)

    async def get_egrn(self, cadastral_number: str) -> EGRNData:
        return EGRNData(
            cadastral_number=cadastral_number,
            owner="",
            encumbrances=[],
            registration_date="",
            raw_json={
                "source": "nspd",
                "official_extract_available": False,
                "note": (
                    "Public NSPD endpoints do not provide an official EGRN extract, owners, "
                    "or legally significant encumbrance data. Request an official extract through "
                    "Rosreestr/Gosuslugi or a contracted API provider."
                ),
            },
        )


def _extract_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("features"), list):
        return payload["features"]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("features"), list):
        return data["features"]
    if isinstance(data, list):
        features: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("features"), list):
                features.extend(item["features"])
            elif isinstance(item, dict):
                features.append(item)
        return features
    return []


def _base_url_with_resolved_ip(base_url: str, resolve_ip: str) -> str:
    if not resolve_ip:
        return base_url
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, resolve_ip, parts.path, parts.query, parts.fragment))


async def _first_successful_json(client: httpx.AsyncClient, paths: tuple[str, ...]) -> dict[str, Any]:
    last_error: Exception | None = None
    for path in paths:
        try:
            response = await client.get(path)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return {}


def get_client(mode: str | None = None) -> MockRosreestrClient | RealRosreestrClient:
    selected_mode = (mode or settings.rosreestr_mode).lower()
    if selected_mode == "real":
        return RealRosreestrClient()
    return MockRosreestrClient()


def plot_to_dict(plot: PlotData) -> dict[str, Any]:
    return asdict(plot)


def egrn_to_dict(egrn: EGRNData) -> dict[str, Any]:
    return asdict(egrn)
