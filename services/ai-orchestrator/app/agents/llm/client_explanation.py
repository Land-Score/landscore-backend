import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.agents.llm.report_context import build_report_context
from app.pipeline.context import AgentContext

CLIENT_EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "verdict": {
            "type": "string",
            "enum": ["proceed", "proceed_with_caution", "do_not_proceed"],
        },
        "stop_factors": {"type": "array", "items": {"type": "string"}},
        "usable_area_ha": {"type": ["number", "null"]},
    },
    "required": ["explanation", "verdict", "stop_factors"],
}


class ClientExplanationAgent(BaseLLMAgent):
    name = "ClientExplanationAgent"
    prompt_file = "client_explanation.txt"
    response_schema = CLIENT_EXPLANATION_SCHEMA
    temperature = 0.3

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        report_context = build_report_context(ctx)
        critical_risk = ctx.get("CriticalRiskAgent") or {}
        decision = ctx.get("ChiefDecisionAgent") or {}
        # Critical-risk override must reach the plain-language output too.
        report_context["critical_risk_summary"] = {
            "stop_has_critical": bool(critical_risk.get("stop_has_critical")),
            "stop_factors": critical_risk.get("stop_factors") or [],
            "recommendation": decision.get("recommendation"),
            "rule": (
                "Если есть стоп-факторы, объясни простыми словами, что покупать нельзя "
                "до их устранения, даже если доходность выглядит привлекательно. "
                "verdict обязан совпадать с recommendation главного решения."
            ),
        }
        return json.dumps(report_context, ensure_ascii=False, default=str)
