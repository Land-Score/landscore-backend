from __future__ import annotations

import json
import os
import sys
from typing import Any

import grpc

from app.restrictions.calculator import calculate_land_use_restrictions
from app.restrictions.models import AgriculturalLandUseLayer, RealEstateObject, RestrictionAnalysisRequest, RestrictionLayer

PROTO_GEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
if PROTO_GEN_DIR not in sys.path:
    sys.path.insert(0, PROTO_GEN_DIR)

try:
    import geo_pb2
except ImportError:  # pragma: no cover - generated stubs may not exist in local smoke tests yet.
    geo_pb2 = None


def _message_or_dict(message_name: str, data: dict[str, Any]):
    if geo_pb2 is None:
        return data
    return getattr(geo_pb2, message_name)(**data)


def _json_geometry(value: str, field_name: str) -> dict[str, Any]:
    try:
        geometry = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid GeoJSON geometry") from exc
    if not isinstance(geometry, dict) or "type" not in geometry:
        raise ValueError(f"{field_name} must be valid GeoJSON geometry")
    return geometry


def _json_object(value: str) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


class GeoServicer:
    """Implements geo.proto GeoService business logic."""

    async def AnalyzePlotLocation(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Location analysis is not wired yet")
        return _message_or_dict("LocationAnalysis", {})

    async def SearchPlotsByArea(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Area search requires PostGIS index")
        return _message_or_dict("AreaSearchResponse", {"plots": []})

    async def GetDistances(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Distance service is not wired yet")
        return _message_or_dict("DistanceResponse", {"distances_km": {}})

    async def CheckProtectedZones(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Protected zone point lookup is not wired yet")
        return _message_or_dict("ZoneResponse", {"zone_types": [], "is_restricted": False, "details_json": "{}"})

    async def AnalyzeLandUseRestrictions(self, request, context):
        try:
            analysis_request = _restriction_request_from_proto_like(request)
            result = calculate_land_use_restrictions(analysis_request)
        except ValueError as exc:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return _message_or_dict("LandUseRestrictionResponse", {})
        return _message_or_dict("LandUseRestrictionResponse", _restriction_result_to_proto_dict(result))


def _restriction_request_from_proto_like(request) -> RestrictionAnalysisRequest:
    restriction_layers = [
        RestrictionLayer(
            id=layer.id,
            layer_type=layer.layer_type,
            name=layer.name,
            source=layer.source,
            geometry=_json_geometry(layer.geometry_geojson, "restriction_layer.geometry_geojson"),
            restrictions=list(layer.restrictions),
            normative_basis=list(layer.normative_basis),
            properties=_json_object(layer.properties_json),
        )
        for layer in request.restriction_layers
    ]
    land_use_layers = [
        AgriculturalLandUseLayer(
            id=layer.id,
            land_use_type=layer.land_use_type,
            label=layer.label,
            geometry=_json_geometry(layer.geometry_geojson, "land_use_layer.geometry_geojson"),
            source=layer.source,
            confidence=layer.confidence,
            properties=_json_object(layer.properties_json),
        )
        for layer in request.land_use_layers
    ]
    real_estate_objects = [
        RealEstateObject(
            cadastral_number=obj.cadastral_number,
            object_type=obj.object_type,
            name=obj.name,
            area_sqm=obj.area_sqm or None,
            geometry=_json_geometry(obj.geometry_geojson, "real_estate_object.geometry_geojson") if obj.geometry_geojson else None,
            properties=_json_object(obj.properties_json),
        )
        for obj in request.real_estate_objects
    ]
    vision = _json_object(getattr(request, "vision_interpretation_json", ""))
    return RestrictionAnalysisRequest(
        cadastral_number=request.cadastral_number,
        scenario=request.scenario or "agriculture",
        parcel_geometry=_json_geometry(request.parcel_geometry_geojson, "parcel_geometry_geojson"),
        parcel_area_ha=request.parcel_area_ha or None,
        restriction_layers=restriction_layers,
        land_use_layers=land_use_layers,
        real_estate_objects=real_estate_objects,
        vision_interpretation=vision or None,
    )


def _restriction_result_to_proto_dict(result) -> dict[str, Any]:
    return {
        "cadastral_number": result.cadastral_number,
        "scenario": result.scenario,
        "parcel_area_ha": result.parcel_area_ha,
        "restricted_area_ha": result.restricted_area_ha,
        "usable_area_ha": result.usable_area_ha,
        "loss_percent": result.loss_percent,
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
                "restrictions": layer.restrictions,
                "normative_basis": layer.normative_basis,
                "source": layer.source,
                "properties_json": json.dumps(layer.properties, ensure_ascii=False),
            }
            for layer in result.layers
        ],
        "land_use_composition": [item.model_dump() for item in result.land_use_composition],
        "child_real_estate_objects": [
            {
                "cadastral_number": item.cadastral_number,
                "object_type": item.object_type,
                "name": item.name,
                "area_sqm": item.area_sqm or 0,
                "intersection_ha": item.intersection_ha or 0,
                "inside_parcel": item.inside_parcel,
                "properties_json": json.dumps(item.properties, ensure_ascii=False),
            }
            for item in result.child_real_estate_objects
        ],
        "map_layers": [
            {
                "id": layer.id,
                "layer_type": layer.layer_type,
                "label": layer.label,
                "geojson": json.dumps(layer.geojson, ensure_ascii=False),
                "style_json": json.dumps(layer.style, ensure_ascii=False),
                "properties_json": json.dumps(layer.properties, ensure_ascii=False),
            }
            for layer in result.map_layers
        ],
        "warnings": result.warnings,
        "assumptions": result.assumptions,
        "result_json": result.model_dump_json(),
    }
