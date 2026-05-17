"""Celery tasks - entry points for the agent pipelines."""
import asyncio
import json
import re
from dataclasses import asdict
from typing import Any

import grpc

from app.celery_app import celery_app
from app.clients.proto_imports import add_generated_proto_path
from app.config import settings
from app.pipeline.base import AgentResult
from app.pipeline.check_pipeline import build_check_pipeline
from app.pipeline.context import AgentContext, PlotPassport, UserProfile
from app.pipeline.runner import PipelineRunner
from app.pipeline.search_pipeline import build_search_pipeline
from app.agents.llm.report_context import build_report_context

add_generated_proto_path()
import check_pb2  # noqa: E402
import check_pb2_grpc  # noqa: E402
import search_pb2  # noqa: E402
import search_pb2_grpc  # noqa: E402


def _run_async(coro):
    return asyncio.run(coro)


def _result_to_dict(result: AgentResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "tokens_used": result.tokens_used,
        "duration_ms": result.duration_ms,
    }


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _json_loads(data: str) -> Any:
    if not data:
        return {}
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {"raw": data}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("explanation", "summary", "text", "message", "report"):
            if value.get(key):
                return _as_text(value[key])
    return _json_dumps(value)


def _fit_db_text(value: Any, limit: int) -> str:
    text = _as_text(value).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        for key in ("text", "explanation", "summary", "message"):
            if key in value:
                return _as_list(value[key])
        for key in ("next_steps", "steps", "stop_factors", "items"):
            if key in value:
                return _as_list(value[key])
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        numbered = [
            match.group(1).strip()
            for match in re.finditer(
                r"(?ms)^\s*(\d+[\.)]\s+.*?)(?=^\s*\d+[\.)]\s+|\Z)",
                text,
            )
        ]
        return numbered or [text]
    return [str(value)]


async def _check_stub_call(method: str, request) -> None:
    async with grpc.aio.insecure_channel(settings.check_grpc) as channel:
        stub = check_pb2_grpc.CheckServiceStub(channel)
        await getattr(stub, method)(request)


async def _search_stub_call(method: str, request) -> None:
    async with grpc.aio.insecure_channel(settings.search_grpc) as channel:
        stub = search_pb2_grpc.SearchServiceStub(channel)
        await getattr(stub, method)(request)


async def _update_check_progress(
    check_id: str,
    agent_name: str,
    progress_pct: int,
    result: AgentResult,
) -> None:
    status = "done" if result.success else "failed"
    output = result.data if result.success else {"error": result.error}
    await _check_stub_call(
        "UpdateCheckProgress",
        check_pb2.UpdateProgressRequest(
            check_id=check_id,
            agent_name=agent_name,
            status=status,
            progress_pct=progress_pct,
            output_json=_json_dumps(output),
        ),
    )


async def _save_check_result(check_id: str, ctx: AgentContext, results: dict[str, AgentResult]) -> None:
    agents = {name: _result_to_dict(result) for name, result in results.items()}
    decision = ctx.get("ChiefDecisionAgent") or {}
    critical = ctx.get("CriticalRiskAgent") or {}
    report = ctx.get("ReportAgent") or {}
    explanation = ctx.get("ClientExplanationAgent") or {}
    next_steps = ctx.get("NextStepsAgent") or {}
    report_context = build_report_context(ctx)
    report_payload = {
        "check_id": check_id,
        "plot": asdict(ctx.plot),
        "data_quality": report_context.get("data_quality"),
        "nspd": report_context.get("nspd"),
        "area_summary": report_context.get("area_summary"),
        "map_summary": ctx.get("map_summary") or ctx.get("GeoAgent", {}).get("map_summary"),
        "soil_summary": report_context.get("soil_summary"),
        "infrastructure_summary": report_context.get("infrastructure_summary"),
        "market_summary": report_context.get("market_summary"),
        "chief_decision": decision,
        "critical_risk": critical,
        "report": report,
        "client_explanation": explanation,
        "next_steps": next_steps,
        "agents": agents,
    }
    await _check_stub_call(
        "SaveCheckResult",
        check_pb2.SaveResultRequest(
            check_id=check_id,
            plot_id=ctx.plot.cadastral_number or "",
            overall_score=int(decision.get("overall_score") or 0),
            legal_risk=_fit_db_text(decision.get("legal_risk"), 20),
            stop_factors=_as_list(critical.get("stop_factors")),
            best_scenario=_fit_db_text(decision.get("best_scenario"), 50),
            report_json=_json_dumps(report_payload),
            explanation=_as_text(explanation),
            next_steps=_as_list(next_steps),
        ),
    )


async def _update_search_progress(
    search_id: str,
    agent_name: str,
    progress_pct: int,
    result: AgentResult,
) -> None:
    await _search_stub_call(
        "UpdateSearchProgress",
        search_pb2.UpdateProgressRequest(
            search_id=search_id,
            agent_name=agent_name,
            status="done" if result.success else "failed",
            progress_pct=progress_pct,
        ),
    )


async def _save_search_criteria(search_id: str, criteria: Any) -> None:
    await _search_stub_call(
        "SaveCriteria",
        search_pb2.SaveCriteriaRequest(
            search_id=search_id,
            criteria_json=_json_dumps(criteria),
        ),
    )


def _candidate_items(ctx: AgentContext) -> list[dict[str, Any]]:
    for key in ("ShortlistRankingAgent", "CandidateFilteringAgent", "LandScoutAgent"):
        data = ctx.get(key) or {}
        if isinstance(data, dict):
            for candidate_key in ("candidates", "results", "shortlist"):
                value = data.get(candidate_key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


async def _save_search_outputs(search_id: str, ctx: AgentContext, results: dict[str, AgentResult]) -> None:
    for index, candidate in enumerate(_candidate_items(ctx), start=1):
        plot_id = str(
            candidate.get("plot_id")
            or candidate.get("cadastral_number")
            or candidate.get("cadastralNumber")
            or ""
        )
        if not plot_id:
            continue
        await _search_stub_call(
            "SaveCandidate",
            search_pb2.SaveCandidateRequest(
                search_id=search_id,
                plot_id=plot_id,
                rank=int(candidate.get("rank") or index),
                scores_json=_json_dumps(candidate.get("scores") or candidate.get("scores_json") or {}),
                plot_summary_json=_json_dumps(candidate.get("summary") or candidate.get("plot_summary") or candidate),
            ),
        )

    decision = ctx.get("ChiefDecisionAgent") or {}
    report = ctx.get("ReportAgent") or {}
    next_steps = ctx.get("NextStepsAgent") or {}
    recommendation_payload = {
        "search_id": search_id,
        "chief_decision": decision,
        "report": report,
        "next_steps": next_steps,
        "agents": {name: _result_to_dict(result) for name, result in results.items()},
    }
    top_plot_ids = [
        str(item.get("plot_id") or item.get("cadastral_number") or item.get("cadastralNumber"))
        for item in _candidate_items(ctx)[:3]
    ]
    await _search_stub_call(
        "SaveRecommendation",
        search_pb2.SaveRecommendationRequest(
            search_id=search_id,
            recommendation_json=_json_dumps(recommendation_payload),
            top_plot_ids=[plot_id for plot_id in top_plot_ids if plot_id],
            explanation=_as_text(report or decision),
        ),
    )


@celery_app.task(bind=True, name="run_check")
def run_check_task(self, payload: dict):
    profile = UserProfile.from_json(json.loads(payload.get("user_profile_json", "{}")))
    ctx = AgentContext(
        job_id=self.request.id,
        owner_id=payload["check_id"],
        owner_type="check",
        profile=profile,
        plot=PlotPassport(
            cadastral_number=payload.get("cadastral_number", ""),
            address=payload.get("address", ""),
            lat=payload.get("lat", 0.0),
            lng=payload.get("lng", 0.0),
        ),
    )
    for optional_key in (
        "parcel_geometry_geojson",
        "raw_features_by_layer_json",
        "vision_interpretation_json",
    ):
        if optional_key in payload:
            ctx.set(optional_key, payload[optional_key])

    async def _run():
        pipeline = build_check_pipeline()

        async def on_progress(agent_name: str, pct: int, result: AgentResult) -> None:
            try:
                await _update_check_progress(payload["check_id"], agent_name, pct, result)
            except grpc.RpcError as exc:
                print(f"[WARN] failed to update check progress: {exc}")

        runner = PipelineRunner(pipeline, on_progress=on_progress)
        results = await runner.run(ctx)
        await _save_check_result(payload["check_id"], ctx, results)
        return {name: _result_to_dict(result) for name, result in results.items()}

    return _run_async(_run())


@celery_app.task(bind=True, name="run_search")
def run_search_task(self, payload: dict):
    profile = UserProfile.from_json(json.loads(payload.get("user_profile_json", "{}")))
    ctx = AgentContext(
        job_id=self.request.id,
        owner_id=payload["search_id"],
        owner_type="search",
        profile=profile,
    )

    async def _run():
        if payload.get("criteria_json"):
            ctx.set("SearchCriteriaAgent", _json_loads(payload.get("criteria_json", "{}")))

        async def on_progress(agent_name: str, pct: int, result: AgentResult) -> None:
            try:
                await _update_search_progress(payload["search_id"], agent_name, pct, result)
            except grpc.RpcError as exc:
                print(f"[WARN] failed to update search progress: {exc}")

        full_pipeline = build_search_pipeline()
        pipeline = full_pipeline[2:] if payload.get("confirmed") else full_pipeline[:2]
        runner = PipelineRunner(pipeline, on_progress=on_progress)
        results = await runner.run(ctx)

        if payload.get("confirmed"):
            await _save_search_outputs(payload["search_id"], ctx, results)
        else:
            await _save_search_criteria(payload["search_id"], ctx.get("SearchCriteriaAgent") or {})

        return {name: _result_to_dict(result) for name, result in results.items()}

    return _run_async(_run())
