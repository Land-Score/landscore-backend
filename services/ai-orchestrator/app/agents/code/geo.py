import json

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app.clients.geo_client import GeoClient


def _properties(layer: dict) -> dict:
    raw = layer.get("properties_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _valid_geometry_json(value) -> bool:
    if isinstance(value, dict):
        geometry = value
    else:
        try:
            geometry = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return False
    return geometry.get("type") in {
        "Point",
        "LineString",
        "Polygon",
        "MultiPoint",
        "MultiLineString",
        "MultiPolygon",
        "GeometryCollection",
    } and bool(geometry.get("coordinates") or geometry.get("geometries"))


def _to_geo_restriction_layer(layer: dict) -> dict:
    return {
        "id": layer.get("id", ""),
        "layer_type": layer.get("normalized_type") or layer.get("layer_type", ""),
        "name": layer.get("label") or layer.get("name", ""),
        "source": layer.get("source", ""),
        "geometry_geojson": layer.get("geometry_geojson", "{}"),
        "restrictions": layer.get("restrictions", []),
        "normative_basis": layer.get("normative_basis", []),
        "properties_json": layer.get("properties_json", "{}"),
    }


def _to_geo_land_use_layer(layer: dict) -> dict:
    props = _properties(layer)
    return {
        "id": layer.get("id", ""),
        "land_use_type": props.get("landUseType") or layer.get("normalized_type") or "unknown",
        "label": layer.get("label") or layer.get("source_layer_name", ""),
        "geometry_geojson": layer.get("geometry_geojson", "{}"),
        "source": layer.get("source", ""),
        "confidence": float(layer.get("confidence") or 0.0),
        "properties_json": layer.get("properties_json", "{}"),
    }


def _to_geo_real_estate_object(layer: dict) -> dict:
    props = _properties(layer)
    area = props.get("area") or props.get("specified_area") or props.get("declared_area") or 0
    try:
        area_sqm = float(area or 0)
    except (TypeError, ValueError):
        area_sqm = 0.0
    return {
        "cadastral_number": str(props.get("cadastralNumber") or ""),
        "object_type": props.get("objectType") or layer.get("normalized_type") or "unknown",
        "name": layer.get("label") or layer.get("source_layer_name", ""),
        "area_sqm": area_sqm,
        "geometry_geojson": layer.get("geometry_geojson", "{}"),
        "properties_json": layer.get("properties_json", "{}"),
    }


def _top_restrictions(layers: list[dict], limit: int = 5) -> list[dict]:
    def _num(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    visible = [layer for layer in layers if layer.get("show_in_report", True)]
    visible.sort(
        key=lambda layer: (_num(layer.get("counted_in_loss_ha")), _num(layer.get("intersection_ha"))),
        reverse=True,
    )
    return [
        {
            "id": layer.get("id", ""),
            "label": layer.get("label", ""),
            "name": layer.get("name", ""),
            "severity": layer.get("severity", ""),
            "area_loss_mode": layer.get("area_loss_mode", ""),
            "intersection_ha": layer.get("intersection_ha", 0),
            "counted_in_loss_ha": layer.get("counted_in_loss_ha", 0),
        }
        for layer in visible[:limit]
    ]


def _map_summary(analysis: dict, scenario: str) -> dict:
    layers = analysis.get("layers", [])
    return {
        "geometry_status": "present",
        "scenario": analysis.get("scenario") or scenario,
        "parcel_area_ha": analysis.get("parcel_area_ha"),
        "restricted_area_ha": analysis.get("restricted_area_ha"),
        "usable_area_ha": analysis.get("usable_area_ha"),
        "loss_percent": analysis.get("loss_percent"),
        "top_restrictions": _top_restrictions(layers),
        "land_use_composition": analysis.get("land_use_composition", []),
        "child_real_estate_objects_count": len(analysis.get("child_real_estate_objects", [])),
    }


class GeoAgent(Agent):
    name = "GeoAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        spatial_layers = ctx.get("spatial_layers") or ctx.get("DataRequestAgent", {}).get("spatial_layers")
        parcel_geometry_geojson = ctx.get("parcel_geometry_geojson") or (spatial_layers or {}).get("parcel_geometry_geojson", "")
        if not spatial_layers or not _valid_geometry_json(parcel_geometry_geojson):
            ctx.set("map_summary", {
                "geometry_status": "missing",
                "parcel_area_ha": None,
                "restricted_area_ha": None,
                "usable_area_ha": None,
                "loss_percent": None,
                "top_restrictions": [],
            })
            return AgentResult(
                success=True,
                data={
                    "geo_analysis_available": False,
                    "reason": "spatial layers or parcel geometry are missing; geo analysis skipped",
                },
            )

        scenario = "agriculture"
        if ctx.profile.preferred_scenarios:
            scenario = ctx.profile.preferred_scenarios[0]

        try:
            analysis = await GeoClient().analyze_land_use_restrictions(
                cadastral_number=ctx.plot.cadastral_number,
                scenario=scenario,
                parcel_geometry_geojson=parcel_geometry_geojson,
                parcel_area_ha=(ctx.plot.area / 10_000.0 if ctx.plot.area > 1000 else ctx.plot.area),
                restriction_layers=[
                    _to_geo_restriction_layer(layer)
                    for layer in spatial_layers.get("restriction_layers", [])
                ],
                land_use_layers=[
                    _to_geo_land_use_layer(layer)
                    for layer in spatial_layers.get("land_use_layers", [])
                ],
                real_estate_objects=[
                    _to_geo_real_estate_object(layer)
                    for layer in spatial_layers.get("real_estate_objects", [])
                ],
                vision_interpretation_json=ctx.get("vision_interpretation_json", ""),
            )
        except Exception as exc:
            return AgentResult(success=False, data={}, error=f"Geo Service restriction analysis failed: {exc}")

        ctx.set("geo_analysis", analysis)
        summary = _map_summary(analysis, scenario)
        ctx.set("map_summary", summary)
        return AgentResult(
            success=True,
            data={
                "geo_analysis_available": True,
                "scenario": scenario,
                "restricted_area_ha": analysis.get("restricted_area_ha", 0),
                "usable_area_ha": analysis.get("usable_area_ha", 0),
                "loss_percent": analysis.get("loss_percent", 0),
                "map_summary": summary,
                "analysis": analysis,
            },
        )
