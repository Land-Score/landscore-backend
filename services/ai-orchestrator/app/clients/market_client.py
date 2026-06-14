from __future__ import annotations

from google.protobuf.json_format import MessageToDict
import grpc

from app.clients.proto_imports import add_generated_proto_path
from app.config import settings

add_generated_proto_path()

try:
    import market_pb2
    import market_pb2_grpc
except ImportError:
    market_pb2 = None
    market_pb2_grpc = None


class MarketClient:
    def __init__(self, target: str | None = None) -> None:
        self.target = target or settings.market_grpc
        self.channel_options = [
            ("grpc.max_send_message_length", 128 * 1024 * 1024),
            ("grpc.max_receive_message_length", 128 * 1024 * 1024),
        ]

    async def get_market_analysis(
        self,
        *,
        region: str,
        category: str,
        allowed_use: str,
        area: float,
        lat: float = 0.0,
        lng: float = 0.0,
        asking_price: float = 0.0,
    ) -> dict:
        if market_pb2 is None or market_pb2_grpc is None:
            raise RuntimeError("Generated market proto stubs are missing. Run `make proto` first.")

        request = market_pb2.MarketRequest(
            region=region,
            category=category,
            allowed_use=allowed_use,
            area=area,
            lat=lat,
            lng=lng,
            asking_price=asking_price,
        )
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = market_pb2_grpc.MarketServiceStub(channel)
            response = await stub.GetMarketAnalysis(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def get_comparables(
        self,
        *,
        region: str,
        category: str,
        area_min: float = 0.0,
        area_max: float = 0.0,
        limit: int = 10,
    ) -> dict:
        if market_pb2 is None or market_pb2_grpc is None:
            raise RuntimeError("Generated market proto stubs are missing. Run `make proto` first.")

        request = market_pb2.ComparablesRequest(
            region=region,
            category=category,
            area_min=area_min,
            area_max=area_max,
            limit=limit,
        )
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = market_pb2_grpc.MarketServiceStub(channel)
            response = await stub.GetComparables(request)
        return MessageToDict(response, preserving_proto_field_name=True)

    async def get_price_stats(self, *, region: str, category: str) -> dict:
        if market_pb2 is None or market_pb2_grpc is None:
            raise RuntimeError("Generated market proto stubs are missing. Run `make proto` first.")

        request = market_pb2.PriceStatsRequest(region=region, category=category)
        async with grpc.aio.insecure_channel(self.target, options=self.channel_options) as channel:
            stub = market_pb2_grpc.MarketServiceStub(channel)
            response = await stub.GetPriceStats(request)
        return MessageToDict(response, preserving_proto_field_name=True)
