from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import grpc
import structlog

from app.clickhouse_client import get_client

PROTO_GEN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
if PROTO_GEN_DIR not in sys.path:
    sys.path.insert(0, PROTO_GEN_DIR)

try:
    import market_pb2
except ImportError:  # pragma: no cover - generated stubs may not exist in local smoke tests yet.
    market_pb2 = None

log = structlog.get_logger(__name__)


def _message_or_dict(message_name: str, data: dict[str, Any]):
    if market_pb2 is None:
        return data
    return getattr(market_pb2, message_name)(**data)


def _price_assessment(asking_ppsqm: float, median_ppsqm: float) -> tuple[str, float]:
    """Return (assessment, deviation_pct) of asking vs. market median ppsqm.

    deviation_pct > 0  => asking is above market.
    """

    if median_ppsqm <= 0 or asking_ppsqm <= 0:
        return "недостаточно данных", 0.0
    deviation = (asking_ppsqm - median_ppsqm) / median_ppsqm * 100.0
    deviation = round(deviation, 1)
    if deviation <= -10.0:
        return "ниже рынка", deviation
    if deviation >= 10.0:
        return "выше рынка", deviation
    return "в рынке", deviation


def _market_activity(total_count: int, recent_count: int) -> str:
    """Activity from sample size and how much of it is recent."""

    if total_count == 0:
        return "нет данных"
    recent_share = recent_count / total_count if total_count else 0.0
    if recent_count >= 40 and recent_share >= 0.30:
        return "высокая"
    if recent_count >= 10:
        return "средняя"
    return "низкая"


def _trend(recent_median: float, older_median: float) -> str:
    """Compare recent vs. older median ppsqm to label the price trend."""

    if recent_median <= 0 or older_median <= 0:
        return "стабильно"
    change = (recent_median - older_median) / older_median * 100.0
    if change >= 5.0:
        return "рост"
    if change <= -5.0:
        return "снижение"
    return "стабильно"


def _commentary(
    assessment: str,
    deviation_pct: float,
    count: int,
    activity: str,
    trend: str,
) -> str:
    """Short rule-based Russian commentary (no LLM call)."""

    if count == 0:
        return "По заданным параметрам сопоставимых предложений не найдено — оценка невозможна."

    parts: list[str] = []
    if assessment == "ниже рынка":
        parts.append(f"Цена примерно на {abs(deviation_pct):.0f}% ниже медианы рынка — предложение выглядит привлекательно.")
    elif assessment == "выше рынка":
        parts.append(f"Цена примерно на {abs(deviation_pct):.0f}% выше медианы рынка — есть запас для торга.")
    elif assessment == "в рынке":
        parts.append("Цена соответствует медиане рынка по сопоставимым участкам.")
    else:
        parts.append("Запрошенная цена не указана, приведена только рыночная статистика.")

    parts.append(f"Выборка: {count} сопоставимых предложений, рыночная активность {activity}.")

    if trend == "рост":
        parts.append("Динамика цен — рост.")
    elif trend == "снижение":
        parts.append("Динамика цен — снижение.")
    else:
        parts.append("Динамика цен стабильна.")

    return " ".join(parts)


class MarketServicer:
    """Implements market.proto MarketService against ClickHouse comparables."""

    def __init__(self) -> None:
        self.ch = get_client()

    async def GetMarketAnalysis(self, request, context):
        region = request.region or ""
        category = request.category or ""
        allowed_use = request.allowed_use or ""
        area = float(request.area or 0.0)
        asking_price = float(request.asking_price or 0.0)

        try:
            agg = await asyncio.to_thread(self.ch.analysis_aggregates, region, category, allowed_use)
            buckets = await asyncio.to_thread(self.ch.trend_buckets, region, category)
        except Exception as exc:  # noqa: BLE001
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"ClickHouse query failed: {exc}")
            return _message_or_dict("MarketAnalysis", {})

        # If allowed_use filtering yields too few rows, fall back to region+category.
        if agg["total_count"] < 5 and allowed_use:
            agg = await asyncio.to_thread(self.ch.analysis_aggregates, region, category, "")

        median_ppsqm = agg["median"]
        avg_ppsqm = agg["avg"]
        count = agg["total_count"]

        asking_ppsqm = (asking_price / area) if (asking_price > 0 and area > 0) else 0.0
        assessment, deviation = _price_assessment(asking_ppsqm, median_ppsqm)
        activity = _market_activity(agg["total_count"], agg["recent_count"])
        trend = _trend(buckets["recent_median"], buckets["older_median"])
        commentary = _commentary(assessment, deviation, count, activity, trend)

        return _message_or_dict(
            "MarketAnalysis",
            {
                "median_price_per_sqm": round(median_ppsqm, 2),
                "avg_price_per_sqm": round(avg_ppsqm, 2),
                "comparables_count": count,
                "price_assessment": assessment,
                "price_deviation_pct": deviation,
                "market_activity": activity,
                "trend": trend,
                "llm_commentary": commentary,
            },
        )

    async def GetComparables(self, request, context):
        region = request.region or ""
        category = request.category or ""
        area_min = float(request.area_min or 0.0)
        area_max = float(request.area_max or 0.0)
        limit = int(request.limit or 0) or 20

        try:
            rows = await asyncio.to_thread(
                self.ch.comparables, region, category, area_min, area_max, limit
            )
        except Exception as exc:  # noqa: BLE001
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"ClickHouse query failed: {exc}")
            return _message_or_dict("ComparablesResponse", {"comparables": []})

        return _message_or_dict("ComparablesResponse", {"comparables": rows})

    async def GetPriceStats(self, request, context):
        region = request.region or ""
        category = request.category or ""

        try:
            stats = await asyncio.to_thread(self.ch.price_stats, region, category)
        except Exception as exc:  # noqa: BLE001
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details(f"ClickHouse query failed: {exc}")
            return _message_or_dict("PriceStatsResponse", {})

        return _message_or_dict(
            "PriceStatsResponse",
            {
                "p25": round(stats["p25"], 2),
                "median": round(stats["median"], 2),
                "p75": round(stats["p75"], 2),
                "avg": round(stats["avg"], 2),
                "sample_size": stats["sample_size"],
                "period": "последние 18 месяцев",
            },
        )
