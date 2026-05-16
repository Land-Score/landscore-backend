"""
gRPC client management for downstream services.

Generated proto stubs live in app/proto_gen/ (created at Docker build time via protoc).
They need their own directory on sys.path because protoc generates cross-imports
without package prefixes (e.g. `import auth_pb2`, not `from app.proto_gen import auth_pb2`).
"""
import sys
import os

_proto_gen = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "proto_gen")
)
if _proto_gen not in sys.path:
    sys.path.insert(0, _proto_gen)


import grpc
import structlog
from fastapi import FastAPI

from app.config import settings

log = structlog.get_logger()


async def setup_clients(app: FastAPI) -> None:
    """Open gRPC channels and attach stubs to app.state at startup."""
    # Import stubs after sys.path is set
    import auth_pb2_grpc
    import check_pb2_grpc
    import search_pb2_grpc
    import document_pb2_grpc

    app.state.auth_channel = grpc.aio.insecure_channel(settings.auth_grpc)
    app.state.auth_stub = auth_pb2_grpc.AuthServiceStub(app.state.auth_channel)

    app.state.check_channel = grpc.aio.insecure_channel(settings.check_grpc)
    app.state.check_stub = check_pb2_grpc.CheckServiceStub(app.state.check_channel)

    app.state.search_channel = grpc.aio.insecure_channel(settings.search_grpc)
    app.state.search_stub = search_pb2_grpc.SearchServiceStub(app.state.search_channel)

    app.state.document_channel = grpc.aio.insecure_channel(settings.document_grpc)
    app.state.document_stub = document_pb2_grpc.DocumentServiceStub(app.state.document_channel)

    log.info(
        "grpc_clients_initialized",
        auth=settings.auth_grpc,
        check=settings.check_grpc,
        search=settings.search_grpc,
        document=settings.document_grpc,
    )


async def close_clients(app: FastAPI) -> None:
    """Close all gRPC channels at shutdown."""
    for name in ("auth_channel", "check_channel", "search_channel", "document_channel"):
        ch = getattr(app.state, name, None)
        if ch:
            await ch.close()
    log.info("grpc_clients_closed")
