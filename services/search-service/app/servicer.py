import uuid
import json
import grpc
import structlog
from datetime import datetime
from sqlalchemy import select, func

from google.protobuf import empty_pb2

import search_pb2
import search_pb2_grpc

from app.database import AsyncSessionLocal
from app.models import (
    LandSearch,
    SearchCriteria,
    SearchStep,
    SearchCandidate,
    SearchRecommendation,
)

log = structlog.get_logger()


def _search_resp(s: LandSearch) -> search_pb2.SearchResponse:
    return search_pb2.SearchResponse(
        search_id=str(s.id),
        user_id=str(s.user_id),
        query=s.query or "",
        status=s.status,
        candidates_count=0,  # updated in list path if needed
        created_at=s.created_at.isoformat() if s.created_at else "",
    )


class SearchServicer(search_pb2_grpc.SearchServiceServicer):

    async def CreateSearch(self, request, context):
        if not request.user_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
            return
        try:
            user_uuid = uuid.UUID(request.user_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid user_id format")
            return

        profile_data = {}
        if request.user_profile_json:
            try:
                profile_data = json.loads(request.user_profile_json)
            except (json.JSONDecodeError, ValueError):
                profile_data = {}

        search = LandSearch(
            id=uuid.uuid4(),
            user_id=user_uuid,
            status="pending",
            query=request.query or "",
            user_profile_json=profile_data,
        )

        try:
            async with AsyncSessionLocal() as session:
                session.add(search)
                await session.flush()

                # Create empty criteria placeholder
                criteria = SearchCriteria(
                    id=uuid.uuid4(),
                    search_id=search.id,
                    criteria_json={},
                    confirmed=False,
                )
                session.add(criteria)
                await session.commit()
                await session.refresh(search)

            log.info("search_created", search_id=str(search.id))
            return _search_resp(search)
        except Exception as exc:
            log.error("create_search_error", error=str(exc))
            await context.abort(grpc.StatusCode.INTERNAL, f"DB error: {exc}")

    async def GetSearch(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        async with AsyncSessionLocal() as session:
            row = await session.execute(select(LandSearch).where(LandSearch.id == search_uuid))
            search = row.scalar_one_or_none()

        if not search:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Search not found")
            return

        return _search_resp(search)

    async def ListSearches(self, request, context):
        limit = max(1, min(request.limit or 20, 100))
        offset = max(0, request.offset or 0)

        try:
            user_uuid = uuid.UUID(request.user_id) if request.user_id else None
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid user_id")
            return

        async with AsyncSessionLocal() as session:
            base = select(LandSearch)
            count_base = select(func.count()).select_from(LandSearch)
            if user_uuid:
                base = base.where(LandSearch.user_id == user_uuid)
                count_base = count_base.where(LandSearch.user_id == user_uuid)

            rows = await session.execute(
                base.order_by(LandSearch.created_at.desc()).limit(limit).offset(offset)
            )
            searches = rows.scalars().all()
            total = (await session.execute(count_base)).scalar() or 0

        return search_pb2.ListSearchesResponse(
            searches=[_search_resp(s) for s in searches],
            total=total,
        )

    async def GetSearchStatus(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        async with AsyncSessionLocal() as session:
            s_row = await session.execute(select(LandSearch).where(LandSearch.id == search_uuid))
            search = s_row.scalar_one_or_none()
            if not search:
                await context.abort(grpc.StatusCode.NOT_FOUND, "Search not found")
                return

            steps_row = await session.execute(
                select(SearchStep)
                .where(SearchStep.search_id == search_uuid)
                .order_by(SearchStep.started_at.nullslast())
            )
            steps = steps_row.scalars().all()

        current_step = ""
        progress_pct = 0
        if steps:
            latest = steps[-1]
            current_step = latest.agent_name
            progress_pct = latest.progress_pct

        return search_pb2.SearchStatusResponse(
            search_id=str(search.id),
            status=search.status,
            current_step=current_step,
            progress_pct=progress_pct,
        )

    async def GetSearchCriteria(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(SearchCriteria).where(SearchCriteria.search_id == search_uuid)
            )
            criteria = row.scalar_one_or_none()

        if not criteria:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Criteria not found")
            return

        criteria_str = "{}"
        if criteria.criteria_json:
            try:
                criteria_str = json.dumps(criteria.criteria_json, ensure_ascii=False)
            except (TypeError, ValueError):
                criteria_str = "{}"

        return search_pb2.SearchCriteriaResponse(
            search_id=str(criteria.search_id),
            criteria_json=criteria_str,
            confirmed=criteria.confirmed,
        )

    async def ConfirmCriteria(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        criteria_data = {}
        if request.criteria_json:
            try:
                criteria_data = json.loads(request.criteria_json)
            except (json.JSONDecodeError, ValueError):
                criteria_data = {}

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(SearchCriteria).where(SearchCriteria.search_id == search_uuid)
            )
            criteria = row.scalar_one_or_none()

            if not criteria:
                await context.abort(grpc.StatusCode.NOT_FOUND, "Criteria not found")
                return

            criteria.criteria_json = criteria_data
            criteria.confirmed = True
            criteria.confirmed_at = datetime.utcnow()

            # Resume pipeline: set search status back to processing
            s_row = await session.execute(select(LandSearch).where(LandSearch.id == search_uuid))
            search = s_row.scalar_one_or_none()
            if search and search.status == "awaiting_confirmation":
                search.status = "processing"

            await session.commit()

        return empty_pb2.Empty()

    async def GetSearchResults(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                select(SearchCandidate)
                .where(SearchCandidate.search_id == search_uuid)
                .order_by(SearchCandidate.rank)
            )
            candidates = rows.scalars().all()

        return search_pb2.SearchResultsResponse(
            candidates=[
                search_pb2.Candidate(
                    plot_id=c.plot_id,
                    rank=c.rank,
                    scores_json=json.dumps(c.scores_json or {}, ensure_ascii=False),
                    plot_summary_json=json.dumps(c.plot_summary_json or {}, ensure_ascii=False),
                )
                for c in candidates
            ]
        )

    async def GetRecommendation(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(SearchRecommendation).where(SearchRecommendation.search_id == search_uuid)
            )
            rec = row.scalar_one_or_none()

        if not rec:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Recommendation not ready yet")
            return

        rec_str = "{}"
        if rec.recommendation_json:
            try:
                rec_str = json.dumps(rec.recommendation_json, ensure_ascii=False)
            except (TypeError, ValueError):
                rec_str = "{}"

        return search_pb2.RecommendationResponse(
            search_id=str(rec.search_id),
            recommendation_json=rec_str,
            top_plot_ids=list(rec.top_plot_ids or []),
            explanation=rec.explanation or "",
        )

    async def SaveCriteria(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        criteria_data = {}
        if request.criteria_json:
            try:
                criteria_data = json.loads(request.criteria_json)
            except (json.JSONDecodeError, ValueError):
                criteria_data = {}

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(SearchCriteria).where(SearchCriteria.search_id == search_uuid)
            )
            criteria = row.scalar_one_or_none()

            if not criteria:
                criteria = SearchCriteria(
                    id=uuid.uuid4(),
                    search_id=search_uuid,
                    criteria_json=criteria_data,
                    confirmed=False,
                )
                session.add(criteria)
            else:
                criteria.criteria_json = criteria_data

            # Advance search to awaiting_confirmation
            s_row = await session.execute(select(LandSearch).where(LandSearch.id == search_uuid))
            search = s_row.scalar_one_or_none()
            if search and search.status == "pending":
                search.status = "awaiting_confirmation"

            await session.commit()

        return empty_pb2.Empty()

    async def SaveCandidate(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        scores_data = {}
        if request.scores_json:
            try:
                scores_data = json.loads(request.scores_json)
            except (json.JSONDecodeError, ValueError):
                scores_data = {}

        async with AsyncSessionLocal() as session:
            candidate = SearchCandidate(
                id=uuid.uuid4(),
                search_id=search_uuid,
                plot_id=request.plot_id or "",
                rank=request.rank or 0,
                scores_json=scores_data,
            )
            session.add(candidate)
            await session.commit()

        return empty_pb2.Empty()

    async def UpdateSearchProgress(self, request, context):
        try:
            search_uuid = uuid.UUID(request.search_id)
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid search_id")
            return

        async with AsyncSessionLocal() as session:
            row = await session.execute(
                select(SearchStep).where(
                    SearchStep.search_id == search_uuid,
                    SearchStep.agent_name == request.agent_name,
                )
            )
            step = row.scalar_one_or_none()

            if not step:
                step = SearchStep(
                    id=uuid.uuid4(),
                    search_id=search_uuid,
                    agent_name=request.agent_name,
                    started_at=datetime.utcnow(),
                )
                session.add(step)

            step.status = request.status
            step.progress_pct = request.progress_pct
            if request.status == "completed":
                step.completed_at = datetime.utcnow()

            s_row = await session.execute(select(LandSearch).where(LandSearch.id == search_uuid))
            search = s_row.scalar_one_or_none()
            if search and search.status not in ("completed", "failed", "awaiting_confirmation"):
                search.status = "processing"

            await session.commit()

        return empty_pb2.Empty()
