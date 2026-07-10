import asyncio
import os
import grpc
import sys
from concurrent import futures

PROTO_GEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
if PROTO_GEN_DIR not in sys.path:
    sys.path.insert(0, PROTO_GEN_DIR)

try:
    import geo_pb2_grpc
except ImportError as exc:  # Сгенерированные stubs обязательны для запуска сервера.
    print(
        "geo-service fatal: не найдены сгенерированные gRPC stubs (geo_pb2_grpc). "
        f"Ожидаются в {PROTO_GEN_DIR}. Сгенерируйте их через `make proto` перед запуском. "
        f"Причина: {exc}",
        file=sys.stderr,
    )
    sys.exit(1)

from app.servicer import GeoServicer


GRPC_MESSAGE_OPTIONS = [
    ("grpc.max_send_message_length", 128 * 1024 * 1024),
    ("grpc.max_receive_message_length", 128 * 1024 * 1024),
]


async def serve() -> None:
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=GRPC_MESSAGE_OPTIONS,
    )
    geo_pb2_grpc.add_GeoServiceServicer_to_server(GeoServicer(), server)
    server.add_insecure_port("[::]:50057")
    print("geo-service listening on :50057")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
