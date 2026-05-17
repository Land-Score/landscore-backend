import uuid
import json
import grpc
import structlog
from datetime import datetime
from sqlalchemy import select, func

from google.protobuf import empty_pb2

import check_pb2
import check_pb2_grpc

from app.database import AsyncSessionLocal
from app.models import LandCheck, CheckStep, CheckResult

log = structlog.get_logger()


def _check_resp(c: LandCheck) -> check_pb2.CheckResponse:
    return check_pb2.CheckResponse(
        check_id=str(c.id),
        user_id=str(c.user_id),
        status=c.status,
        cadastral_number=c.cadastral_number or "",
        address=c.address or "",
        created_at=c.created_at.isoformat() if c.created_at else "",
        completed_at=c.completed_at.isoformat() if c.completed_at else "",
    )


class CheckServicer(check_pb2_grpc.CheckServiceServicer):

    async def CreateCheck(self, request, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
            return
        try:
            user_uuid = uuid.UUID(request.user_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid user_id format")
            return

        check = LandCheck(
            id=uuid.uuid4(),
            user_id=user_uuid,
            status="pending",
            cadastral_number=request.cadastral_number or None,
            address=request.address or None,
            lat=request.lat if request.lat != 0.0 else None,
            lng=request.lng if request.lng != 0.0 else None,
            purpose=request.purpose or "",
        )

        try:
            async with AsyncSessionLocal() as session:
                session.add(check)
                await session.commit()
                await session.refresh(check)

            log.info("check_created", check_id=str(check.id))
            return _check_resp(check)
        except Exception as exc:
            log.error("create_check_error", error=str(exc))
            await context.abort(grpc.StatusCode.INTERNAL, f"DB error: {exc}")

    async def GetCheck(self, request, context):
        try:
            check_uuid = uuid.UUID(request.check_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid check_id")
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(LandCheck).where(LandCheck.id == check_uuid))
            check = result.scalar_one_or_none()

        if not check:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Check not found")
            return

        return _check_resp(check)

    async def ListChecks(self, request, context):
        limit = max(1, min(request.limit or 20, 100))
        offset = max(0, request.offset or 0)

        try:
            user_uuid = uuid.UUID(request.user_id) if request.user_id else None
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid user_id")
            return

        async with AsyncSessionLocal() as session:
            base = select(LandCheck)
            count_base = select(func.count()).select_from(LandCheck)
            if user_uuid:
                base = base.where(LandCheck.user_id == user_uuid)
                count_base = count_base.where(LandCheck.user_id == user_uuid)

            rows = await session.execute(
                base.order_by(LandCheck.created_at.desc()).limit(limit).offset(offset)
            )
            checks = rows.scalars().all()
            total = (await session.execute(count_base)).scalar() or 0

        return check_pb2.ListChecksResponse(
            checks=[_check_resp(c) for c in checks],
            total=total,
        )

    async def GetCheckStatus(self, request, context):
        try:
            check_uuid = uuid.UUID(request.check_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid check_id")
            return

        async with AsyncSessionLocal() as session:
            check_row = await session.execute(select(LandCheck).where(LandCheck.id == check_uuid))
            check = check_row.scalar_one_or_none()
            if not check:
                await context.abort(grpc.StatusCode.NOT_FOUND, "Check not found")
                return

            steps_row = await session.execute(
                select(CheckStep)
                .where(CheckStep.check_id == check_uuid)
                .order_by(CheckStep.started_at.nullslast())
            )
            steps = steps_row.scalars().all()

        current_step = ""
        progress_pct = 0
        if steps:
            latest = steps[-1]
            current_step = latest.agent_name
            progress_pct = latest.progress_pct

        return check_pb2.CheckStatusResponse(
            check_id=str(check.id),
            status=check.status,
            current_step=current_step,
            progress_pct=progress_pct,
            completed_steps=[
                check_pb2.StepResult(
                    agent_name=s.agent_name,
                    status=s.status,
                    duration_ms=0,
                )
                for s in steps
                if s.status == "completed"
            ],
            error_message="",
        )

    async def UpdateCheckProgress(self, request, context):
        try:
            check_uuid = uuid.UUID(request.check_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid check_id")
            return

        async with AsyncSessionLocal() as session:
            step_row = await session.execute(
                select(CheckStep).where(
                    CheckStep.check_id == check_uuid,
                    CheckStep.agent_name == request.agent_name,
                )
            )
            step = step_row.scalar_one_or_none()

            if not step:
                step = CheckStep(
                    id=uuid.uuid4(),
                    check_id=check_uuid,
                    agent_name=request.agent_name,
                    started_at=datetime.utcnow(),
                )
                session.add(step)

            step.status = request.status
            step.progress_pct = request.progress_pct
            if request.output_json:
                try:
                    step.output_json = json.loads(request.output_json)
                except (json.JSONDecodeError, ValueError):
                    step.output_json = {"raw": request.output_json}
            if request.status == "completed":
                step.completed_at = datetime.utcnow()

            # Mirror status onto the parent check
            check_row = await session.execute(select(LandCheck).where(LandCheck.id == check_uuid))
            check = check_row.scalar_one_or_none()
            if check and check.status not in ("completed", "failed"):
                check.status = "processing"

            await session.commit()

        return empty_pb2.Empty()

    async def SaveCheckResult(self, request, context):
        try:
            check_uuid = uuid.UUID(request.check_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid check_id")
            return

        report_data = {}
        if request.report_json:
            try:
                report_data = json.loads(request.report_json)
            except (json.JSONDecodeError, ValueError):
                report_data = {"raw": request.report_json}

        async with AsyncSessionLocal() as session:
            existing_row = await session.execute(
                select(CheckResult).where(CheckResult.check_id == check_uuid)
            )
            result = existing_row.scalar_one_or_none()

            if not result:
                result = CheckResult(check_id=check_uuid)
                session.add(result)

            result.plot_id = request.plot_id or None
            result.overall_score = request.overall_score or None
            result.legal_risk = request.legal_risk or None
            result.stop_factors = list(request.stop_factors)
            result.best_scenario = request.best_scenario or None
            result.report_json = report_data
            result.explanation = request.explanation or ""
            result.next_steps = list(request.next_steps)

            # Mark check as completed
            check_row = await session.execute(select(LandCheck).where(LandCheck.id == check_uuid))
            check = check_row.scalar_one_or_none()
            if check:
                check.status = "completed"
                check.completed_at = datetime.utcnow()

            await session.commit()

        log.info("check_result_saved", check_id=str(check_uuid))
        return empty_pb2.Empty()

    async def GetCheckReport(self, request, context):
        try:
            check_uuid = uuid.UUID(request.check_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid check_id")
            return

        async with AsyncSessionLocal() as session:
            check_row = await session.execute(select(LandCheck).where(LandCheck.id == check_uuid))
            check = check_row.scalar_one_or_none()
            if not check:
                await context.abort(grpc.StatusCode.NOT_FOUND, "Check not found")
                return

            result_row = await session.execute(
                select(CheckResult).where(CheckResult.check_id == check_uuid)
            )
            result = result_row.scalar_one_or_none()

        # Always embed geo metadata so the frontend can render the map
        # even after a page refresh (sessionStorage is gone but report_json persists).
        geo_meta = {
            "_coords": {
                "lat": check.lat,
                "lng": check.lng,
                "cadastral_number": check.cadastral_number,
                "address": check.address,
            }
        }

        if not result:
            report_str = json.dumps(geo_meta, ensure_ascii=False)
            return check_pb2.CheckReportResponse(
                check_id=str(check.id),
                status=check.status,
                overall_score=0,
                legal_risk="",
                stop_factors=[],
                best_scenario="",
                report_json=report_str,
                explanation="",
                next_steps=[],
            )

        merged = {**geo_meta, **(result.report_json or {})}
        try:
            report_str = json.dumps(merged, ensure_ascii=False)
        except (TypeError, ValueError):
            report_str = json.dumps(geo_meta, ensure_ascii=False)

        return check_pb2.CheckReportResponse(
            check_id=str(check.id),
            status=check.status,
            overall_score=result.overall_score or 0,
            legal_risk=result.legal_risk or "",
            stop_factors=list(result.stop_factors or []),
            best_scenario=result.best_scenario or "",
            report_json=report_str,
            explanation=result.explanation or "",
            next_steps=list(result.next_steps or []),
        )
