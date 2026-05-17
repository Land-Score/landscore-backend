from __future__ import annotations

from google.protobuf.json_format import MessageToDict
import grpc

from app.clients.proto_imports import add_generated_proto_path
from app.config import settings

add_generated_proto_path()

try:
    import data_collector_pb2
    import data_collector_pb2_grpc
except ImportError:
    data_collector_pb2 = None
    data_collector_pb2_grpc = None


class DataCollectorClient:
    def __init__(self, target: str | None = None) -> None:
        self.target = target or settings.data_collector_grpc

    async def collect_spatial_layers(
        self,
        *,
        cadastral_number: str,
        parcel_geometry_geojson: str,
        source_layer_keys: list[str] | None = None,
        raw_features_by_layer_json: str = "{}",
        include_restrictions: bool = True,
        include_land_use: bool = True,
        include_real_estate_objects: bool = True,
        include_informational_layers: bool = True,
        use_cache: bool = True,
    ) -> dict:
        if data_collector_pb2 is None or data_collector_pb2_grpc is None:
            raise RuntimeError("Generated data_collector proto stubs are missing. Run `make proto` first.")

        request = data_collector_pb2.SpatialLayersRequest(
            cadastral_number=cadastral_number,
            parcel_geometry_geojson=parcel_geometry_geojson,
            raw_features_by_layer_json=raw_features_by_layer_json,
            include_restrictions=include_restrictions,
            include_land_use=include_land_use,
            include_real_estate_objects=include_real_estate_objects,
            include_informational_layers=include_informational_layers,
            use_cache=use_cache,
        )
        if source_layer_keys:
            request.source_layer_keys.extend(source_layer_keys)
        async with grpc.aio.insecure_channel(self.target) as channel:
            stub = data_collector_pb2_grpc.DataCollectorServiceStub(channel)
            response = await stub.CollectPlotSpatialLayers(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def collect_plot_dataset(self, cadastral_number: str) -> dict:
        if data_collector_pb2 is None or data_collector_pb2_grpc is None:
            raise RuntimeError("Generated data_collector proto stubs are missing. Run `make proto` first.")

        request = data_collector_pb2.CadastralRequest(cadastral_number=cadastral_number)
        async with grpc.aio.insecure_channel(self.target) as channel:
            stub = data_collector_pb2_grpc.DataCollectorServiceStub(channel)
            response = await stub.CollectPlotDataset(request)
        return MessageToDict(response, preserving_proto_field_name=True)
