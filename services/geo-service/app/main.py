import asyncio
import os
import grpc
import sys
from concurrent import futures

try:
    PROTO_GEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
    if PROTO_GEN_DIR not in sys.path:
        sys.path.insert(0, PROTO_GEN_DIR)
    import geo_pb2_grpc
except ImportError:
    geo_pb2_grpc = None

from app.servicer import GeoServicer


async def serve() -> None:
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    if geo_pb2_grpc is not None:
        geo_pb2_grpc.add_GeoServiceServicer_to_server(GeoServicer(), server)
    else:
        print("geo-service warning: generated proto stubs not found; service starts without registered RPC handlers")
    server.add_insecure_port("0.0.0.0:50057")
    print("geo-service listening on :50057")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
