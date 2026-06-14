from __future__ import annotations

from app.pipeline.base import Agent, AgentResult
from app.pipeline.context import AgentContext
from app.agents.code.scenario_selector import (
    _construction_allowed,
    _agriculture_allowed,
)


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _scenario_context(ctx: AgentContext) -> tuple[dict, float, str, str]:
    """Возвращает (facts, usable_area_sqm, category, allowed_use)."""
    facts = ctx.get("normalized_facts") or {}
    areas = facts.get("areas", {}) if isinstance(facts, dict) else {}
    usable_ha = _to_float(areas.get("usable_area_ha"))
    cadastral_sqm = _to_float(areas.get("cadastral_area_sqm")) or _to_float(ctx.plot.area)
    usable_sqm = usable_ha * 10_000.0 if usable_ha else cadastral_sqm
    category = facts.get("category") or ctx.plot.category or ""
    allowed_use = facts.get("allowed_use") or ctx.plot.allowed_use or ""
    return facts, usable_sqm, category, allowed_use


class _BaseScenarioAgent(Agent):
    scenario_key = ""

    def _store(self, ctx: AgentContext, data: dict) -> AgentResult:
        store = ctx.get("scenario_inputs") or {}
        store[self.scenario_key] = data
        ctx.set("scenario_inputs", store)
        return AgentResult(success=True, data=data)


class ConstructionScenarioAgent(_BaseScenarioAgent):
    name = "ConstructionScenarioAgent"
    scenario_key = "construction"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        _, usable_sqm, category, allowed_use = _scenario_context(ctx)
        applicable = _construction_allowed(category, allowed_use)
        buildable_sqm = round(usable_sqm * 0.30, 1)  # ~30% застройки полезной площади
        data = {
            "scenario": "construction",
            "applicable": applicable,
            "feasible": applicable,
            "usable_area_sqm": round(usable_sqm, 1),
            "buildable_area_sqm": buildable_sqm,
            "build_cost_per_sqm": 80_000,
            "sale_price_per_sqm": 120_000,
            "note": "" if applicable else "Строительство недопустимо для категории/ВРИ участка.",
        }
        return self._store(ctx, data)


class AgricultureScenarioAgent(_BaseScenarioAgent):
    name = "AgricultureScenarioAgent"
    scenario_key = "agriculture"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        _, usable_sqm, category, allowed_use = _scenario_context(ctx)
        applicable = _agriculture_allowed(category, allowed_use)
        usable_ha = usable_sqm / 10_000.0
        data = {
            "scenario": "agriculture",
            "applicable": applicable,
            "feasible": applicable,
            "usable_area_sqm": round(usable_sqm, 1),
            "usable_area_ha": round(usable_ha, 3),
            "setup_cost_per_ha": 60_000,        # подготовка/ввод в оборот
            "annual_revenue_per_ha": 45_000,    # валовый доход с гектара в год
            "annual_cost_per_ha": 25_000,       # операционные затраты в год
            "note": "" if applicable else "Сельхозиспользование не соответствует категории/ВРИ.",
        }
        return self._store(ctx, data)


class LeaseScenarioAgent(_BaseScenarioAgent):
    name = "LeaseScenarioAgent"
    scenario_key = "lease"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        _, usable_sqm, category, allowed_use = _scenario_context(ctx)
        usable_ha = usable_sqm / 10_000.0
        data = {
            "scenario": "lease",
            "applicable": True,
            "feasible": True,
            "usable_area_sqm": round(usable_sqm, 1),
            "usable_area_ha": round(usable_ha, 3),
            "annual_lease_per_ha": 30_000,   # ставка аренды за гектар в год
            "annual_holding_cost_per_ha": 5_000,
            "note": "Сдача в аренду применима для большинства участков.",
        }
        return self._store(ctx, data)


class ResaleScenarioAgent(_BaseScenarioAgent):
    name = "ResaleScenarioAgent"
    scenario_key = "resale"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        _, usable_sqm, _, _ = _scenario_context(ctx)
        data = {
            "scenario": "resale",
            "applicable": True,
            "feasible": True,
            "usable_area_sqm": round(usable_sqm, 1),
            "transaction_cost_pct": 0.05,  # издержки сделки от цены покупки
            "note": "Перепродажа применима независимо от категории.",
        }
        return self._store(ctx, data)


class MixedUseScenarioAgent(_BaseScenarioAgent):
    name = "MixedUseScenarioAgent"
    scenario_key = "mixed_use"

    async def run(self, input: dict, ctx: AgentContext) -> AgentResult:
        _, usable_sqm, category, allowed_use = _scenario_context(ctx)
        applicable = _construction_allowed(category, allowed_use) and _agriculture_allowed(category, allowed_use)
        # Половина площади под застройку, половина под сельхоз.
        build_part_sqm = round(usable_sqm * 0.5, 1)
        agri_part_ha = (usable_sqm * 0.5) / 10_000.0
        data = {
            "scenario": "mixed_use",
            "applicable": applicable,
            "feasible": applicable,
            "usable_area_sqm": round(usable_sqm, 1),
            "build_part_sqm": build_part_sqm,
            "buildable_area_sqm": round(build_part_sqm * 0.30, 1),
            "agri_part_ha": round(agri_part_ha, 3),
            "build_cost_per_sqm": 80_000,
            "sale_price_per_sqm": 120_000,
            "annual_revenue_per_ha": 45_000,
            "annual_cost_per_ha": 25_000,
            "note": "" if applicable else "Смешанный сценарий недоступен: несовместимые виды использования.",
        }
        return self._store(ctx, data)
