import json
import uuid
from datetime import datetime
from typing import Any

import grpc
from google.protobuf.empty_pb2 import Empty
from sqlalchemy import delete, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

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


def _compact_layer_for_client(layer: Any) -> dict[str, Any] | None:
    if not isinstance(layer, dict):
        return None
    compact: dict[str, Any] = {}
    for key in (
        "id",
        "layer_type",
        "normalized_type",
        "label",
        "name",
        "source",
        "source_layer_name",
        "geometry_geojson",
        "intersection_ha",
        "counted_in_loss_ha",
        "area_loss_mode",
        "severity",
        "properties_json",
        "restrictions",
        "normative_basis",
        "confidence",
    ):
        value = layer.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    return compact or None


def _compact_layers_for_client(layers: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(layers, list):
        return []
    selected = layers[:limit] if limit else layers
    return [item for item in (_compact_layer_for_client(layer) for layer in selected) if item is not None]


def _compact_report_for_client(report_json: dict[str, Any] | None) -> dict[str, Any]:
    report = report_json or {}
    agents = report.get("agents") if isinstance(report.get("agents"), dict) else {}
    data_agent = agents.get("DataRequestAgent") if isinstance(agents.get("DataRequestAgent"), dict) else {}
    data_request = data_agent.get("data") if isinstance(data_agent.get("data"), dict) else {}
    spatial = data_request.get("spatial_layers") if isinstance(data_request.get("spatial_layers"), dict) else {}

    land_parts = spatial.get("land_parts")
    land_parts_count = len(land_parts) if isinstance(land_parts, list) else 0
    land_parts_limit = 500

    compact: dict[str, Any] = {
        key: report.get(key)
        for key in (
            "check_id",
            "plot",
            "nspd",
            "data_quality",
            "area_summary",
            "map_summary",
            "soil_summary",
            "infrastructure_summary",
            "market_summary",
            "chief_decision",
            "critical_risk",
            "report",
            "client_explanation",
            "next_steps",
            # --- Promoted structured data (pinned report_json contract) ---
            "scenario_ranking",
            "scenario_economics",
            "legal",
            "land_use",
            "restrictions",
            "score_breakdown",
        )
        if key in report
    }
    compact["spatial_layers"] = {
        "cadastral_number": spatial.get("cadastral_number") or (report.get("plot") or {}).get("cadastral_number"),
        "parcel_geometry_geojson": spatial.get("parcel_geometry_geojson") or "{}",
        "restriction_layers": _compact_layers_for_client(spatial.get("restriction_layers")),
        "land_use_layers": _compact_layers_for_client(spatial.get("land_use_layers")),
        "valuation_layers": _compact_layers_for_client(spatial.get("valuation_layers")),
        "informational_layers": _compact_layers_for_client(spatial.get("informational_layers")),
        "real_estate_objects": _compact_layers_for_client(spatial.get("real_estate_objects")),
        "child_real_estate_objects": _compact_layers_for_client(spatial.get("child_real_estate_objects")),
        "land_parts": _compact_layers_for_client(land_parts, limit=land_parts_limit),
        "land_parts_total": land_parts_count,
        "land_parts_returned": min(land_parts_count, land_parts_limit),
        "land_composition": spatial.get("land_composition") if isinstance(spatial.get("land_composition"), list) else [],
        "warnings": spatial.get("warnings") if isinstance(spatial.get("warnings"), list) else [],
    }
    return compact


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


class CheckServicer(check_pb2_grpc.CheckServiceServicer):
    """Implements check.proto CheckService."""

    async def CreateCheck(self, request, context):
        user_id = await _parse_uuid(request.user_id, "user_id", context)
        # proto3 scalars have no presence: lat/lng default to 0.0 when unset, so a
        # valid equator/prime-meridian coordinate is indistinguishable from "missing".
        # Treat coords as a locator whenever at least one component is non-zero, and
        # accept the values as given (0.0 stays 0.0 — never coerced to NULL).
        has_coords = bool(request.lat) or bool(request.lng)
        if not request.cadastral_number and not request.address and not has_coords:
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
                lat=request.lat,
                lng=request.lng,
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
            except Exception:
                # The row is already committed; if enqueue fails we must still
                # return the check_id so the client can poll and observe the
                # FAILED state instead of being left with an orphan row.
                check.status = CheckStatus.FAILED
                check.completed_at = datetime.utcnow()
                await session.commit()
                await session.refresh(check)

            return _check_response(check)

    async def GetCheck(self, request, context):
        check_id = await _parse_uuid(request.check_id, "check_id", context)
        async with AsyncSessionLocal() as session:
            check = await session.get(LandCheck, check_id)
            if check is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check not found")
            return _check_response(check)

    async def DeleteCheck(self, request, context):
        check_id = await _parse_uuid(request.check_id, "check_id", context)
        async with AsyncSessionLocal() as session:
            check = await session.get(LandCheck, check_id)
            if check is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check not found")
            # Remove dependent rows first (no DB-level cascade is guaranteed).
            await session.execute(delete(CheckResult).where(CheckResult.check_id == check_id))
            await session.execute(delete(CheckStep).where(CheckStep.check_id == check_id))
            await session.delete(check)
            await session.commit()
            return check_pb2.DeleteCheckResponse(success=True)

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

    async def ListAllChecks(self, request, context):
        # Admin-wide listing across all users. Unlike ListChecks there is no
        # user_id filter; an optional status narrows the result set.
        limit = min(max(request.limit or 20, 1), 100)
        offset = max(request.offset or 0, 0)
        status = (request.status or "").strip()
        async with AsyncSessionLocal() as session:
            count_stmt = select(func.count()).select_from(LandCheck)
            list_stmt = select(LandCheck).order_by(desc(LandCheck.created_at)).limit(limit).offset(offset)
            if status:
                count_stmt = count_stmt.where(LandCheck.status == status)
                list_stmt = list_stmt.where(LandCheck.status == status)
            total = await session.scalar(count_stmt)
            rows = await session.scalars(list_stmt)
            return check_pb2.ListChecksResponse(
                checks=[_check_response(row) for row in rows],
                total=int(total or 0),
            )

    async def CountChecks(self, request, context):
        # Aggregate check counts grouped by status for the admin dashboard.
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                select(LandCheck.status, func.count()).group_by(LandCheck.status)
            )
            by_status = {status: int(count) for status, count in rows}
            return check_pb2.CountChecksResponse(
                total=sum(by_status.values()),
                completed=by_status.get(CheckStatus.COMPLETED, 0),
                processing=by_status.get(CheckStatus.PROCESSING, 0),
                failed=by_status.get(CheckStatus.FAILED, 0),
                pending=by_status.get(CheckStatus.PENDING, 0),
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
        status = request.status or StepStatus.RUNNING
        progress_pct = min(max(request.progress_pct, 0), 100)
        output_json = _json_loads(request.output_json)
        is_active = status in {StepStatus.RUNNING, StepStatus.DONE, StepStatus.FAILED}
        is_finished = status in {StepStatus.DONE, StepStatus.FAILED}
        started_at = now if is_active else None
        completed_at = now if is_finished else None

        async with AsyncSessionLocal() as session:
            check = await session.get(LandCheck, check_id)
            if check is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "check not found")

            # Atomic upsert: the unique index on (check_id, agent_name) means a
            # concurrent read-modify-insert would race into an IntegrityError.
            # ON CONFLICT DO UPDATE keeps the operation a single statement.
            stmt = pg_insert(CheckStep).values(
                id=uuid.uuid4(),
                check_id=check_id,
                agent_name=request.agent_name,
                status=status,
                progress_pct=progress_pct,
                output_json=output_json,
                started_at=started_at,
                completed_at=completed_at,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["check_id", "agent_name"],
                set_={
                    "status": status,
                    "progress_pct": progress_pct,
                    "output_json": output_json,
                    # Preserve the first start time; only backfill if still NULL.
                    "started_at": func.coalesce(CheckStep.started_at, stmt.excluded.started_at),
                    # Stamp completion when finished; otherwise keep prior value.
                    "completed_at": (
                        stmt.excluded.completed_at if is_finished else CheckStep.completed_at
                    ),
                },
            )
            await session.execute(stmt)

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

        # Always embed geo metadata so the frontend can render the cadastral map
        # even after a page refresh (sessionStorage is gone, but report_json persists).
        geo_meta = {
            "_coords": {
                "lat": check.lat,
                "lng": check.lng,
                "cadastral_number": check.cadastral_number,
                "address": check.address,
            }
        }

        if result is None:
            # Check still processing — return stub with geo coords so the map works
            return check_pb2.CheckReportResponse(
                check_id=str(check.id),
                status=check.status,
                overall_score=0,
                legal_risk="",
                stop_factors=[],
                best_scenario="",
                report_json=json.dumps(geo_meta, ensure_ascii=False),
                explanation="",
                next_steps=[],
            )

        # Any malformed report_json must degrade gracefully rather than raise
        # gRPC INTERNAL: fall back to the raw report, then to the geo-only stub.
        try:
            merged = {**geo_meta, **_compact_report_for_client(result.report_json)}
            report_str = json.dumps(merged, ensure_ascii=False)
        except (TypeError, ValueError, AttributeError, KeyError):
            try:
                raw = result.report_json if isinstance(result.report_json, dict) else {}
                report_str = json.dumps({**geo_meta, **raw}, ensure_ascii=False)
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
