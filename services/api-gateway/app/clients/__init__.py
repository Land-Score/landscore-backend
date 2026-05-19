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


import grpc  # noqa: E402
import structlog  # noqa: E402
from celery import Celery  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.config import settings  # noqa: E402

log = structlog.get_logger()


GRPC_MESSAGE_OPTIONS = [
    ("grpc.max_send_message_length", 128 * 1024 * 1024),
    ("grpc.max_receive_message_length", 128 * 1024 * 1024),
]


async def setup_clients(app: FastAPI) -> None:
    """Open gRPC channels and attach stubs to app.state at startup."""
    # Import stubs after sys.path is set
    import auth_pb2_grpc
    import check_pb2_grpc
    import search_pb2_grpc
    import document_pb2_grpc
    import data_collector_pb2_grpc
    import geo_pb2_grpc

    app.state.auth_channel = grpc.aio.insecure_channel(settings.auth_grpc, options=GRPC_MESSAGE_OPTIONS)
    app.state.auth_stub = auth_pb2_grpc.AuthServiceStub(app.state.auth_channel)

    app.state.check_channel = grpc.aio.insecure_channel(settings.check_grpc, options=GRPC_MESSAGE_OPTIONS)
    app.state.check_stub = check_pb2_grpc.CheckServiceStub(app.state.check_channel)

    app.state.search_channel = grpc.aio.insecure_channel(settings.search_grpc, options=GRPC_MESSAGE_OPTIONS)
    app.state.search_stub = search_pb2_grpc.SearchServiceStub(app.state.search_channel)

    app.state.document_channel = grpc.aio.insecure_channel(settings.document_grpc, options=GRPC_MESSAGE_OPTIONS)
    app.state.document_stub = document_pb2_grpc.DocumentServiceStub(app.state.document_channel)

    app.state.data_collector_channel = grpc.aio.insecure_channel(settings.data_collector_grpc, options=GRPC_MESSAGE_OPTIONS)
    app.state.data_collector_stub = data_collector_pb2_grpc.DataCollectorServiceStub(app.state.data_collector_channel)

    app.state.geo_channel = grpc.aio.insecure_channel(settings.geo_grpc, options=GRPC_MESSAGE_OPTIONS)
    app.state.geo_stub = geo_pb2_grpc.GeoServiceStub(app.state.geo_channel)

    # Celery producer — used only for send_task() to ai-orchestrator worker.
    # No task modules imported here; we send by name to avoid coupling.
    app.state.celery = Celery(broker=settings.celery_broker_url)
    app.state.celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
    )

    log.info(
        "grpc_clients_initialized",
        auth=settings.auth_grpc,
        check=settings.check_grpc,
        search=settings.search_grpc,
        document=settings.document_grpc,
        data_collector=settings.data_collector_grpc,
        geo=settings.geo_grpc,
        celery_broker=settings.celery_broker_url,
    )


async def close_clients(app: FastAPI) -> None:
    """Close all gRPC channels at shutdown."""
    for name in ("auth_channel", "check_channel", "search_channel", "document_channel", "data_collector_channel", "geo_channel"):
        ch = getattr(app.state, name, None)
        if ch:
            await ch.close()
    log.info("grpc_clients_closed")
