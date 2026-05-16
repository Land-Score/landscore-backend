"""
AI Orchestrator — gRPC server entry point.
Workers are started separately via Celery.
"""
import asyncio
import grpc
from concurrent import futures
from app.config import settings


async def serve() -> None:
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=4))
    # ai_orchestrator_pb2_grpc.add_AIOrchestatorServicer_to_server(OrchestratorServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")
    print(f"ai-orchestrator gRPC listening on :{settings.grpc_port}")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
