import asyncio
import grpc
from concurrent import futures
from app.config import settings


async def serve() -> None:
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")
    print(f"search-service listening on :{settings.grpc_port}")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
