import json

from app.agents.llm.base_llm import BaseLLMAgent
from app.agents.llm.spatial_context import compact_spatial_context
from app.pipeline.context import AgentContext

class RestrictionsAgent(BaseLLMAgent):
    name = "RestrictionsAgent"
    prompt_file = "restrictions_agent.txt"

    def _build_user_message(self, input: dict, ctx: AgentContext) -> str:
        return json.dumps({
            "plot": ctx.plot.__dict__,
            "data_request": {
                key: value
                for key, value in (ctx.get("DataRequestAgent") or {}).items()
                if key not in {"spatial_layers", "nspd"}
            },
            "spatial_layers": compact_spatial_context(ctx.get("spatial_layers")),
            "rule": (
                "Используй найденные слои NSPD, если они есть. "
                "Если слоев нет из-за сбоя источника, это не подтвержденное отсутствие ограничений."
            ),
        }, ensure_ascii=False, default=str)
