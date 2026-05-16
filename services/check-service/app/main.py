import asyncio
import grpc
from concurrent import futures
from app.config import settings
from app.servicer import CheckServicer


async def serve() -> None:
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    # check_pb2_grpc.add_CheckServiceServicer_to_server(CheckServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")
    print(f"check-service listening on :{settings.grpc_port}")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
