import asyncio
import os
import grpc
import sys
from concurrent import futures

try:
    PROTO_GEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
    if PROTO_GEN_DIR not in sys.path:
        sys.path.insert(0, PROTO_GEN_DIR)
    import data_collector_pb2_grpc
except ImportError:
    data_collector_pb2_grpc = None

from app.servicer import DataCollectorServicer


GRPC_MESSAGE_OPTIONS = [
    ("grpc.max_send_message_length", 128 * 1024 * 1024),
    ("grpc.max_receive_message_length", 128 * 1024 * 1024),
]


async def serve() -> None:
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=GRPC_MESSAGE_OPTIONS,
    )
    if data_collector_pb2_grpc is not None:
        data_collector_pb2_grpc.add_DataCollectorServiceServicer_to_server(DataCollectorServicer(), server)
    else:
        print("data-collector warning: generated proto stubs not found; service starts without registered RPC handlers")
    server.add_insecure_port("0.0.0.0:50056")
    print("data-collector listening on :50056")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
