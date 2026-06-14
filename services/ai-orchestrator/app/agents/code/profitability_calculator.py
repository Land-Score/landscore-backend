from __future__ import annotations

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class ProfitabilityCalculatorAgent(Agent):
    """
    Pure math agent — NO LLM.
    Calculates ROI, payback period, and margin for each selected scenario,
    using scenario-agent inputs (scenario_inputs) and market price_per_sqm.
    """
    name = "ProfitabilityCalculatorAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        scenario_selector = ctx.get("ScenarioSelectorAgent", {}) or {}
        selected = scenario_selector.get("selected_scenarios") or ctx.get("selected_scenarios") or []
        market = ctx.get("market_summary") or ctx.get("MarketAgent", {}) or {}
        scenario_inputs = ctx.get("scenario_inputs") or {}
        plot = ctx.plot

        results: dict[str, dict] = {}
        for scenario in selected:
            inputs = scenario_inputs.get(scenario, {})
            results[scenario] = self._calc(scenario, plot, market, inputs)

        ctx.set("scenario_profitability", results)
        return AgentResult(success=True, data={"scenarios": results})

    def _market_value(self, plot, market: dict) -> float:
        area = _to_float(plot.area)
        median_sqm = _to_float(market.get("median_price_per_sqm"))
        if not median_sqm:
            median_sqm = _to_float(market.get("avg_price_per_sqm"))
        estimated = _to_float(market.get("estimated_market_value"))
        if estimated:
            return estimated
        return area * median_sqm if area and median_sqm else 0.0

    def _calc(self, scenario: str, plot, market: dict, inputs: dict) -> dict:
        price = _to_float(plot.price)
        market_value = self._market_value(plot, market)
        applicable = inputs.get("applicable", True)

        if not applicable:
            return {
                "applicable": False,
                "profit": None,
                "roi_pct": None,
                "payback_years": None,
                "note": inputs.get("note") or "Сценарий неприменим для данного участка.",
            }

        if scenario == "resale":
            cost_pct = _to_float(inputs.get("transaction_cost_pct")) or 0.05
            costs = price * cost_pct
            profit = market_value - price - costs
            roi = (profit / price * 100) if price else 0.0
            return {
                "applicable": True,
                "investment": round(price + costs),
                "revenue": round(market_value),
                "profit": round(profit),
                "roi_pct": round(roi, 1),
                "margin_pct": round((profit / market_value * 100) if market_value else 0.0, 1),
                "payback_years": None,
            }

        if scenario == "construction":
            buildable = _to_float(inputs.get("buildable_area_sqm"))
            build_cost = buildable * _to_float(inputs.get("build_cost_per_sqm"))
            total_investment = price + build_cost
            sale_value = buildable * _to_float(inputs.get("sale_price_per_sqm"))
            profit = sale_value - total_investment
            roi = (profit / total_investment * 100) if total_investment else 0.0
            return {
                "applicable": True,
                "investment": round(total_investment),
                "revenue": round(sale_value),
                "profit": round(profit),
                "roi_pct": round(roi, 1),
                "margin_pct": round((profit / sale_value * 100) if sale_value else 0.0, 1),
                "payback_years": 3,
            }

        if scenario == "agriculture":
            usable_ha = _to_float(inputs.get("usable_area_ha"))
            setup = usable_ha * _to_float(inputs.get("setup_cost_per_ha"))
            annual_net = usable_ha * (
                _to_float(inputs.get("annual_revenue_per_ha")) - _to_float(inputs.get("annual_cost_per_ha"))
            )
            total_investment = price + setup
            payback = (total_investment / annual_net) if annual_net > 0 else None
            roi = (annual_net / total_investment * 100) if total_investment else 0.0
            return {
                "applicable": True,
                "investment": round(total_investment),
                "annual_profit": round(annual_net),
                "profit": round(annual_net),
                "roi_pct": round(roi, 1),
                "margin_pct": round(
                    (annual_net / (usable_ha * _to_float(inputs.get("annual_revenue_per_ha"))) * 100)
                    if usable_ha and _to_float(inputs.get("annual_revenue_per_ha"))
                    else 0.0,
                    1,
                ),
                "payback_years": round(payback, 1) if payback else None,
            }

        if scenario == "lease":
            usable_ha = _to_float(inputs.get("usable_area_ha"))
            annual_net = usable_ha * (
                _to_float(inputs.get("annual_lease_per_ha")) - _to_float(inputs.get("annual_holding_cost_per_ha"))
            )
            total_investment = price
            payback = (total_investment / annual_net) if annual_net > 0 else None
            roi = (annual_net / total_investment * 100) if total_investment else 0.0
            return {
                "applicable": True,
                "investment": round(total_investment),
                "annual_profit": round(annual_net),
                "profit": round(annual_net),
                "roi_pct": round(roi, 1),
                "margin_pct": round(
                    (annual_net / (usable_ha * _to_float(inputs.get("annual_lease_per_ha"))) * 100)
                    if usable_ha and _to_float(inputs.get("annual_lease_per_ha"))
                    else 0.0,
                    1,
                ),
                "payback_years": round(payback, 1) if payback else None,
            }

        if scenario == "mixed_use":
            buildable = _to_float(inputs.get("buildable_area_sqm"))
            build_cost = buildable * _to_float(inputs.get("build_cost_per_sqm"))
            sale_value = buildable * _to_float(inputs.get("sale_price_per_sqm"))
            build_profit = sale_value - build_cost
            agri_ha = _to_float(inputs.get("agri_part_ha"))
            agri_annual = agri_ha * (
                _to_float(inputs.get("annual_revenue_per_ha")) - _to_float(inputs.get("annual_cost_per_ha"))
            )
            # Грубая сводная оценка: разовая прибыль от застройки + 3 года сельхоздохода.
            total_investment = price + build_cost
            profit = build_profit + agri_annual * 3
            roi = (profit / total_investment * 100) if total_investment else 0.0
            return {
                "applicable": True,
                "investment": round(total_investment),
                "build_profit": round(build_profit),
                "agriculture_annual_profit": round(agri_annual),
                "profit": round(profit),
                "roi_pct": round(roi, 1),
                "payback_years": 3,
            }

        return {
            "applicable": True,
            "profit": None,
            "roi_pct": None,
            "payback_years": None,
            "note": "Требуется ручная оценка.",
        }
