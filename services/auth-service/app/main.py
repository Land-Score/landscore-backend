import asyncio
import grpc
from concurrent import futures

from app.config import settings
from app.servicer import AuthServicer

# proto_generated imports added after `make proto`
# from landscore_shared.proto_generated import auth_pb2_grpc


async def serve() -> None:
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))

    # auth_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")

    print(f"auth-service listening on :{settings.grpc_port}")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
