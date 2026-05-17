import json
from app.agents.llm.base_llm import BaseLLMAgent
from app.agents.llm.report_context import build_report_context
from app.pipeline.context import AgentContext

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "recommendation": {"type": "string", "enum": ["proceed", "proceed_with_caution", "do_not_proceed"]},
        "verdict": {"type": "string"},
        "legal_risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "best_scenario": {"type": "string", "maxLength": 50},
        "stop_factors_active": {"type": "boolean"},
        "key_findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overall_score", "recommendation", "verdict", "legal_risk", "best_scenario"],
}


class ChiefDecisionAgent(BaseLLMAgent):
    name = "ChiefDecisionAgent"
    prompt_file = "chief_decision.txt"
    response_schema = DECISION_SCHEMA
    temperature = 0.1  # deterministic decisions

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Chief Decision receives structured outputs from all analysis agents
        # CRITICAL: Must check CriticalRiskAgent output first.
        report_context = build_report_context(ctx)
        return json.dumps({
            "profile": ctx.profile.__dict__,
            "plot_summary": ctx.plot.__dict__,
            "data_quality": report_context.get("data_quality"),
            "area_summary": report_context.get("area_summary"),
            "map_summary": ctx.get("map_summary") or ctx.get("GeoAgent", {}).get("map_summary"),
            "legal": ctx.get("LegalAgent"),
            "land_use": ctx.get("LandUseAgent"),
            "restrictions": ctx.get("RestrictionsAgent"),
            "infrastructure": ctx.get("InfrastructureAgent"),
            "soil_summary": report_context.get("soil_summary"),
            "market": ctx.get("MarketAgent"),
            "critical_risk": ctx.get("CriticalRiskAgent"),
            "scenario_ranking": ctx.get("ScenarioRankingAgent"),
        }, ensure_ascii=False, default=str)
