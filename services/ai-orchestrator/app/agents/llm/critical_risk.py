import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.pipeline.context import AgentContext

CRITICAL_RISK_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "stop_has_critical": {"type": "boolean"},
        "stop_factors": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "data_quality_issue": {"type": "boolean"},
    },
    "required": ["risk_level", "stop_has_critical", "stop_factors", "summary"],
}


class CriticalRiskAgent(BaseLLMAgent):
    name = "CriticalRiskAgent"
    prompt_file = "critical_risk.txt"
    response_schema = CRITICAL_RISK_SCHEMA

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        data_request = ctx.get("DataRequestAgent") or {}
        warnings = data_request.get("warnings") or []
        return json.dumps({
            "plot": ctx.plot.__dict__,
            "data_quality": {
                "nspd_unavailable": any(
                    "nspd" in str(item).lower()
                    and ("failed" in str(item).lower() or "connection" in str(item).lower())
                    for item in warnings
                ),
                "warnings": warnings,
                "rule": (
                    "Недоступность NSPD или карты — это риск качества данных/повторной проверки, "
                    "но не юридический стоп-фактор сама по себе."
                ),
            },
            "legal": ctx.get("LegalAgent"),
            "land_use": ctx.get("LandUseAgent"),
            "restrictions": ctx.get("RestrictionsAgent"),
            "geo": ctx.get("GeoAgent"),
        }, ensure_ascii=False, default=str)
