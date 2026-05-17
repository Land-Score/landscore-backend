import json
from app.agents.llm.base_llm import BaseLLMAgent
from app.pipeline.context import AgentContext

LEGAL_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                    "is_stop_factor": {"type": "boolean"},
                },
            },
        },
        "ownership_confirmed": {"type": "boolean"},
        "encumbrances": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["risk_level", "risks", "summary"],
}


class LegalAgent(BaseLLMAgent):
    name = "LegalAgent"
    prompt_file = "legal_agent.txt"
    response_schema = LEGAL_SCHEMA

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Context isolation: Legal agent sees ONLY legal-relevant data
        legal_context = ctx.get_for_agent("FactNormalizationAgent", "DocumentExtractionAgent")
        data_request = ctx.get("DataRequestAgent") or {}
        warnings = data_request.get("warnings") or []
        nspd_unavailable = any(
            "nspd" in str(item).lower() and ("failed" in str(item).lower() or "connection" in str(item).lower())
            for item in warnings
        )
        return json.dumps({
            "cadastral_number": ctx.plot.cadastral_number,
            "category": ctx.plot.category,
            "allowed_use": ctx.plot.allowed_use,
            "owner_type": ctx.plot.owner_type,
            "address": ctx.plot.address,
            "area_sqm": ctx.plot.area,
            "cadastral_price": ctx.plot.price,
            "egrn_data": ctx.plot.egrn_data,
            "extracted_documents": ctx.plot.extracted_documents,
            "user_purpose": ctx.profile.main_task,
            "data_quality": {
                "nspd_unavailable": nspd_unavailable,
                "dataset_available": bool(data_request.get("dataset_available")),
                "warnings": warnings,
                "rule": (
                    "Если nspd_unavailable=true, это сбой получения публичных данных, "
                    "а не подтвержденный юридический дефект участка."
                ),
            },
            **legal_context,
        }, ensure_ascii=False)
