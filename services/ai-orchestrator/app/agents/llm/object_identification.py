import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.pipeline.context import AgentContext

OBJECT_IDENTIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["identified", "ambiguous", "not_found"]},
        "cadastral_number": {"type": ["string", "null"]},
        "address": {"type": ["string", "null"]},
        "lat": {"type": ["number", "null"]},
        "lng": {"type": ["number", "null"]},
        "area_sqm": {"type": ["number", "null"]},
        "category": {"type": ["string", "null"]},
        "allowed_use": {"type": ["string", "null"]},
        "owner_type": {"type": ["string", "null"]},
        "discrepancies": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
}


class ObjectIdentificationAgent(BaseLLMAgent):
    name = "ObjectIdentificationAgent"
    prompt_file = "object_identification.txt"
    response_schema = OBJECT_IDENTIFICATION_SCHEMA
    temperature = 0.2

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Identification works on raw object identifiers and the request
        # understanding output. No financial or scenario data.
        request_understanding = ctx.get("RequestUnderstandingAgent") or {}
        return json.dumps({
            "request_understanding": {
                "cadastral_number": request_understanding.get("cadastral_number"),
                "address": request_understanding.get("address"),
                "region": request_understanding.get("region"),
            },
            "plot": {
                "cadastral_number": ctx.plot.cadastral_number or None,
                "address": ctx.plot.address or None,
                "lat": ctx.plot.lat or None,
                "lng": ctx.plot.lng or None,
                "area_sqm": ctx.plot.area or None,
                "category": ctx.plot.category or None,
                "allowed_use": ctx.plot.allowed_use or None,
                "owner_type": ctx.plot.owner_type or None,
                "egrn_data": ctx.plot.egrn_data,
            },
            "profile_region": ctx.profile.region,
            "rule": (
                "Приоритет у данных ЕГРН. При расхождениях зафиксируй их в discrepancies. "
                "Не исправляй кадастровый номер самовольно. Если объект не найден, верни status=not_found."
            ),
        }, ensure_ascii=False, default=str)
