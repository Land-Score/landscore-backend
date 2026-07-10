import asyncio
import os
import sys
from concurrent import futures

import grpc
import structlog

try:
    PROTO_GEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
    if PROTO_GEN_DIR not in sys.path:
        sys.path.insert(0, PROTO_GEN_DIR)
    import market_pb2_grpc
except ImportError:
    market_pb2_grpc = None

from app.clickhouse_client import get_client
from app.servicer import MarketServicer

log = structlog.get_logger(__name__)


GRPC_MESSAGE_OPTIONS = [
    ("grpc.max_send_message_length", 128 * 1024 * 1024),
    ("grpc.max_receive_message_length", 128 * 1024 * 1024),
]


async def _bootstrap_clickhouse() -> None:
    """Ensure schema + seed in a worker thread, tolerating a slow ClickHouse.

    The client itself retries the connection with backoff; if bootstrap still
    fails we log and continue so the container does not crash-loop. RPCs will
    surface UNAVAILABLE until the store is ready.
    """

    try:
        await asyncio.to_thread(get_client().bootstrap)
        log.info("market_clickhouse_bootstrap_done")
    except Exception as exc:  # noqa: BLE001
        log.error("market_clickhouse_bootstrap_failed", error=str(exc))


async def serve() -> None:
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=GRPC_MESSAGE_OPTIONS,
    )
    if market_pb2_grpc is not None:
        market_pb2_grpc.add_MarketServiceServicer_to_server(MarketServicer(), server)
    else:
        print("market-service warning: generated proto stubs not found; service starts without registered RPC handlers")
    server.add_insecure_port("[::]:50058")
    print("market-service listening on :50058")
    await server.start()
    # Bootstrap ClickHouse (schema + seed) in the background so we accept the
    # port immediately and never crash-loop on a slow datastore.
    asyncio.create_task(_bootstrap_clickhouse())
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
