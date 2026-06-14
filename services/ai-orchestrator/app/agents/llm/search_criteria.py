import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.pipeline.context import AgentContext

SEARCH_CRITERIA_SCHEMA = {
    "type": "object",
    "properties": {
        "region": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "allowed_use": {"type": "array", "items": {"type": "string"}},
        "area_min_ha": {"type": ["number", "null"]},
        "area_max_ha": {"type": ["number", "null"]},
        "price_max": {"type": ["number", "null"]},
        "ownership_form": {"type": ["string", "null"]},
        "scenarios": {"type": "array", "items": {"type": "string"}},
        "soft_filters": {"type": "array", "items": {"type": "string"}},
        "prioritization": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["scenarios", "summary"],
}


class SearchCriteriaAgent(BaseLLMAgent):
    name = "SearchCriteriaAgent"
    prompt_file = "search_criteria.txt"
    response_schema = SEARCH_CRITERIA_SCHEMA
    temperature = 0.2

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Criteria extraction sees the free-text request and the profile
        # (profile drives defaults). No downstream analysis outputs.
        request_understanding = ctx.get("RequestUnderstandingAgent") or {}
        return json.dumps({
            "raw_input": input,
            "request_understanding": {
                "purpose": request_understanding.get("purpose"),
                "region": request_understanding.get("region"),
                "budget": request_understanding.get("budget"),
                "address": request_understanding.get("address"),
            },
            "profile": {
                "client_type": ctx.profile.client_type,
                "main_task": ctx.profile.main_task,
                "region": ctx.profile.region,
                "priority": ctx.profile.priority,
                "risk_tolerance": ctx.profile.risk_tolerance,
                "preferred_scenarios": ctx.profile.preferred_scenarios,
            },
            "rule": (
                "Не выдумывай значения, которых нет в запросе или профиле; оставляй null. "
                "Используй профиль как источник значений по умолчанию (регион, сценарии)."
            ),
        }, ensure_ascii=False, default=str)
