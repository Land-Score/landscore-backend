import json
import math
import re
from typing import Any

import grpc
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.errors import raise_for_grpc
from app.models import (
    CadastralLookupRequest,
    CadastralLookupResponse,
    CadastralMapAnalysisRequest,
    CadastralMapAnalysisResponse,
    CadastralSpatialLayersRequest,
    CadastralSpatialLayersResponse,
)

router = APIRouter()

_CADASTRAL_NUMBER_RE = re.compile(r"^\d{2}:\d{2}:\d{1,12}:\d{1,12}$")
_MERCATOR_RADIUS = 6_378_137.0


def _loads(value: str) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _layer_to_dict(layer) -> dict[str, Any]:
    return {
        "id": layer.id,
        "source_layer_key": layer.source_layer_key,
        "source_layer_name": layer.source_layer_name,
        "source_group": layer.source_group,
        "payload_kind": layer.payload_kind,
        "normalized_type": layer.normalized_type,
        "label": layer.label,
        "geometry": _loads(layer.geometry_geojson),
        "source": layer.source,
        "confidence": layer.confidence,
        "restrictions": list(layer.restrictions),
        "normative_basis": list(layer.normative_basis),
        "properties": _loads(layer.properties_json),
    }


def _json_dict(value: str) -> dict[str, Any]:
    try:
        return _loads(value)
    except json.JSONDecodeError:
        return {}


def _layer_properties(layer) -> dict[str, Any]:
    return _json_dict(getattr(layer, "properties_json", "") or "{}")


def _to_geo_restriction_layer(layer) -> dict[str, Any]:
    return {
        "id": layer.id,
        "layer_type": layer.normalized_type,
        "name": layer.label,
        "source": layer.source,
        "geometry_geojson": layer.geometry_geojson,
        "restrictions": list(layer.restrictions),
        "normative_basis": list(layer.normative_basis),
        "properties_json": layer.properties_json,
    }


def _to_geo_land_use_layer(layer) -> dict[str, Any]:
    props = _layer_properties(layer)
    return {
        "id": layer.id,
        "land_use_type": props.get("landUseType") or layer.normalized_type or "unknown",
        "label": layer.label or layer.source_layer_name,
        "geometry_geojson": layer.geometry_geojson,
        "source": layer.source,
        "confidence": layer.confidence,
        "properties_json": layer.properties_json,
    }


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_geo_real_estate_object(layer) -> dict[str, Any]:
    props = _layer_properties(layer)
    area = props.get("area") or props.get("specified_area") or props.get("declared_area") or 0
    return {
        "cadastral_number": str(props.get("cadastralNumber") or props.get("cad_num") or ""),
        "object_type": props.get("objectType") or layer.normalized_type or "unknown",
        "name": layer.label or layer.source_layer_name,
        "area_sqm": _to_float(area),
        "geometry_geojson": layer.geometry_geojson,
        "properties_json": layer.properties_json,
    }


def _is_position(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], int | float)
        and isinstance(value[1], int | float)
    )


def _coords_need_projection(value: Any) -> bool:
    if _is_position(value):
        return abs(float(value[0])) > 180 or abs(float(value[1])) > 90
    if isinstance(value, list):
        return any(_coords_need_projection(item) for item in value)
    return False


def _web_mercator_to_wgs84(x: float, y: float) -> list[float]:
    lon = math.degrees(x / _MERCATOR_RADIUS)
    lat = math.degrees(2 * math.atan(math.exp(y / _MERCATOR_RADIUS)) - math.pi / 2)
    return [lon, lat]


def _transform_coords(value: Any, *, project: bool) -> Any:
    if _is_position(value):
        x = float(value[0])
        y = float(value[1])
        converted = _web_mercator_to_wgs84(x, y) if project else [x, y]
        if len(value) > 2:
            converted.extend(value[2:])
        return converted
    if isinstance(value, list):
        return [_transform_coords(item, project=project) for item in value]
    return value


def _geometry_crs_name(geometry: dict[str, Any]) -> str:
    crs = geometry.get("crs") or {}
    return str((crs.get("properties") or {}).get("name") or "")


def _geometry_to_wgs84(geometry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(geometry, dict):
        return {}
    geometry_type = geometry.get("type")
    if geometry_type == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [_geometry_to_wgs84(item) for item in geometry.get("geometries", []) if isinstance(item, dict)],
        }
    coordinates = geometry.get("coordinates")
    project = "3857" in _geometry_crs_name(geometry) or _coords_need_projection(coordinates)
    return {
        "type": geometry_type,
        "coordinates": _transform_coords(coordinates, project=project),
    }


def _collect_positions(value: Any, out: list[list[float]]) -> None:
    if _is_position(value):
        out.append([float(value[0]), float(value[1])])
        return
    if isinstance(value, list):
        for item in value:
            _collect_positions(item, out)


def _feature_collection_bbox(features: list[dict[str, Any]]) -> list[float]:
    positions: list[list[float]] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        _collect_positions(geometry.get("coordinates"), positions)
    if not positions:
        return []
    lons = [point[0] for point in positions]
    lats = [point[1] for point in positions]
    return [min(lons), min(lats), max(lons), max(lats)]


def _map_style(style: dict[str, Any]) -> dict[str, Any]:
    fill = style.get("fill") or style.get("fillColor") or "#60a5fa"
    stroke = style.get("stroke") or style.get("color") or fill
    opacity = _to_float(style.get("opacity") if "opacity" in style else style.get("fillOpacity") or 0.25)
    return {
        "color": stroke,
        "fillColor": fill,
        "weight": 2,
        "opacity": min(1.0, max(0.0, opacity + 0.25)),
        "fillOpacity": min(1.0, max(0.0, opacity)),
    }


def _geo_response_to_dict(response) -> dict[str, Any]:
    return {
        "cadastral_number": response.cadastral_number,
        "scenario": response.scenario,
        "parcel_area_ha": response.parcel_area_ha,
        "restricted_area_ha": response.restricted_area_ha,
        "usable_area_ha": response.usable_area_ha,
        "loss_percent": response.loss_percent,
        "layers": [
            {
                "id": layer.id,
                "layer_type": layer.layer_type,
                "label": layer.label,
                "group": layer.group,
                "name": layer.name,
                "severity": layer.severity,
                "area_loss_mode": layer.area_loss_mode,
                "show_in_report": layer.show_in_report,
                "intersection_ha": layer.intersection_ha,
                "counted_in_loss_ha": layer.counted_in_loss_ha,
                "overlap_not_double_counted_ha": layer.overlap_not_double_counted_ha,
                "restrictions": list(layer.restrictions),
                "normative_basis": list(layer.normative_basis),
                "source": layer.source,
                "properties": _json_dict(layer.properties_json),
            }
            for layer in response.layers
        ],
        "land_use_composition": [
            {
                "land_use_type": item.land_use_type,
                "label": item.label,
                "area_ha": item.area_ha,
                "share_percent": item.share_percent,
                "source": item.source,
                "confidence": item.confidence,
            }
            for item in response.land_use_composition
        ],
        "child_real_estate_objects": [
            {
                "cadastral_number": item.cadastral_number,
                "object_type": item.object_type,
                "name": item.name,
                "area_sqm": item.area_sqm,
                "intersection_ha": item.intersection_ha,
                "inside_parcel": item.inside_parcel,
                "properties": _json_dict(item.properties_json),
            }
            for item in response.child_real_estate_objects
        ],
        "warnings": list(response.warnings),
        "assumptions": list(response.assumptions),
        "raw": _json_dict(response.result_json),
    }


def _map_response_from_geo(response) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for layer in response.map_layers:
        geometry = _geometry_to_wgs84(_json_dict(layer.geojson))
        style = _map_style(_json_dict(layer.style_json))
        feature = {
            "type": "Feature",
            "id": layer.id,
            "geometry": geometry,
            "properties": {
                **_json_dict(layer.properties_json),
                "id": layer.id,
                "layerType": layer.layer_type,
                "label": layer.label,
                "style": style,
            },
        }
        features.append(feature)

    feature_collection = {"type": "FeatureCollection", "features": features}
    bbox = _feature_collection_bbox(features)
    if bbox:
        feature_collection["bbox"] = bbox
    return {
        "crs": "EPSG:4326",
        "bbox": bbox,
        "feature_collection": feature_collection,
        "layers": [
            {
                "id": feature.get("id"),
                "layer_type": feature.get("properties", {}).get("layerType"),
                "label": feature.get("properties", {}).get("label"),
                "style": feature.get("properties", {}).get("style", {}),
                "feature": feature,
            }
            for feature in features
        ],
    }


@router.post(
    "/lookup",
    response_model=CadastralLookupResponse,
    summary="Получить данные по кадастровому номеру",
    description=(
        "Публичная ручка без авторизации. Принимает кадастровый номер и возвращает "
        "собранный data-collector набор: кадастровые данные, почвы, инфраструктуру, "
        "рыночные сигналы и предупреждения."
    ),
)
async def lookup_cadastral_plot(body: CadastralLookupRequest, request: Request) -> CadastralLookupResponse:
    cadastral_number = body.cadastral_number.strip()
    if not _CADASTRAL_NUMBER_RE.match(cadastral_number):
        raise HTTPException(status_code=400, detail="Кадастровый номер должен быть в формате 26:11:101101:53")

    import data_collector_pb2

    try:
        response = await request.app.state.data_collector_stub.CollectPlotDataset(
            data_collector_pb2.CadastralRequest(cadastral_number=cadastral_number),
            timeout=settings.cadastral_lookup_timeout,
        )
    except grpc.RpcError as exc:
        raise_for_grpc(exc)

    try:
        return CadastralLookupResponse(
            success=response.success,
            cadastral_number=response.cadastral_number or cadastral_number,
            source=response.source,
            nspd=_loads(response.nspd_json),
            soil=_loads(response.soil_json),
            infrastructure=_loads(response.infrastructure_json),
            market_liquidity=_loads(response.market_json),
            warnings=list(response.warnings),
            raw=_loads(response.raw_json),
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"data-collector returned invalid JSON: {exc}") from exc


@router.post(
    "/spatial-layers",
    response_model=CadastralSpatialLayersResponse,
    summary="Проверить слои карты NSPD по кадастровому номеру",
    description=(
        "Тестовая ручка для проверки парсинга многослойной карты NSPD. "
        "Если `parcel_geometry` не передан, data-collector сначала получает геометрию участка по кадастровому номеру, "
        "а затем собирает пересечения с поддержанными слоями `nspd.gov.ru/map`."
    ),
)
async def collect_cadastral_spatial_layers(
    body: CadastralSpatialLayersRequest,
    request: Request,
) -> CadastralSpatialLayersResponse:
    cadastral_number = body.cadastral_number.strip()
    if not _CADASTRAL_NUMBER_RE.match(cadastral_number):
        raise HTTPException(status_code=400, detail="Кадастровый номер должен быть в формате 26:11:101101:53")

    import data_collector_pb2

    grpc_request = data_collector_pb2.SpatialLayersRequest(
        cadastral_number=cadastral_number,
        parcel_geometry_geojson=json.dumps(body.parcel_geometry or {}, ensure_ascii=False) if body.parcel_geometry else "",
        raw_features_by_layer_json=json.dumps(body.raw_features_by_layer or {}, ensure_ascii=False),
        include_restrictions=body.include_restrictions,
        include_land_use=body.include_land_use,
        include_real_estate_objects=body.include_real_estate_objects,
        include_informational_layers=body.include_informational_layers,
        use_cache=True,
    )
    grpc_request.source_layer_keys.extend(body.source_layer_keys)

    try:
        response = await request.app.state.data_collector_stub.CollectPlotSpatialLayers(
            grpc_request,
            timeout=settings.cadastral_lookup_timeout,
        )
    except grpc.RpcError as exc:
        raise_for_grpc(exc)

    try:
        return CadastralSpatialLayersResponse(
            success=True,
            cadastral_number=response.cadastral_number or cadastral_number,
            parcel_geometry=_loads(response.parcel_geometry_geojson),
            restriction_layers=[_layer_to_dict(layer) for layer in response.restriction_layers],
            land_use_layers=[_layer_to_dict(layer) for layer in response.land_use_layers],
            real_estate_objects=[_layer_to_dict(layer) for layer in response.real_estate_objects],
            valuation_layers=[_layer_to_dict(layer) for layer in response.valuation_layers],
            informational_layers=[_layer_to_dict(layer) for layer in response.informational_layers],
            warnings=list(response.warnings),
            raw=_loads(response.raw_json),
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"data-collector returned invalid JSON: {exc}") from exc


@router.post(
    "/map-analysis",
    response_model=CadastralMapAnalysisResponse,
    summary="Собрать слои NSPD и подготовить карту с расчетом площадей",
    description=(
        "Фронтовая ручка для карты: собирает spatial layers в data-collector, "
        "считает площади и пересечения в geo-service, затем возвращает map-ready FeatureCollection в EPSG:4326."
    ),
)
async def collect_cadastral_map_analysis(
    body: CadastralMapAnalysisRequest,
    request: Request,
) -> CadastralMapAnalysisResponse:
    cadastral_number = body.cadastral_number.strip()
    if not _CADASTRAL_NUMBER_RE.match(cadastral_number):
        raise HTTPException(status_code=400, detail="Кадастровый номер должен быть в формате 26:11:101101:53")

    import data_collector_pb2
    import geo_pb2

    spatial_request = data_collector_pb2.SpatialLayersRequest(
        cadastral_number=cadastral_number,
        parcel_geometry_geojson=json.dumps(body.parcel_geometry or {}, ensure_ascii=False) if body.parcel_geometry else "",
        raw_features_by_layer_json=json.dumps(body.raw_features_by_layer or {}, ensure_ascii=False),
        include_restrictions=body.include_restrictions,
        include_land_use=body.include_land_use,
        include_real_estate_objects=body.include_real_estate_objects,
        include_informational_layers=body.include_informational_layers,
        use_cache=True,
    )
    spatial_request.source_layer_keys.extend(body.source_layer_keys)

    try:
        spatial_response = await request.app.state.data_collector_stub.CollectPlotSpatialLayers(
            spatial_request,
            timeout=settings.cadastral_lookup_timeout,
        )
    except grpc.RpcError as exc:
        raise_for_grpc(exc)

    if not spatial_response.parcel_geometry_geojson or spatial_response.parcel_geometry_geojson == "{}":
        return CadastralMapAnalysisResponse(
            success=False,
            cadastral_number=cadastral_number,
            scenario=body.scenario,
            warnings=[*list(spatial_response.warnings), "parcel_geometry_missing_after_spatial_collection"],
            spatial_layers={
                "restriction_layers_count": len(spatial_response.restriction_layers),
                "land_use_layers_count": len(spatial_response.land_use_layers),
                "real_estate_objects_count": len(spatial_response.real_estate_objects),
            },
        )

    geo_request = geo_pb2.LandUseRestrictionRequest(
        cadastral_number=cadastral_number,
        scenario=body.scenario,
        parcel_geometry_geojson=spatial_response.parcel_geometry_geojson,
        parcel_area_ha=0.0,
        restriction_layers=[
            geo_pb2.RestrictionLayer(**_to_geo_restriction_layer(layer))
            for layer in spatial_response.restriction_layers
        ],
        land_use_layers=[
            geo_pb2.LandUseLayer(**_to_geo_land_use_layer(layer))
            for layer in spatial_response.land_use_layers
        ],
        real_estate_objects=[
            geo_pb2.RealEstateObjectLayer(**_to_geo_real_estate_object(layer))
            for layer in spatial_response.real_estate_objects
        ],
    )

    try:
        geo_response = await request.app.state.geo_stub.AnalyzeLandUseRestrictions(
            geo_request,
            timeout=settings.cadastral_lookup_timeout,
        )
    except grpc.RpcError as exc:
        raise_for_grpc(exc)

    analysis = _geo_response_to_dict(geo_response)
    map_payload = _map_response_from_geo(geo_response)
    spatial_summary = {
        "parcel_geometry_present": True,
        "restriction_layers_count": len(spatial_response.restriction_layers),
        "land_use_layers_count": len(spatial_response.land_use_layers),
        "real_estate_objects_count": len(spatial_response.real_estate_objects),
        "valuation_layers_count": len(spatial_response.valuation_layers),
        "informational_layers_count": len(spatial_response.informational_layers),
        "warnings": list(spatial_response.warnings),
    }

    return CadastralMapAnalysisResponse(
        success=True,
        cadastral_number=cadastral_number,
        scenario=geo_response.scenario or body.scenario,
        analysis=analysis,
        map=map_payload,
        spatial_layers=spatial_summary,
        warnings=[*list(spatial_response.warnings), *list(geo_response.warnings)],
    )
