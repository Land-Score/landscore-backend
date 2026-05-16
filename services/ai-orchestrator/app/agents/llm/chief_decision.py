import json
from app.agents.llm.base_llm import BaseLLMAgent
from app.pipeline.context import AgentContext

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "recommendation": {"type": "string", "enum": ["proceed", "proceed_with_caution", "do_not_proceed"]},
        "verdict": {"type": "string"},
        "legal_risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "best_scenario": {"type": "string"},
        "stop_factors_active": {"type": "boolean"},
        "key_findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overall_score", "recommendation", "verdict", "legal_risk", "best_scenario"],
}


class ChiefDecisionAgent(BaseLLMAgent):
    name = "ChiefDecisionAgent"
    prompt_file = "chief_decision_agent.txt"
    response_schema = DECISION_SCHEMA
    temperature = 0.1  # deterministic decisions

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Chief Decision receives structured outputs from all analysis agents
        # CRITICAL: Must check CriticalRiskAgent output first
        critical = ctx.get("CriticalRiskAgent", {})
        if critical.get("has_stop_factor"):
            # Override: stop factors block positive recommendation
            stop_factors = critical.get("stop_factors", [])

        return json.dumps({
            "profile": ctx.profile.__dict__,
            "plot_summary": ctx.plot.__dict__,
            "legal": ctx.get("LegalAgent"),
            "land_use": ctx.get("LandUseAgent"),
            "restrictions": ctx.get("RestrictionsAgent"),
            "infrastructure": ctx.get("InfrastructureAgent"),
            "market": ctx.get("MarketAgent"),
            "critical_risk": ctx.get("CriticalRiskAgent"),
            "scenario_ranking": ctx.get("ScenarioRankingAgent"),
        }, ensure_ascii=False, default=str)
