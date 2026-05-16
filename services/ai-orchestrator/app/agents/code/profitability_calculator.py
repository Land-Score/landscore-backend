from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext


class ProfitabilityCalculatorAgent(Agent):
    """
    Pure math agent — NO LLM.
    Calculates ROI, payback period, and margin for each selected scenario.
    """
    name = "ProfitabilityCalculatorAgent"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        scenario_selector = ctx.get("ScenarioSelectorAgent", {})
        selected = scenario_selector.get("selected_scenarios", [])
        market = ctx.get("MarketAgent", {})
        plot = ctx.plot

        results = {}
        for scenario in selected:
            results[scenario] = self._calc(scenario, plot, market)

        return AgentResult(success=True, data={"scenarios": results})

    def _calc(self, scenario: str, plot, market: dict) -> dict:
        price = plot.price or 0
        area = plot.area or 0
        median_sqm = market.get("median_price_per_sqm", 0)
        market_value = area * median_sqm if area and median_sqm else 0

        if scenario == "resale":
            # Simple resale: market_value - purchase_price - costs(5%)
            costs = price * 0.05
            profit = market_value - price - costs
            roi = (profit / price * 100) if price else 0
            return {"profit": round(profit), "roi_pct": round(roi, 1), "payback_years": None}

        if scenario == "construction":
            # Estimated: land + build cost, sell as built property
            build_cost_per_sqm = 80_000  # RUB/m², rough estimate
            build_area = area * 0.3  # 30% buildable
            build_cost = build_area * build_cost_per_sqm
            total_investment = price + build_cost
            sale_value = build_area * 120_000  # resale ~120k/m²
            profit = sale_value - total_investment
            roi = (profit / total_investment * 100) if total_investment else 0
            return {"profit": round(profit), "roi_pct": round(roi, 1), "payback_years": 3}

        # Default stub for other scenarios
        return {"profit": None, "roi_pct": None, "payback_years": None, "note": "manual estimate needed"}
