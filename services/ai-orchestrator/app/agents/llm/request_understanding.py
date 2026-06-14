import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.pipeline.context import AgentContext

REQUEST_UNDERSTANDING_SCHEMA = {
    "type": "object",
    "properties": {
        "request_type": {"type": "string", "enum": ["check", "search", "unknown"]},
        "cadastral_number": {"type": ["string", "null"]},
        "address": {"type": ["string", "null"]},
        "purpose": {"type": ["string", "null"]},
        "region": {"type": ["string", "null"]},
        "budget": {"type": ["string", "null"]},
        "timeframe": {"type": ["string", "null"]},
        "ambiguity": {"type": ["string", "null"]},
        "summary": {"type": "string"},
    },
    "required": ["request_type", "summary"],
}


class RequestUnderstandingAgent(BaseLLMAgent):
    name = "RequestUnderstandingAgent"
    prompt_file = "request_understanding.txt"
    response_schema = REQUEST_UNDERSTANDING_SCHEMA
    temperature = 0.2

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Sees only the raw request signals: what the user typed plus
        # the structured hints already on the plot/profile. No agent outputs.
        return json.dumps({
            "owner_type": ctx.owner_type,
            "raw_input": input,
            "known_cadastral_number": ctx.plot.cadastral_number or None,
            "known_address": ctx.plot.address or None,
            "known_coordinates": (
                {"lat": ctx.plot.lat, "lng": ctx.plot.lng}
                if (ctx.plot.lat or ctx.plot.lng) else None
            ),
            "profile": {
                "client_type": ctx.profile.client_type,
                "main_task": ctx.profile.main_task,
                "region": ctx.profile.region,
                "preferred_scenarios": ctx.profile.preferred_scenarios,
            },
            "rule": (
                "Если уже есть кадастровый номер, адрес или координаты — это запрос на проверку (check). "
                "Если пользователь описывает задачу без конкретного объекта — это поиск (search)."
            ),
        }, ensure_ascii=False, default=str)
