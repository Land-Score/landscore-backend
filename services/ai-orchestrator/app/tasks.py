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


GRPC_MESSAGE_OPTIONS = [
    ("grpc.max_send_message_length", 128 * 1024 * 1024),
    ("grpc.max_receive_message_length", 128 * 1024 * 1024),
]


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


def _safe_score(value: Any) -> int:
    """Coerce an LLM-provided overall_score to a clamped 0..100 int.

    The structured-output fallback path can return non-numeric strings
    ('78%', 'высокий', 'N/A'); int() on those would crash _save_check_result
    AFTER the whole pipeline already ran. Degrade to 0 instead of losing the run.
    """
    try:
        n = int(float(str(value).strip().rstrip("%") or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


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


def _step_to_text(item: dict) -> str:
    """Render a NextSteps-style step dict as a single human-readable Russian string.

    Joins the title and action (the two meaningful text fields) instead of
    repr(dict), which previously leaked "{'title': ...}" into next_steps.
    """
    title = str(item.get("title") or "").strip()
    action = str(item.get("action") or "").strip()
    if title and action and action != title:
        return f"{title}: {action}"
    text = title or action
    if text:
        return text
    # Fall back to any single text-like field before giving up on the dict.
    for key in ("text", "description", "reason", "summary", "name"):
        candidate = str(item.get(key) or "").strip()
        if candidate:
            return candidate
    return ""


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = _step_to_text(item)
            else:
                text = str(item).strip()
            if text:
                items.append(text)
        return items
    if isinstance(value, dict):
        # Prefer the structured list payloads (e.g. NextStepsAgent {steps:[...]})
        # over the single summary/text fields so the array keeps per-item entries
        # instead of collapsing into one summary line.
        for key in ("next_steps", "steps", "stop_factors", "items"):
            if key in value:
                return _as_list(value[key])
        for key in ("text", "explanation", "summary", "message"):
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
    async with grpc.aio.insecure_channel(settings.check_grpc, options=GRPC_MESSAGE_OPTIONS) as channel:
        stub = check_pb2_grpc.CheckServiceStub(channel)
        await getattr(stub, method)(request)


async def _search_stub_call(method: str, request) -> None:
    async with grpc.aio.insecure_channel(settings.search_grpc, options=GRPC_MESSAGE_OPTIONS) as channel:
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


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _build_legal(ctx: AgentContext) -> dict[str, Any]:
    """Surface the LegalAgent output in the pinned contract shape.

    Defensive: the agent may have failed (ctx.get returns None) — degrade to an
    empty-but-valid object so the frontend always has the keys.
    """
    legal = _as_dict(ctx.get("LegalAgent"))
    return {
        "risk_level": legal.get("risk_level"),
        "ownership_confirmed": bool(legal.get("ownership_confirmed")),
        "risks": [r for r in (legal.get("risks") or []) if isinstance(r, dict)],
        "encumbrances": legal.get("encumbrances") or [],
        "summary": legal.get("summary"),
    }


def _build_land_use(ctx: AgentContext) -> dict[str, Any]:
    land_use = _as_dict(ctx.get("LandUseAgent"))
    return {
        "category": land_use.get("category"),
        # LandUseAgent emits `allowed_use`; the contract names this `main_vri`.
        "main_vri": land_use.get("main_vri") or land_use.get("allowed_use"),
        "permitted_activities": land_use.get("permitted_activities") or [],
        "conditionally_permitted": land_use.get("conditionally_permitted") or [],
        "purpose_conformance": land_use.get("purpose_conformance"),
        "change_possibility": land_use.get("change_possibility"),
        "risks": [r for r in (land_use.get("risks") or []) if isinstance(r, dict)],
        "summary": land_use.get("summary"),
    }


def _build_restrictions(ctx: AgentContext) -> list[dict[str, Any]]:
    restrictions = _as_dict(ctx.get("RestrictionsAgent"))
    items = restrictions.get("restrictions") or []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append({
            "type": item.get("type"),
            "name": item.get("name"),
            "what_is_limited": item.get("what_is_limited"),
            "normative_basis": item.get("normative_basis"),
            "impact": item.get("impact"),
            "severity": item.get("severity"),
            "is_stop_factor": bool(item.get("is_stop_factor")),
        })
    return out


def _build_scenario_ranking(ctx: AgentContext) -> list[dict[str, Any]]:
    ranking = _as_dict(ctx.get("ScenarioRankingAgent")) or _as_dict(ctx.get("scenario_ranking"))
    ranked = ranking.get("ranked_scenarios") or []
    out: list[dict[str, Any]] = []
    for item in ranked:
        if not isinstance(item, dict):
            continue
        out.append({
            "scenario": item.get("scenario"),
            "applicable": bool(item.get("applicable")),
            "roi_pct": item.get("roi_pct"),
            "margin_pct": item.get("margin_pct"),
            "payback_years": item.get("payback_years"),
            "score": item.get("score"),
            "rank": item.get("rank"),
        })
    return out


def _build_scenario_economics(ctx: AgentContext) -> list[dict[str, Any]]:
    profitability = _as_dict(ctx.get("ProfitabilityCalculatorAgent"))
    scenarios = profitability.get("scenarios") or _as_dict(ctx.get("scenario_profitability"))
    if not isinstance(scenarios, dict):
        return []
    out: list[dict[str, Any]] = []
    for scenario, econ in scenarios.items():
        if not isinstance(econ, dict):
            continue
        out.append({
            "scenario": scenario,
            "investment": econ.get("investment"),
            "revenue": econ.get("revenue"),
            "profit": econ.get("profit"),
            "annual_profit": econ.get("annual_profit"),
        })
    return out


_SCORE_WEIGHTS = {
    "legal": 0.30,
    "vri_fit": 0.20,
    "market": 0.20,
    "infrastructure": 0.15,
    "location_eco": 0.15,
}

_SCORE_LABELS = {
    "legal": "Правовая чистота",
    "vri_fit": "Соответствие ВРИ",
    "market": "Рыночный потенциал",
    "infrastructure": "Инфраструктура",
    "location_eco": "Локация и экология",
}

_LEGAL_RISK_SCORE = {"low": 90, "medium": 65, "high": 35, "critical": 10}


def _fallback_category_scores(ctx: AgentContext, decision: dict[str, Any]) -> dict[str, int]:
    """Compute sensible per-category 0..100 scores from the structured agent
    outputs when the LLM omits score_breakdown.

    legal      <- legal_risk level
    market     <- price_deviation (closer to market = better)
    infra      <- accessibility_score
    vri_fit    <- map loss_percent + purpose conformance
    """
    legal_risk = str(
        decision.get("legal_risk")
        or _as_dict(ctx.get("LegalAgent")).get("risk_level")
        or ""
    ).lower()
    legal_score = _LEGAL_RISK_SCORE.get(legal_risk, 50)

    market = _as_dict(ctx.get("market_summary"))
    deviation = market.get("price_deviation_pct")
    try:
        market_score = max(0, min(100, int(100 - abs(float(deviation)) * 1.5))) if deviation is not None else 50
    except (TypeError, ValueError):
        market_score = 50

    infra = _as_dict(ctx.get("infrastructure_summary"))
    try:
        infra_score = max(0, min(100, int(float(infra.get("accessibility_score") or 50))))
    except (TypeError, ValueError):
        infra_score = 50

    map_summary = _as_dict(ctx.get("map_summary"))
    loss = map_summary.get("loss_percent")
    try:
        vri_score = max(0, min(100, int(100 - float(loss)))) if loss is not None else 60
    except (TypeError, ValueError):
        vri_score = 60
    conformance = str(_as_dict(ctx.get("LandUseAgent")).get("purpose_conformance") or "").lower()
    if conformance == "not_permitted":
        vri_score = min(vri_score, 20)
    elif conformance == "requires_change":
        vri_score = min(vri_score, 55)

    return {
        "legal": legal_score,
        "vri_fit": vri_score,
        "market": market_score,
        "infrastructure": infra_score,
        # No dedicated eco agent; anchor on infra/locality as a proxy.
        "location_eco": infra_score,
    }


def _build_score_breakdown(ctx: AgentContext, decision: dict[str, Any], critical: dict[str, Any]) -> dict[str, Any]:
    """Assemble score_breakdown. Prefer the LLM's per-category scores; fall back
    to computed scores. Always returns the full contract shape."""
    llm_breakdown = _as_dict(decision.get("score_breakdown"))
    fallback = _fallback_category_scores(ctx, decision)

    categories: list[dict[str, Any]] = []
    for key, weight in _SCORE_WEIGHTS.items():
        cat = _as_dict(llm_breakdown.get(key))
        try:
            score = int(float(cat.get("score")))
            score = max(0, min(100, score))
        except (TypeError, ValueError):
            score = fallback[key]
        contribution = cat.get("contribution")
        if not isinstance(contribution, (int, float)):
            contribution = round(score * weight, 1)
        categories.append({
            "key": key,
            "label": _SCORE_LABELS[key],
            "score": score,
            "weight": weight,
            "contribution": contribution,
        })

    try:
        penalty = int(float(llm_breakdown.get("data_quality_penalty")))
    except (TypeError, ValueError):
        penalty = 0

    stop_override = llm_breakdown.get("stop_factor_override")
    if not isinstance(stop_override, bool):
        stop_override = bool(critical.get("stop_has_critical")) or bool(decision.get("stop_factors_active"))

    overall = _safe_score(decision.get("overall_score"))
    return {
        "overall": overall,
        "categories": categories,
        "data_quality_penalty": penalty,
        "stop_factor_override": stop_override,
    }


def _build_structured_next_steps(next_steps: Any) -> dict[str, Any]:
    """Un-flatten NextStepsAgent output into {steps:[{title,action,reason,priority}]}."""
    data = _as_dict(next_steps)
    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list):
        steps_raw = []
    steps: list[dict[str, Any]] = []
    for item in steps_raw:
        if isinstance(item, dict):
            steps.append({
                "title": item.get("title"),
                "action": item.get("action"),
                "reason": item.get("reason"),
                "priority": item.get("priority"),
            })
        elif isinstance(item, str) and item.strip():
            steps.append({"title": item.strip(), "action": item.strip(), "reason": None, "priority": None})
    return {"steps": steps}


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
        # next_steps stays the raw agent dict for backward-compat; the structured
        # contract version is promoted to its own top-level key below.
        "next_steps": _build_structured_next_steps(next_steps),
        # --- Promoted structured data (pinned report_json contract) ---
        "scenario_ranking": _build_scenario_ranking(ctx),
        "scenario_economics": _build_scenario_economics(ctx),
        "legal": _build_legal(ctx),
        "land_use": _build_land_use(ctx),
        "restrictions": _build_restrictions(ctx),
        "score_breakdown": _build_score_breakdown(ctx, decision, critical),
        "agents": agents,
    }
    await _check_stub_call(
        "SaveCheckResult",
        check_pb2.SaveResultRequest(
            check_id=check_id,
            plot_id=ctx.plot.cadastral_number or "",
            overall_score=_safe_score(decision.get("overall_score")),
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
