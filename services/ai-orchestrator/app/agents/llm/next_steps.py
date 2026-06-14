import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.agents.llm.report_context import build_report_context
from app.pipeline.context import AgentContext

NEXT_STEPS_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "action": {"type": "string"},
                    "reason": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["title", "action"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["steps"],
}


class NextStepsAgent(BaseLLMAgent):
    name = "NextStepsAgent"
    prompt_file = "next_steps.txt"
    response_schema = NEXT_STEPS_SCHEMA
    temperature = 0.3

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        report_context = build_report_context(ctx)
        critical_risk = ctx.get("CriticalRiskAgent") or {}
        report_context["critical_risk_summary"] = {
            "stop_has_critical": bool(critical_risk.get("stop_has_critical")),
            "stop_factors": critical_risk.get("stop_factors") or [],
            "rule": (
                "Если есть стоп-факторы, первым шагом поставь их устранение/проверку "
                "и предупреди не вносить аванс до этого."
            ),
        }
        return json.dumps(report_context, ensure_ascii=False, default=str)
