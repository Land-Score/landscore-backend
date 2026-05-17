import json
import grpc
from fastapi import APIRouter, Request, Query

from app.errors import raise_for_grpc
from app.models import (
    CreateSearchRequest, SearchItemResponse, SearchStatusResponse,
    SearchCriteriaResponse, ConfirmCriteriaRequest,
    SearchResultsResponse, CandidateResponse,
    RecommendationResponse, ListSearchesResponse,
)

router = APIRouter()


def _search_item(r) -> SearchItemResponse:
    return SearchItemResponse(
        search_id=r.search_id,
        status=r.status,
        query=r.query,
        candidates_count=r.candidates_count,
        created_at=r.created_at,
    )


@router.post(
    "/",
    response_model=SearchItemResponse,
    status_code=202,
    summary="Запустить поиск участка",
    description=(
        "Принимает описание задачи в свободной форме. "
        "AI извлекает критерии поиска — их нужно подтвердить на следующем шаге. "
        "После создания опрашивайте `/status` до `status=awaiting_confirmation`, "
        "затем покажите критерии пользователю на экране `/criteria`."
    ),
)
async def create_search(body: CreateSearchRequest, request: Request) -> SearchItemResponse:
    import search_pb2
    import auth_pb2

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
        pass

    stub = request.app.state.search_stub
    try:
        resp = await stub.CreateSearch(search_pb2.CreateSearchRequest(
            user_id=request.state.user_id,
            query=body.query,
            user_profile_json=user_profile_json,
        ))
    except grpc.RpcError as e:
        raise_for_grpc(e)

    # Dispatch the AI search pipeline to ai-orchestrator Celery worker.
    import asyncio
    await asyncio.to_thread(
        request.app.state.celery.send_task,
        "run_search",
        args=[{
            "search_id": resp.search_id,
            "query": body.query,
            "user_profile_json": user_profile_json,
        }],
    )

    return _search_item(resp)


@router.get(
    "/",
    response_model=ListSearchesResponse,
    summary="История поисков текущего пользователя",
)
async def list_searches(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> ListSearchesResponse:
    import search_pb2
    stub = request.app.state.search_stub
    try:
        resp = await stub.ListSearches(search_pb2.ListSearchesRequest(
            user_id=request.state.user_id,
            limit=limit,
            offset=offset,
        ))
        return ListSearchesResponse(
            searches=[_search_item(s) for s in resp.searches],
            total=resp.total,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/{search_id}/status",
    response_model=SearchStatusResponse,
    summary="Прогресс поиска",
    description=(
        "Статусы жизненного цикла:\n"
        "- `pending` → запуск\n"
        "- `awaiting_confirmation` → критерии извлечены, ждут подтверждения\n"
        "- `processing` → разведка кандидатов\n"
        "- `completed` → результаты готовы\n"
        "- `failed` → ошибка\n"
    ),
)
async def get_status(search_id: str, request: Request) -> SearchStatusResponse:
    import search_pb2
    stub = request.app.state.search_stub
    try:
        resp = await stub.GetSearchStatus(search_pb2.SearchIdRequest(search_id=search_id))
        return SearchStatusResponse(
            search_id=resp.search_id,
            status=resp.status,
            current_step=resp.current_step,
            progress_pct=resp.progress_pct,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/{search_id}/criteria",
    response_model=SearchCriteriaResponse,
    summary="Критерии поиска (для подтверждения пользователем)",
    description=(
        "Возвращает структурированные критерии, извлечённые AI из запроса. "
        "Пока `confirmed=false`, пайплайн приостановлен. "
        "Пользователь может отредактировать критерии и подтвердить через PUT."
    ),
)
async def get_criteria(search_id: str, request: Request) -> SearchCriteriaResponse:
    import search_pb2
    stub = request.app.state.search_stub
    try:
        resp = await stub.GetSearchCriteria(search_pb2.SearchIdRequest(search_id=search_id))
        return SearchCriteriaResponse(
            search_id=resp.search_id,
            criteria_json=resp.criteria_json,
            confirmed=resp.confirmed,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.put(
    "/{search_id}/criteria",
    status_code=204,
    summary="Подтвердить критерии поиска",
    description=(
        "Передайте `criteria_json` (можно отредактированный). "
        "После подтверждения пайплайн возобновляется — LandScout начинает поиск кандидатов."
    ),
    responses={
        204: {"description": "Критерии подтверждены, поиск возобновлён"},
        404: {"description": "Поиск не найден"},
    },
)
async def confirm_criteria(
    search_id: str, body: ConfirmCriteriaRequest, request: Request
):
    import search_pb2
    stub = request.app.state.search_stub
    try:
        await stub.ConfirmCriteria(search_pb2.ConfirmCriteriaRequest(
            search_id=search_id,
            criteria_json=body.criteria_json,
        ))
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/{search_id}/results",
    response_model=SearchResultsResponse,
    summary="Найденные кандидаты",
    description="Список участков-кандидатов, ранжированных по соответствию критериям.",
)
async def get_results(search_id: str, request: Request) -> SearchResultsResponse:
    import search_pb2
    stub = request.app.state.search_stub
    try:
        resp = await stub.GetSearchResults(search_pb2.SearchIdRequest(search_id=search_id))
        return SearchResultsResponse(
            candidates=[
                CandidateResponse(
                    plot_id=c.plot_id,
                    rank=c.rank,
                    scores_json=c.scores_json,
                    plot_summary_json=c.plot_summary_json,
                )
                for c in resp.candidates
            ]
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)


@router.get(
    "/{search_id}/recommendation",
    response_model=RecommendationResponse,
    summary="Финальная рекомендация",
    description="Рекомендация ChiefDecisionAgent с объяснением выбора лучшего кандидата.",
    responses={
        404: {"description": "Рекомендация ещё не готова"},
    },
)
async def get_recommendation(search_id: str, request: Request) -> RecommendationResponse:
    import search_pb2
    stub = request.app.state.search_stub
    try:
        resp = await stub.GetRecommendation(search_pb2.SearchIdRequest(search_id=search_id))
        return RecommendationResponse(
            search_id=resp.search_id,
            recommendation_json=resp.recommendation_json,
            top_plot_ids=list(resp.top_plot_ids),
            explanation=resp.explanation,
        )
    except grpc.RpcError as e:
        raise_for_grpc(e)
