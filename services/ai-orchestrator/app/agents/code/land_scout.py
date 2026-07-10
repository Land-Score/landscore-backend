from __future__ import annotations

from typing import Any

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app.clients.data_collector_client import DataCollectorClient


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


def _allowed_use_str(value: Any) -> str:
    """Criteria may carry allowed_use as a string or a list of strings
    (the frontend criteria-view emits an array). Flatten to a single
    comma-joined string for the data-collector contract / matching."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts)
    return str(value).strip()


class LandScoutAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Запрашивает кандидатов в data-collector по критериям SearchCriteriaAgent.
    Деградирует мягко, если сервис недоступен.
    """

    name = "LandScoutAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        criteria = ctx.get("SearchCriteriaAgent") or {}
        if not isinstance(criteria, dict):
            criteria = {}

        region = str(_first(criteria, "region") or ctx.profile.region or "")
        category = str(_first(criteria, "category") or "")
        allowed_use = _allowed_use_str(_first(criteria, "allowed_use", "vri"))
        area_min = _to_float(_first(criteria, "area_min_ha", "area_min", "min_area"))
        area_max = _to_float(_first(criteria, "area_max_ha", "area_max", "max_area"))
        price_min = _to_float(_first(criteria, "price_min", "min_price", "budget_min"))
        price_max = _to_float(_first(criteria, "price_max", "max_price", "budget_max", "budget"))
        limit = int(_to_float(_first(criteria, "limit"), 20))

        try:
            response = await DataCollectorClient().search_plots(
                region=region,
                category=category,
                allowed_use=allowed_use,
                area_min=area_min,
                area_max=area_max,
                price_min=price_min,
                price_max=price_max,
                limit=limit,
            )
        except Exception as exc:
            result = {
                "candidates": [],
                "candidates_count": 0,
                "available": False,
                "warnings": [f"land_scout_failed:{exc}"],
            }
            ctx.set("scout_candidates", [])
            return AgentResult(success=True, data=result)

        plots = response.get("plots", []) or []
        candidates: list[dict] = []
        for index, plot in enumerate(plots, start=1):
            if not isinstance(plot, dict):
                continue
            cadastral = str(plot.get("cadastral_number") or "")
            candidates.append({
                "plot_id": cadastral or f"candidate-{index}",
                "cadastral_number": cadastral,
                "summary": {
                    "address": plot.get("address", ""),
                    "area": _to_float(plot.get("area")),
                    "category": plot.get("category", ""),
                    "allowed_use": plot.get("allowed_use", ""),
                    "price": _to_float(plot.get("price")),
                    "lat": _to_float(plot.get("lat")),
                    "lng": _to_float(plot.get("lng")),
                },
                "raw": plot,
            })

        ctx.set("scout_candidates", candidates)
        result = {
            "candidates": candidates,
            "candidates_count": len(candidates),
            "total": int(_to_float(response.get("total"), len(candidates))),
            "available": True,
            "criteria_used": {
                "region": region,
                "category": category,
                "allowed_use": allowed_use,
                "area_min": area_min,
                "area_max": area_max,
                "price_min": price_min,
                "price_max": price_max,
            },
        }
        return AgentResult(success=True, data=result)
