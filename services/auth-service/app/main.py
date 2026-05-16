import asyncio
import sys
import os
from concurrent import futures

# Ensure proto_gen stubs are importable before any other app import
_proto_gen = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
if _proto_gen not in sys.path:
    sys.path.insert(0, _proto_gen)

import grpc  # noqa: E402
import structlog  # noqa: E402

import auth_pb2_grpc  # noqa: E402
from app.config import settings  # noqa: E402
from app.servicer import AuthServicer  # noqa: E402

log = structlog.get_logger()


async def serve() -> None:
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    auth_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")
    log.info("auth_service_starting", port=settings.grpc_port)
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
