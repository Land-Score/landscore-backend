from __future__ import annotations

from google.protobuf.json_format import MessageToDict
import grpc

from app.clients.proto_imports import add_generated_proto_path
from app.config import settings

add_generated_proto_path()

try:
    import geo_pb2
    import geo_pb2_grpc
except ImportError:
    geo_pb2 = None
    geo_pb2_grpc = None


class GeoClient:
    def __init__(self, target: str | None = None) -> None:
        self.target = target or settings.geo_grpc

    async def analyze_land_use_restrictions(
        self,
        *,
        cadastral_number: str,
        scenario: str,
        parcel_geometry_geojson: str,
        parcel_area_ha: float,
        restriction_layers: list[dict],
        land_use_layers: list[dict],
        real_estate_objects: list[dict],
        vision_interpretation_json: str = "",
    ) -> dict:
        if geo_pb2 is None or geo_pb2_grpc is None:
            raise RuntimeError("Generated geo proto stubs are missing. Run `make proto` first.")

        request = geo_pb2.LandUseRestrictionRequest(
            cadastral_number=cadastral_number,
            scenario=scenario,
            parcel_geometry_geojson=parcel_geometry_geojson,
            parcel_area_ha=parcel_area_ha,
            restriction_layers=[geo_pb2.RestrictionLayer(**layer) for layer in restriction_layers],
            land_use_layers=[geo_pb2.LandUseLayer(**layer) for layer in land_use_layers],
            real_estate_objects=[geo_pb2.RealEstateObjectLayer(**obj) for obj in real_estate_objects],
            vision_interpretation_json=vision_interpretation_json,
        )
        async with grpc.aio.insecure_channel(self.target) as channel:
            stub = geo_pb2_grpc.GeoServiceStub(channel)
            response = await stub.AnalyzeLandUseRestrictions(request)
        return MessageToDict(response, preserving_proto_field_name=True)
