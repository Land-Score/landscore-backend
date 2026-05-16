from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app.clients.data_collector_client import DataCollectorClient

class DataRequestAgent(Agent):
    name = "DataRequestAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        parcel_geometry_geojson = ctx.get("parcel_geometry_geojson", "")
        raw_features_by_layer_json = ctx.get("raw_features_by_layer_json", "{}")
        if not parcel_geometry_geojson:
            return AgentResult(
                success=True,
                data={
                    "spatial_layers_available": False,
                    "reason": "parcel_geometry_geojson is missing; spatial layer collection skipped",
                },
            )

        try:
            spatial_layers = await DataCollectorClient().collect_spatial_layers(
                cadastral_number=ctx.plot.cadastral_number,
                parcel_geometry_geojson=parcel_geometry_geojson,
                raw_features_by_layer_json=raw_features_by_layer_json,
            )
        except Exception as exc:
            return AgentResult(success=False, data={}, error=f"Data Collector spatial collection failed: {exc}")

        ctx.set("spatial_layers", spatial_layers)
        return AgentResult(
            success=True,
            data={
                "spatial_layers_available": True,
                "restriction_layers_count": len(spatial_layers.get("restriction_layers", [])),
                "land_use_layers_count": len(spatial_layers.get("land_use_layers", [])),
                "real_estate_objects_count": len(spatial_layers.get("real_estate_objects", [])),
                "spatial_layers": spatial_layers,
            },
        )
