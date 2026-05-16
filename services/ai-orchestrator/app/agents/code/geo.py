from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app.clients.geo_client import GeoClient

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
                restriction_layers=spatial_layers.get("restriction_layers", []),
                land_use_layers=spatial_layers.get("land_use_layers", []),
                real_estate_objects=spatial_layers.get("real_estate_objects", []),
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
