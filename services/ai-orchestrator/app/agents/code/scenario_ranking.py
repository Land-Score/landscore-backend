from __future__ import annotations

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# Базовая оценка риска сценария (чем выше, тем рискованнее) — для учёта risk_tolerance.
_SCENARIO_RISK = {
    "resale": 0.4,
    "lease": 0.2,
    "agriculture": 0.3,
    "construction": 0.7,
    "mixed_use": 0.6,
}

# Множитель допустимого риска в зависимости от толерантности профиля.
_RISK_TOLERANCE_FACTOR = {"low": 0.5, "medium": 1.0, "high": 1.5}


class ScenarioRankingAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Ранжирует сценарии по ROI с поправкой на приоритеты профиля
    (margin / legal_risk / infrastructure) и толерантность к риску.
    """

    name = "ScenarioRankingAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        profitability = ctx.get("ProfitabilityCalculatorAgent", {}) or {}
        scenarios = profitability.get("scenarios") or ctx.get("scenario_profitability") or {}

        priorities = [p.lower() for p in (ctx.profile.priority or [])]
        risk_tolerance = (ctx.profile.risk_tolerance or "medium").lower()
        tolerance_factor = _RISK_TOLERANCE_FACTOR.get(risk_tolerance, 1.0)

        critical = ctx.get("CriticalRiskAgent", {}) or {}
        stop_factors = int(_to_float(critical.get("stop_factors_count") or len(critical.get("stop_factors", []) or [])))
        infra = ctx.get("infrastructure_summary") or {}
        infra_score = _to_float(infra.get("accessibility_score"))

        ranked: list[dict] = []
        for key, result in scenarios.items():
            if not isinstance(result, dict):
                continue
            applicable = result.get("applicable", True)
            roi = _to_float(result.get("roi_pct"))
            margin = _to_float(result.get("margin_pct"))

            scenario_risk = _SCENARIO_RISK.get(key, 0.5)
            # Чем ниже толерантность, тем сильнее штраф за рискованный сценарий.
            risk_penalty = scenario_risk / tolerance_factor

            score = 0.0
            if applicable:
                score = roi
                if "margin" in priorities:
                    score += margin * 0.5
                if "legal_risk" in priorities:
                    score -= stop_factors * 15.0
                if "infrastructure" in priorities:
                    score += (infra_score - 50.0) * 0.3
                score -= risk_penalty * 20.0
            else:
                score = -1_000.0  # неприменимые сценарии в конец списка

            ranked.append({
                "scenario": key,
                "applicable": applicable,
                "roi_pct": result.get("roi_pct"),
                "margin_pct": result.get("margin_pct"),
                "payback_years": result.get("payback_years"),
                "score": round(score, 2),
            })

        ranked.sort(key=lambda item: item["score"], reverse=True)
        for index, item in enumerate(ranked, start=1):
            item["rank"] = index

        applicable_ranked = [item for item in ranked if item.get("applicable")]
        best = applicable_ranked[0]["scenario"] if applicable_ranked else (ranked[0]["scenario"] if ranked else None)

        result_data = {
            "ranked_scenarios": ranked,
            "best_scenario": best,
            "priorities_applied": priorities,
            "risk_tolerance": risk_tolerance,
        }
        ctx.set("scenario_ranking", result_data)
        return AgentResult(success=True, data=result_data)
