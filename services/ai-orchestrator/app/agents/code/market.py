from __future__ import annotations

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app.clients.market_client import MarketClient


def _to_float(value) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    # NaN/inf must never propagate: they crash round()/int() and corrupt report_json.
    if result != result or result in (float("inf"), float("-inf")):
        return 0.0
    return result


def _region(ctx: AgentContext) -> str:
    if ctx.profile.region:
        return ctx.profile.region
    address = ctx.plot.address or ""
    if address:
        # Берём первый сегмент адреса как регион (область/край/республика).
        return address.split(",")[0].strip()
    return ""


class MarketAgent(Agent):
    """Чисто вычислительный агент — без LLM.

    Запрашивает рыночную аналитику по участку (медианная цена за м²,
    отклонение от рынка, активность) и формирует market_summary в ctx.
    Деградирует мягко, если market-service недоступен.
    """

    name = "MarketAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        plot = ctx.plot
        region = _region(ctx)
        area = _to_float(plot.area)
        asking_price = _to_float(plot.price)

        try:
            analysis = await MarketClient().get_market_analysis(
                region=region,
                category=plot.category or "",
                allowed_use=plot.allowed_use or "",
                area=area,
                lat=_to_float(plot.lat),
                lng=_to_float(plot.lng),
                asking_price=asking_price,
            )
        except Exception as exc:
            summary = {
                "available": False,
                "reason": "market-service недоступен; рыночная аналитика пропущена",
                "median_price_per_sqm": 0,
                "asking_price": asking_price,
            }
            ctx.set("market_summary", summary)
            return AgentResult(
                success=True,
                data={
                    "market_available": False,
                    "warnings": [f"market_analysis_failed:{exc}"],
                    "market_summary": summary,
                    # Эти ключи читает ProfitabilityCalculatorAgent.
                    "median_price_per_sqm": 0,
                    "avg_price_per_sqm": 0,
                },
            )

        median_sqm = _to_float(analysis.get("median_price_per_sqm"))
        avg_sqm = _to_float(analysis.get("avg_price_per_sqm"))
        estimated_market_value = round(area * median_sqm) if area and median_sqm else 0

        summary = {
            "available": True,
            "region": region,
            "median_price_per_sqm": median_sqm,
            "avg_price_per_sqm": avg_sqm,
            "comparables_count": int(_to_float(analysis.get("comparables_count"))),
            "price_assessment": analysis.get("price_assessment", ""),
            "price_deviation_pct": _to_float(analysis.get("price_deviation_pct")),
            "market_activity": analysis.get("market_activity", ""),
            "trend": analysis.get("trend", ""),
            "asking_price": asking_price,
            "estimated_market_value": estimated_market_value,
            "commentary": analysis.get("llm_commentary", ""),
        }
        ctx.set("market_summary", summary)
        ctx.set("market_analysis", analysis)

        return AgentResult(
            success=True,
            data={
                "market_available": True,
                "market_summary": summary,
                "median_price_per_sqm": median_sqm,
                "avg_price_per_sqm": avg_sqm,
                "estimated_market_value": estimated_market_value,
            },
        )
