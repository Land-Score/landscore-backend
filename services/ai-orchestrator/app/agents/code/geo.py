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


class GeoAgent(Agent):
    name = "GeoAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        spatial_layers = ctx.get("spatial_layers") or ctx.get("DataRequestAgent", {}).get("spatial_layers")
        parcel_geometry_geojson = ctx.get("parcel_geometry_geojson") or (spatial_layers or {}).get("parcel_geometry_geojson", "")
        if not spatial_layers or not parcel_geometry_geojson:
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
        return AgentResult(
            success=True,
            data={
                "geo_analysis_available": True,
                "scenario": scenario,
                "restricted_area_ha": analysis.get("restricted_area_ha", 0),
                "usable_area_ha": analysis.get("usable_area_ha", 0),
                "loss_percent": analysis.get("loss_percent", 0),
                "analysis": analysis,
            },
        )
