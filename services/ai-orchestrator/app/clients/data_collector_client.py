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
        self.channel_options = [
            ("grpc.max_send_message_length", 128 * 1024 * 1024),
            ("grpc.max_receive_message_length", 128 * 1024 * 1024),
        ]

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
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = data_collector_pb2_grpc.DataCollectorServiceStub(channel)
            response = await stub.CollectPlotSpatialLayers(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def collect_plot_dataset(self, cadastral_number: str) -> dict:
        if data_collector_pb2 is None or data_collector_pb2_grpc is None:
            raise RuntimeError("Generated data_collector proto stubs are missing. Run `make proto` first.")

        request = data_collector_pb2.CadastralRequest(cadastral_number=cadastral_number)
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = data_collector_pb2_grpc.DataCollectorServiceStub(channel)
            response = await stub.CollectPlotDataset(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def get_plot_by_cadastral(self, cadastral_number: str) -> dict:
        if data_collector_pb2 is None or data_collector_pb2_grpc is None:
            raise RuntimeError("Generated data_collector proto stubs are missing. Run `make proto` first.")

        request = data_collector_pb2.CadastralRequest(cadastral_number=cadastral_number)
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = data_collector_pb2_grpc.DataCollectorServiceStub(channel)
            response = await stub.GetPlotByCadastral(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def get_plot_by_address(self, address: str) -> dict:
        if data_collector_pb2 is None or data_collector_pb2_grpc is None:
            raise RuntimeError("Generated data_collector proto stubs are missing. Run `make proto` first.")

        request = data_collector_pb2.AddressRequest(address=address)
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = data_collector_pb2_grpc.DataCollectorServiceStub(channel)
            response = await stub.GetPlotByAddress(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def get_egrn(self, cadastral_number: str, use_cache: bool = True) -> dict:
        if data_collector_pb2 is None or data_collector_pb2_grpc is None:
            raise RuntimeError("Generated data_collector proto stubs are missing. Run `make proto` first.")

        request = data_collector_pb2.EGRNRequest(cadastral_number=cadastral_number, use_cache=use_cache)
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = data_collector_pb2_grpc.DataCollectorServiceStub(channel)
            response = await stub.GetEGRN(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def search_plots(
        self,
        *,
        region: str = "",
        category: str = "",
        allowed_use: str = "",
        area_min: float = 0.0,
        area_max: float = 0.0,
        price_min: float = 0.0,
        price_max: float = 0.0,
        limit: int = 20,
    ) -> dict:
        if data_collector_pb2 is None or data_collector_pb2_grpc is None:
            raise RuntimeError("Generated data_collector proto stubs are missing. Run `make proto` first.")

        request = data_collector_pb2.SearchPlotsRequest(
            region=region,
            category=category,
            allowed_use=allowed_use,
            area_min=area_min,
            area_max=area_max,
            price_min=price_min,
            price_max=price_max,
            limit=limit,
        )
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = data_collector_pb2_grpc.DataCollectorServiceStub(channel)
            response = await stub.SearchPlots(request)
        return MessageToDict(response, preserving_proto_field_name=True)
