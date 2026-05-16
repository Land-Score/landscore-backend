import grpc
import json
from app.config import settings


class CheckServicer:
    """Implements check.proto CheckService."""

    async def CreateCheck(self, request, context):
        # 1. Insert LandCheck row (status=pending)
        # 2. Publish job to Redis queue → ai-orchestrator Celery
        # 3. Return check_id
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def GetCheckStatus(self, request, context):
        # 1. Query check_steps for progress
        # 2. Return current_step + progress_pct
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def UpdateCheckProgress(self, request, context):
        # Called by ai-orchestrator after each agent step
        # Updates check_steps table + Redis for fast polling
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def SaveCheckResult(self, request, context):
        # Called by ai-orchestrator when pipeline is complete
        # Saves to check_results, sets LandCheck.status=completed
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def GetCheckReport(self, request, context):
        # Reads check_results by check_id
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
