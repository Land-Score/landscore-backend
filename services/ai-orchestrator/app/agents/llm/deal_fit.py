import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.pipeline.context import AgentContext

DEAL_FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "fit_reasons": {"type": "array", "items": {"type": "string"}},
        "mismatch_reasons": {"type": "array", "items": {"type": "string"}},
        "personal_recommendation": {"type": "string"},
    },
    "required": ["fit_score", "fit_reasons", "mismatch_reasons", "personal_recommendation"],
}


class DealFitAgent(BaseLLMAgent):
    name = "DealFitAgent"
    prompt_file = "deal_fit.txt"
    response_schema = DEAL_FIT_SCHEMA
    temperature = 0.2

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        # Personalization agent: this one MAY see the full profile, including
        # priorities and risk tolerance — that is its whole purpose.
        decision = ctx.get("ChiefDecisionAgent") or {}
        ranking = ctx.get("ScenarioRankingAgent") or {}
        return json.dumps({
            "profile": ctx.profile.__dict__,
            "plot_summary": {
                "cadastral_number": ctx.plot.cadastral_number,
                "address": ctx.plot.address,
                "area": ctx.plot.area,
                "category": ctx.plot.category,
                "allowed_use": ctx.plot.allowed_use,
                "price": ctx.plot.price,
            },
            "chief_decision": decision,
            "scenario_ranking": ranking,
            "rule": (
                "Оцени соответствие участка именно этому профилю. Учитывай приоритеты "
                "и толерантность к риску пользователя. Не противоречь стоп-факторам: если "
                "решение do_not_proceed, fit_score не может быть высоким."
            ),
        }, ensure_ascii=False, default=str)
