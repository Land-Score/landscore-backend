from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.layers.catalog import LAYER_CATALOG, get_catalog_entry
from app.rosreestr_client import _base_url_with_resolved_ip, _extract_features


@dataclass(frozen=True)
class NspdLayerSource:
    layer_key: str
    category_id: int


NSPD_LAYER_SOURCES: dict[str, NspdLayerSource] = {
    "buildings": NspdLayerSource("buildings", 36369),
    "structures": NspdLayerSource("structures", 36383),
    "unfinished_construction": NspdLayerSource("unfinished_construction", 36384),
    "single_real_estate_complex": NspdLayerSource("single_real_estate_complex", 39663),
    "enterprise_property_complex": NspdLayerSource("enterprise_property_complex", 39664),
    "cultural_heritage_zouit": NspdLayerSource("cultural_heritage_zouit", 36940),
    "energy_transport_zouit": NspdLayerSource("energy_transport_zouit", 36940),
    "water_protection_zone": NspdLayerSource("water_protection_zone", 36940),
    "coastal_protective_strip": NspdLayerSource("coastal_protective_strip", 36940),
    "natural_area_zouit": NspdLayerSource("natural_area_zouit", 36940),
    "security_zouit": NspdLayerSource("security_zouit", 36940),
    "other_zouit": NspdLayerSource("other_zouit", 36940),
    "territorial_zones": NspdLayerSource("territorial_zones", 472819),
    "red_lines": NspdLayerSource("red_lines", 38942),
    "oopt": NspdLayerSource("oopt", 472825),
    "hunting_grounds": NspdLayerSource("hunting_grounds", 472827),
    "forestry": NspdLayerSource("forestry", 472847),
    "forest_park_boundary": NspdLayerSource("forest_park_boundary", 472853),
    "water_boundary_polygon": NspdLayerSource("water_boundary_polygon", 472813),
    "water_boundary_line": NspdLayerSource("water_boundary_line", 472816),
    "special_economic_zone": NspdLayerSource("special_economic_zone", 472826),
    "advanced_development_territory": NspdLayerSource("advanced_development_territory", 472828),
    "gambling_zone": NspdLayerSource("gambling_zone", 472846),
    "heritage_object_territory": NspdLayerSource("heritage_object_territory", 472820),
    "heritage_registry_territory": NspdLayerSource("heritage_registry_territory", 472820),
    "complex_cadastral_works": NspdLayerSource("complex_cadastral_works", 39228),
    "water_erosion": NspdLayerSource("water_erosion", 38967),
    "linear_erosion": NspdLayerSource("linear_erosion", 38967),
    "wind_erosion": NspdLayerSource("wind_erosion", 38967),
    "desertification": NspdLayerSource("desertification", 38967),
    "overmoistening": NspdLayerSource("overmoistening", 38967),
    "underflooding": NspdLayerSource("underflooding", 38967),
    "bogging": NspdLayerSource("bogging", 38967),
    "flooding": NspdLayerSource("flooding", 38967),
    "littering": NspdLayerSource("littering", 38967),
    "landslide": NspdLayerSource("landslide", 38967),
    "abrasion": NspdLayerSource("abrasion", 38967),
    "disturbed_land": NspdLayerSource("disturbed_land", 38967),
    "burned_area": NspdLayerSource("burned_area", 38967),
    "salinization": NspdLayerSource("salinization", 38967),
    "negative_process_absent": NspdLayerSource("negative_process_absent", 38967),
}


CATEGORY_DEFAULT_LAYER_KEY: dict[int, str] = {}
for source in NSPD_LAYER_SOURCES.values():
    CATEGORY_DEFAULT_LAYER_KEY.setdefault(source.category_id, source.layer_key)


def _feature_collection(geometry: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {},
            }
        ],
    }


def _feature_text(feature: dict[str, Any]) -> str:
    try:
        return json.dumps(feature.get("properties") or feature, ensure_ascii=False).casefold()
    except TypeError:
        return str(feature).casefold()


def _feature_category_id(feature: dict[str, Any], fallback: int) -> int:
    properties = feature.get("properties") or {}
    for key in ("category", "categoryId", "category_id"):
        value = properties.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return fallback


def _first_requested(candidates: tuple[str, ...], requested_keys: set[str]) -> str | None:
    for key in candidates:
        if key in requested_keys:
            return key
    return None


def _classify_zouit_feature(text: str, requested_keys: set[str]) -> str:
    if "культур" in text:
        return _first_requested(("cultural_heritage_zouit", "other_zouit"), requested_keys) or "cultural_heritage_zouit"
    if "водоохран" in text:
        return _first_requested(("water_protection_zone", "natural_area_zouit", "other_zouit"), requested_keys) or "water_protection_zone"
    if "прибреж" in text:
        return _first_requested(("coastal_protective_strip", "natural_area_zouit", "other_zouit"), requested_keys) or "coastal_protective_strip"
    if any(token in text for token in ("природ", "санитар", "водоохран", "прибреж", "курорт")):
        return _first_requested(("natural_area_zouit", "other_zouit"), requested_keys) or "natural_area_zouit"
    if any(token in text for token in ("энергет", "транспорт", "связ", "электр", "газопровод", "линия электропередач")):
        return _first_requested(("energy_transport_zouit", "other_zouit"), requested_keys) or "energy_transport_zouit"
    if any(token in text for token in ("безопасност", "охран", "защит")):
        return _first_requested(("security_zouit", "other_zouit"), requested_keys) or "security_zouit"
    return _first_requested(("other_zouit",), requested_keys) or "other_zouit"


def _classify_negative_process_feature(text: str, requested_keys: set[str]) -> str:
    checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("linear_erosion", ("линейн", "овраг", "промоин")),
        ("water_erosion", ("водн", "водная эроз")),
        ("wind_erosion", ("ветров", "дефляц")),
        ("desertification", ("опустын",)),
        ("overmoistening", ("переувлаж",)),
        ("underflooding", ("подтоп",)),
        ("bogging", ("заболач",)),
        ("flooding", ("затоп",)),
        ("littering", ("захлам",)),
        ("landslide", ("ополз", "обваль", "осып")),
        ("abrasion", ("абраз",)),
        ("burned_area", ("гари", "гарь", "выгор")),
        ("salinization", ("засол",)),
        ("negative_process_absent", ("отсутств",)),
        ("disturbed_land", ("наруш", "рекультив")),
    )
    for key, tokens in checks:
        if any(token in text for token in tokens):
            return key if key in requested_keys else _first_requested((key, "disturbed_land"), requested_keys) or key
    return _first_requested(("disturbed_land",), requested_keys) or "disturbed_land"


def classify_layer_key(feature: dict[str, Any], fallback_category_id: int, requested_keys: set[str]) -> str | None:
    category_id = _feature_category_id(feature, fallback_category_id)
    text = _feature_text(feature)

    if category_id == 36940:
        layer_key = _classify_zouit_feature(text, requested_keys)
    elif category_id == 38967:
        layer_key = _classify_negative_process_feature(text, requested_keys)
    elif category_id == 472820:
        layer_key = _first_requested(("heritage_object_territory", "heritage_registry_territory"), requested_keys)
        layer_key = layer_key or "heritage_object_territory"
    else:
        layer_key = CATEGORY_DEFAULT_LAYER_KEY.get(category_id)

    if layer_key not in requested_keys:
        return None
    return layer_key


def parcel_geometry_from_plot_raw(raw_payload: dict[str, Any]) -> dict[str, Any] | None:
    features = _extract_features(raw_payload)
    if not features:
        return None
    geometry = features[0].get("geometry")
    return geometry if isinstance(geometry, dict) else None


def selected_layer_keys(
    source_layer_keys: list[str],
    *,
    include_restrictions: bool,
    include_land_use: bool,
    include_real_estate_objects: bool,
    include_informational_layers: bool,
) -> set[str]:
    requested = set(source_layer_keys) if source_layer_keys else set(NSPD_LAYER_SOURCES)
    selected: set[str] = set()
    for key in requested:
        if key not in NSPD_LAYER_SOURCES:
            continue
        entry = get_catalog_entry(key)
        if entry is None:
            continue
        if entry.payload_kind == "restriction" and include_restrictions:
            selected.add(key)
        elif entry.payload_kind == "land_use" and include_land_use:
            selected.add(key)
        elif entry.payload_kind == "real_estate_object" and include_real_estate_objects:
            selected.add(key)
        elif entry.payload_kind in {"info", "valuation"} and include_informational_layers:
            selected.add(key)
    return selected


class NspdMapLayerClient:
    """Collects raw intersecting NSPD map features for known source layers."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        verify_ssl: bool | None = None,
    ) -> None:
        self.base_url = (base_url or settings.rosreestr_api_url).rstrip("/")
        self.timeout = timeout or settings.nspd_map_layers_timeout
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

    async def collect_raw_layers(
        self,
        *,
        parcel_geometry: dict[str, Any],
        source_layer_keys: list[str],
        include_restrictions: bool = True,
        include_land_use: bool = True,
        include_real_estate_objects: bool = True,
        include_informational_layers: bool = True,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
        requested_keys = selected_layer_keys(
            source_layer_keys,
            include_restrictions=include_restrictions,
            include_land_use=include_land_use,
            include_real_estate_objects=include_real_estate_objects,
            include_informational_layers=include_informational_layers,
        )
        category_ids = sorted({NSPD_LAYER_SOURCES[key].category_id for key in requested_keys})
        raw_features_by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        warnings: list[str] = []

        if not category_ids:
            return {}, ["nspd_map_layers_no_supported_layers_selected"]

        async with await self._client() as client:
            try:
                body = {
                    "geom": _feature_collection(parcel_geometry),
                    "categories": [{"id": category_id} for category_id in category_ids],
                }
                response = await client.post("/geoportal/v1/intersects?typeIntersect=fullObject", json=body)
                response.raise_for_status()
                features = _extract_features(response.json())
                for feature in features:
                    layer_key = classify_layer_key(feature, 0, requested_keys)
                    if layer_key is None:
                        continue
                    raw_features_by_layer[layer_key].append(feature)
                return dict(raw_features_by_layer), warnings
            except Exception as exc:
                warnings.append(f"nspd_map_layer_batch_failed:{exc}")

            for category_id in category_ids:
                body = {
                    "geom": _feature_collection(parcel_geometry),
                    "categories": [{"id": category_id}],
                }
                try:
                    response = await client.post("/geoportal/v1/intersects?typeIntersect=fullObject", json=body)
                    response.raise_for_status()
                    features = _extract_features(response.json())
                except Exception as exc:
                    warnings.append(f"nspd_map_layer_category_failed:{category_id}:{exc}")
                    continue

                for feature in features:
                    layer_key = classify_layer_key(feature, category_id, requested_keys)
                    if layer_key is None:
                        continue
                    raw_features_by_layer[layer_key].append(feature)

        return dict(raw_features_by_layer), warnings


def supported_live_layer_keys() -> set[str]:
    return {key for key in NSPD_LAYER_SOURCES if key in LAYER_CATALOG}
