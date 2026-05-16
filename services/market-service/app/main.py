import asyncio
import grpc
from concurrent import futures


async def serve() -> None:
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    server.add_insecure_port("0.0.0.0:50058")
    print("market-service listening on :50058")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
