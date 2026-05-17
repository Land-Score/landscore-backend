import asyncio
import os
import sys
from concurrent import futures

_proto_gen = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
if _proto_gen not in sys.path:
    sys.path.insert(0, _proto_gen)

import grpc  # noqa: E402
import check_pb2_grpc  # noqa: E402
from app.config import settings  # noqa: E402
from app.servicer import CheckServicer  # noqa: E402


async def serve() -> None:
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    check_pb2_grpc.add_CheckServiceServicer_to_server(CheckServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")
    print(f"check-service listening on :{settings.grpc_port}")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
