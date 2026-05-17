import json
import uuid
from datetime import datetime
from typing import Any

import grpc
from google.protobuf.empty_pb2 import Empty
from sqlalchemy import desc, func, select

import search_pb2
import search_pb2_grpc
from app.celery_client import enqueue_search
from app.constants import SearchStatus, StepStatus
from app.database import AsyncSessionLocal
from app.models import (
    LandSearch,
    SearchCandidate,
    SearchCriteria,
    SearchRecommendation,
    SearchStep,
)


async def _parse_uuid(value: str, field_name: str, context) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"invalid {field_name}")


def _json_loads(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _search_response(search: LandSearch, candidates_count: int = 0) -> search_pb2.SearchResponse:
    return search_pb2.SearchResponse(
        search_id=str(search.id),
        user_id=str(search.user_id),
        query=search.query or "",
        status=search.status,
        candidates_count=candidates_count,
        created_at=_iso(search.created_at),
    )


class SearchServicer(search_pb2_grpc.SearchServiceServicer):
    """Implements search.proto SearchService."""

    async def CreateSearch(self, request, context):
        user_id = await _parse_uuid(request.user_id, "user_id", context)
        if not request.query.strip():
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "query is required")

        async with AsyncSessionLocal() as session:
            search = LandSearch(
                id=uuid.uuid4(),
                user_id=user_id,
                status=SearchStatus.PENDING,
                query=request.query.strip(),
                user_profile_json=_json_loads(request.user_profile_json),
            )
            session.add(search)
            session.add(SearchCriteria(search_id=search.id, criteria_json={}, confirmed=False))
            await session.commit()
            await session.refresh(search)

            try:
                enqueue_search(
                    {
                        "search_id": str(search.id),
                        "query": search.query,
                        "user_profile_json": request.user_profile_json or "{}",
                    }
                )
            except Exception as exc:
                search.status = SearchStatus.FAILED
                search.completed_at = datetime.utcnow()
                await session.commit()
                await context.abort(grpc.StatusCode.UNAVAILABLE, f"failed to enqueue search: {exc}")

            return _search_response(search)

    async def GetSearch(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            count = await session.scalar(
                select(func.count()).select_from(SearchCandidate).where(SearchCandidate.search_id == search_id)
            )
            return _search_response(search, int(count or 0))

    async def ListSearches(self, request, context):
        user_id = await _parse_uuid(request.user_id, "user_id", context)
        limit = min(max(request.limit or 20, 1), 100)
        offset = max(request.offset or 0, 0)
        async with AsyncSessionLocal() as session:
            total = await session.scalar(
                select(func.count()).select_from(LandSearch).where(LandSearch.user_id == user_id)
            )
            searches = list(
                await session.scalars(
                    select(LandSearch)
                    .where(LandSearch.user_id == user_id)
                    .order_by(desc(LandSearch.created_at))
                    .limit(limit)
                    .offset(offset)
                )
            )
            counts: dict[uuid.UUID, int] = {}
            if searches:
                rows = await session.execute(
                    select(SearchCandidate.search_id, func.count())
                    .where(SearchCandidate.search_id.in_([s.id for s in searches]))
                    .group_by(SearchCandidate.search_id)
                )
                counts = {row[0]: int(row[1]) for row in rows}
            return search_pb2.ListSearchesResponse(
                searches=[_search_response(s, counts.get(s.id, 0)) for s in searches],
                total=int(total or 0),
            )

    async def GetSearchStatus(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            steps = list(
                await session.scalars(
                    select(SearchStep).where(SearchStep.search_id == search_id).order_by(desc(SearchStep.started_at))
                )
            )
            running = next((step for step in steps if step.status == StepStatus.RUNNING), None)
            current = running or (steps[0] if steps else None)
            progress = max([step.progress_pct for step in steps], default=0)
            if search.status == SearchStatus.COMPLETED:
                progress = 100
            return search_pb2.SearchStatusResponse(
                search_id=str(search.id),
                status=search.status,
                current_step=current.agent_name if current else "",
                progress_pct=progress,
            )

    async def GetSearchCriteria(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            criteria = await session.scalar(select(SearchCriteria).where(SearchCriteria.search_id == search_id))
            if criteria is None:
                criteria = SearchCriteria(search_id=search_id, criteria_json={}, confirmed=False)
                session.add(criteria)
                await session.commit()
            return search_pb2.SearchCriteriaResponse(
                search_id=str(search_id),
                criteria_json=_json_dumps(criteria.criteria_json),
                confirmed=criteria.confirmed,
            )

    async def ConfirmCriteria(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            criteria = await session.scalar(select(SearchCriteria).where(SearchCriteria.search_id == search_id))
            if criteria is None:
                criteria = SearchCriteria(search_id=search_id)
                session.add(criteria)
            criteria.criteria_json = _json_loads(request.criteria_json)
            criteria.confirmed = True
            criteria.confirmed_at = datetime.utcnow()
            search.status = SearchStatus.PROCESSING
            await session.commit()
            try:
                enqueue_search(
                    {
                        "search_id": str(search.id),
                        "query": search.query,
                        "user_profile_json": _json_dumps(search.user_profile_json),
                        "criteria_json": _json_dumps(criteria.criteria_json),
                        "confirmed": True,
                    }
                )
            except Exception as exc:
                search.status = SearchStatus.FAILED
                search.completed_at = datetime.utcnow()
                await session.commit()
                await context.abort(grpc.StatusCode.UNAVAILABLE, f"failed to resume search: {exc}")
            return Empty()

    async def GetSearchResults(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            candidates = await session.scalars(
                select(SearchCandidate)
                .where(SearchCandidate.search_id == search_id)
                .order_by(SearchCandidate.rank.asc(), SearchCandidate.plot_id.asc())
            )
            return search_pb2.SearchResultsResponse(
                candidates=[
                    search_pb2.Candidate(
                        plot_id=candidate.plot_id,
                        rank=candidate.rank,
                        scores_json=_json_dumps(candidate.scores_json),
                        plot_summary_json=_json_dumps(candidate.plot_summary_json),
                    )
                    for candidate in candidates
                ]
            )

    async def GetRecommendation(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            recommendation = await session.get(SearchRecommendation, search_id)
            if recommendation is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "recommendation not ready")
            return search_pb2.RecommendationResponse(
                search_id=str(search_id),
                recommendation_json=_json_dumps(recommendation.recommendation_json),
                top_plot_ids=list(recommendation.top_plot_ids or []),
                explanation=recommendation.explanation or "",
            )

    async def SaveCriteria(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            criteria = await session.scalar(select(SearchCriteria).where(SearchCriteria.search_id == search_id))
            if criteria is None:
                criteria = SearchCriteria(search_id=search_id)
                session.add(criteria)
            criteria.criteria_json = _json_loads(request.criteria_json)
            criteria.confirmed = False
            criteria.confirmed_at = None
            search.status = SearchStatus.AWAITING_CONFIRMATION
            await session.commit()
            return Empty()

    async def SaveCandidate(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        if not request.plot_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "plot_id is required")
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            candidate = await session.scalar(
                select(SearchCandidate).where(
                    SearchCandidate.search_id == search_id,
                    SearchCandidate.plot_id == request.plot_id,
                )
            )
            if candidate is None:
                candidate = SearchCandidate(search_id=search_id, plot_id=request.plot_id)
                session.add(candidate)
            candidate.rank = request.rank
            candidate.scores_json = _json_loads(request.scores_json)
            candidate.plot_summary_json = _json_loads(request.plot_summary_json)
            await session.commit()
            return Empty()

    async def SaveRecommendation(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            recommendation = await session.get(SearchRecommendation, search_id)
            if recommendation is None:
                recommendation = SearchRecommendation(search_id=search_id)
                session.add(recommendation)
            recommendation.recommendation_json = _json_loads(request.recommendation_json)
            recommendation.top_plot_ids = list(request.top_plot_ids)
            recommendation.explanation = request.explanation or ""
            search.status = SearchStatus.COMPLETED
            search.completed_at = datetime.utcnow()
            await session.commit()
            return Empty()

    async def UpdateSearchProgress(self, request, context):
        search_id = await _parse_uuid(request.search_id, "search_id", context)
        if not request.agent_name:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "agent_name is required")
        now = datetime.utcnow()
        async with AsyncSessionLocal() as session:
            search = await session.get(LandSearch, search_id)
            if search is None:
                await context.abort(grpc.StatusCode.NOT_FOUND, "search not found")
            step = await session.scalar(
                select(SearchStep).where(
                    SearchStep.search_id == search_id,
                    SearchStep.agent_name == request.agent_name,
                )
            )
            if step is None:
                step = SearchStep(search_id=search_id, agent_name=request.agent_name, started_at=now)
                session.add(step)
            step.status = request.status or StepStatus.RUNNING
            step.progress_pct = min(max(request.progress_pct, 0), 100)
            if step.status in {StepStatus.DONE, StepStatus.FAILED}:
                step.completed_at = now
            if search.status == SearchStatus.PENDING:
                search.status = SearchStatus.PROCESSING
            if step.status == StepStatus.FAILED:
                search.status = SearchStatus.FAILED
                search.completed_at = now
            await session.commit()
            return Empty()
