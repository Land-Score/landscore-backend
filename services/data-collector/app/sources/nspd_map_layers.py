from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.config import settings
from app.layers.models import CollectedSpatialLayer
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


CADASTRAL_NUMBER_RE = re.compile(r"\b\d{1,2}:\d{1,2}:\d{1,10}:\d{1,10}(?:/\d+)?\b")


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


def _plot_feature_from_raw(raw_payload: dict[str, Any]) -> dict[str, Any] | None:
    features = _extract_features(raw_payload)
    if not features:
        return None
    feature = features[0]
    return feature if isinstance(feature, dict) else None


def _feature_category_id_for_tabs(feature: dict[str, Any]) -> int:
    props = feature.get("properties") or {}
    for value in (
        props.get("category"),
        props.get("categoryId"),
        props.get("category_id"),
        ((feature.get("meta") or {}) if isinstance(feature.get("meta"), dict) else {}).get("categoryId"),
    ):
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 36368


def _feature_geom_id(feature: dict[str, Any]) -> str:
    props = feature.get("properties") or {}
    value = feature.get("id") or props.get("interactionId") or props.get("id")
    return str(value or "")


def _feature_registers_id(feature: dict[str, Any]) -> str:
    props = feature.get("properties") or {}
    options = props.get("options") if isinstance(props.get("options"), dict) else {}
    value = props.get("registersId") or options.get("registersId") or options.get("register_id")
    return str(value or "")


def _collect_cadastral_numbers(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        out.update(match.group(0) for match in CADASTRAL_NUMBER_RE.finditer(value))
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_cadastral_numbers(item, out)
        return
    if isinstance(value, list):
        for item in value:
            _collect_cadastral_numbers(item, out)


def _geometry_from_feature(feature: dict[str, Any] | None) -> dict[str, Any] | None:
    if not feature:
        return None
    geometry = feature.get("geometry")
    return geometry if isinstance(geometry, dict) else None


def _attrs_from_feature(feature: dict[str, Any] | None) -> dict[str, Any]:
    if not feature:
        return {}
    props = feature.get("properties") or {}
    options = props.get("options") if isinstance(props.get("options"), dict) else {}
    attrs: dict[str, Any] = {}
    if isinstance(options, dict):
        attrs.update(options)
    if isinstance(props, dict):
        attrs.update(props)
    return attrs


def _child_layer(
    *,
    cadastral_number: str,
    layer_type: str,
    label: str,
    geometry: dict[str, Any] | None,
    source: str,
    properties: dict[str, Any] | None = None,
) -> CollectedSpatialLayer:
    props = dict(properties or {})
    props.setdefault("cadastralNumber", cadastral_number)
    props.setdefault("sourceLayerKey", layer_type)
    return CollectedSpatialLayer(
        id=f"{layer_type}:{cadastral_number}",
        source_layer_key=layer_type,
        source_layer_name=label,
        source_group="parcel_children",
        payload_kind="real_estate_object" if layer_type == "child_real_estate_object" else "info",
        normalized_type=layer_type,
        label=label,
        geometry=geometry,
        properties=props,
        source=source,
        confidence=0.7 if geometry else 0.45,
    )


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


class NspdChildObjectClient:
    """Collects NSPD tab data from parcel card: objects, land parts, composition."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        verify_ssl: bool | None = None,
    ) -> None:
        self.base_url = (base_url or settings.rosreestr_api_url).rstrip("/")
        self.timeout = timeout or settings.nspd_map_layers_timeout
        self.child_lookup_concurrency = max(1, int(settings.nspd_child_lookup_concurrency))
        self.child_lookup_limit = max(1, int(settings.nspd_child_lookup_limit))
        self.child_lookup_timeout = max(1.0, float(settings.nspd_child_lookup_timeout))
        self.child_lookup_total_timeout = max(self.child_lookup_timeout, float(settings.nspd_child_lookup_total_timeout))
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

    async def _get_first_successful_json(self, client: httpx.AsyncClient, paths: tuple[str, ...]) -> dict[str, Any]:
        last_error: Exception | None = None
        for path in paths:
            try:
                response = await client.get(path)
                response.raise_for_status()
                payload = response.json()
                if payload not in ({}, [], None):
                    return payload
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return {}

    async def _lookup_feature(self, client: httpx.AsyncClient, cadastral_number: str) -> dict[str, Any] | None:
        query = quote(cadastral_number.strip(), safe="")
        payload = await self._get_first_successful_json(
            client,
            (
                f"/geoportal/v2/search/geoportal?thematicSearchId=1&query={query}&CRS=EPSG:3857",
                f"/geoportal/v2/search/geoportal?query={query}&CRS=EPSG:3857",
                f"/geoportal/v1/search/geoportal?thematicSearchId=1&query={query}&CRS=EPSG:3857",
                f"/geoportal/v2/search/geoportal?query={query}",
                f"/geoportal/v1/search/geoportal?query={query}",
            ),
        )
        features = _extract_features(payload)
        return features[0] if features and isinstance(features[0], dict) else None

    async def _lookup_features_for_numbers(
        self,
        client: httpx.AsyncClient,
        numbers: list[str],
        *,
        warning_prefix: str,
    ) -> tuple[dict[str, dict[str, Any] | None], list[str]]:
        warnings: list[str] = []
        limited_numbers = numbers[: self.child_lookup_limit]
        if len(numbers) > len(limited_numbers):
            warnings.append(f"{warning_prefix}_truncated:{len(numbers)}>{len(limited_numbers)}")

        semaphore = asyncio.Semaphore(self.child_lookup_concurrency)

        async def lookup(number: str) -> tuple[str, dict[str, Any] | None, str | None]:
            async with semaphore:
                try:
                    feature = await asyncio.wait_for(
                        self._lookup_feature(client, number),
                        timeout=self.child_lookup_timeout,
                    )
                    return number, feature, None
                except Exception as exc:
                    return number, None, str(exc)

        result: dict[str, dict[str, Any] | None] = {}
        failed = 0
        tasks = [asyncio.create_task(lookup(number)) for number in limited_numbers]
        deadline = time.monotonic() + self.child_lookup_total_timeout

        for task in asyncio.as_completed(tasks, timeout=self.child_lookup_total_timeout):
            try:
                number, feature, error = await task
            except asyncio.TimeoutError:
                break
            except Exception:
                failed += 1
                continue
            result[number] = feature
            if error:
                failed += 1
            if time.monotonic() >= deadline:
                break

        for task in tasks:
            if not task.done():
                task.cancel()

        pending_count = len([task for task in tasks if not task.done()])
        if pending_count:
            warnings.append(f"{warning_prefix}_timeout_pending:{pending_count}")
        if failed:
            warnings.append(f"{warning_prefix}_lookup_failed:{failed}")

        return result, warnings

    async def collect_for_plot(
        self,
        *,
        cadastral_number: str,
        plot_raw_json: dict[str, Any],
    ) -> tuple[list[CollectedSpatialLayer], list[CollectedSpatialLayer], list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        child_objects: list[CollectedSpatialLayer] = []
        land_parts: list[CollectedSpatialLayer] = []
        land_composition: list[dict[str, Any]] = []

        feature = _plot_feature_from_raw(plot_raw_json)
        if feature is None:
            warnings.append("nspd_child_tabs_skipped_missing_plot_feature")
            return child_objects, land_parts, land_composition, warnings

        category_id = _feature_category_id_for_tabs(feature)
        geom_id = _feature_geom_id(feature)
        registers_id = _feature_registers_id(feature)
        if not geom_id:
            warnings.append("nspd_child_tabs_skipped_missing_geom_id")
            return child_objects, land_parts, land_composition, warnings

        async with await self._client() as client:
            async def collect_objects() -> tuple[list[CollectedSpatialLayer], list[str]]:
                local_warnings: list[str] = []
                local_layers: list[CollectedSpatialLayer] = []
                try:
                    objects_payload = await self._get_first_successful_json(
                        client,
                        (
                            f"/geoportal/v1/tab-group-data?tabClass=objectsList&categoryId={category_id}&geomId={geom_id}",
                            f"/geoportal/v1/tab-group-data?tabClass=objectsList&geomId={geom_id}&categoryId={category_id}",
                        ),
                    )
                    object_numbers: set[str] = set()
                    _collect_cadastral_numbers(objects_payload, object_numbers)
                    object_numbers.discard(cadastral_number)
                    child_features, lookup_warnings = await self._lookup_features_for_numbers(
                        client,
                        sorted(object_numbers),
                        warning_prefix="nspd_child_objects",
                    )
                    local_warnings.extend(lookup_warnings)
                    for number, child_feature in child_features.items():
                        local_layers.append(
                            _child_layer(
                                cadastral_number=number,
                                layer_type="child_real_estate_object",
                                label="Объект недвижимости",
                                geometry=_geometry_from_feature(child_feature),
                                source="nspd_tab_objects",
                                properties=_attrs_from_feature(child_feature),
                            )
                        )
                except Exception as exc:
                    local_warnings.append(f"nspd_child_objects_failed:{exc}")
                return local_layers, local_warnings

            async def collect_parts() -> tuple[list[CollectedSpatialLayer], list[str]]:
                local_warnings: list[str] = []
                local_layers: list[CollectedSpatialLayer] = []
                try:
                    parts_payload = await self._get_first_successful_json(
                        client,
                        (
                            f"/geoportal/v1/tab-values-data?tabClass=landParts&categoryId={category_id}&geomId={geom_id}",
                            f"/geoportal/v1/tab-values-data?tabClass=landParts&geomId={geom_id}&categoryId={category_id}",
                        ),
                    )
                    part_numbers: set[str] = set()
                    _collect_cadastral_numbers(parts_payload, part_numbers)
                    part_numbers.discard(cadastral_number)
                    part_features, lookup_warnings = await self._lookup_features_for_numbers(
                        client,
                        sorted(part_numbers),
                        warning_prefix="nspd_land_parts",
                    )
                    local_warnings.extend(lookup_warnings)
                    for number, part_feature in part_features.items():
                        local_layers.append(
                            _child_layer(
                                cadastral_number=number,
                                layer_type="land_part",
                                label="Часть земельного участка",
                                geometry=_geometry_from_feature(part_feature),
                                source="nspd_tab_land_parts",
                                properties=_attrs_from_feature(part_feature),
                            )
                        )
                except Exception as exc:
                    local_warnings.append(f"nspd_land_parts_failed:{exc}")
                return local_layers, local_warnings

            async def collect_composition() -> tuple[list[dict[str, Any]], list[str]]:
                local_warnings: list[str] = []
                try:
                    composition_paths = [
                        f"/geoportal/v1/tab-values-data?tabClass=compositionLand&categoryId={category_id}&geomId={geom_id}",
                        f"/geoportal/v1/tab-values-data?tabClass=compositionLand&geomId={geom_id}&categoryId={category_id}",
                    ]
                    if registers_id:
                        composition_paths.append(
                            f"/geoportal/v1/tab-values-data?tabClass=compositionLand&objdocId={geom_id}&registersId={registers_id}"
                        )
                    composition_payload = await self._get_first_successful_json(client, tuple(composition_paths))
                    if isinstance(composition_payload, list):
                        return [item for item in composition_payload if isinstance(item, dict)], local_warnings
                    if isinstance(composition_payload, dict):
                        return [composition_payload], local_warnings
                except Exception as exc:
                    local_warnings.append(f"nspd_land_composition_failed:{exc}")
                return [], local_warnings

            objects_result, parts_result, composition_result = await asyncio.gather(
                collect_objects(),
                collect_parts(),
                collect_composition(),
            )
            child_objects, object_warnings = objects_result
            land_parts, part_warnings = parts_result
            land_composition, composition_warnings = composition_result
            warnings.extend([*object_warnings, *part_warnings, *composition_warnings])

        return child_objects, land_parts, land_composition, warnings


def supported_live_layer_keys() -> set[str]:
    return {key for key in NSPD_LAYER_SOURCES if key in LAYER_CATALOG}
