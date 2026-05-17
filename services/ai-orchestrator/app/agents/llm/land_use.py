import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.agents.llm.spatial_context import compact_spatial_context
from app.pipeline.context import AgentContext

class LandUseAgent(BaseLLMAgent):
    name = "LandUseAgent"
    prompt_file = "land_use_agent.txt"

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        return json.dumps({
            "profile": ctx.profile.__dict__,
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
