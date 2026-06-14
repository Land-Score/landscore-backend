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


class ShortlistRankingAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Оценивает и ранжирует оставшихся кандидатов. Каждый получает словарь
    scores и rank. Возвращает top-кандидатов в форме, ожидаемой tasks.py:
    candidates -> [{plot_id, rank, scores, summary}, ...].
    """

    name = "ShortlistRankingAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        filtered = ctx.get("CandidateFilteringAgent") or {}
        candidates = filtered.get("candidates") if isinstance(filtered, dict) else None
        if candidates is None:
            candidates = ctx.get("filtered_candidates") or []

        criteria = ctx.get("SearchCriteriaAgent") or {}
        if not isinstance(criteria, dict):
            criteria = {}
        target_area = _to_float(_first(criteria, "target_area", "area"))
        price_max = _to_float(_first(criteria, "price_max", "max_price", "budget_max", "budget"))
        priorities = [str(p).lower() for p in (ctx.profile.priority or [])]

        scored: list[dict] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            summary = candidate.get("summary") or {}
            area = _to_float(summary.get("area"))
            price = _to_float(summary.get("price"))

            # Цена: чем дешевле относительно бюджета, тем выше балл (0..100).
            if price_max and price:
                price_score = max(0.0, min(100.0, (1 - price / price_max) * 100 + 50))
            elif price:
                price_score = 50.0
            else:
                price_score = 30.0

            # Соответствие по площади (0..100).
            if target_area and area:
                deviation = abs(area - target_area) / target_area
                area_score = max(0.0, 100.0 - deviation * 100)
            else:
                area_score = 60.0 if area else 30.0

            # Полнота данных.
            completeness = sum(
                10.0
                for field in ("address", "category", "allowed_use", "lat", "lng", "price")
                if summary.get(field)
            )
            completeness_score = min(100.0, completeness + 40.0)

            weights = {"price": 0.4, "area": 0.35, "completeness": 0.25}
            if "margin" in priorities:
                weights = {"price": 0.55, "area": 0.3, "completeness": 0.15}
            if "infrastructure" in priorities:
                weights = {"price": 0.3, "area": 0.3, "completeness": 0.4}

            total = (
                price_score * weights["price"]
                + area_score * weights["area"]
                + completeness_score * weights["completeness"]
            )
            scores = {
                "price": round(price_score, 1),
                "area_match": round(area_score, 1),
                "completeness": round(completeness_score, 1),
                "total": round(total, 1),
            }
            scored.append({
                "plot_id": candidate.get("plot_id"),
                "cadastral_number": candidate.get("cadastral_number", ""),
                "scores": scores,
                "summary": summary,
            })

        scored.sort(key=lambda item: item["scores"]["total"], reverse=True)
        for index, item in enumerate(scored, start=1):
            item["rank"] = index

        top = scored[:10]
        ctx.set("ranked_candidates", scored)
        result = {
            "candidates": top,
            "candidates_count": len(top),
            "total_ranked": len(scored),
            "best_candidate": top[0]["plot_id"] if top else None,
        }
        return AgentResult(success=True, data=result)
