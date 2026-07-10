from __future__ import annotations

from typing import Any

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _first(criteria: dict, *keys: str) -> Any:
    for key in keys:
        if key in criteria and criteria[key] not in (None, ""):
            return criteria[key]
    return None


def _norm(text: str) -> str:
    return (text or "").strip().lower()


class CandidateFilteringAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Отсеивает заведомо неподходящих кандидатов по критериям
    (площадь / цена / категория). Сохраняет причины отсева.
    """

    name = "CandidateFilteringAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        scout = ctx.get("LandScoutAgent") or {}
        candidates = scout.get("candidates") if isinstance(scout, dict) else None
        if candidates is None:
            candidates = ctx.get("scout_candidates") or []

        criteria = ctx.get("SearchCriteriaAgent") or {}
        if not isinstance(criteria, dict):
            criteria = {}

        area_min = _to_float(_first(criteria, "area_min_ha", "area_min", "min_area"))
        area_max = _to_float(_first(criteria, "area_max_ha", "area_max", "max_area"))
        price_min = _to_float(_first(criteria, "price_min", "min_price", "budget_min"))
        price_max = _to_float(_first(criteria, "price_max", "max_price", "budget_max", "budget"))
        category = _norm(str(_first(criteria, "category") or ""))

        kept: list[dict] = []
        rejected: list[dict] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            summary = candidate.get("summary") or {}
            area = _to_float(summary.get("area"))
            price = _to_float(summary.get("price"))
            cand_category = _norm(str(summary.get("category") or ""))

            # Criteria area_min/max are in hectares; candidate area is in m².
            area_ha = area / 10_000.0 if area else 0.0

            reasons: list[str] = []
            if area_min and area_ha and area_ha < area_min:
                reasons.append("площадь меньше минимальной")
            if area_max and area_ha and area_ha > area_max:
                reasons.append("площадь больше максимальной")
            if price_min and price and price < price_min:
                reasons.append("цена ниже диапазона")
            if price_max and price and price > price_max:
                reasons.append("цена выше бюджета")
            if category and cand_category and category not in cand_category and cand_category not in category:
                reasons.append("категория не соответствует критериям")

            if reasons:
                rejected.append({"plot_id": candidate.get("plot_id"), "reasons": reasons})
            else:
                kept.append(candidate)

        ctx.set("filtered_candidates", kept)
        result = {
            "candidates": kept,
            "candidates_count": len(kept),
            "rejected_count": len(rejected),
            "rejected": rejected,
        }
        return AgentResult(success=True, data=result)
