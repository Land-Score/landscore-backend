import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.agents.llm.report_context import build_report_context
from app.pipeline.context import AgentContext

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "legal": {"type": "string"},
        "land_use": {"type": "string"},
        "restrictions": {"type": "string"},
        "infrastructure": {"type": "string"},
        "market": {"type": "string"},
        "scenarios": {"type": "string"},
        "decision": {"type": "string"},
        "stop_factors": {"type": "array", "items": {"type": "string"}},
        "overall_score": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
    },
    "required": ["summary", "legal", "land_use", "restrictions", "decision", "stop_factors"],
}


class ReportAgent(BaseLLMAgent):
    name = "ReportAgent"
    prompt_file = "report.txt"
    response_schema = REPORT_SCHEMA
    temperature = 0.3

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        report_context = build_report_context(ctx)
        critical_risk = ctx.get("CriticalRiskAgent") or {}
        decision = ctx.get("ChiefDecisionAgent") or {}
        # Surface stop-factors explicitly so the report can never say
        # "high ROI -> buy" while a stop-factor is active.
        report_context["critical_risk_summary"] = {
            "stop_has_critical": bool(critical_risk.get("stop_has_critical")),
            "stop_factors": critical_risk.get("stop_factors") or [],
            "recommendation": decision.get("recommendation"),
            "overall_score": decision.get("overall_score"),
            "rule": (
                "Если stop_has_critical=true, в разделе stop_factors перечисли стоп-факторы, "
                "а в decision и summary прямо укажи, что сделку нельзя продолжать независимо от доходности."
            ),
        }
        return json.dumps(report_context, ensure_ascii=False, default=str)
