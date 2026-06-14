import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.agents.llm.spatial_context import compact_spatial_context
from app.pipeline.context import AgentContext

LAND_USE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": ["string", "null"]},
        "allowed_use": {"type": ["string", "null"]},
        "permitted_activities": {"type": "array", "items": {"type": "string"}},
        "conditionally_permitted": {"type": "array", "items": {"type": "string"}},
        "purpose_conformance": {
            "type": "string",
            "enum": ["conforms", "requires_change", "not_permitted", "unknown"],
        },
        "change_possibility": {"type": ["string", "null"]},
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
        "summary": {"type": "string"},
    },
    "required": ["purpose_conformance", "risks", "summary"],
}


class LandUseAgent(BaseLLMAgent):
    name = "LandUseAgent"
    prompt_file = "land_use_agent.txt"
    response_schema = LAND_USE_SCHEMA
    temperature = 0.1

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Context isolation: Land Use sees only the user's purpose, never
        # financial priorities (budget, desired margin, risk tolerance).
        return json.dumps({
            "user_purpose": ctx.profile.main_task,
            "plot": ctx.plot.__dict__,
            "data_request": {
                key: value
                for key, value in (ctx.get("DataRequestAgent") or {}).items()
                if key not in {"spatial_layers", "nspd"}
            },
            "spatial_layers": compact_spatial_context(ctx.get("spatial_layers")),
            "rule": (
                "Если публичный источник не ответил, не проси данные у пользователя заново; "
                "объясни, что вывод ограничен недоступностью источника."
            ),
        }, ensure_ascii=False, default=str)
