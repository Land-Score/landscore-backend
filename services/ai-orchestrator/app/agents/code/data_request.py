import json
from typing import Any

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app.clients.data_collector_client import DataCollectorClient


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _valid_geometry(value: Any) -> bool:
    geometry = _json_dict(value)
    return geometry.get("type") in {
        "Point",
        "LineString",
        "Polygon",
        "MultiPoint",
        "MultiLineString",
        "MultiPolygon",
        "GeometryCollection",
    } and bool(geometry.get("coordinates") or geometry.get("geometries"))


def _first_feature_geometry(raw_payload: dict[str, Any]) -> dict[str, Any]:
    features = ((raw_payload.get("data") or {}).get("features") or raw_payload.get("features") or [])
    if not isinstance(features, list):
        return {}
    for feature in features:
        if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict):
            geometry = feature["geometry"]
            if _valid_geometry(geometry):
                return geometry
    return {}


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _apply_nspd_to_plot(ctx: AgentContext, nspd: dict[str, Any]) -> None:
    if not nspd:
        return
    ctx.plot.cadastral_number = nspd.get("cadastral_number") or ctx.plot.cadastral_number
    ctx.plot.address = nspd.get("address") or ctx.plot.address
    ctx.plot.area = _to_float(nspd.get("area")) or ctx.plot.area
    ctx.plot.category = nspd.get("category") or ctx.plot.category
    ctx.plot.allowed_use = nspd.get("allowed_use") or ctx.plot.allowed_use
    ctx.plot.owner_type = nspd.get("owner_type") or ctx.plot.owner_type
    ctx.plot.lat = _to_float(nspd.get("lat")) or ctx.plot.lat
    ctx.plot.lng = _to_float(nspd.get("lng")) or ctx.plot.lng
    ctx.plot.price = _to_float(nspd.get("price")) or ctx.plot.price
    ctx.plot.egrn_data = {"nspd_status": nspd.get("status") or ""}


class DataRequestAgent(Agent):
    name = "DataRequestAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        parcel_geometry_geojson = ctx.get("parcel_geometry_geojson", "")
        raw_features_by_layer_json = ctx.get("raw_features_by_layer_json", "{}")
        cadastral_number = getattr(ctx.plot, "cadastral_number", "")
        if not parcel_geometry_geojson and not cadastral_number:
            return AgentResult(
                success=True,
                data={
                    "spatial_layers_available": False,
                    "reason": "parcel_geometry_geojson and cadastral_number are missing; spatial layer collection skipped",
                },
            )

        client = DataCollectorClient()
        dataset: dict[str, Any] = {}
        nspd: dict[str, Any] = {}
        warnings: list[str] = []

        if cadastral_number:
            try:
                dataset = await client.collect_plot_dataset(cadastral_number)
                nspd = _json_dict(dataset.get("nspd_json"))
                _apply_nspd_to_plot(ctx, nspd)
                ctx.set("plot_dataset", dataset)
                ctx.set("nspd", nspd)
            except Exception as exc:
                warnings.append(f"plot_dataset_collection_failed:{exc}")

        if not _valid_geometry(parcel_geometry_geojson):
            geometry = _first_feature_geometry(_json_dict(nspd.get("raw_json")))
            if geometry:
                parcel_geometry_geojson = json.dumps(geometry, ensure_ascii=False)
                ctx.set("parcel_geometry_geojson", parcel_geometry_geojson)

        try:
            spatial_layers = await client.collect_spatial_layers(
                cadastral_number=cadastral_number,
                parcel_geometry_geojson=parcel_geometry_geojson if _valid_geometry(parcel_geometry_geojson) else "",
                raw_features_by_layer_json=raw_features_by_layer_json,
            )
        except Exception as exc:
            warnings.append(f"spatial_layer_collection_failed:{exc}")
            return AgentResult(
                success=True,
                data={
                    "dataset_available": bool(dataset),
                    "nspd": nspd,
                    "spatial_layers_available": False,
                    "warnings": warnings,
                },
            )

        ctx.set("spatial_layers", spatial_layers)
        spatial_warnings = list(spatial_layers.get("warnings", []))
        return AgentResult(
            success=True,
            data={
                "dataset_available": bool(dataset),
                "nspd": nspd,
                "spatial_layers_available": True,
                "restriction_layers_count": len(spatial_layers.get("restriction_layers", [])),
                "land_use_layers_count": len(spatial_layers.get("land_use_layers", [])),
                "real_estate_objects_count": len(spatial_layers.get("real_estate_objects", [])),
                "child_real_estate_objects_count": len(spatial_layers.get("child_real_estate_objects", [])),
                "land_parts_count": len(spatial_layers.get("land_parts", [])),
                "spatial_layers": spatial_layers,
                "warnings": warnings + spatial_warnings,
            },
        )
