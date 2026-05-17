import json
import grpc
from fastapi import APIRouter, Request, Query

from app.errors import raise_for_grpc
from app.models import (
    CreateCheckRequest, CheckItemResponse, CheckStatusResponse,
    CheckReportResponse, ListChecksResponse,
)

router = APIRouter()


def _check_item(r) -> CheckItemResponse:
    return CheckItemResponse(
        check_id=r.check_id,
        status=r.status,
        cadastral_number=r.cadastral_number or None,
        address=r.address or None,
        purpose=getattr(r, "purpose", ""),
        created_at=r.created_at,
        completed_at=r.completed_at or None,
    )


@router.post(
    "/",
    response_model=CheckItemResponse,
    status_code=202,
    summary="Запустить проверку участка",
    description=(
        "Создаёт задачу анализа участка. Требует хотя бы одно из: "
        "`cadastral_number`, `address` или `lat`+`lng`. "
        "После создания опрашивайте `/status` до получения `status=completed`."
    ),
    responses={
        400: {"description": "Не указан идентификатор участка"},
        202: {"description": "Задача принята в обработку"},
    },
)
async def create_check(body: CreateCheckRequest, request: Request) -> CheckItemResponse:
    if not body.cadastral_number and not body.address and not (body.lat and body.lng):
        from fastapi import HTTPException
        raise HTTPException(
            400,
            detail="Укажите cadastral_number, address или координаты lat+lng",
        )

    import check_pb2
    import auth_pb2

    # Fetch user profile to pass to the pipeline
    user_profile_json = "{}"
    try:
        profile = await request.app.state.auth_stub.GetProfile(
            auth_pb2.GetProfileRequest(user_id=request.state.user_id)
        )
        user_profile_json = json.dumps({
            "user_id": profile.user_id,
            "client_type": profile.client_type,
            "main_task": profile.main_task,
            "region": profile.region,
            "priority": list(profile.priority),
            "risk_tolerance": profile.risk_tolerance,
            "preferred_scenarios": list(profile.preferred_scenarios),
        }, ensure_ascii=False)
    except grpc.RpcError:
        pass  # proceed with empty profile if auth-service unavailable

    stub = request.app.state.check_stub
    try:
        resp = await stub.CreateCheck(check_pb2.CreateCheckRequest(
            user_id=request.state.user_id,
            cadastral_number=body.cadastral_number or "",
            address=body.address or "",
            lat=body.lat or 0.0,
            lng=body.lng or 0.0,
            purpose=body.purpose,
            user_profile_json=user_profile_json,
        ))
        # check-service enqueues the Celery pipeline task internally on CreateCheck
        return _check_item(resp)
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/",
    response_model=ListChecksResponse,
    summary="История проверок текущего пользователя",
)
async def list_checks(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение для пагинации"),
) -> ListChecksResponse:
    import check_pb2
    stub = request.app.state.check_stub
    try:
        resp = await stub.ListChecks(check_pb2.ListChecksRequest(
            user_id=request.state.user_id,
            limit=limit,
            offset=offset,
        ))
        return ListChecksResponse(
            checks=[_check_item(c) for c in resp.checks],
            total=resp.total,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/{check_id}/status",
    response_model=CheckStatusResponse,
    summary="Прогресс выполнения анализа",
    description=(
        "Опрашивайте каждые 2 секунды. "
        "Когда `status=completed` или `status=failed`, прекратите polling."
    ),
)
async def get_status(check_id: str, request: Request) -> CheckStatusResponse:
    import check_pb2
    stub = request.app.state.check_stub
    try:
        resp = await stub.GetCheckStatus(check_pb2.CheckIdRequest(check_id=check_id))
        return CheckStatusResponse(
            check_id=resp.check_id,
            status=resp.status,
            current_step=resp.current_step,
            progress_pct=resp.progress_pct,
            completed_steps=[
                {"agent_name": s.agent_name, "status": s.status, "duration_ms": s.duration_ms}
                for s in resp.completed_steps
            ],
            error_message=resp.error_message,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/{check_id}/report",
    response_model=CheckReportResponse,
    summary="Полный отчёт по участку",
    description="Доступен только после `status=completed`. Содержит LandScore, риски, сценарии и следующие шаги.",
    responses={
        404: {"description": "Результат ещё не готов или проверка не найдена"},
    },
)
async def get_report(check_id: str, request: Request) -> CheckReportResponse:
    import check_pb2
    stub = request.app.state.check_stub
    try:
        resp = await stub.GetCheckReport(check_pb2.CheckIdRequest(check_id=check_id))
        return CheckReportResponse(
            check_id=resp.check_id,
            status=resp.status,
            overall_score=resp.overall_score,
            legal_risk=resp.legal_risk or None,
            stop_factors=list(resp.stop_factors),
            best_scenario=resp.best_scenario or None,
            report_json=resp.report_json,
            explanation=resp.explanation,
            next_steps=list(resp.next_steps),
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)
