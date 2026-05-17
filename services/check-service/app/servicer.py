import json
import uuid
from datetime import datetime
from typing import Any

import grpc
from google.protobuf.empty_pb2 import Empty
from sqlalchemy import desc, func, select

import check_pb2
import check_pb2_grpc
from app.celery_client import enqueue_check
from app.constants import CheckStatus, StepStatus
from app.database import AsyncSessionLocal
from app.models import CheckResult, CheckStep, LandCheck


async def _parse_uuid(value: str, field_name: str, context) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"invalid {field_name}")


def _json_loads(value: str) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _check_response(check: LandCheck) -> check_pb2.CheckResponse:
    return check_pb2.CheckResponse(
        check_id=str(check.id),
        user_id=str(check.user_id),
        status=check.status,
        cadastral_number=check.cadastral_number or "",
        address=check.address or "",
        created_at=_iso(check.created_at),
        completed_at=_iso(check.completed_at),
        purpose=check.purpose or "",
    )


def _step_duration_ms(step: CheckStep) -> int:
    if not step.started_at or not step.completed_at:
        return 0
    return max(0, int((step.completed_at - step.started_at).total_seconds() * 1000))


def _report_json(result: CheckResult) -> str:
    if result.report_json is None:
        return "{}"
    return json.dumps(result.report_json, ensure_ascii=False)


class CheckServicer(check_pb2_grpc.CheckServiceServicer):
    """Implements check.proto CheckService."""

    async def CreateCheck(self, request, context):
        user_id = await _parse_uuid(request.user_id, "user_id", context)
        if not request.cadastral_number and not request.address and not (request.lat and request.lng):
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "cadastral_number, address or lat+lng is required",
            )

        async with AsyncSessionLocal() as session:
            check = LandCheck(
                id=uuid.uuid4(),
                user_id=user_id,
                status=CheckStatus.PENDING,
                cadastral_number=request.cadastral_number or None,
                address=request.address or None,
                lat=request.lat or None,
                lng=request.lng or None,
                purpose=request.purpose or "",
            )
            session.add(check)
            await session.commit()
            await session.refresh(check)

            payload = {
                "check_id": str(check.id),
                "user_profile_json": request.user_profile_json or "{}",
                "cadastral_number": request.cadastral_number or "",
                "address": request.address or "",
                "lat": request.lat,
                "lng": request.lng,
                "purpose": request.purpose or "",
            }
            try:
                enqueue_check(payload)
            except Exception as exc:
                check.status = CheckStatus.FAILED
                check.completed_at = datetime.utcnow()
                await session.commit()
                await context.abort(grpc.StatusCode.UNAVAILABLE, f"failed to enqueue check: {exc}")

            return _check_response(check)

    async def GetCheck(self, request, context):
        check_id = await _parse_uuid(request.check_id, "check_id", context)
        async with AsyncSessionLocal() as session:
            check = await session.get(LandCheck, check_id)
            if check is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check not found")
            return _check_response(check)

    async def ListChecks(self, request, context):
        user_id = await _parse_uuid(request.user_id, "user_id", context)
        limit = min(max(request.limit or 20, 1), 100)
        offset = max(request.offset or 0, 0)
        async with AsyncSessionLocal() as session:
            total = await session.scalar(
                select(func.count()).select_from(LandCheck).where(LandCheck.user_id == user_id)
            )
            rows = await session.scalars(
                select(LandCheck)
                .where(LandCheck.user_id == user_id)
                .order_by(desc(LandCheck.created_at))
                .limit(limit)
                .offset(offset)
            )
            return check_pb2.ListChecksResponse(
                checks=[_check_response(row) for row in rows],
                total=int(total or 0),
            )

    async def GetCheckStatus(self, request, context):
        check_id = await _parse_uuid(request.check_id, "check_id", context)
        async with AsyncSessionLocal() as session:
            check = await session.get(LandCheck, check_id)
            if check is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check not found")

            steps = list(
                await session.scalars(
                    select(CheckStep)
                    .where(CheckStep.check_id == check_id)
                    .order_by(desc(CheckStep.started_at), desc(CheckStep.completed_at))
                )
            )
            running = next((step for step in steps if step.status == StepStatus.RUNNING), None)
            latest = steps[0] if steps else None
            current = running or latest
            completed = [
                step
                for step in steps
                if step.status in {StepStatus.DONE, StepStatus.FAILED} or step.completed_at is not None
            ]
            progress = max([step.progress_pct for step in steps], default=0)
            if check.status == CheckStatus.COMPLETED:
                progress = 100

            error_message = ""
            failed = next((step for step in steps if step.status == StepStatus.FAILED), None)
            if failed and isinstance(failed.output_json, dict):
                error_message = str(failed.output_json.get("error") or failed.output_json.get("message") or "")

            return check_pb2.CheckStatusResponse(
                check_id=str(check.id),
                status=check.status,
                current_step=current.agent_name if current else "",
                progress_pct=progress,
                completed_steps=[
                    check_pb2.StepResult(
                        agent_name=step.agent_name,
                        status=step.status,
                        duration_ms=_step_duration_ms(step),
                    )
                    for step in completed
                ],
                error_message=error_message,
            )

    async def UpdateCheckProgress(self, request, context):
        check_id = await _parse_uuid(request.check_id, "check_id", context)
        if not request.agent_name:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "agent_name is required")

        now = datetime.utcnow()
        async with AsyncSessionLocal() as session:
            check = await session.get(LandCheck, check_id)
            if check is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check not found")

            step = await session.scalar(
                select(CheckStep).where(
                    CheckStep.check_id == check_id,
                    CheckStep.agent_name == request.agent_name,
                )
            )
            if step is None:
                step = CheckStep(
                    id=uuid.uuid4(),
                    check_id=check_id,
                    agent_name=request.agent_name,
                    started_at=now,
                )
                session.add(step)

            step.status = request.status or StepStatus.RUNNING
            step.progress_pct = min(max(request.progress_pct, 0), 100)
            step.output_json = _json_loads(request.output_json)
            if step.started_at is None and step.status in {StepStatus.RUNNING, StepStatus.DONE, StepStatus.FAILED}:
                step.started_at = now
            if step.status in {StepStatus.DONE, StepStatus.FAILED}:
                step.completed_at = now

            if check.status == CheckStatus.PENDING:
                check.status = CheckStatus.PROCESSING

            await session.commit()
            return Empty()

    async def SaveCheckResult(self, request, context):
        check_id = await _parse_uuid(request.check_id, "check_id", context)
        now = datetime.utcnow()
        async with AsyncSessionLocal() as session:
            check = await session.get(LandCheck, check_id)
            if check is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check not found")

            result = await session.get(CheckResult, check_id)
            if result is None:
                result = CheckResult(check_id=check_id)
                session.add(result)

            result.plot_id = request.plot_id or None
            result.overall_score = request.overall_score
            result.legal_risk = request.legal_risk or None
            result.stop_factors = list(request.stop_factors)
            result.best_scenario = request.best_scenario or None
            result.report_json = _json_loads(request.report_json) or {}
            result.explanation = request.explanation or None
            result.next_steps = list(request.next_steps)

            check.status = CheckStatus.COMPLETED
            check.completed_at = now
            await session.commit()
            return Empty()

    async def GetCheckReport(self, request, context):
        check_id = await _parse_uuid(request.check_id, "check_id", context)
        async with AsyncSessionLocal() as session:
            check = await session.get(LandCheck, check_id)
            if check is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check not found")
            result = await session.get(CheckResult, check_id)
            if result is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check result not ready")

            return check_pb2.CheckReportResponse(
                check_id=str(check.id),
                status=check.status,
                overall_score=result.overall_score or 0,
                legal_risk=result.legal_risk or "",
                stop_factors=list(result.stop_factors or []),
                best_scenario=result.best_scenario or "",
                report_json=_report_json(result),
                explanation=result.explanation or "",
                next_steps=list(result.next_steps or []),
            )
