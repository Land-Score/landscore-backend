from __future__ import annotations

from collections import defaultdict
import math
from typing import Any

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.restrictions.models import (
    ChildRealEstateObjectImpact,
    LandUseCompositionItem,
    MapLayerOutput,
    RestrictionAnalysisRequest,
    RestrictionAnalysisResult,
    RestrictionLayerImpact,
)
from app.restrictions.rules import get_rule


SQM_PER_HECTARE = 10_000.0
WEB_MERCATOR_RADIUS_M = 6_378_137.0


def _geometry_from_geojson(geometry: dict[str, Any]) -> BaseGeometry:
    parsed = shape(geometry)
    if not parsed.is_valid:
        parsed = parsed.buffer(0)
    return parsed


def _is_position(value: Any) -> bool:
    return (
        isinstance(value, list | tuple)
        and len(value) >= 2
        and isinstance(value[0], int | float)
        and isinstance(value[1], int | float)
    )


def _transform_coordinates(value: Any, point_transform) -> Any:
    if _is_position(value):
        transformed = list(point_transform(float(value[0]), float(value[1])))
        if len(value) > 2:
            transformed.extend(value[2:])
        return transformed
    if isinstance(value, list | tuple):
        return [_transform_coordinates(item, point_transform) for item in value]
    return value


def _transform_geojson_geometry(geometry: dict[str, Any], point_transform) -> dict[str, Any]:
    if geometry.get("type") == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [
                _transform_geojson_geometry(item, point_transform)
                for item in geometry.get("geometries", [])
                if isinstance(item, dict)
            ],
        }
    return {
        "type": geometry.get("type"),
        "coordinates": _transform_coordinates(geometry.get("coordinates"), point_transform),
    }


def _web_mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / WEB_MERCATOR_RADIUS_M)
    lat = math.degrees(2 * math.atan(math.exp(y / WEB_MERCATOR_RADIUS_M)) - math.pi / 2)
    return lon, lat


def _lonlat_to_local_meters(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    lat0_rad = math.radians(lat0)
    return (
        WEB_MERCATOR_RADIUS_M * math.radians(lon - lon0) * math.cos(lat0_rad),
        WEB_MERCATOR_RADIUS_M * math.radians(lat - lat0),
    )


def _is_lonlat_geometry(geometry: BaseGeometry) -> bool:
    minx, miny, maxx, maxy = geometry.bounds
    return -180 <= minx <= 180 and -180 <= maxx <= 180 and -90 <= miny <= 90 and -90 <= maxy <= 90


def _is_web_mercator_geometry(geometry: BaseGeometry) -> bool:
    minx, miny, maxx, maxy = geometry.bounds
    return max(abs(minx), abs(maxx)) > 180 or max(abs(miny), abs(maxy)) > 90


def _metric_geometry_for_area(geometry: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty:
        return geometry

    centroid = geometry.centroid
    geojson = mapping(geometry)
    if _is_lonlat_geometry(geometry):
        lon0, lat0 = centroid.x, centroid.y

        def project_lonlat(lon: float, lat: float) -> tuple[float, float]:
            return _lonlat_to_local_meters(lon, lat, lon0, lat0)

        return shape(_transform_geojson_geometry(geojson, project_lonlat))

    if _is_web_mercator_geometry(geometry):
        lon0, lat0 = _web_mercator_to_lonlat(centroid.x, centroid.y)

        def project_web_mercator(x: float, y: float) -> tuple[float, float]:
            lon, lat = _web_mercator_to_lonlat(x, y)
            return _lonlat_to_local_meters(lon, lat, lon0, lat0)

        return shape(_transform_geojson_geometry(geojson, project_web_mercator))

    return geometry


def _area_ha(geometry: BaseGeometry | None) -> float:
    if geometry is None or geometry.is_empty:
        return 0.0
    metric_geometry = _metric_geometry_for_area(geometry)
    return max(0.0, metric_geometry.area / SQM_PER_HECTARE)


def _round_ha(value: float) -> float:
    return round(value, 4)


def _round_percent(value: float) -> float:
    return round(value, 2)


def _style_for_layer(layer_type: str, severity: str) -> dict[str, Any]:
    if severity == "hard_limit":
        return {"fill": "#ef4444", "stroke": "#991b1b", "opacity": 0.35}
    if severity == "restricted_use":
        return {"fill": "#f59e0b", "stroke": "#92400e", "opacity": 0.32}
    if layer_type.startswith("land_use"):
        return {"fill": "#84cc16", "stroke": "#3f6212", "opacity": 0.35}
    return {"fill": "#60a5fa", "stroke": "#1d4ed8", "opacity": 0.22}


def _scenario_effect_from_data_collector(properties: dict[str, Any], scenario: str) -> tuple[str | None, bool | None]:
    """Read Data Collector scenario policy if it was supplied with the layer."""

    effects = properties.get("scenarioEffects")
    if not isinstance(effects, dict):
        return None, None
    effect = effects.get(scenario) or effects.get("*")
    if not isinstance(effect, dict):
        return None, None
    mode = effect.get("area_loss_mode")
    show = effect.get("show_in_report")
    return (str(mode) if mode else None, bool(show) if show is not None else None)


def _land_use_label(land_use_type: str) -> str:
    return {
        "arable": "Пашня",
        "pasture": "Пастбища",
        "hayfield": "Сенокосы",
        "fallow": "Залежь",
        "perennial_planting": "Многолетние насаждения",
        "forest": "Лес",
        "water": "Водные объекты",
        "built_up": "Застроенная территория",
        "unknown": "Не классифицировано",
    }.get(land_use_type, land_use_type)


def calculate_land_use_restrictions(request: RestrictionAnalysisRequest) -> RestrictionAnalysisResult:
    parcel = _geometry_from_geojson(request.parcel_geometry)
    parcel_area_ha = request.parcel_area_ha or _area_ha(parcel)
    warnings: list[str] = []
    assumptions = [
        "Площади считаются после локального пересчета в метровую плоскость; EPSG:3857 и WGS84 не используются напрямую как равноплощадные системы.",
        "Нейрозрение может классифицировать карту/легенду, но итоговые гектары должны подтверждаться геометрическими слоями.",
    ]

    counted_union_parts: list[BaseGeometry] = []
    layer_impacts: list[RestrictionLayerImpact] = []
    map_layers: list[MapLayerOutput] = [
        MapLayerOutput(
            id="parcel",
            layer_type="parcel_boundary",
            label="Граница земельного участка",
            geojson=request.parcel_geometry,
            style={"stroke": "#111827", "fill": "#ffffff", "opacity": 0.06},
        )
    ]

    for layer in request.restriction_layers:
        rule = get_rule(layer.layer_type)
        layer_geometry = _geometry_from_geojson(layer.geometry)
        intersection = parcel.intersection(layer_geometry)
        intersection_ha = _area_ha(intersection)
        collector_mode, collector_show = _scenario_effect_from_data_collector(layer.properties, request.scenario)
        mode = collector_mode or rule.loss_mode(request.scenario)
        show = rule.show_in_report(request.scenario) if collector_show is None else collector_show
        counted_in_loss_ha = 0.0
        overlap_not_double_counted_ha = 0.0

        if not intersection.is_empty and mode == "exclude_from_usable":
            before = unary_union(counted_union_parts) if counted_union_parts else None
            before_ha = _area_ha(before)
            counted_union_parts.append(intersection)
            after_ha = _area_ha(unary_union(counted_union_parts))
            counted_in_loss_ha = max(0.0, after_ha - before_ha)
            overlap_not_double_counted_ha = max(0.0, intersection_ha - counted_in_loss_ha)

        restrictions = layer.restrictions or rule.default_restrictions
        normative_basis = layer.normative_basis or rule.normative_basis
        label = str(layer.properties.get("sourceLayerName") or rule.label)
        group = str(layer.properties.get("sourceGroup") or rule.group)

        if intersection_ha > 0 or show:
            layer_impacts.append(
                RestrictionLayerImpact(
                    id=layer.id,
                    layer_type=layer.layer_type,
                    label=label,
                    group=group,
                    name=layer.name or label,
                    severity=rule.severity,
                    area_loss_mode=mode,
                    show_in_report=show,
                    intersection_ha=_round_ha(intersection_ha),
                    counted_in_loss_ha=_round_ha(counted_in_loss_ha),
                    overlap_not_double_counted_ha=_round_ha(overlap_not_double_counted_ha),
                    restrictions=restrictions,
                    normative_basis=normative_basis,
                    source=layer.source,
                    properties=layer.properties,
                )
            )

        if not intersection.is_empty and show:
            map_layers.append(
                MapLayerOutput(
                    id=f"restriction_{layer.id}",
                    layer_type=layer.layer_type,
                    label=layer.name or label,
                    geojson=intersection.__geo_interface__,
                    style=_style_for_layer(layer.layer_type, rule.severity),
                    properties={
                        "severity": rule.severity,
                        "areaLossMode": mode,
                        "intersectionHa": _round_ha(intersection_ha),
                        "countedInLossHa": _round_ha(counted_in_loss_ha),
                    },
                )
            )

    restricted_geometry = unary_union(counted_union_parts) if counted_union_parts else None
    restricted_area_ha = min(parcel_area_ha, _area_ha(restricted_geometry))
    usable_area_ha = max(0.0, parcel_area_ha - restricted_area_ha)
    loss_percent = (restricted_area_ha / parcel_area_ha * 100.0) if parcel_area_ha else 0.0

    if restricted_geometry is not None and not restricted_geometry.is_empty:
        map_layers.append(
            MapLayerOutput(
                id="restriction_union_loss_area",
                layer_type="restriction_union",
                label="Площадь, исключаемая из полезной по сценарию",
                geojson=restricted_geometry.__geo_interface__,
                style={"fill": "#dc2626", "stroke": "#7f1d1d", "opacity": 0.42},
                properties={"areaHa": _round_ha(restricted_area_ha), "scenario": request.scenario},
            )
        )

    land_use_composition = _calculate_land_use(parcel, parcel_area_ha, request.land_use_layers, warnings, map_layers)
    child_objects = _calculate_child_objects(parcel, request.real_estate_objects, map_layers)

    if request.vision_interpretation and request.vision_interpretation.enabled:
        warnings.extend(request.vision_interpretation.warnings)
        assumptions.append("Классификация нейрозрением должна иметь confidence и не заменяет координатный каталог/геометрию.")

    return RestrictionAnalysisResult(
        cadastral_number=request.cadastral_number,
        scenario=request.scenario,
        parcel_area_ha=_round_ha(parcel_area_ha),
        restricted_area_ha=_round_ha(restricted_area_ha),
        usable_area_ha=_round_ha(usable_area_ha),
        loss_percent=_round_percent(loss_percent),
        layers=sorted(layer_impacts, key=lambda item: (item.counted_in_loss_ha == 0, item.group, item.label)),
        land_use_composition=land_use_composition,
        child_real_estate_objects=child_objects,
        map_layers=map_layers,
        warnings=warnings,
        assumptions=assumptions,
    )


def _calculate_land_use(
    parcel: BaseGeometry,
    parcel_area_ha: float,
    land_use_layers: list[Any],
    warnings: list[str],
    map_layers: list[MapLayerOutput],
) -> list[LandUseCompositionItem]:
    if not land_use_layers:
        warnings.append("Состав угодий не рассчитан: отсутствуют входные слои пашни/пастбищ/сенокосов/залежи.")
        return []

    areas: dict[str, float] = defaultdict(float)
    confidence_sum: dict[str, float] = defaultdict(float)
    sources: dict[str, set[str]] = defaultdict(set)

    for layer in land_use_layers:
        geom = _geometry_from_geojson(layer.geometry)
        intersection = parcel.intersection(geom)
        area = _area_ha(intersection)
        if area <= 0:
            continue
        areas[layer.land_use_type] += area
        confidence_sum[layer.land_use_type] += layer.confidence * area
        if layer.source:
            sources[layer.land_use_type].add(layer.source)
        map_layers.append(
            MapLayerOutput(
                id=f"land_use_{layer.id}",
                layer_type=f"land_use_{layer.land_use_type}",
                label=layer.label or _land_use_label(layer.land_use_type),
                geojson=intersection.__geo_interface__,
                style=_style_for_layer(f"land_use_{layer.land_use_type}", "land_use"),
                properties={"areaHa": _round_ha(area), "source": layer.source, "confidence": layer.confidence},
            )
        )

    result = []
    for land_use_type, area in sorted(areas.items(), key=lambda item: item[0]):
        result.append(
            LandUseCompositionItem(
                land_use_type=land_use_type,
                label=_land_use_label(land_use_type),
                area_ha=_round_ha(area),
                share_percent=_round_percent(area / parcel_area_ha * 100.0 if parcel_area_ha else 0.0),
                source=", ".join(sorted(sources[land_use_type])),
                confidence=round(confidence_sum[land_use_type] / area, 3) if area else 0.0,
            )
        )
    return result


def _calculate_child_objects(
    parcel: BaseGeometry,
    real_estate_objects: list[Any],
    map_layers: list[MapLayerOutput],
) -> list[ChildRealEstateObjectImpact]:
    result = []
    for obj in real_estate_objects:
        intersection_ha = None
        inside = False
        if obj.geometry:
            geom = _geometry_from_geojson(obj.geometry)
            intersection = parcel.intersection(geom)
            intersection_ha = _round_ha(_area_ha(intersection))
            inside = intersection_ha > 0 or parcel.contains(geom)
            if inside:
                map_layers.append(
                    MapLayerOutput(
                        id=f"real_estate_{obj.cadastral_number}",
                        layer_type=f"real_estate_{obj.object_type}",
                        label=obj.name or obj.cadastral_number,
                        geojson=intersection.__geo_interface__ if not intersection.is_empty else geom.__geo_interface__,
                        style={"fill": "#a855f7", "stroke": "#581c87", "opacity": 0.35},
                        properties={"cadastralNumber": obj.cadastral_number, "objectType": obj.object_type},
                    )
                )
        result.append(
            ChildRealEstateObjectImpact(
                cadastral_number=obj.cadastral_number,
                object_type=obj.object_type,
                name=obj.name,
                area_sqm=obj.area_sqm,
                intersection_ha=intersection_ha,
                inside_parcel=inside,
                properties=obj.properties,
            )
        )
    return result
