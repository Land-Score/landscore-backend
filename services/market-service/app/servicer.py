from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import grpc
import statistics
import structlog

from app.clickhouse_client import get_client
from app.torgi_client import fetch_land_comparables

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


def _safe_float(value: Any) -> float:
    """Coerce to float, mapping None / NaN / inf to 0.0 so they never propagate
    into round()/division and crash-loop the RPC."""
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if result != result or result in (float("inf"), float("-inf")):
        return 0.0
    return result


def _price_assessment(asking_ppsqm: float, median_ppsqm: float) -> tuple[str, float]:
    """Return (assessment, deviation_pct) of asking vs. market median ppsqm.

    deviation_pct > 0  => asking is above market.

    NOTE: only call this with a TRUE seller asking price. Cadastral value
    (государственная кадастровая стоимость) is structurally below market and
    must NOT be labelled "ниже рынка" — use NEUTRAL_CADASTRAL_ASSESSMENT instead.
    """

    # NaN/inf guard: a malformed input must never propagate into round()/labels.
    if asking_ppsqm != asking_ppsqm or median_ppsqm != median_ppsqm:
        return "недостаточно данных", 0.0
    if median_ppsqm <= 0 or asking_ppsqm <= 0:
        return "недостаточно данных", 0.0
    deviation = (asking_ppsqm - median_ppsqm) / median_ppsqm * 100.0
    deviation = round(deviation, 1)
    if deviation <= -10.0:
        return "ниже рынка", deviation
    if deviation >= 10.0:
        return "выше рынка", deviation
    return "в рынке", deviation


# The incoming MarketRequest.asking_price is the cadastral value, not a real
# seller asking price. Comparing it against the market median would falsely flag
# the plot as "ниже рынка" because cadastral is structurally below market. Until
# a separate true-asking-price field exists in the proto, treat the value as a
# cadastral reference and report this neutral assessment.
NEUTRAL_CADASTRAL_ASSESSMENT = "оценка по кадастровой стоимости"
NO_COMPS_ASSESSMENT = "нет рыночных аналогов"


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
        area = _safe_float(request.area)
        # NB: asking_price carries the cadastral value (государственная кадастровая
        # стоимость), NOT a real seller asking price. See module-level notes.
        cadastral_value = _safe_float(request.asking_price)

        # Cadastral ₽/m² — the real, official anchor (государственная кадастровая
        # стоимость from НСПД). Always our base signal, but it is structurally
        # BELOW market and must never be compared against the market median.
        cadastral_ppsqm = (cadastral_value / area) if (cadastral_value > 0 and area > 0) else 0.0

        # 1) Real live comparables from torgi.gov.ru (best-effort; sparse for land).
        try:
            comps = await asyncio.to_thread(fetch_land_comparables, region=region, category=category, allowed_use=allowed_use)
        except Exception as exc:  # noqa: BLE001 - never fail the RPC on the live source
            log.warning("torgi_unavailable", error=str(exc))
            comps = []
        ppsqm_values = sorted(c["price_per_sqm"] for c in comps if c.get("price_per_sqm", 0) > 0)

        if len(ppsqm_values) >= 3:
            # Real market median from live torgi comparables.
            median_ppsqm = round(statistics.median(ppsqm_values), 2)
            avg_ppsqm = round(sum(ppsqm_values) / len(ppsqm_values), 2)
            count = len(ppsqm_values)
            source = "torgi.gov.ru (госимущество)"
            # We DO have a market median, but the price we hold is the cadastral
            # value, not a real seller asking price. Comparing cadastral against
            # the market median would falsely flag the plot as "ниже рынка"
            # (cadastral is structurally below market). So we report a neutral
            # cadastral assessment and surface the cadastral ₽/м² for reference
            # only — we do NOT call _price_assessment on the cadastral value.
            deviation = 0.0
            activity = _market_activity(count, count)
            trend = "стабильно"
            if cadastral_ppsqm > 0:
                ratio_note = ""
                # Informational ratio, not a "below/above market" verdict.
                gap_pct = round((median_ppsqm - cadastral_ppsqm) / median_ppsqm * 100.0, 0) if median_ppsqm > 0 else 0.0
                if gap_pct > 0:
                    ratio_note = (
                        f" Кадастровая стоимость примерно на {gap_pct:.0f}% ниже рыночной медианы, "
                        f"что типично и не является признаком выгодной цены."
                    )
                assessment = NEUTRAL_CADASTRAL_ASSESSMENT
                commentary = (
                    f"Рыночный ориентир рассчитан по {count} реальным лотам torgi.gov.ru "
                    f"(медиана {median_ppsqm:,.0f} ₽/м²). Кадастровая стоимость — "
                    f"{cadastral_ppsqm:,.0f} ₽/м².{ratio_note} "
                    f"Реальная цена продавца не передана, оценка отклонения от рынка не выполнялась."
                ).replace(",", " ")
            else:
                assessment = NEUTRAL_CADASTRAL_ASSESSMENT
                commentary = (
                    f"Рыночный ориентир рассчитан по {count} реальным лотам torgi.gov.ru "
                    f"(медиана {median_ppsqm:,.0f} ₽/м²). Кадастровая стоимость не передана."
                ).replace(",", " ")
        else:
            # No live comparables. We must NOT equate the market median to the
            # cadastral value: doing so makes downstream resale ROI compute
            # profit = market_value - price - costs = -costs (price == market_value),
            # a phantom always-negative ROI. Report zero market median / zero
            # comparables and surface the cadastral ₽/м² only as a reference in
            # the commentary.
            median_ppsqm = 0.0
            avg_ppsqm = 0.0
            count = 0
            deviation = 0.0
            activity = "нет данных"
            trend = "стабильно"
            if cadastral_ppsqm > 0:
                source = "Государственная кадастровая стоимость (НСПД)"
                assessment = NEUTRAL_CADASTRAL_ASSESSMENT
                commentary = (
                    f"Рыночные аналоги по региону в открытых источниках (torgi.gov.ru) не найдены — "
                    f"рыночная медиана не рассчитывалась. Для справки кадастровая стоимость составляет "
                    f"{cadastral_ppsqm:,.0f} ₽/м² ({cadastral_ppsqm * 100:,.0f} ₽/сотка); это официальный "
                    f"ориентир, который структурно ниже рынка и не равен рыночной цене."
                ).replace(",", " ")
            else:
                source = "нет данных"
                assessment = NO_COMPS_ASSESSMENT
                commentary = (
                    "Недостаточно данных: рыночные аналоги не найдены и кадастровая стоимость не передана."
                )

        return _message_or_dict(
            "MarketAnalysis",
            {
                "median_price_per_sqm": median_ppsqm,
                "avg_price_per_sqm": avg_ppsqm,
                "comparables_count": count,
                "price_assessment": assessment,
                "price_deviation_pct": deviation,
                "market_activity": activity,
                "trend": trend,
                "llm_commentary": f"Источник: {source}. {commentary}",
            },
        )

    async def GetComparables(self, request, context):
        region = request.region or ""
        category = request.category or ""
        # ComparablesRequest has no allowed_use field today; read it defensively
        # so we stay consistent with GetMarketAnalysis (which always forwards it)
        # and pick it up automatically if the proto later gains the field.
        allowed_use = getattr(request, "allowed_use", "") or ""
        area_min = _safe_float(request.area_min)
        area_max = _safe_float(request.area_max)
        limit = int(_safe_float(request.limit)) or 20

        # Prefer real live comparables from torgi.gov.ru.
        try:
            live = await asyncio.to_thread(
                fetch_land_comparables, region=region, category=category, allowed_use=allowed_use
            )
        except Exception:  # noqa: BLE001
            live = []
        rows = [
            {
                "id": c["id"], "address": c["address"], "area": c["area"], "price": c["price"],
                "price_per_sqm": c["price_per_sqm"], "source": c["source"], "listed_at": c["listed_at"],
            }
            for c in live
            if (not area_min or c["area"] >= area_min) and (not area_max or c["area"] <= area_max)
        ][:limit]

        # Fall back to the labeled synthetic regional estimate only if no live data.
        if not rows:
            try:
                rows = await asyncio.to_thread(self.ch.comparables, region, category, area_min, area_max, limit)
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
